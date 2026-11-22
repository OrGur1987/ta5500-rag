"""Export the indexed chunks used by the PDF viewer."""
import argparse
from pathlib import Path

import pandas as pd


def export_pdf_chunks(source: Path, output: Path) -> None:
    chunks = pd.read_parquet(source)
    required = {"chunk_id", "text", "page_start", "page_end", "regions", "bbox_exact"}
    missing = required.difference(chunks.columns)
    if missing:
        raise ValueError(f"PDF viewer data is missing columns: {', '.join(sorted(missing))}")
    chunks = chunks.drop(columns=["node_ids", "embed_text"])
    chunks.insert(0, "seq", range(len(chunks)))
    chunks.to_json(output, orient="records", force_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_pdf_chunks(args.source, args.output)
