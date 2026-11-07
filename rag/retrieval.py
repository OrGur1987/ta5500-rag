"""Retrieval over the chunk index: location-aware search, table-fragment grouping and gated reference expansion.

Location (planning areas, zone types) comes from explicit filters when given; otherwise it is detected in the query text.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from rag import config
from rag.embed import embed, embed_query, load_index, normalize_for_embedding
from rag.tree import in_subtree

QUOTES = re.compile(r"[\"'׳״`]")
AREA_NUMBER = re.compile(r"(?<![\d.])(\d{3})(?![\d.%])")
SUBAREA = re.compile(r"(?<!\d)(\d{3})\s*([א-ת])(?![א-ת])")
SUBAREA_PREFIX = re.compile(r"^\d{3}\s*[א-ת]?['׳]?\s*[-–.]?\s*")
AREA_VALUE = re.compile(r"^(\d{3})([א-ת])?$|^([א-ת])(\d{3})$")
PUNCT = re.compile(r"[\s\u200e\u200f\u202a-\u202e'\"׳״`\-–—־.]")  # spacing, bidi marks, quotes and dashes that vary between sources
BIDI = re.compile(r"[\u200e\u200f\u202a-\u202e]")
MIN_ENTITY_NAME = 6  # shortest row label looked for in free text
HEADING_REF = re.compile(r"(?<![א-ת])[הוכלבמש]{0,2}(?:פרק|סעיפים|סעיף)\s+(\d+(?:\.\d+)*)")
STREET_CODE = re.compile(r"(?<![א-ת])ר\s*[-־–]\s*(\d+)")
STREET_PREFIX = re.compile(r"^(?:רחוב|רח)\s")
ZONE_TYPE_ID = re.compile(r"^3\.(?:[2-9]|1[0-2])\.\d+$")
CATEGORY_ID = re.compile(r"^3\.\d+$")
NUMBERED = re.compile(r"^\d+(\.\d+)*$")
CLAUSE_ID = re.compile(r"^\d+(?:\.\d+)*(?:\([^()]+\))+$")
CLAUSE_LABEL = re.compile(r"\([^()]+\)$")
HEB_PREFIXES = "בלהוכמש"
LONG_DASHES = str.maketrans({"–": "-", "—": "-"})
MIN_TITLE = 4
VIAS = {"location": 0, "zone": 0, "search": 1, "section": 2, "ref": 3, "focus_note": 4}
FOLLOWERS = ("general", "notes")  # shown right after the hits they qualify, whatever their own score


def norm(text: str) -> str:
    """Comparison form: no quotes or extra spaces, and one spelling of בנייה / בניה (the table uses both)."""
    return re.sub(r"\s+", " ", QUOTES.sub("", text)).replace("בנייה", "בניה").strip()


def phrase_pattern(text: str) -> re.Pattern:
    """Whole-word match of a phrase in a label or a question: Hebrew prefixes on its first word, the article on the others."""
    first, *rest = (re.escape(w) for w in text.split())
    tail = "".join(rf"\s+ה?{w}" for w in rest)
    return re.compile(rf"(?<![א-ת])[{HEB_PREFIXES}]{{0,2}}{first}{tail}(?![א-ת])")


def parse_area(value: str) -> tuple[str, str | None]:
    """Area code and optional sub-area letter from '703', '101ה', "101 ה'", '101-ה' or 'ה101'; anything else is returned as is."""
    s = PUNCT.sub("", str(value))
    m = AREA_VALUE.match(s)
    if not m:
        return str(value).strip(), None
    return (m[1], m[2]) if m[1] else (m[4], m[3])


@dataclass
class Result:
    hits: list[dict]  # chunk_id, score, via, reason (Hebrew), tokens, pages, plus from_chunk/mandatory/depth for follow-ups
    location: dict  # areas, subareas, zone_types and where they came from
    top_score: float  # best similarity among the directly retrieved chunks
    band: str  # high / medium / low
    band_label: str  # the same in Hebrew, for the assistant
    omitted: list[dict] = field(default_factory=list)  # chunk_id, citation, tokens of zone chunks left out for size


class Retriever:
    def __init__(self, out_dir: Path, model_key: str | None = None):
        self.m = config.model(model_key)
        self.cfg = config.RETRIEVAL
        self._note_cache: dict[str, np.ndarray] = {}
        self.chunks, self.matrix = load_index(out_dir / "chunks.parquet", out_dir / f"embeddings_{self.m['name']}.parquet")
        nodes = pd.read_parquet(out_dir / "nodes.parquet")
        ids = self.chunks.index
        self.owner = {n: cid for cid, ns in self.chunks["node_ids"].items() for n in ns}
        self.plan_record = pd.Series(ids.str.startswith("5.4["), index=ids)
        self.in_degree = Counter(
            t for cid, refs in self.chunks["refs_out"].items()
            for t in {self.owner.get(r["target"]) for r in refs} - {None, cid})
        self.by_section = self.chunks.groupby("section_id").groups
        self._index_zone_types(nodes)
        self._index_headings(nodes)
        self._index_areas()
        self._index_entities()

    # ---- gazetteers ---------------------------------------------------------------------------

    def _index_zone_types(self, nodes: pd.DataFrame) -> None:
        """Zone types are the sub-sections of 3.2-3.12 plus every category without such sub-sections.

        Categories are the sections 3.2-3.12; a category id as a filter stands for all its types. A title shorter than
        MIN_TITLE (e.g. 'ים') is a type by id but is not looked for in free text.
        """
        names = dict(zip(nodes["id"], nodes["title"]))
        categories = {i: norm(t) for i, t in names.items() if CATEGORY_ID.match(i) and 2 <= int(i[2:]) <= 12}
        types = {i: norm(t) for i, t in names.items() if ZONE_TYPE_ID.match(i) and norm(t) != "כללי"}
        for c, title in categories.items():
            if not any(i.startswith(f"{c}.") for i in types):
                types[c] = title
        titles = {t: i for i, t in types.items() if len(t) >= MIN_TITLE}
        self.zone_title = {i: names[i] for i in types}
        self.zone_category_title = {c: names[c] for c in categories}
        self.zone_members = {c: [i for i in self.zone_title if i == c or i.startswith(f"{c}.")] for c in categories}
        self.zone_patterns = [
            (i, phrase_pattern(t)) for t, i in sorted(titles.items(), key=lambda kv: -len(kv[0]))]
        self.zone_scope = {
            i: [cid for cid, ns in self.chunks["node_ids"].items() if any(n == i or n.startswith((f"{i}(", f"{i}[")) for n in ns)]
            for i in self.zone_title}
        # the 'כללי' sub-section of a category holds instructions for all its zone types
        self.zone_general = {
            f"3.{i.split('.')[1]}": [cid for cid, ns in self.chunks["node_ids"].items() if any(n == i or n.startswith((f"{i}(", f"{i}[")) for n in ns)]
            for i, t in names.items() if ZONE_TYPE_ID.match(i) and i.endswith(".1") and norm(t) == "כללי"}
        zone_text = self.chunks["zone"].fillna("")
        self.record_zones = {cid: set(self.find_zone_types(z)) for cid, z in zone_text.items() if z}

    def _index_headings(self, nodes: pd.DataFrame) -> None:
        """Chapters, sections and subsections that a filter can name by number or by an unambiguous full title."""
        titles = {i: norm(t) for i, k, t in zip(nodes["id"], nodes["kind"], nodes["title"])
                  if k in ("chapter", "section", "subsection") and not i.startswith("root")}
        self.heading_nodes = set(titles)
        counts = Counter(titles.values())
        self.heading_title = dict(zip(nodes["id"], nodes["title"]))
        self.heading_by_title = {t: i for i, t in titles.items() if counts[t] == 1}
        children = dict(zip(nodes["id"], nodes["children"]))
        self.heading_children = {i: [c for c in children[i] if c in titles] for i in titles}
        self.heading_scope = {i: [cid for cid, ns in self.chunks["node_ids"].items() if any(in_subtree(n, i) for n in ns)]
                              for i in titles}
        # a clause id names the chunk that holds it (small sibling clauses share a chunk)
        self.clause_nodes = {n for n in self.owner if CLAUSE_ID.match(n)}
        self.heading_scope.update({n: [self.owner[n]] for n in self.clause_nodes})

    def _index_areas(self) -> None:
        records = self.chunks[self.chunks["area"].notna()]
        self.area_codes = set(records["area"])
        self.subareas = []
        for cid, row in records.iterrows():
            m = SUBAREA.match(norm(row["zone"])) if isinstance(row["zone"], str) else None
            if m:
                self.subareas.append({"code": f"{m[1]}{m[2]}", "area": m[1], "name": norm(SUBAREA_PREFIX.sub("", norm(row["zone"])))})
        self.subareas.sort(key=lambda s: s["code"])
        self.area_by_name = {}
        for code, name in records[["area", "area_name"]].drop_duplicates().itertuples(index=False):
            for part in [name, *name.split(",")] if name else []:
                part = norm(part)
                for variant in {part, part.removeprefix("ה")}:
                    if len(variant) >= MIN_TITLE:
                        self.area_by_name[variant] = code
        # sub-area names that occur in a single planning area, e.g. 'מתחם הנגרים'
        tails = records["zone"].dropna().map(lambda z: norm(SUBAREA_PREFIX.sub("", z)))
        areas_of = pd.DataFrame({"tail": tails, "area": records["area"]}).groupby("tail")["area"].agg(set)
        for tail, areas in areas_of.items():
            if len(tail) >= 6 and len(areas) == 1:
                self.area_by_name.setdefault(tail, next(iter(areas)))

    @staticmethod
    def _street_name(label: str) -> str:
        """A street's name without its code and the word 'רחוב'/'רח', to match the same street written differently."""
        name = re.sub(r"(?<![א-ת])(?:רחוב|רח)(?![א-ת])", " ", STREET_CODE.sub(" ", label))
        return re.sub(r"[\s,\-]+", " ", name).strip()

    def _index_entities(self) -> None:
        """Every table 5.4 row is an entity named by its first column (the record's `zone`).

        Types: subarea ('101ה'), street (a code 'R-12' or a street name), symbol, area (a whole-area row), zone_type (the
        label is a chapter-3 zone) and named (anything else). A street written without its code takes the code of
        the same street elsewhere in the table. Rows with the same code or label (a street under several areas) are one entity.
        """
        rows = [(cid, row["area"], row["zone"], norm(row["zone"])) for cid, row in self.chunks[self.plan_record].iterrows()
                if isinstance(row["zone"], str)]
        code_of_street = {self._street_name(key): f"ר-{m[1]}" for _, _, _, key in rows if (m := STREET_CODE.search(key))}
        self.entities: dict[str, dict] = {}
        self.entity_lookup: dict[str, list[str]] = {}
        self.row_label = {cid: key for cid, _, _, key in rows}
        for cid, area, label, key in rows:
            if m := SUBAREA.match(key):
                kind, code = "subarea", f"{m[1]}{m[2]}"
            elif m := STREET_CODE.search(key):
                kind, code = "street", f"ר-{m[1]}"
            elif STREET_PREFIX.match(key):
                kind, code = "street", code_of_street.get(self._street_name(key), "")
            elif key.startswith(("סימבול", "מוסד ציבורי")):
                kind, code = "symbol", ""
            elif re.match(r"^אזור תכנון \d{3}", key):
                kind, code = "area", ""
            elif self.record_zones.get(cid):
                kind, code = "zone_type", ""
            else:
                kind, code = "named", ""
            entity = self.entities.setdefault(code or key, {"code": code, "type": kind, "label": label, "areas": [], "chunks": []})
            entity["chunks"].append(cid)
            if code and code not in norm(entity["label"]) and code in key:  # name the entity by a row that carries its code
                entity["label"] = label
            if area not in entity["areas"]:
                entity["areas"].append(area)
            names = [code, key, self._street_name(key) if kind == "street" else ""]
            for name in dict.fromkeys(n for n in names if n):
                if cid not in self.entity_lookup.setdefault(name, []):
                    self.entity_lookup[name].append(cid)
        for entity in self.entities.values():  # the rows a filter by this entity returns, whole-phrase matches included
            entity["rows"] = len(self.resolve_entity(entity["code"] or norm(entity["label"])))
        names = {self._street_name(norm(e["label"])) if e["type"] == "street" else norm(e["label"])
                 for e in self.entities.values() if e["type"] in ("street", "symbol", "named")}
        self.entity_patterns = [(n, phrase_pattern(n)) for n in sorted(names, key=lambda n: -len(n)) if len(n) >= MIN_ENTITY_NAME]
        self._entity_names = [n for n, _ in self.entity_patterns]
        self._entity_vecs: np.ndarray | None = None

    def _entity_matrix(self) -> np.ndarray:
        """Embeddings of the row names, computed once on first use (a few short requests)."""
        if self._entity_vecs is None:
            texts = [self.m["doc_prefix"] + n for n in self._entity_names]
            self._entity_vecs = np.vstack([embed(texts[i:i + 8], self.m["name"]) for i in range(0, len(texts), 8)])
        return self._entity_vecs

    def link_entities(self, question: str) -> list[str]:
        """The row name the question is clearly closest to, for a name written in another form (the plural) that the exact match misses."""
        if not self._entity_names:
            return []
        sims = self._entity_matrix() @ embed_query(self.m["query_prefix"] + question, self.m["name"])
        order = np.argsort(-sims)
        runner_up = sims[order[1]] if len(order) > 1 else 0.0
        if sims[order[0]] >= self.cfg["link_min"] and sims[order[0]] - runner_up >= self.cfg["link_margin"]:
            return [self._entity_names[order[0]]]
        return []

    def resolve_entity(self, value: str) -> list[str]:
        """Chunk ids of the table rows an entity filter names: a code in any spelling, or a phrase of a row's label."""
        if m := re.fullmatch(r"ר(\d+)", PUNCT.sub("", str(value))):
            found = self.entity_lookup.get(f"ר-{m[1]}", [])
        elif (parsed := parse_area(value))[1]:
            found = self.entity_lookup.get(f"{parsed[0]}{parsed[1]}", [])
        else:
            key = norm(BIDI.sub("", str(value)))
            found = [*self.entity_lookup.get(key, []), *self.entity_lookup.get(self._street_name(key), [])]
            if len(key) >= MIN_TITLE:
                pattern = phrase_pattern(key)
                found += [cid for cid, label in self.row_label.items() if pattern.search(label)]
        wanted = set(found)
        return [cid for cid in self.row_label if cid in wanted]  # in table order

    def find_entities(self, text: str) -> list[str]:
        """Names of streets, symbols and other named rows written in `text`; the longest name wins where names overlap."""
        text, found = norm(text), []
        for name, pattern in self.entity_patterns:
            text, n = pattern.subn(" # ", text)
            if n:
                found.append(name)
        return found

    def resolve_heading(self, value: str) -> str | None:
        """Node id of a chapter, section or clause named by a filter value: its number ('4.3', 'סעיף 4.7.1(ג)') or a full title."""
        key = norm(BIDI.sub("", str(value)))
        m = re.fullmatch(r"(?:(?:פרק|סעיף)\s*)?(\d+(?:\.\d+)*(?:\([^()]+\))*)", key)
        return m[1] if m and (m[1] in self.heading_nodes or m[1] in self.clause_nodes) else self.heading_by_title.get(key)

    def heading_citation(self, node: str) -> str:
        c = config.LABELS["citation"]
        return f"{c['chapter'] if node.isdigit() else c['section']} {node}"

    def _pick_heading(self, node: str, scores: pd.Series, ranked: bool) -> list[str]:
        """The chunks returned for a named heading: all of its subtree if small, else its own chunk and a few more.

        With a question the few are the most similar ones; without, the first in document order up to the zone budget.
        """
        cfg = self.cfg
        scope = self.heading_scope[node]
        own = [c for c in (self.owner.get(node),) if c]
        rest = [c for c in scope if c not in own]
        tokens = self.chunks["tokens"]
        if tokens.loc[scope].sum() <= cfg["zone_max_tokens"]:
            return [*own, *rest]
        if ranked:
            return [*own, *scores.loc[rest].nlargest(cfg["zone_k"]).index]
        picked, spent = [], tokens.loc[own].sum()
        for cid in rest:
            spent += tokens[cid]
            if spent > cfg["zone_max_tokens"]:
                break
            picked.append(cid)
        return [*own, *picked]

    def outline(self, node: str | None = None, depth: int | None = None) -> list[dict]:
        """The plan's headings as a tree with each one's size: the chapters, or the subtree of `node` (number or title), `depth` levels deep."""
        def build(n: str, d: int | None) -> dict:
            scope = self.heading_scope[n]
            return {"id": n, "title": self.heading_title[n], "citation": self.heading_citation(n), "chunks": len(scope),
                    "tokens": int(self.chunks.loc[scope, "tokens"].sum()),
                    "children": [] if d == 1 else [build(c, d and d - 1) for c in self.heading_children[n]]}
        if node is None:
            return [build(i, depth) for i in sorted((i for i in self.heading_nodes if i.isdigit()), key=int)]
        if not (root := self.resolve_heading(node)) or root not in self.heading_nodes:
            raise ValueError(f"unknown heading: {node}")
        return [build(root, depth)]

    def find_headings(self, text: str) -> list[str]:
        """Chapters and sections named in `text` by number ('פרק 4', 'סעיף 4.3')."""
        found = (m[1] for m in HEADING_REF.finditer(norm(text)))
        return list(dict.fromkeys(h for h in found if h in self.heading_nodes))

    def find_zone_types(self, text: str) -> list[str]:
        """Zone-type section ids named in `text`; the longest title wins where titles overlap."""
        text, found = norm(text), []
        for zone_id, pattern in self.zone_patterns:
            text, n = pattern.subn(" # ", text)
            if n:
                found.append(zone_id)
        return found

    def resolve_zone(self, value: str) -> list[str]:
        """Zone-type ids for a filter value: a type id, a category id (all its types), or a type or category name."""
        if value in self.zone_scope:
            return [value]
        if value in self.zone_members:
            return self.zone_members[value]
        named = self.find_zone_types(value)
        if named:
            return named
        key = norm(value)
        return next((self.zone_members[c] for c, t in self.zone_category_title.items() if norm(t) == key), [])

    def locate(self, query: str, areas: list[str] | None, zone_types: list[str] | None, entities: list[str] | None = None,
               headings: list[str] | None = None) -> dict:
        """Explicit filters win; otherwise areas, zone types, row names and headings are read from the query text."""
        if areas is not None or zone_types is not None or entities is not None or headings is not None:
            zones = list(dict.fromkeys(z for v in zone_types or [] for z in self.resolve_zone(v)))
            parsed = [parse_area(a) for a in areas or []]
            codes = list(dict.fromkeys(code for code, _ in parsed))
            whole = {code for code, letter in parsed if not letter}  # a plain code asks for the whole area
            subareas = list(dict.fromkeys(f"{code}{letter}" for code, letter in parsed if letter and code not in whole))
            resolved = {str(v): self.resolve_entity(v) for v in entities or []}
            named = {str(v): self.resolve_heading(v) for v in headings or []}
            return {"areas": codes, "subareas": subareas, "zone_types": zones, "source": "filters", "linked": [],
                    "headings": list(dict.fromkeys(h for h in named.values() if h)),
                    "headings_not_found": [v for v, h in named.items() if not h],
                    "areas_without_records": [a for a in codes if a not in self.area_codes],
                    "entities": list(resolved), "entities_not_found": [v for v, ids in resolved.items() if not ids],
                    "entity_records": list(dict.fromkeys(cid for ids in resolved.values() for cid in ids))}
        q = norm(query)
        found = {c for c in AREA_NUMBER.findall(q) if c in self.area_codes}
        found |= {code for name, code in self.area_by_name.items() if name in q}
        sub = [f"{c}{letter}" for c, letter in SUBAREA.findall(q) if c in self.area_codes]
        named = self.find_entities(q)
        linked = [] if named else self.link_entities(query)  # similarity only when the exact match found no row name
        named += linked
        heads = self.find_headings(q)
        zones = list(dict.fromkeys([*self.find_zone_types(q), *(h for h in heads if h in self.zone_title)]))  # 'section 3.3.5' is a zone type
        loc = {"areas": sorted(found), "subareas": sub, "zone_types": zones, "entities": named,
               "headings": [h for h in heads if h not in self.zone_title]}
        return {**loc, "source": "text" if any(loc.values()) else "none", "areas_without_records": [], "linked": linked,
                "entities_not_found": [], "headings_not_found": [], "entity_records": list(dict.fromkeys(c for n in named for c in self.resolve_entity(n)))}

    # ---- context for the assistant ------------------------------------------------------------

    def citation(self, cid: str) -> str:
        """How a person refers to the chunk: 'סעיף 3.3.2(ב)', 'פרק 4' or 'טבלה 5.4 - אזור תכנון 703 ...'."""
        c = config.LABELS["citation"]
        row = self.chunks.loc[cid]
        if isinstance(row["area"], str):
            where = " ".join(x for x in (c["area"], row["area"], row["area_name"]) if isinstance(x, str))
            return f"{c['table']} - {where}" + (f", {row['zone']}" if isinstance(row["zone"], str) else "")
        first = row["node_ids"][0]
        if first.startswith("5.4["):
            return c["table"]
        if first.startswith("root/"):
            return c["intro"]
        base = re.sub(r"\[\d+\]$", "", first)
        if row["kind"] == "definition":
            return f"{c['section']} {base}, {c['definition']} {row['text'].split(':')[0].strip()}"
        numbered = [n for n in row["node_ids"] if NUMBERED.match(n)]
        sections = [n for n in numbered if not any(n.startswith(f"{o}.") for o in numbered)]  # drop sub-sections of listed ones
        if len(sections) > 1:  # a chunk that merged neighbouring sections
            listed = ", ".join(sections) if len(sections) <= 3 else f"{sections[0]} - {sections[-1]}"
            return f"{c['sections']} {listed}"
        clauses = [n for n in row["node_ids"] if not any(n.startswith(f"{o}(") for o in row["node_ids"])]  # top-level clauses
        parents = {CLAUSE_LABEL.sub("", n) for n in clauses}
        if not numbered and len(clauses) > 1 and len(parents) == 1 and all(CLAUSE_LABEL.search(n) for n in clauses):  # small sibling clauses merged into one chunk
            return f"{c['section']} {parents.pop()}{CLAUSE_LABEL.search(clauses[0])[0]}-{CLAUSE_LABEL.search(clauses[-1])[0]}"
        return f"{c['chapter'] if base.isdigit() else c['section']} {base}"

    def context(self, result: Result) -> str:
        """Hebrew text block for the assistant: match level, detected location and the numbered chunks with pages and reasons."""
        t = config.LABELS["context"]
        loc = result.location
        head = [f"{t['band']}: {result.band_label}" + (f" - {t['low_note']}" if result.band == "low" else "")]
        if loc["areas"]:
            head.append(f"{t['areas']}: {', '.join(loc['areas'])}")
        if loc["zone_types"]:
            head.append(f"{t['zones']}: {', '.join(self.zone_title[z] for z in loc['zone_types'])}")
        if loc["areas_without_records"]:
            head.append(f"{t['no_record']}: {', '.join(loc['areas_without_records'])}")
        if loc["entities_not_found"]:
            head.append(f"{t['entities_missing']}: {', '.join(loc['entities_not_found'])}")
        blocks = ["\n".join(head)]
        for n, h in enumerate(result.hits, 1):
            row = self.chunks.loc[h["chunk_id"]]
            first, last = row.get("page_start"), row.get("page_end")
            pages = "" if pd.isna(first) else f"{t['page']} {int(first)}" if first == last else f"{t['pages']} {int(first)}-{int(last)}"
            via = h["reason"]
            if h["via"] == "ref":
                via += f" ({t['followed_from']}{self.citation(h['from_chunk'])}" + (f", {t['mandatory']}" if h["mandatory"] else "") + ")"
            title = " > ".join(row["heading_path"])
            blocks.append(f"[{n}] {' | '.join(x for x in (h['citation'], pages, via) if x)}\n{title}\n{row['text']}")
        if result.omitted:
            blocks.append(f"{t['omitted']}: " + "; ".join(f"{o['citation']} ({o['chunk_id']})" for o in result.omitted))
        return "\n\n".join(blocks).translate(LONG_DASHES)

    # ---- search -------------------------------------------------------------------------------

    def warm_up(self) -> None:
        """Embeds one throwaway query so the first user does not wait for the embedding model to load."""
        embed_query(self.m["query_prefix"] + "בדיקה", self.m["name"])
        self._entity_matrix()

    def scores(self, query: str) -> pd.Series:
        q = embed_query(self.m["query_prefix"] + query, self.m["name"])
        return pd.Series(self.matrix @ q, index=self.chunks.index)

    def search(self, query: str | None = None, areas: list[str] | None = None, zone_types: list[str] | None = None,
               entities: list[str] | None = None, headings: list[str] | None = None, focus_notes: list[dict] | None = None) -> Result:
        """Without a question, filters alone are searched with the generic question (a plan border from the GIS).

        A zone scope too large to return whole is cut to its most similar chunks, unless there is no question to rank by.
        `focus_notes` (text, layer, priority, weight) add chunks similar to secondary map layers after all other hits.
        """
        filter_only = not query
        if filter_only:
            if areas is None and zone_types is None and entities is None and headings is None:
                raise ValueError("a question or at least one filter is required")
            query = self.cfg["generic_question"]
        cfg = self.cfg
        scores = self.scores(query)
        loc = self.locate(query, areas, zone_types, entities, headings)
        hits: dict[str, dict] = {}

        def add(cid: str, via: str, **why) -> None:
            if cid not in hits:
                row = self.chunks.loc[cid]
                hits[cid] = {"chunk_id": cid, "citation": self.citation(cid), "score": float(scores[cid]), "via": via,
                             "reason": config.LABELS["via"][via], "tokens": int(row["tokens"]),
                             "pages": (row.get("page_start"), row.get("page_end")), **why}

        capped: list[str] = []
        for z in loc["zone_types"]:
            general = self.zone_general.get(f"3.{z.split('.')[1]}", [])
            for scope, kind in ((self.zone_scope[z], "zone"), (general, "general")):
                if not scope:
                    continue
                whole = filter_only or self.chunks.loc[scope, "tokens"].sum() <= cfg["zone_max_tokens"]
                if not whole:
                    capped += scope
                for cid in scope if whole else scores.loc[scope].nlargest(cfg["zone_k"]).index:
                    add(cid, kind, zone=z, after=self.zone_scope[z])
        for h in loc["headings"]:  # a chapter or section: all of it if small, else its own chunk and a few more
            for cid in self._pick_heading(h, scores, ranked=not filter_only):
                add(cid, "heading")
        for cid in [] if filter_only else scores[~self.plan_record].nlargest(cfg["clause_k"]).index:
            add(cid, "search")
        self._add_records(add, scores, loc, ranked=not filter_only)

        top = max((h["score"] for h in hits.values()), default=0.0)
        self._add_table_notes(hits, add)
        self._add_fragments(hits, add)
        self._expand(hits, add, scores)
        self._add_focus_notes(hits, add, focus_notes)
        first = {"location", "zone", "heading"} if loc["source"] == "filters" else {"zone", "heading"}  # a zone or heading named by title ranks high
        heads = [h for h in hits.values() if h["via"] == "heading"]  # a named heading starts the answer
        ordered = sorted((h for h in hits.values() if h["via"] not in FOLLOWERS and h not in heads),
                         key=lambda h: (VIAS["location" if h["via"] in first else "search" if h["via"] in ("location", "zone") else h["via"]], -h["score"]))
        if loc["source"] != "filters" and ordered and not heads:  # in free text the best match leads and the named zone follows it
            lead = max((h for h in ordered if h["via"] in ("search", "zone", "location", "heading")), key=lambda h: h["score"], default=None)
            if lead:
                ordered.remove(lead)
                ordered.insert(0, lead)
        ordered = [*heads, *ordered]
        for h in (h for h in hits.values() if h["via"] in FOLLOWERS):
            anchors = [i for i, o in enumerate(ordered) if o["chunk_id"] in h["after"]]
            ordered.insert(max(anchors) + 1 if anchors else len(ordered), h)
        # an explicit location, or a row label named in the question, that matched records or zone sections is a structural match, whatever the wording
        structural = (loc["source"] == "filters" or bool(loc["entity_records"]) or bool(loc["headings"])) and any(
            h["via"] in ("location", "zone", "heading") for h in hits.values())
        omitted = [{"chunk_id": c, "citation": self.citation(c), "tokens": int(self.chunks.at[c, "tokens"])}
                   for c in dict.fromkeys(capped) if c not in hits]
        band = "high" if structural else next(b for b in ("high", "medium", "low") if b == "low" or top >= cfg["bands"][b])
        return Result(ordered, loc, top, band, config.LABELS["band"][band], omitted)

    def _add_records(self, add, scores: pd.Series, loc: dict, ranked: bool = True) -> None:
        """Table 5.4 records: per planning area those of the requested zone types, else the most similar ones.

        Requested entities (`loc["entity_records"]`) narrow the area's records to their own rows, and are added
        even when no area is given. `ranked` is False when there is no question, so similarity picks nothing: the
        area's whole table stands in for records of the requested zone types.
        """
        cfg = self.cfg
        forced = loc["entity_records"]
        if not loc["areas"]:
            for cid in forced:
                add(cid, "location")
            if not forced and ranked:
                for cid in scores[self.plan_record].nlargest(cfg["records_k_global"]).index:
                    add(cid, "search")
            return
        wanted = set(loc["zone_types"])
        for area in loc["areas"]:
            pool = self.plan_record & (self.chunks["area"] == area)
            subs = [s for s in loc["subareas"] if s.startswith(area)]
            if subs:  # the records of every requested sub-area, or the whole area when none of them has one
                zone = self.chunks["zone"].fillna("").str.replace(r"[\s\"'׳״]", "", regex=True)
                narrowed = pool & pd.concat([zone.str.startswith(s) for s in subs], axis=1).any(axis=1)
                pool = narrowed if narrowed.any() else pool
            narrowed = pool & pool.index.isin(forced)
            pool = narrowed if narrowed.any() else pool
            matched = [cid for cid in pool.index[pool] if self.record_zones.get(cid, set()) & wanted]
            for cid in matched or (scores[pool].nlargest(cfg["records_k"]).index if ranked else pool.index[pool]):
                add(cid, "location")
        for cid in forced:  # entity rows outside the requested areas
            add(cid, "location")

    def _note_vectors(self, texts: list[str]) -> np.ndarray:
        """Vectors of note texts; the ones not seen before go to the embedding server in one request."""
        missing = [t for t in dict.fromkeys(texts) if t not in self._note_cache]
        if missing:
            if len(self._note_cache) > 2000:
                self._note_cache.clear()
            vectors = embed([normalize_for_embedding(self.m["query_prefix"] + t) for t in missing], self.m["name"])
            self._note_cache.update(zip(missing, vectors))
        return np.vstack([self._note_cache[t] for t in texts])

    def _add_focus_notes(self, hits: dict, add, notes: list[dict] | None) -> None:
        """Chunks similar to the notes of secondary map layers, added after every other hit so they never displace one.

        A chunk needs `focus_min` similarity to a note. Notes of priority 1 come first, then the others by similarity
        weighted with their overlap share; a layer adds at most `focus_per_layer` chunks and all notes together at most
        `focus_max_tokens`. Table 5.4 records are reached by planning area, not by similarity.
        """
        cfg = self.cfg
        notes = sorted((n for n in notes or [] if n["text"].strip()), key=lambda n: (n["priority"], -n["weight"]))[:cfg["focus_max_notes"]]
        if not notes:
            return
        sims = self.matrix @ self._note_vectors([n["text"] for n in notes]).T  # chunks x notes
        eligible = ~self.plan_record.to_numpy()
        candidates = sorted(
            (n["priority"], -float(sims[i, j]) * (0.5 + 0.5 * n["weight"]), int(i), j)
            for j, n in enumerate(notes) for i in np.flatnonzero(eligible & (sims[:, j] >= cfg["focus_min"])))
        spent, per_layer = 0, Counter()
        for _, _, i, j in candidates:
            cid, note = self.chunks.index[i], notes[j]
            layer, tokens = note["layer"] or "", int(self.chunks.iloc[i]["tokens"])
            if cid in hits or per_layer[layer] >= cfg["focus_per_layer"] or spent + tokens > cfg["focus_max_tokens"]:
                continue
            label = config.LABELS["via"]["focus_note"]
            add(cid, "focus_note", score=float(sims[i, j]), reason=f"{label}: {layer + ' - ' if layer else ''}{note['text'][:80]}")
            per_layer[layer] += 1
            spent += tokens

    def _add_table_notes(self, hits: dict, add) -> None:
        """Any table 5.4 record brings the notes that precede the table and qualify its values."""
        records = [cid for cid in hits if self.plan_record[cid]]
        if records:
            add("5.4", "notes", after=records)

    def _add_fragments(self, hits: dict, add) -> None:
        """A row of a split clause table brings the rest of its section, if the section is small."""
        for cid in list(hits):
            row = self.chunks.loc[cid]
            if row["kind"] == "record" and not self.plan_record[cid]:
                siblings = self.by_section[row["section_id"]]
                if self.chunks.loc[siblings, "tokens"].sum() <= self.cfg["sibling_max_tokens"]:
                    for s in siblings:
                        add(s, "section", from_chunk=cid)

    def _expand(self, hits: dict, add, scores: pd.Series) -> None:
        """Follows references like a reader: mandatory ones always, others while they stay relevant to the question."""
        cfg = self.cfg
        spent = sum(h["tokens"] for h in hits.values() if h["via"] != "search")
        frontier = list(hits)
        for depth in range(1, cfg["expand_depth"] + 1):
            gate = cfg["expand_min"] + cfg["expand_step"] * (depth - 1)
            following = []
            for cid in frontier:
                hub = self.in_degree[cid] >= cfg["hub_in_degree"]
                for ref in self.chunks.at[cid, "refs_out"]:
                    target = self.owner.get(ref["target"])
                    if not target or target in hits:
                        continue
                    if not (ref["mandatory"] or (not hub and scores[target] >= gate)):
                        continue
                    cost = int(self.chunks.at[target, "tokens"])
                    if spent + cost > cfg["budget_tokens"]:
                        continue
                    spent += cost
                    add(target, "ref", from_chunk=cid, mandatory=bool(ref["mandatory"]), depth=depth)
                    following.append(target)
            frontier = following
