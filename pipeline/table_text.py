"""Render docx tables as labelled key-value text for chunking and embedding."""
import re

from docx.oxml.ns import qn
from docx.text.run import Run

BULLETS = {"\uf0a7", "▪", "•"}
NULL_VALUES = {"", "-", "–", "—"}


def paragraph_text(p) -> str:
    """Paragraph text including tracked insertions (python-docx skips w:ins); text boxes are excluded."""
    return "".join(Run(r, p).text for r in p._p.xpath(".//w:r[not(ancestor::w:txbxContent)]"))


def _is_null(value: str) -> bool:
    return value.strip() in NULL_VALUES


def _flatten_unlabelled(value: str) -> str:
    """Joins a multi-paragraph cell into one line unless it holds a bulleted or numbered list."""
    lines = value.split("\n")
    if any(re.match(r"(- |\d+\. )", line) for line in lines):
        return value
    return " ".join(lines)


def _cell_text(cell, numbering) -> str:
    """Cell paragraphs, one per line, prefixed with their resolved list label."""
    lines = []
    for p in cell.paragraphs:
        text = " ".join(paragraph_text(p).split())
        if not text:
            continue
        label = numbering.label(p)
        if label in BULLETS:
            label = "-"
        lines.append(f"{label} {text}" if label else text)
    return "\n".join(lines)


def _rows(table, numbering) -> list[list[str]]:
    """Cell texts per row; horizontal merges collapse, vertical-merge continuations are empty."""
    seen_in_table = set()
    rows = []
    for row in table.rows:
        seen_in_row, values = set(), []
        for c in row.cells:
            if c._tc in seen_in_row:
                continue
            seen_in_row.add(c._tc)
            values.append("" if c._tc in seen_in_table else _cell_text(c, numbering))
            seen_in_table.add(c._tc)
        rows.append(values)
    return rows


def _repeats_header(row) -> bool:
    return row._tr.trPr is not None and row._tr.trPr.find(qn("w:tblHeader")) is not None


def _pipe_table(rows) -> list[str]:
    return ["| " + " | ".join(v.replace("\n", " ") for v in r) + " |" for r in rows]


def _key_value_rows(rows, header_row_present) -> list[str]:
    """Two-column tables: one `key: value` line per row."""
    out = []
    for key, value in rows[1 if header_row_present else 0:]:
        out.append(key.replace("\n", " ") if _is_null(value) else f"{key.replace(chr(10), ' ')}: {value}")
    return out


def _record_rows(rows, header) -> list[str]:
    """Header tables: one record per data row, prefixed by its group-row context path."""
    width = len(header)
    context, out = {}, []
    for values in rows[1:]:
        if len(values) < width:  # group row (e.g. district / zone) that labels the rows below it
            parts = [v.replace("\n", " ") for v in values if not _is_null(v)]
            if parts:
                context = {k: v for k, v in context.items() if k < len(parts)}
                context[len(parts)] = " ".join(parts)
            continue
        fields = [(h, v) for h, v in zip(header, values) if not _is_null(v)]
        if not fields:
            continue
        fields = [(h, _flatten_unlabelled(v)) for h, v in fields]
        path = [context[k] for k in sorted(context)]
        if all("\n" not in v and len(v) <= 60 for _, v in fields):
            if path:
                out.append("[" + " > ".join(path) + "]")
            out.append(" | ".join(f"{h}: {v}" for h, v in fields))
        else:
            title, *rest = fields
            out.append("[" + " > ".join(path + [title[1]]) + "]")
            for h, v in rest:
                if "\n" in v:
                    out.append(f"- {h}:")
                    out.extend(f"  {line}" for line in v.split("\n"))
                else:
                    out.append(f"- {h}: {v}")
        out.append("")
    return out


def render_table(table, numbering) -> list[str]:
    """Markdown-ish lines for a table; irregular tables fall back to pipe rows."""
    rows = _rows(table, numbering)
    width = max(len(r) for r in rows)
    flagged = _repeats_header(table.rows[0])
    if width == 2 and all(len(r) == 2 for r in rows):
        return _key_value_rows(rows, flagged)
    if width >= 3 and len(rows[0]) == width:
        header = [" ".join(h.split()) for h in rows[0]]
        return _record_rows(rows, header)
    return _pipe_table(rows)
