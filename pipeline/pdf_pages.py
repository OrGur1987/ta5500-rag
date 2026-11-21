"""Adds PDF page numbers to chunks by matching their text against the exported PDF."""
import bisect
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
import pandas as pd

HEB = re.compile(r"[א-ת]")
PAGE_NO = re.compile(r"עמוד\s+(\d+)")
NOTES = re.compile(r"נוספות\(1\) \(2\):(.*)", re.S)
SNIPPET_LENGTHS = (30, 15, 8)
# Hebrew letters a match may lie ahead of the previous one, per snippet length (short snippets are ambiguous)
MAX_JUMP = {30: 8000, 15: 3000, 8: 600}
# nb_changed 2026-11-12: sizes (PDF points) for the fixed-width boxes: padding left/right of the page's text area,
# padding above/below a box, the smallest gap between consecutive boxes on a page, and the smallest box height.
PAD_X, PAD_Y, GAP, MIN_H = 4.0, 2.0, 2.0, 6.0


def _letters(text: str) -> str:
    """Hebrew letters only: immune to the digit and punctuation reordering of PDF text extraction."""
    return "".join(HEB.findall(text))


def _norm_line(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip())


def _repeated_norms(pages: list[str]) -> set[str]:
    """Normalised lines (digits ignored) that recur on many pages: the running headers and footers."""
    # nb_changed: split out of _strip_repeated_lines unchanged, so the word table below drops exactly the same lines.
    counts = Counter(n for t in pages for n in {_norm_line(x) for x in t.split("\n")} if n)
    return {n for n, c in counts.items() if c >= 0.3 * len(pages)}


def _strip_repeated_lines(pages: list[str]) -> list[str]:
    """Drops running headers and footers: lines that recur, ignoring digits, on many pages."""
    repeated = _repeated_norms(pages)  # nb_changed: the repeated-line detection moved to _repeated_norms
    return ["\n".join(x for x in t.split("\n") if _norm_line(x) not in repeated) for t in pages]


class PageIndex:
    """Hebrew-only text of the whole PDF with the printed page number of every offset."""

    def __init__(self, pdf: Path):
        doc = fitz.open(pdf)  # nb_changed: keep the document open (was inline) so the word table can read it too
        raw = [p.get_text() for p in doc]
        self.numbers = [int(m.group(1)) if (m := PAGE_NO.search(t)) else i + 1 for i, t in enumerate(raw)]
        letters = [_letters(t) for t in _strip_repeated_lines(raw)]
        self.starts = [sum(map(len, letters[:i])) for i in range(len(letters))]
        self.text = "".join(letters)
        self._index_words(doc, raw)  # nb_changed: word positions for the bounding boxes

    # nb_changed: everything from here to `page_at` is new. The matching text above is untouched; the word table only
    # adds, for each word with Hebrew letters, where it sits in `self.text` (offset) and where it is on the page (box).
    words = None  # None when there is no word table (e.g. the synthetic index of the unit tests): no regions then

    def _index_words(self, doc, raw: list[str]) -> None:
        """Offset in `self.text`, physical page and box of every kept word that has Hebrew letters."""
        repeated = _repeated_norms(raw)
        offsets, boxes, pages, pos = [], [], [], 0
        text_x, orient = [], []  # nb_changed 2026-11-12: per page, the fixed (left, right) X of every box and its orientation
        for pi, page in enumerate(doc):
            xs = []  # nb_changed 2026-11-12: left/right edges of the body words and table lines of this page
            # same header/footer lines as the text above, found per (block, line) of the page
            dropped = {(b["number"], li) for b in page.get_text("dict")["blocks"] if b["type"] == 0
                       for li, line in enumerate(b["lines"])
                       if _norm_line("".join(s["text"] for s in line["spans"])) in repeated}
            for x0, y0, x1, y1, word, block, line, _ in page.get_text("words"):
                letters = _letters(word)
                if letters and (block, line) not in dropped:
                    offsets.append(pos)
                    boxes.append((x0, y0, x1, y1))
                    pages.append(pi)
                    pos += len(letters)
                    xs += [x0, x1]  # nb_changed 2026-11-12: body words (headers/footers already dropped) set the text area
            # nb_changed 2026-11-12: table borders and cell rectangles widen the text area (a landscape table spans all
            # its columns); the page frame / full-page background and empty drawings are skipped
            pw, ph = page.rect.width, page.rect.height  # page.rect already accounts for the page rotation
            for d in page.get_drawings():
                r = d["rect"]
                if (r.width == 0 and r.height == 0) or (r.width >= 0.95 * pw and r.height >= 0.95 * ph):
                    continue
                xs += [r.x0, r.x1]
            # nb_changed 2026-11-12: one X range per page: the extreme edges plus a uniform padding, kept inside the page
            text_x.append((round(max(min(xs) - PAD_X, 0), 2), round(min(max(xs) + PAD_X, pw), 2)) if xs else None)
            orient.append("landscape" if pw > ph else "portrait")
        # the offsets are only valid if the words give exactly the same letters as the page text
        if pos != len(self.text):
            print(f"warning: word table has {pos} letters, page text {len(self.text)}; bounding boxes are skipped")
            return
        self.words = {"offsets": offsets, "boxes": boxes, "pages": pages, "end": pos,
                      "sizes": [(p.rect.width, p.rect.height) for p in doc],
                      "text_x": text_x, "orient": orient}  # nb_changed 2026-11-12: fixed X range and orientation per page

    def regions(self, start: int, end: int, text_x: list[tuple[float, float] | None] | None = None) -> list[dict]:
        """One box per page for the text between two offsets: the union of the boxes of the words in that range.

        A chunk that runs over a page break gets one region per page (start word to the end of the first page,
        the top of the next page to the end word). Boxes are in PDF points from the top-left of the page.

        Each box uses the supplied X range, or the page's fixed text-area range by default; y0/y1 are based on the
        first and last lines, plus PAD_Y.
        """
        w = self.words
        if w is None or end <= start or start >= w["end"]:
            return []
        if text_x is None:
            text_x = w["text_x"]
        first = bisect.bisect_right(w["offsets"], max(start, 0)) - 1
        last = max(bisect.bisect_right(w["offsets"], min(end, w["end"]) - 1) - 1, first)
        union: dict[int, list[float]] = {}
        for i in range(first, last + 1):
            x0, y0, x1, y1 = w["boxes"][i]
            u = union.setdefault(w["pages"][i], [x0, y0, x1, y1])
            u[:] = [min(u[0], x0), min(u[1], y0), max(u[2], x1), max(u[3], y1)]
        # nb_changed 2026-11-12: X from the page's fixed range (was b[0], b[2]); Y padded by PAD_Y (was unpadded);
        # "orientation" added. A page without a text area (no words or drawings) has no X range, so no region.
        return [{"page": self.numbers[p], "pdf_page": p + 1, "x0": text_x[p][0], "y0": round(b[1] - PAD_Y, 2),
                 "x1": text_x[p][1], "y1": round(b[3] + PAD_Y, 2),
                 "page_w": round(w["sizes"][p][0], 2), "page_h": round(w["sizes"][p][1], 2),
                 "orientation": w["orient"][p]}
                for p, b in union.items() if text_x[p]]

    # nb_changed 2026-11-17: new (box version 3). One X range per page orientation for the whole document.
    def use_orientation_x(self) -> list[tuple[float, float] | None] | None:
        """Return each page's orientation X range: the leftmost left edge and the rightmost right edge found on
        ANY page of that orientation (the values closest to the page edges, not the first page's), so all boxes on all
        portrait pages share one X pair and all boxes on all landscape pages share another. Y is not touched.
        `words["x_source"]` records, per orientation, [left, page of the left, right, page of the right] (PDF pages)."""
        w = self.words
        if w is None:
            return
        ext: dict[str, list] = {}
        for p, (tx, o) in enumerate(zip(w["text_x"], w["orient"])):
            if tx is None:  # a page without a text area does not take part
                continue
            e = ext.setdefault(o, [tx[0], p + 1, tx[1], p + 1])
            if tx[0] < e[0]:
                e[0], e[1] = tx[0], p + 1
            if tx[1] > e[2]:
                e[2], e[3] = tx[1], p + 1
        w["x_source"] = ext
        return [(ext[o][0], ext[o][2]) if tx else None for tx, o in zip(w["text_x"], w["orient"])]

    def page_at(self, pos: int) -> int:
        return self.numbers[bisect.bisect_right(self.starts, pos) - 1]

    def offset_of_page(self, number: int) -> int:
        return self.starts[self.numbers.index(number)]

    def find(self, snippet: str, cursor: int) -> int:
        pos = self.text.find(snippet, cursor)
        return pos if 0 <= pos - cursor <= MAX_JUMP.get(len(snippet), MAX_JUMP[30]) else -1


def _anchor_text(row: pd.Series) -> str:
    """Hebrew-only text used for matching; table records use their notes, as table cells extract out of order."""
    text = row["text"]
    if row["kind"] == "record":
        notes = "\n".join(x for x in text.split("\n") if x.startswith("  "))
        if not notes and (m := NOTES.search(text)):
            notes = m.group(1)
        text = notes or text.split("\n")[0]
    return _letters(text)


# nb_changed 2026-11-17: new (box version 4). A list marker such as "(א)" or "(12)".
MARKER = re.compile(r"\(\s*[א-ת0-9]{1,3}\s*\)")


# nb_changed 2026-11-17: new (box version 4). Other anchors to try ONLY when the normal anchor finds nothing in the PDF.
def _alt_anchors(row: pd.Series, anchor: str) -> list[str]:
    """Fallback anchors for chunks the normal anchor cannot find.

    - record (table row without note lines): the normal anchor is only the short label ("מגורים"), too short to search.
      The cells of a row are contiguous in the PDF text in the order label, uses, example, so the label plus the text after
      the ":" of every following line is used; its last letters then end at the example cell (the full row height).
    - any other chunk: the text with list markers "(א)" removed, because the marker letter is glued to the next word in the
      chunk text but is not in that place in the PDF text.
    Anchors equal to the normal one are left out."""
    text = row["text"]
    if row["kind"] == "record":
        lines = text.split("\n")
        alt = _letters(lines[0].strip().strip("[]")) + "".join(_letters(x.split(":", 1)[-1]) for x in lines[1:])
    else:
        alt = _letters(MARKER.sub("", text))
    return [alt] if alt and alt != anchor else []


# nb_changed 2026-11-17: new (box version 6). The chunk text can differ from the PDF text by a few letters (a reference such
# as "סעיף (ב)" that lost its letter, a word whose letters the PDF extraction scrambled), so the last 30 letters are not
# found. The end is then searched again with the last 15 and the last 8 letters, but only near where the chunk should end.
def _find_short_tail(index: "PageIndex", anchor: str, pos: int) -> tuple[int, int]:
    """(offset, length) of the last 15 (else 8) letters of `anchor` found within 60%..140% of the anchor's length from `pos`,
    the occurrence closest to the expected end; (-1, 0) when there is none."""
    size, expected = len(anchor), pos + len(anchor)
    lo, hi = pos + int(0.6 * size), pos + int(1.4 * size) + 40
    for n in SNIPPET_LENGTHS[1:]:
        tail, found, p = anchor[-n:], [], index.text.find(anchor[-n:], lo, hi)
        while p >= 0:
            found.append(p)
            p = index.text.find(tail, p + 1, hi)
        if found:
            return min(found, key=lambda q: abs(q + n - expected)), n
    return -1, 0


# nb_changed 2026-11-17: new (box version 6). The letters a chunk's own text can be recognised by, for chunks that were not
# found by their start. Records: their cells (see _cell_snippets). Other chunks: the first 30 letters of each line after the
# heading line (the heading is what the PDF often garbles), with list markers removed; lines under 15 letters are left out.
def _own_snippets(row: pd.Series) -> list[str]:
    if row["kind"] == "record":
        # a record's fields share one line ("a: x | b: y | ..."), and the PDF prints their cells in another order, so only
        # the first field (the cell that names the row: zone / area / rule) is used, next to the parts of the bracket path
        lines = row["text"].split("\n")
        parts = lines[0].strip().strip("[]").split(">")
        if len(lines) > 1:
            parts.append(lines[1].split(" | ")[0].split(":", 1)[-1])
        return [s for s in (_letters(p) for p in parts) if len(s) >= 8]
    lines = row["text"].split("\n")[1:]
    return [s for s in (_letters(MARKER.sub("", x)) for x in lines) if len(s) >= 15]


# nb_changed 2026-11-17: new (box version 6). Places chunks that were not found by their start (runs of consecutive
# unmatched chunks) by their own text, inside the gap between the matched chunk before the run and the one after it.
def _place_unmatched(chunks: pd.DataFrame, index: "PageIndex", spans: list, first_offset: int) -> list[int]:
    """Gives a span to every unmatched chunk of a run whose own text can be found, in order, inside the run's gap, and
    returns their indexes. The first chunk of the run starts where the gap starts, each next one where its own text is
    found, and each ends where the next begins (the last at the end of the gap), so the run's text is shared by what it
    says and not by text length. A run in which some chunk has no own text found is left alone (it is still estimated),
    and so is a gap much longer than the run's text (it must hold something else)."""
    placed, i = [], 0
    while i < len(spans):
        if spans[i]:
            i += 1
            continue
        j = i
        while j < len(spans) and not spans[j]:
            j += 1
        lo = spans[i - 1][1] if i > 0 else first_offset
        hi = spans[j][0] if j < len(spans) else len(index.text)
        rows = [chunks.iloc[k] for k in range(i, j)]
        found, cursor = [], lo
        for row in rows:
            # the first 30 letters of a snippet, else its second half (the PDF sometimes drops a word of a cell, e.g. "אזור")
            hits = [p for sn in _own_snippets(row)
                    if (p := index.text.find(sn[:30], cursor, hi)) >= 0 or (p := index.text.find(sn[len(sn) // 2:][-30:], cursor, hi)) >= 0]
            if not hits:
                break
            found.append(min(hits))
            cursor = found[-1] + 1
        if len(found) == len(rows) and hi - lo <= 2 * sum(len(_letters(r["text"])) for r in rows) + 200:
            cuts = [lo] + found[1:] + [hi]
            for n, k in enumerate(range(i, j)):
                spans[k] = (cuts[n], max(cuts[n + 1], cuts[n] + 1))
                placed.append(k)
        i = j
    return placed


# nb_changed 2026-11-17: new (box version 5). Longest window (Hebrew letters) in which a record's own cells are looked for.
CELL_WINDOW = 700


# nb_changed 2026-11-17: new (box version 5). The text of a table record's cells, to find where its row starts in the PDF.
def _cell_snippets(text: str, before: str = "") -> list[str]:
    """First 30 letters of each cell text of a record: the parts of its bracket path ("[a > b > c]") and the values of its
    "key: value" lines other than the notes. Texts under 8 letters are left out (too ambiguous to search for).
    `before` is the text of the (unmatched) chunk right before it: path parts it also has are that chunk's own header, e.g.
    the area name "904 ..." above the zone rows, so they are not taken as cells of this record."""
    lines = text.split("\n")
    shared = {_letters(p) for p in before.split("\n")[0].strip().strip("[]").split(">")}
    parts = [p for p in lines[0].strip().strip("[]").split(">") if _letters(p) not in shared]
    parts += [x.split(":", 1)[1] for x in lines[1:] if ":" in x and not x.startswith("  ") and "הערות" not in x.split(":")[0]]
    return [s[:30] for s in (_letters(p) for p in parts) if len(s) >= 8]


def locate(chunks: pd.DataFrame, index: PageIndex, start_page: int,
           x_by_orientation: bool = False) -> tuple[list, list, list, list, list]:  # nb_changed 2026-11-17: new flag
    """Start and end page per chunk, in document order; unmatched chunks inherit the previous position.

    nb_changed: also returns `regions` (one box per page of each chunk, see PageIndex.regions) and `exact`
    (True when the chunk's text was found in the PDF, False when its regions are only an estimate).
    """
    text_x = index.use_orientation_x() if x_by_orientation else None
    cursor = index.offset_of_page(start_page)
    first_offset = cursor  # nb_changed: where the body starts, the lower bound for unmatched chunks at the beginning
    starts, ends, missed = [], [], []
    # nb_changed: per chunk, the offsets (in the PDF letter text) where it starts and ends; None when not matched
    spans, anchors, exact = [], [], []
    via_alt: set[int] = set()  # nb_changed 2026-11-17: indexes of the chunks that were matched by a fallback anchor
    end_unfound: set[int] = set()  # nb_changed 2026-11-17: matched chunks whose end was not found with any end snippet
    for _, row in chunks.iterrows():
        anchor = _anchor_text(row)
        pos = next((p for n in SNIPPET_LENGTHS if len(anchor) >= n and (p := index.find(anchor[:n], cursor)) >= 0), -1)
        # nb_changed 2026-11-17: when the normal anchor finds nothing, try the fallback anchors (see _alt_anchors); the one
        # that matches replaces `anchor`, so the end of the chunk below is searched with the same letters. Chunks that
        # already match are not affected.
        # A table row with no note lines has only its label as the normal anchor (too short to search for an end, so the
        # box would be one label high even when the label is found): for those the full-row anchor is tried FIRST.
        alts = _alt_anchors(row, anchor)
        if alts and row["kind"] == "record" and len(anchor) < SNIPPET_LENGTHS[1]:
            pos = -1
        if pos < 0:
            for alt in alts:
                pos = next((p for n in SNIPPET_LENGTHS if len(alt) >= n and (p := index.find(alt[:n], cursor)) >= 0), -1)
                if pos >= 0:
                    anchor = alt
                    via_alt.add(len(spans))  # nb_changed 2026-11-17: remember it, its end is checked after the loop
                    break
            if pos < 0 and alts and row["kind"] == "record" and len(anchor) < SNIPPET_LENGTHS[1]:
                # the full-row anchor was not found: fall back to the normal (label) anchor as before
                pos = next((p for n in SNIPPET_LENGTHS if len(anchor) >= n and (p := index.find(anchor[:n], cursor)) >= 0), -1)
        if pos >= 0:
            cursor = pos + 1
        else:
            missed.append(row["chunk_id"])
        start = index.page_at(cursor)
        end = start
        epos = -1  # nb_changed: kept so the end offset of the chunk is known, not only its end page
        tail_n = SNIPPET_LENGTHS[0]  # nb_changed 2026-11-17: length of the end snippet that was found (30 unless a shorter one was needed)
        if pos >= 0 and len(anchor) >= SNIPPET_LENGTHS[1]:
            epos = index.find(anchor[-SNIPPET_LENGTHS[0]:], pos)
            if epos < 0:  # nb_changed 2026-11-17: last 30 letters not found: try the last 15 / 8 near the expected end
                epos, tail_n = _find_short_tail(index, anchor, pos)
            if epos < 0:
                end_unfound.add(len(spans))  # nb_changed 2026-11-17: no end snippet at all, handled after the loop
        # nb_changed: end offset = end of the last-30-letters snippet when found; when the anchor was too short to
        # search for an end (it matched whole) or the end was not found, the length of the anchor from the start
        # nb_changed 2026-11-17: the length of the snippet that was found (tail_n) instead of always 30
        end_off = epos + len(anchor[-tail_n:]) if epos >= 0 else pos + len(anchor)  # (len(...) because a short anchor is shorter than tail_n)
        if epos >= 0:
            end = index.page_at(end_off - 1)
        starts.append(start)
        ends.append(max(start, end))
        spans.append((pos, max(end_off, pos + 1)) if pos >= 0 else None)
        anchors.append(anchor)
        exact.append(pos >= 0 and (len(anchor) < SNIPPET_LENGTHS[1] or epos >= 0))
    # nb_changed 2026-11-17: a chunk matched by a fallback anchor must not run past the start of the next matched chunk.
    # Example: "(א) סוגי השימושים" ends with a footnote that the PDF prints BELOW the table, so its end would swallow
    # every row of the table that follows. The end is cut at the next chunk's start and the end page follows it.
    for i in sorted(via_alt):
        nxt = next((s for s in spans[i + 1:] if s), None)
        if nxt and spans[i][1] > nxt[0]:
            spans[i] = (spans[i][0], max(nxt[0], spans[i][0] + 1))
            ends[i] = max(starts[i], index.page_at(spans[i][1] - 1))
    # nb_changed 2026-11-17: in the PDF text a table row's other cells (zone name, tashrit...) come BEFORE its notes, but a
    # matched record starts at its notes, so its own cells were left in the gap above it, i.e. in the text from which the
    # unmatched chunk before it is estimated (too tall), and its own box was only as tall as the notes. For a matched record
    # whose previous chunk is NOT matched, the start moves back to the first of its own cells found within CELL_WINDOW
    # letters before its notes (rfind: the occurrence nearest to the notes, so a zone name repeated in the gap does not win).
    for i in range(1, len(spans)):
        if not spans[i] or spans[i - 1] or chunks.iloc[i]["kind"] != "record":
            continue
        lo = next((s[1] for s in reversed(spans[:i]) if s), first_offset)  # end of the last matched chunk before it
        if not 0 < spans[i][0] - lo <= CELL_WINDOW:
            continue
        found = [p for sn in _cell_snippets(chunks.iloc[i]["text"], chunks.iloc[i - 1]["text"]) if (p := index.text.rfind(sn, lo, spans[i][0])) >= 0]
        if found:
            spans[i] = (min(found), spans[i][1])
            starts[i] = index.page_at(spans[i][0])
            ends[i] = max(starts[i], index.page_at(spans[i][1] - 1))
    # nb_changed 2026-11-17 (box version 6): a chunk whose end was not found with any snippet ends where the NEXT chunk starts,
    # when that chunk is matched and its start is about one chunk length away (60%..140%), since consecutive chunks follow
    # each other in the PDF. Otherwise the old end (start + the chunk's length) stays. These chunks stay flagged as estimated.
    for i in sorted(end_unfound):
        if i + 1 < len(spans) and spans[i + 1]:
            size, gap = len(anchors[i]), spans[i + 1][0] - spans[i][0]
            if 0.6 * size <= gap <= 1.4 * size:
                spans[i] = (spans[i][0], max(spans[i + 1][0], spans[i][0] + 1))
                ends[i] = max(starts[i], index.page_at(spans[i][1] - 1))
    # nb_changed 2026-11-17 (box version 6): chunks that were not found by their start are placed by their own text inside the
    # gap between their matched neighbours (see _place_unmatched). They are no longer a guess, so they count as exact, and
    # their pages follow the new span (before they inherited the previous chunk's page). `missed` is not changed: it still
    # lists the chunks the normal matching did not find.
    for k in _place_unmatched(chunks, index, spans, first_offset):
        exact[k] = True
        starts[k] = index.page_at(spans[k][0])
        ends[k] = max(starts[k], index.page_at(spans[k][1] - 1))
    regions = _regions(index, spans, anchors, first_offset, text_x)
    return starts, ends, missed, regions, exact


# nb_changed: new. Turns the offsets found by `locate` into regions, estimating the unmatched chunks.
def _regions(index: PageIndex, spans: list, anchors: list[str], first_offset: int,
             text_x: list[tuple[float, float] | None] | None = None) -> list[list[dict]]:
    """Regions per chunk. A matched chunk gets the boxes of its own text. A run of unmatched chunks gets the text
    between the end of the matched chunk before it and the start of the one after it, split among the run by
    text length; this is an estimate (the caller flags it)."""
    regions = [index.regions(*s, text_x=text_x) if s else None for s in spans]
    i = 0
    while i < len(spans):
        if spans[i]:
            i += 1
            continue
        j = i
        while j < len(spans) and not spans[j]:
            j += 1
        lo = spans[i - 1][1] if i > 0 else first_offset  # end of the previous matched chunk
        hi = spans[j][0] if j < len(spans) else len(index.text)  # start of the next matched chunk
        weights = [max(len(a), 1) for a in anchors[i:j]]
        done = 0
        for k in range(i, j):
            a = lo + (hi - lo) * done // sum(weights)
            done += weights[k - i]
            b = lo + (hi - lo) * done // sum(weights)
            regions[k] = index.regions(a, b, text_x=text_x)
        i = j
    return _separate(regions)  # nb_changed 2026-11-12: consecutive boxes on a page must not overlap


# nb_changed 2026-11-12: new. Walks the chunks in document order and, per page, keeps a GAP between each box and the
# box before it on that page.
def _separate(regions: list[list[dict]]) -> list[list[dict]]:
    """Removes the vertical overlap between consecutive boxes of a page.

    If a box starts less than GAP below the previous box on the same page, the boundary is the midpoint of the overlap
    (previous bottom = mid - GAP/2, this top = mid + GAP/2). If that would leave either box shorter than MIN_H (e.g. a
    chunk inside the previous chunk's last line), the earlier box is kept and this one is pushed below it instead.
    """
    last: dict[int, dict] = {}  # physical page -> the previous box drawn on it
    for chunk_regions in regions:
        for r in chunk_regions:
            prev = last.get(r["pdf_page"])
            if prev and r["y0"] < prev["y1"] + GAP:
                mid = (r["y0"] + prev["y1"]) / 2
                if mid - GAP / 2 - prev["y0"] >= MIN_H and r["y1"] - (mid + GAP / 2) >= MIN_H:
                    prev["y1"], r["y0"] = round(mid - GAP / 2, 2), round(mid + GAP / 2, 2)
                else:
                    r["y0"] = round(prev["y1"] + GAP, 2)
                    r["y1"] = round(max(r["y1"], r["y0"] + MIN_H), 2)
            last[r["pdf_page"]] = r
    return regions


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("chunks", type=Path)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--start-page", type=int, required=True, help="printed page of the first body chunk (skips the TOC)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    df = pd.read_parquet(args.chunks)
    # nb_changed: locate() now also returns the regions and the exact flags; store them like add_pages does
    # nb_changed 2026-11-17: 4th argument True = one X pair per page orientation, so this command gives the same version 6
    # boxes as pipeline.preprocess. Old call: locate(df, PageIndex(args.pdf), args.start_page)
    df["page_start"], df["page_end"], missed, df["regions"], df["bbox_exact"] = locate(df, PageIndex(args.pdf), args.start_page, True)
    print(f"chunks: {len(df)}, unmatched: {len(missed)}, spanning pages: {(df.page_end > df.page_start).sum()}")
    print("unmatched sample:", missed[:15])
    if args.out:
        config.write_parquet(df, args.out, config.read_meta(args.chunks))

