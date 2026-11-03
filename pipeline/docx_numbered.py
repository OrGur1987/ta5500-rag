"""Convert a docx to Markdown with Word auto-numbering resolved (python-docx + numbering.xml)."""
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from pipeline.table_text import paragraph_text, render_table

HEBREW_LETTERS = "אבגדהוזחטיכלמנסעפצקרשת"


def _roman(n: int) -> str:
    out = ""
    for value, sym in [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]:
        while n >= value:
            out, n = out + sym, n - value
    return out


def _letters(n: int) -> str:
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


FORMATTERS = {
    "decimal": str,
    "lowerLetter": _letters,
    "lowerRoman": _roman,
    "hebrew1": lambda n: HEBREW_LETTERS[(n - 1) % len(HEBREW_LETTERS)] * ((n - 1) // len(HEBREW_LETTERS) + 1),
    "bullet": lambda n: "•",
}


class Numbering:
    """Resolves the display label ("1.2.3", "(א)") of numbered paragraphs, in document order."""

    def __init__(self, doc):
        self.styles = {s.style_id: s.element for s in doc.styles}
        root = doc.part.numbering_part.element
        self.levels = {}  # abstractNumId -> {ilvl: (start, fmt, lvlText, pStyle, hidden)}
        for a in root.findall(qn("w:abstractNum")):
            lv = {}
            for l in a.findall(qn("w:lvl")):
                g = lambda tag: (l.find(qn(tag)).get(qn("w:val")) if l.find(qn(tag)) is not None else None)
                color = l.find(f"{qn('w:rPr')}/{qn('w:color')}")
                hidden = color is not None and color.get(qn("w:val")).upper() == "FFFFFF"  # white-on-white label
                lv[int(l.get(qn("w:ilvl")))] = (
                    int(g("w:start") or 1), g("w:numFmt"), g("w:lvlText"), g("w:pStyle"), hidden,
                )
            self.levels[a.get(qn("w:abstractNumId"))] = lv
        self.abstract_of = {
            n.get(qn("w:numId")): n.find(qn("w:abstractNumId")).get(qn("w:val")) for n in root.findall(qn("w:num"))
        }
        self.counters = {}  # abstractNumId -> {ilvl: count}

    def _num_pr(self, p: Paragraph):
        """Returns (numId, ilvl or None, styleId) from the paragraph, else from its style chain."""
        style_id = p.style.style_id if p.style is not None else None
        pr = p._p.pPr.numPr if p._p.pPr is not None else None
        if pr is not None and pr.numId is not None:
            return pr.numId.val, (pr.ilvl.val if pr.ilvl is not None else None), style_id
        el = self.styles.get(style_id)
        while el is not None:
            spr = el.pPr.numPr if el.pPr is not None else None
            if spr is not None and spr.numId is not None:
                return spr.numId.val, (spr.ilvl.val if spr.ilvl is not None else None), style_id
            based = el.find(qn("w:basedOn"))
            el = self.styles.get(based.get(qn("w:val"))) if based is not None else None
        return None, None, style_id

    def is_numbered(self, p: Paragraph) -> bool:
        return bool(self._num_pr(p)[0])

    def label(self, p: Paragraph) -> str | None:
        """Advances the list counters and returns the paragraph's visible number label, or None."""
        num_id, ilvl, style_id = self._num_pr(p)
        if not num_id:  # absent or numId 0
            return None
        abstract = self.abstract_of[str(num_id)]
        levels = self.levels[abstract]
        if ilvl is None:  # level linked to the style via lvl/pStyle
            ilvl = next((i for i, l in levels.items() if l[3] == style_id), 0)
        counts = self.counters.setdefault(abstract, {})
        counts[ilvl] = counts.get(ilvl, levels[ilvl][0] - 1) + 1
        for deeper in [i for i in counts if i > ilvl]:
            del counts[deeper]

        def fmt(m):
            lvl = int(m.group(1)) - 1
            return FORMATTERS[levels[lvl][1]](counts.get(lvl, levels[lvl][0]))

        return None if levels[ilvl][4] else re.sub(r"%(\d)", fmt, levels[ilvl][2])


def _heads_clauses(body: list, i: int, doc, numbering: Numbering) -> bool:
    """True if the next non-empty paragraph after body[i] is a numbered non-heading (a child clause)."""
    for child in body[i + 1:]:
        if child.tag != qn("w:p"):
            return False
        q = Paragraph(child, doc)
        if q.style.style_id.startswith("TOC") or not paragraph_text(q).strip():
            continue
        return numbering.is_numbered(q) and not (q.style.name or "").startswith("Heading")
    return False


def docx_to_markdown(src: Path, dst: Path) -> Path:
    """Write Markdown with headings (from Heading N styles), resolved numbers and tables as key-value text."""
    doc = Document(str(src))
    numbering = Numbering(doc)
    lines = []
    body = list(doc.element.body.iterchildren())
    for i, child in enumerate(body):
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            text = paragraph_text(p).strip()
            if p.style.style_id.startswith("TOC"):
                continue
            # An empty numbered paragraph is kept only as the parent of the clauses that follow it.
            if not text and not (numbering.is_numbered(p) and _heads_clauses(body, i, doc, numbering)):
                continue
            label = numbering.label(p)
            level = re.fullmatch(r"Heading (\d)", p.style.name or "")
            text = f"{label} {text}".strip() if label else text
            lines.append(f"{'#' * int(level.group(1))} {text}" if level else text)
        elif child.tag == qn("w:tbl"):
            lines.extend(render_table(Table(child, doc), numbering))
        lines.append("")
    dst.write_text("\n".join(lines), encoding="utf-8")
    return dst

