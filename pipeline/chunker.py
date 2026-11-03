"""Splits the document tree (nodes.parquet) into embeddable chunks."""
import re
from dataclasses import dataclass
from statistics import median

import pandas as pd
from tokenizers import Tokenizer

LABEL_TAIL = re.compile(r"(\([^()]+\))$")
AREA_CODE = re.compile(r"^(\d{3})\s*(.*)$")
FIRST_COLUMN = re.compile(r"^המרחב עליו חלות ההוראות:\s*([^|]*?)\s*(?:\||$)")
TERM_LINE = re.compile(r"^[^:\n]{1,120}:\s")
SENTENCE = re.compile(r"(?<=[.;])\s+")
HEADING_KINDS = {"chapter", "section", "subsection"}
MAX_CRUMB_WORDS = 10


@dataclass
class Piece:
    """Consecutive parts of the document that become one chunk."""
    nodes: list[str]
    texts: list[str]
    tokens: int
    atomic: bool = False  # never merged with neighbours
    kind: str | None = None
    crumb: str = ""  # heading of a node whose body was split across several pieces


class Chunker:
    def __init__(self, df: pd.DataFrame, tokenizer: Tokenizer, min_tokens: int, max_tokens: int):
        self.nodes = {r["id"]: r for r in df.to_dict("records")}
        self.tok, self.min, self.max = tokenizer, min_tokens, max_tokens
        self.own = {i: self._render(n) for i, n in self.nodes.items()}
        self.size = {i: self.count(t) for i, t in self.own.items()}
        self.used_ids: dict[str, int] = {}

    def count(self, text: str) -> int:
        return len(self.tok.encode(text, add_special_tokens=False).ids) if text else 0

    @staticmethod
    def _heading(n: dict) -> str:
        """Label plus title of a node, without its body text."""
        i, title, kind = n["id"], n["title"], n["kind"]
        if kind == "root":
            return ""
        if kind == "clause":
            m = LABEL_TAIL.search(i)
            return m.group(1) if m else "" if "/p" in i else i
        if kind == "record":
            return f"[{title}]"
        if kind == "chapter":
            return title if i.startswith("root/") else f"פרק {i} – {title}"
        return title if "/p" in i else f"{i}. {title}"

    def _render(self, n: dict) -> str:
        head = self._heading(n)
        if n["kind"] == "clause":
            return f"{head} {n['text']}".strip()
        return "\n".join(x for x in (head, n["text"]) if x)

    def _ancestors(self, nid: str) -> list[str]:
        out, parent = [], self.nodes[nid]["parent"]
        while parent and parent != "root":
            out.append(parent)
            parent = self.nodes[parent]["parent"]
        return out[::-1]

    def _crumb(self, nid: str) -> str:
        """Short label of an ancestor for the breadcrumb: long clause-like titles shrink to their number."""
        n = self.nodes[nid]
        if n["kind"] in HEADING_KINDS and len(n["title"].split()) > MAX_CRUMB_WORDS:
            return f"{nid}." if "/p" not in nid else ""
        return self._heading(n)

    # ---- piece construction -------------------------------------------------------------------

    def _preorder(self, nid: str) -> list[str]:
        return [nid, *(d for c in self.nodes[nid]["children"] for d in self._preorder(c))]

    def _piece(self, ids: list[str]) -> Piece:
        atomic = any(self.nodes[i]["kind"] == "record" for i in ids)
        return Piece(ids, [self.own[i] for i in ids], sum(self.size[i] for i in ids), atomic)

    def _pack(self, units: list[str]) -> list[str]:
        """Greedy groups of consecutive units up to max tokens; over-long lines are cut at sentences."""
        flat = [u for line in units for u in (SENTENCE.split(line) if self.count(line) > self.max else [line])]
        groups, cur, size = [], [], 0
        for u in flat:
            n = self.count(u)
            if cur and size + n > self.max:
                groups.append("\n".join(cur))
                cur, size = [], 0
            cur.append(u)
            size += n
        return [*groups, "\n".join(cur)] if cur else groups

    def _split_leaf(self, nid: str) -> list[Piece]:
        """Splits the body of one node: term lists become one piece per term, other text is packed."""
        node = self.nodes[nid]
        lines = [x for x in node["text"].split("\n") if x.strip()]
        glossary = len(lines) >= 5 and sum(bool(TERM_LINE.match(x)) for x in lines) >= 0.8 * len(lines)
        if glossary:
            groups: list[str] = []
            for x in lines:
                if TERM_LINE.match(x) or not groups:
                    groups.append(x)
                else:
                    groups[-1] += f"\n{x}"
        else:
            groups = self._pack(lines)
        kind = "definition" if glossary else None
        crumb = self._heading(node)
        return [Piece([nid], [g], self.count(g), glossary, kind, crumb) for g in groups]

    def _merge(self, pieces: list[Piece]) -> list[Piece]:
        out: list[Piece] = []
        for p in pieces:
            prev = out[-1] if out else None
            if (prev and not (prev.atomic or p.atomic) and prev.crumb == p.crumb == ""
                    and min(prev.tokens, p.tokens) < self.min and prev.tokens + p.tokens <= self.max):
                out[-1] = Piece(prev.nodes + p.nodes, prev.texts + p.texts, prev.tokens + p.tokens)
            else:
                out.append(p)
        return out

    def pieces(self, nid: str) -> list[Piece]:
        node = self.nodes[nid]
        ids = self._preorder(nid)
        if sum(self.size[i] for i in ids) <= self.max:
            return [self._piece(ids)]
        if not len(node["children"]):
            return self._split_leaf(nid)
        lead = []
        if self.own[nid]:
            lead = self._split_leaf(nid) if self.size[nid] > self.max else [self._piece([nid])]
        return self._merge([*lead, *(p for c in node["children"] for p in self.pieces(c))])

    # ---- chunk records ------------------------------------------------------------------------

    def _chunk_id(self, first: str) -> str:
        k = self.used_ids.get(first, 0)
        self.used_ids[first] = k + 1
        return first if k == 0 else f"{first}#{k + 1}"

    def _location(self, nid: str) -> dict:
        """District, planning-area code/name and zone of a 5.4 record.

        The zone is the row's first column: the last part of the `district > area > zone` title, or, for short rows
        rendered on one line, the first field of that line.
        """
        loc = {"district": None, "area": None, "area_name": None, "zone": None}
        node = self.nodes[nid]
        if node["kind"] != "record" or not nid.startswith("5.4["):
            return loc
        parts = node["title"].split(" > ")
        if len(parts) < 2 or not (m := AREA_CODE.match(parts[1])):
            return loc
        first_line = FIRST_COLUMN.match(node["text"].split("\n")[0])
        return {"district": parts[0], "area": m.group(1), "area_name": m.group(2) or None,
                "zone": " > ".join(parts[2:]) or (first_line.group(1) if first_line else None) or None}

    def _record(self, p: Piece) -> dict:
        first = p.nodes[0]
        ancestors = self._ancestors(first)
        path = [c for c in (self._crumb(a) for a in ancestors) if c] + ([p.crumb] if p.crumb else [])
        text = "\n".join(p.texts)
        embed_text = "\n".join(x for x in (" > ".join(path), text) if x)
        chain = [first, *ancestors]
        section = next((a for a in chain if self.nodes[a]["kind"] in {"section", "subsection"}), first)
        refs: dict[tuple, dict] = {}
        squeezed = re.sub(r"\s+", "", text)
        for i in dict.fromkeys(p.nodes):
            for r in self.nodes[i]["refs_out"]:
                if p.crumb and re.sub(r"\s+", "", r["raw"]) not in squeezed:  # piece holds only part of the node
                    continue
                key = (r["target"] or r["raw"], r["status"])
                refs[key] = {**r, "mandatory": bool(r["mandatory"] or refs.get(key, {}).get("mandatory"))}
        binding = not any(a.startswith("root/") for a in chain)
        return {
            "chunk_id": self._chunk_id(first),
            "node_ids": list(dict.fromkeys(p.nodes)),
            "section_id": section,
            "kind": p.kind or (self.nodes[first]["kind"] if binding else "intro"),
            "binding": binding,
            "heading_path": path,
            "text": text,
            "embed_text": embed_text,
            "tokens": self.count(embed_text),
            "refs_out": list(refs.values()),
            **self._location(first),
        }

    def chunks(self) -> pd.DataFrame:
        """Chunks per chapter, never merged across chapters."""
        pieces = [p for c in self.nodes["root"]["children"] for p in self.pieces(c)]
        return pd.DataFrame([self._record(p) for p in pieces])


def report(df: pd.DataFrame, max_tokens: int, min_tokens: int) -> None:
    print("chunks:", len(df), "by kind:", df["kind"].value_counts().to_dict())
    t = df["tokens"]
    print(f"tokens min/median/max: {t.min()}/{median(t)}/{t.max()}; over {max_tokens}: {(t > max_tokens).sum()}; "
          f"under {min_tokens}: {(t < min_tokens).sum()}")
    print("non-binding:", int((~df["binding"]).sum()), "with refs:", int(df["refs_out"].map(len).gt(0).sum()))
    print("largest:", df.nlargest(5, "tokens")[["chunk_id", "tokens"]].values.tolist())
