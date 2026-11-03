"""Containment tree and cross-reference graph for numbered Hebrew planning documents."""
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

import pandas as pd

HEB = "אבגדהוזחטיכלמנסעפצקרשת"
HEADING = re.compile(r"^(#{2,4}) (?:פרק (\d+)\s*[–-]\s*|(\d+(?:\.\d+)+)\.\s*)?(.*)$")
TAB_CLAUSE = re.compile(r"^(\d+(?:\.\d+)+)\t(.*)$")
LABEL = re.compile(r"^\(\(?([א-ת]|\d{1,2})\)\)?\s*(.*)$")
RECORD = re.compile(r"^\[(.+)\]$")
KINDS = {1: "chapter", 2: "section", 3: "subsection"}

SUBLABEL = r"\((?:[א-ת]|\d{1,2})\)"
NUM = rf"\d+(?:\.\d+)+(?:\s?{SUBLABEL})*"
SEP = r"(?:,|ו-?|(?<!ו)[-–])"
REF = re.compile(rf"(?:סעי(?:ף|פים)|פרק(?:ים)?)\s+({NUM}(?:\s*{SEP}\s*(?:{NUM}|(?:{SUBLABEL}\s?)+))*)")
ITEM = rf"(?:[א-ת](?=\())?(?:{SUBLABEL}\s?)+"
REL = re.compile(
    rf"סעי(?:ף|פים)\s+(?:קט(?:ן|נה|נים)\s+)?({ITEM}(?:\s*(?:,|ו-?|(?<!ו)[-–])\s*{ITEM})*)"
)
REL_TOKEN = re.compile(rf"{ITEM}|(?<!ו)[-–]")
TOKEN = re.compile(rf"{NUM}|(?:{SUBLABEL}\s?)+|(?<!ו)[-–]")
EXTERNAL_TAIL = re.compile(r'תמ"[אמ]')
EXTERNAL_HEAD = re.compile(r'תמ"[אמ]\s*\d*\s*\($')
MANDATORY = re.compile("בכפוף|על אף האמור|יגבר|תגבר|גוברת|בהתאם לקבוע|בהתאם להוראות|בהתאם לאמור|יחולו|למרות האמור")


@dataclass
class Ref:
    raw: str
    target: str | None
    status: str  # internal | external | dangling
    mandatory: bool


@dataclass
class Node:
    id: str
    kind: str  # root | chapter | section | subsection | clause | record
    title: str
    parent: str | None
    text: str = ""
    children: list[str] = field(default_factory=list)
    refs_out: list[Ref] = field(default_factory=list)
    refs_in: list[str] = field(default_factory=list)


class DocTree:
    def __init__(self, nodes: dict[str, Node]):
        self.nodes = nodes

    def ancestors(self, node_id: str) -> list[str]:
        out, parent = [], self.nodes[node_id].parent
        while parent:
            out.append(parent)
            parent = self.nodes[parent].parent
        return out

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for n in self.nodes.values():
            row = asdict(n)
            row["depth"] = len(self.ancestors(n.id))
            rows.append(row)
        return pd.DataFrame(rows)


class _Builder:
    """Stack-based parser: headings set the containment level, list labels nest below them."""

    def __init__(self):
        self.nodes = {"root": Node("root", "root", "", None)}
        self.heads = [(0, "root")]
        self.labels: list[tuple[str, int, str]] = []  # (type, value, node id)
        self.cur = "root"

    def add(self, node_id: str | None, kind: str, title: str, text: str, parent: str) -> str:
        node_id = node_id or f"{parent}/p{len(self.nodes[parent].children) + 1}"
        base, n = node_id, 1
        while node_id in self.nodes:
            n += 1
            node_id = f"{base}~{n}"
        self.nodes[node_id] = Node(node_id, kind, title, parent, text)
        self.nodes[parent].children.append(node_id)
        return node_id

    def heading(self, level: int, node_id: str | None, title: str) -> None:
        while self.heads[-1][0] >= level:
            self.heads.pop()
        self.cur = self.add(node_id, KINDS[level], title, "", self.heads[-1][1])
        self.heads.append((level, self.cur))
        self.labels = []

    def label(self, label: str, body: str) -> None:
        typ, val = ("h", HEB.index(label) + 1) if label in HEB else ("n", int(label))
        stack = self.labels
        idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][:2] == (typ, val - 1)), None)
        if idx is None and val == 1:
            idx = len(stack) - 1 if stack and stack[-1][0] == typ else None
        elif idx is None:
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == typ), None)
        if idx is not None:
            del stack[idx:]
        parent = stack[-1][2] if stack else self.heads[-1][1]
        self.cur = self.add(f"{parent}({label})", "clause", "", body, parent)
        stack.append((typ, val, self.cur))

    def record(self, title: str) -> None:
        head = self.heads[-1][1]
        self.cur = self.add(f"{head}[{len(self.nodes[head].children) + 1}]", "record", title, "", head)

    def end_record(self) -> None:
        if self.nodes[self.cur].kind == "record":
            self.cur = self.labels[-1][2] if self.labels else self.heads[-1][1]

    def append(self, line: str) -> None:
        node = self.nodes[self.cur]
        node.text = f"{node.text}\n{line}" if node.text else line


def parse_markdown(lines: list[str]) -> DocTree:
    b = _Builder()
    for raw in lines:
        line = raw.strip()
        if not line:
            b.end_record()
        elif m := HEADING.match(line):
            chapter, number, title = m.group(2), m.group(3), m.group(4)
            b.heading(len(m.group(1)) - 1, chapter or number, title)
        elif m := TAB_CLAUSE.match(line):
            b.heading(min(m.group(1).count(".") + 1, 3), m.group(1), m.group(2))
        elif m := RECORD.match(line):
            b.record(m.group(1))
        elif m := LABEL.match(line):
            b.label(m.group(1), m.group(2))
        else:
            b.append(raw.rstrip())
    _demote_bare_headings(b.nodes)
    tree = DocTree(b.nodes)
    _resolve_refs(tree)
    return tree


def _demote_bare_headings(nodes: dict[str, Node]) -> None:
    """A heading-styled subsection with no text and no sub-nodes (1.1.1) is a numbered clause: its title is its text."""
    for n in nodes.values():
        if n.kind == "subsection" and not n.children and not n.text:
            n.kind, n.text, n.title = "clause", n.title, ""


def _resolve(ref_id: str, nodes: dict[str, Node]) -> str | None:
    """Exact id, else the enclosing node after dropping trailing sub-clause labels."""
    while ref_id not in nodes:
        stripped = re.sub(r"\([^()]+\)$", "", ref_id)
        if stripped == ref_id:
            return None
        ref_id = stripped
    return ref_id


def _range_members(start: str, end: str, nodes: dict[str, Node]) -> list[str]:
    """Existing sibling ids after `start` up to `end` (inclusive) when both share a parent, else [end]."""
    (p1, n1), (p2, n2) = start.rsplit(".", 1), end.rsplit(".", 1)
    if p1 != p2 or not (n1.isdigit() and n2.isdigit()):
        return [end]
    members = [f"{p1}.{k}" for k in range(int(n1) + 1, int(n2) + 1) if f"{p1}.{k}" in nodes]
    return members if end in members else [*members, end]


def _expand_group(group: str, nodes: dict[str, Node]) -> list[str]:
    """Section ids in a reference list: ranges are expanded, a bare label replaces the previous label."""
    ids, range_start = [], None
    for tok in TOKEN.findall(group):
        tok = re.sub(r"\s+", "", tok)
        if tok in "-–":
            range_start = ids[-1]
        elif tok.startswith("("):
            ids.append(re.sub(r"\([^()]+\)$", "", ids[-1]) + tok)
        elif range_start:
            ids.extend(_range_members(range_start, tok, nodes))
            range_start = None
        else:
            ids.append(tok)
    return ids


def _label_range(start: str, end: str) -> list[str]:
    """Label paths from `start` (exclusive) to `end` (inclusive) when they differ only in the last label."""
    (p1, a), (p2, b) = (re.fullmatch(r"(.*)\(([^()]+)\)", s).groups() for s in (start, end))
    if p1 != p2:
        return [end]
    if a.isdigit() and b.isdigit():
        seq = [str(k) for k in range(int(a) + 1, int(b) + 1)]
    elif a in HEB and b in HEB:
        seq = list(HEB[HEB.index(a) + 1:HEB.index(b) + 1])
    else:
        return [end]
    return [f"{p1}({x})" for x in seq] or [end]


def _relative_labels(group: str) -> list[str]:
    """Label paths in a relative reference, e.g. 'ג(1)-ג(3)' gives (ג)(1), (ג)(2), (ג)(3)."""
    out, range_start = [], None
    for tok in REL_TOKEN.findall(group):
        tok = re.sub(r"\s+", "", tok)
        if tok in "-–":
            range_start = out[-1]
            continue
        if not tok.startswith("("):
            tok = f"({tok[0]}){tok[1:]}"
        out.extend(_label_range(range_start, tok) if range_start else [tok])
        range_start = None
    return out


def _resolve_relative(labels: str, scopes: list[str], order: dict[str, int], here: str, direction: int) -> str | None:
    """Nearest clause with this label path; direction -1 ('לעיל') keeps earlier clauses, +1 ('להלן') later ones."""
    for scope in scopes:
        cand = scope + labels
        if cand in order and (direction == 0 or (order[cand] - order[here]) * direction > 0):
            return cand
    return None


def _resolve_refs(tree: DocTree) -> None:
    nodes = tree.nodes
    order = {node_id: i for i, node_id in enumerate(nodes)}
    for node in nodes.values():
        text = f"{node.title}\n{node.text}"
        ancestors = tree.ancestors(node.id)
        skip = {node.id, *ancestors}
        seen = set()
        for rx, relative in ((REF, False), (REL, True)):
            for m in rx.finditer(text):
                tail = re.split(r"\.\s|;", text[m.end():m.end() + 30])[0]
                head = text[max(0, m.start() - 18):m.start()]
                external = not relative and bool(EXTERNAL_TAIL.search(tail) or EXTERNAL_HEAD.search(head))
                mandatory = bool(MANDATORY.search(text[max(0, m.start() - 60):m.end() + 40]))
                if relative:
                    scopes = [node.id, *ancestors]
                    direction = -1 if "לעיל" in tail else 1 if "להלן" in tail else 0
                    raws = _relative_labels(m.group(1))
                else:
                    raws = _expand_group(m.group(1), nodes)
                for raw in raws:
                    if external:
                        target, status = None, "external"
                    elif target := (_resolve_relative(raw, scopes, order, node.id, direction) if relative else _resolve(raw, nodes)):
                        status = "internal"
                    else:
                        status = "dangling"
                    if target in skip or (raw, status) in seen:
                        continue
                    seen.add((raw, status))
                    node.refs_out.append(Ref(raw, target, status, mandatory))
                    if target:
                        nodes[target].refs_in.append(node.id)


def _heading_id(node_id: str) -> str:
    return re.split(r"[(\[]", node_id)[0]


def report(tree: DocTree) -> None:
    refs = [(n.id, r) for n in tree.nodes.values() for r in n.refs_out]
    print("nodes:", dict(Counter(n.kind for n in tree.nodes.values())))
    print("refs:", dict(Counter(r.status for _, r in refs)), "mandatory:", sum(r.mandatory for _, r in refs))
    print("dangling:", [(i, r.raw) for i, r in refs if r.status == "dangling"])
    print("duplicate labels:", [i for i in tree.nodes if "~" in i and "(" not in i.split("~")[1]])
    edges = {(_heading_id(i), _heading_id(r.target)) for i, r in refs if r.target}
    edges = {e for e in edges if e[0] != e[1]}
    print("cycles:", sorted({tuple(sorted(e)) for e in edges if e[::-1] in edges}))
    print("hubs:", Counter(b for _, b in edges).most_common(8))
