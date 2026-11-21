"""Runs the preprocessing pipeline: docx -> markdown -> tree -> chunks -> pages -> embeddings.

Usage: python -m pipeline.preprocess plan.docx plan.pdf [--model KEY] [--start-page 4] [--from STAGE]
Stages run in order from --from; earlier artifacts must already exist in the output directory.
"""
import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from tokenizers import Tokenizer

from pipeline.chunker import Chunker, report as report_chunks
from pipeline.convert import docx_to_markdown
from pipeline.doc_tree import parse_markdown, report as report_tree
from pipeline.pdf_pages import PageIndex, locate
from rag import config
from rag.embed import embed_chunks

STAGES = ["markdown", "tree", "chunks", "pages", "embeddings"]


@dataclass
class Paths:
    """Artifacts of one run."""
    out_dir: Path
    model: str

    @property
    def markdown(self) -> Path:
        return self.out_dir / "ta5500_pydocx.md"

    @property
    def nodes(self) -> Path:
        return self.out_dir / "nodes.parquet"

    @property
    def chunks(self) -> Path:
        return self.out_dir / "chunks.parquet"

    @property
    def embeddings(self) -> Path:
        return self.out_dir / f"embeddings_{self.model}.parquet"


def build_markdown(docx: Path, paths: Paths) -> None:
    docx_to_markdown(docx, "pydocx", paths.markdown)


def build_tree(paths: Paths) -> None:
    tree = parse_markdown(paths.markdown.read_text(encoding="utf-8").splitlines())
    report_tree(tree)
    tree.to_frame().to_parquet(paths.nodes, index=False)


def build_chunks(paths: Paths) -> None:
    tokenizer_id = config.model(paths.model)["tokenizer"]
    min_tokens, max_tokens = config.CHUNKING["min_tokens"], config.CHUNKING["max_tokens"]
    chunker = Chunker(pd.read_parquet(paths.nodes), Tokenizer.from_pretrained(tokenizer_id), min_tokens, max_tokens)
    chunks = chunker.chunks()
    report_chunks(chunks, max_tokens, min_tokens)
    config.write_parquet(chunks, paths.chunks, {"tokenizer": tokenizer_id, "min_tokens": min_tokens, "max_tokens": max_tokens})


# nb_changed 2026-11-17: default of x_by_orientation is now True, so add_pages() gives the version 6 boxes (one X pair per
# page orientation) when called without the flag. Old signature: x_by_orientation: bool = False
def add_pages(pdf: Path, start_page: int, paths: Paths, x_by_orientation: bool = True) -> None:  # nb_changed 2026-11-17: new flag
    chunks = pd.read_parquet(paths.chunks)
    index = PageIndex(pdf)  # nb_changed 2026-11-17: kept in a variable so the X ranges can be reported below
    # nb_changed: locate() also returns `regions` (one PDF box per page of the chunk) and `bbox_exact` (False when the
    # box is only estimated between the neighbouring matched chunks); both are saved with the chunks.
    chunks["page_start"], chunks["page_end"], missed, chunks["regions"], chunks["bbox_exact"] = locate(
        chunks, index, start_page, x_by_orientation)  # nb_changed 2026-11-17: was PageIndex(pdf) and no flag
    if x_by_orientation and index.words:  # nb_changed 2026-11-17: report the X range per orientation and the pages that set it
        for o, (x0, p0, x1, p1) in index.words["x_source"].items():
            print(f"x range {o}: {x0} (PDF page {p0}) to {x1} (PDF page {p1})")
    print(f"pages: {len(chunks)} chunks, unmatched {len(missed)} (inherit the previous page), "
          f"spanning pages {(chunks.page_end > chunks.page_start).sum()}")
    # nb_changed: report how many boxes are exact, estimated or missing
    print(f"regions: exact {int(chunks.bbox_exact.sum())}, estimated {int((~chunks.bbox_exact).sum())}, "
          f"none {int(chunks.regions.map(lambda r: not r).sum())}, over 1 page {int(chunks.regions.map(len).gt(1).sum())}")
    config.write_parquet(chunks, paths.chunks, config.read_meta(paths.chunks))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docx", type=Path)
    ap.add_argument("pdf", type=Path, help="PDF exported from the docx after accepting tracked changes")
    ap.add_argument("--model", help="registry key in models.yml (default: active)")
    ap.add_argument("--start-page", type=int, default=4, help="printed page of the first body chunk (skips the TOC)")
    # nb_changed 2026-11-17: box version 3, one X range per page orientation instead of one per page
    # nb_changed 2026-11-17: now on by default (box version 6); BooleanOptionalAction also gives --no-x-by-orientation to
    # switch it off (per-page X, version 2 style). Old line, which could only switch it on:
    # ap.add_argument("--x-by-orientation", action="store_true", help="same X range on all pages of one orientation")
    ap.add_argument("--x-by-orientation", action=argparse.BooleanOptionalAction, default=True,
                    help="same X range on all pages of one orientation (default: on; --no-x-by-orientation for per-page X)")
    ap.add_argument("--out-dir", type=Path, default=config.DATA_DIR)
    ap.add_argument("--from", dest="first", choices=STAGES, default=STAGES[0])
    args = ap.parse_args()

    paths = Paths(args.out_dir, config.model(args.model)["name"])
    stages = {
        "markdown": lambda: build_markdown(args.docx, paths),
        "tree": lambda: build_tree(paths),
        "chunks": lambda: build_chunks(paths),
        "pages": lambda: add_pages(args.pdf, args.start_page, paths, args.x_by_orientation),  # nb_changed 2026-11-17: flag passed on
        "embeddings": lambda: embed_chunks(paths.chunks, paths.embeddings, paths.model),
    }
    for name in STAGES[STAGES.index(args.first):]:
        print(f"== {name}")
        stages[name]()


if __name__ == "__main__":
    main()
