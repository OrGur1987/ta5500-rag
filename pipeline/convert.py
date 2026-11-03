"""Convert Word documents to Markdown with a selectable engine (pandoc or python-docx)."""
import argparse
from pathlib import Path

import pypandoc

from pipeline.docx_numbered import docx_to_markdown as pydocx_to_markdown


def pandoc_to_markdown(src: Path, dst: Path) -> Path:
    """GitHub-flavored Markdown via Pandoc (drops Word auto-numbering)."""
    pypandoc.convert_file(
        str(src), "gfm", format="docx", outputfile=str(dst),
        extra_args=["--wrap=none"],
    )
    return dst


ENGINES = {"pandoc": pandoc_to_markdown, "pydocx": pydocx_to_markdown}


def docx_to_markdown(src: Path, engine: str = "pandoc", dst: Path | None = None) -> Path:
    """Convert a docx to Markdown; default output is `<src stem>_<engine>.md` next to the source."""
    dst = dst or src.with_name(f"{src.stem}_{engine}.md")
    return ENGINES[engine](src, dst)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("--engine", choices=ENGINES, default="pandoc")
    parser.add_argument("--dst", type=Path)
    args = parser.parse_args()
    print(docx_to_markdown(args.src, args.engine, args.dst))
