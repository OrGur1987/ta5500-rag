nb_changed: new file. Chunk locator demo (PDF + chunk bounding boxes).

Start (from the repository root):
    python -m http.server 8765 --directory pdf_viewer_demo
Open http://localhost:8765/   (a browser blocks the PDF and JSON when index.html is opened as a file)

Files: index.html (the page; pdf.js comes from cdnjs, so the browser needs internet access),
       chunks.json (exported from bundle/chunks_seq_num_bbox.parquet), plan.pdf (the PDF the boxes were measured on).
URL examples: ?mode=seq&v=176  ?mode=page&v=37  ?mode=all&v=37  ?mode=id&v=3.5.4  ?mode=section&v=3.5
Page lists include chunks with boxes in the currently selected versions.

nb_changed 2026-11-12: two versions of the boxes can be compared.
       chunks_v2.json = version 2 of the boxes (fixed X per page, from bundle/chunks_seq_num_bbox_1.parquet); chunks.json is version 1.
       The "boxes" switch above the PDF shows v1, v2 or both (?boxes=1 | ?boxes=2 | ?boxes=both, e.g. ?mode=all&v=5&boxes=both).

nb_changed 2026-11-17: a third version of the boxes.
       chunks_v3.json = version 3 (from bundle/chunks_seq_num_bbox_2.parquet): like v2 but ONE X range per page orientation,
       the widest page of that orientation decides. The toolbar has one toggle per version (v1, v2, v3) and "all".
       URL: ?boxes=all | ?boxes=1,3 | ?boxes=none (1, 2 and both still work), e.g. ?mode=all&v=80&boxes=all

nb_changed 2026-11-17: version 4 of the boxes
  chunks_v4.json = bundle/chunks_seq_num_bbox_3.parquet. Same X rule as v3 (one X range per page orientation), but the
  chunks are located better: table rows without note lines are found by label + uses + example, and chunks that start with a
  list marker such as "(א)" are found without the marker. Estimated chunks: 108 -> 36 (22 not found at all, 14 found at the
  start but not at the end). A chunk matched this way never runs past the start of the next matched chunk.
  Toolbar: v1 v2 v3 v4 all; ?boxes=4, ?boxes=1,4, ?boxes=all (= 1,2,3,4), ?boxes=none. v4 is drawn as a grooved line.
  "exact / estimated" is now per version (status line, tags, chunk panel).
  Note: the page_start / page_end shown in the chunk panel still come from chunks.json (v1).

nb_changed 2026-11-17: version 5 of the boxes
  chunks_v5.json = bundle/chunks_seq_num_bbox_4.parquet. v4 plus better boxes around table rows (table 5.4): in the PDF text a
  row's other cells (zone name, tashrit) come BEFORE its notes, so a matched record whose previous chunk is not matched now
  starts at its own cells. That makes its box the real row height and removes those cells from the gap the unmatched chunk
  before it is estimated from (smaller, better estimate). Which chunks are exact or estimated is the same as in v4; only 32
  boxes differ. Toolbar: v1 .. v5 and all; ?boxes=5, ?boxes=4,5, ?boxes=all (= 1..5). v5 is drawn as a ridged line.

nb_changed 2026-11-17: version 6 of the boxes
  chunks_v6.json = bundle/chunks_seq_num_bbox_5.parquet. v5 plus a second look at every chunk that was still estimated (36):
    - the end of a chunk is also searched with its last 15 / 8 letters near where it should end (text differs from the PDF by a
      few letters); if that fails the chunk ends where the next matched chunk starts (kept flagged as estimated);
    - chunks not found by their start (table rows without notes, rows 115-118, subsections 192 and 270) are placed by their own
      text inside the gap between their matched neighbours, in order, and count as exact.
  Estimated boxes left: 4 (chunks 141, 174, 234, 403: no end snippet found). Toolbar: v1 .. v6 and all; ?boxes=6, ?boxes=all (= 1..6).
  v6 is drawn as an outset line.
