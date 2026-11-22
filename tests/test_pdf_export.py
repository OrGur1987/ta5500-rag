import json

import pandas as pd

from pipeline.export_pdf_chunks import export_pdf_chunks


def test_export_preserves_viewer_content_and_boxes(tmp_path):
    source = tmp_path / "chunks.parquet"
    output = tmp_path / "chunks.json"
    pd.DataFrame([
        {
            "chunk_id": "first",
            "node_ids": ["node"],
            "embed_text": "embedded",
            "text": "טקסט",
            "page_start": 7,
            "page_end": 8,
            "regions": [{"pdf_page": 7, "x0": 1.0}, {"pdf_page": 8, "x0": 2.0}],
            "bbox_exact": True,
        },
        {
            "chunk_id": "second",
            "node_ids": ["node2"],
            "embed_text": "embedded2",
            "text": "קטע נוסף",
            "page_start": 9,
            "page_end": 9,
            "regions": [{"pdf_page": 9, "x0": 3.0}],
            "bbox_exact": False,
        },
    ]).to_parquet(source, index=False)

    export_pdf_chunks(source, output)

    rows = json.loads(output.read_text(encoding="utf-8"))
    assert [row["seq"] for row in rows] == [0, 1]
    assert rows[0] == {
        "seq": 0,
        "chunk_id": "first",
        "text": "טקסט",
        "page_start": 7,
        "page_end": 8,
        "regions": [{"pdf_page": 7, "x0": 1.0}, {"pdf_page": 8, "x0": 2.0}],
        "bbox_exact": True,
    }
    assert rows[1]["bbox_exact"] is False
