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


def _letters(text: str) -> str:
    """Hebrew letters only: immune to the digit and punctuation reordering of PDF text extraction."""
    return "".join(HEB.findall(text))


def _strip_repeated_lines(pages: list[str]) -> list[str]:
    """Drops running headers and footers: lines that recur, ignoring digits, on many pages."""
    norm = lambda line: re.sub(r"\d+", "#", line.strip())
    counts = Counter(n for t in pages for n in {norm(x) for x in t.split("\n")} if n)
    repeated = {n for n, c in counts.items() if c >= 0.3 * len(pages)}
    return ["\n".join(x for x in t.split("\n") if norm(x) not in repeated) for t in pages]


class PageIndex:
    """Hebrew-only text of the whole PDF with the printed page number of every offset."""

    def __init__(self, pdf: Path):
        raw = [p.get_text() for p in fitz.open(pdf)]
        self.numbers = [int(m.group(1)) if (m := PAGE_NO.search(t)) else i + 1 for i, t in enumerate(raw)]
        letters = [_letters(t) for t in _strip_repeated_lines(raw)]
        self.starts = [sum(map(len, letters[:i])) for i in range(len(letters))]
        self.text = "".join(letters)

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


def locate(chunks: pd.DataFrame, index: PageIndex, start_page: int) -> tuple[list, list, list]:
    """Start and end page per chunk, in document order; unmatched chunks inherit the previous position."""
    cursor = index.offset_of_page(start_page)
    starts, ends, missed = [], [], []
    for _, row in chunks.iterrows():
        anchor = _anchor_text(row)
        pos = next((p for n in SNIPPET_LENGTHS if len(anchor) >= n and (p := index.find(anchor[:n], cursor)) >= 0), -1)
        if pos >= 0:
            cursor = pos + 1
        else:
            missed.append(row["chunk_id"])
        start = index.page_at(cursor)
        end = start
        if pos >= 0 and len(anchor) >= SNIPPET_LENGTHS[1]:
            epos = index.find(anchor[-SNIPPET_LENGTHS[0]:], pos)
            end = index.page_at(epos) if epos >= 0 else start
        starts.append(start)
        ends.append(max(start, end))
    return starts, ends, missed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("chunks", type=Path)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--start-page", type=int, required=True, help="printed page of the first body chunk (skips the TOC)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    df = pd.read_parquet(args.chunks)
    df["page_start"], df["page_end"], missed = locate(df, PageIndex(args.pdf), args.start_page)
    print(f"chunks: {len(df)}, unmatched: {len(missed)}, spanning pages: {(df.page_end > df.page_start).sum()}")
    print("unmatched sample:", missed[:15])
    if args.out:
        config.write_parquet(df, args.out, config.read_meta(args.chunks))

