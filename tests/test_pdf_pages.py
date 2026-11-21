from pipeline.pdf_pages import GAP, PAD_Y, PageIndex, _alt_anchors, _letters, locate  # nb_changed 2026-11-12: GAP, PAD_Y for the new tests; 2026-11-17: _alt_anchors
import pandas as pd

P1 = "שטחי הבנייה המרביים בתכניות מפורטות יקבעו בהתאם להוראות הבאות"
P2A = "הוועדה רשאית לקבוע בתכנית שטחי בניה מעל מפלס הכניסה הקובעת"
P2B = "בהיקף שיחושב על ידי הכפלת הרחק הבסיסי בשטח הקרקע לחישוב זכויות"
P3 = "תנאים לעלייה מעבר לרחק המרבי יחולו כמפורט להלן בסעיף שלהלן"


def index(pages: dict[int, str]) -> PageIndex:
    idx = PageIndex.__new__(PageIndex)  # skips reading a PDF
    letters = [_letters(t) for t in pages.values()]
    idx.numbers = list(pages)
    idx.starts = [sum(map(len, letters[:i])) for i in range(len(letters))]
    idx.text = "".join(letters)
    return idx


def chunks(*texts: str) -> pd.DataFrame:
    return pd.DataFrame({"chunk_id": [f"c{i}" for i in range(len(texts))], "kind": "clause", "text": list(texts)})


def test_pages_follow_document_order_and_a_chunk_can_span_pages():
    idx = index({4: P1 + " " + P2A, 5: P2B + " " + P3})
    # nb_changed: locate() returns 5 values now; `*_` ignores the regions and exact flags this test does not check
    starts, ends, missed, *_ = locate(chunks(P1, P2A + " " + P2B, P3), idx, start_page=4)
    assert (starts, ends) == ([4, 4, 5], [4, 5, 5])
    assert missed == []


def test_unmatched_chunk_inherits_the_previous_page():
    idx = index({4: P1 + " " + P2A, 5: P2B + " " + P3})
    # nb_changed: locate() returns 5 values now; `*_` ignores the regions and exact flags this test does not check
    starts, _, missed, *_ = locate(chunks(P1, "טקסט שאינו מופיע בקובץ המקור כלל ולכן לא יימצא", P3), idx, start_page=4)
    assert missed == ["c1"]
    assert starts == [4, 4, 5]


def test_start_page_skips_earlier_pages():
    idx = index({3: P1, 4: P2A, 5: P2B})
    # nb_changed: locate() returns 5 values now; `*_` ignores the regions and exact flags this test does not check
    starts, _, missed, *_ = locate(chunks(P2A), idx, start_page=4)
    assert (starts, missed) == ([4], [])


# nb_changed: everything below is new, for the regions (bounding boxes) and the exact flag.
def words_index(pages: dict[int, str]) -> PageIndex:
    """Like `index`, plus a word table: every word is a 10x10 box, one line of words per page 100 points wide."""
    idx = index(pages)
    offsets, boxes, page_of, pos = [], [], [], 0
    for pi, text in enumerate(pages.values()):
        for wi, word in enumerate(text.split()):
            offsets.append(pos)
            boxes.append((10.0 * wi, 20.0 * pi, 10.0 * wi + 10, 20.0 * pi + 10))
            page_of.append(pi)
            pos += len(_letters(word))
    # nb_changed 2026-11-12: the word table now also has the fixed X range and the orientation of every page
    idx.words = {"offsets": offsets, "boxes": boxes, "pages": page_of, "end": pos, "sizes": [(100.0, 200.0)] * len(pages),
                 "text_x": [(0.0, 100.0)] * len(pages), "orient": ["portrait"] * len(pages)}
    return idx


# nb_changed 2026-11-12: like words_index but every line of every page is given separately (one 10-point-high row each,
# 20 points apart), so chunks can sit on different lines; page 1 is portrait, a second page landscape with a wider X range.
def lines_index(pages: dict[int, list[str]]) -> PageIndex:
    idx = index({n: " ".join(lines) for n, lines in pages.items()})
    offsets, boxes, page_of, pos = [], [], [], 0
    for pi, lines in enumerate(pages.values()):
        for li, line in enumerate(lines):
            for wi, word in enumerate(line.split()):
                offsets.append(pos)
                boxes.append((10.0 * wi + 5, 20.0 * li, 10.0 * wi + 15, 20.0 * li + 10))  # words start at x=5
                page_of.append(pi)
                pos += len(_letters(word))
    idx.words = {"offsets": offsets, "boxes": boxes, "pages": page_of, "end": pos,
                 "sizes": [(100.0, 200.0), (200.0, 100.0)][:len(pages)] + [(100.0, 200.0)] * max(len(pages) - 2, 0),
                 "text_x": [(1.0, 99.0), (1.0, 199.0)][:len(pages)] + [(1.0, 99.0)] * max(len(pages) - 2, 0),
                 "orient": ["portrait", "landscape"][:len(pages)] + ["portrait"] * max(len(pages) - 2, 0)}
    return idx


# nb_changed 2026-11-17: tests for box version 3 (one X range per orientation, from the widest page of that orientation).
def orientation_index() -> PageIndex:
    """Three pages: a portrait page with a narrow text area FIRST, a portrait page with a wide one, then a landscape page."""
    idx = lines_index({4: [P1], 5: [P2A], 6: [P3]})
    idx.words["sizes"] = [(100.0, 200.0), (100.0, 200.0), (200.0, 100.0)]
    idx.words["text_x"] = [(30.0, 60.0), (5.0, 95.0), (10.0, 190.0)]
    idx.words["orient"] = ["portrait", "portrait", "landscape"]
    return idx


def test_x_by_orientation_uses_the_widest_page_of_each_orientation_not_the_first():
    regions = locate(chunks(P1, P2A, P3), orientation_index(), start_page=4, x_by_orientation=True)[3]
    pairs = [(r["x0"], r["x1"]) for chunk in regions for r in chunk]
    # both portrait pages get the smallest left (5) and the largest right (95) of the two, the first page's 30..60 loses
    assert pairs == [(5.0, 95.0), (5.0, 95.0), (10.0, 190.0)]


def test_x_by_orientation_reports_the_pages_that_set_the_edges_and_leaves_y_alone():
    default = locate(chunks(P1, P2A, P3), orientation_index(), start_page=4)[3]
    idx = orientation_index()
    by_orientation = locate(chunks(P1, P2A, P3), idx, start_page=4, x_by_orientation=True)[3]
    assert idx.words["x_source"] == {"portrait": [5.0, 2, 95.0, 2], "landscape": [10.0, 3, 190.0, 3]}
    assert [[(r["y0"], r["y1"]) for r in c] for c in default] == [[(r["y0"], r["y1"]) for r in c] for c in by_orientation]
    assert default[0][0]["x0"] == 30.0  # without the flag every page keeps its own range (version 2)


def test_reusing_index_keeps_page_ranges_and_orientation_sources():
    idx = lines_index({4: [P1, P2B], 5: [P2A], 6: [P3]})
    idx.words["sizes"] = [(100.0, 200.0), (100.0, 200.0), (200.0, 100.0)]
    idx.words["text_x"] = [(30.0, 60.0), (5.0, 95.0), (10.0, 190.0)]
    idx.words["orient"] = ["portrait", "portrait", "landscape"]
    missing = "טקסט שאינו מופיע בקובץ המקור כלל ולכן לא יימצא"
    data = chunks(P1, missing, P3)

    for flag, expected in [(True, (5.0, 95.0)), (False, (30.0, 60.0)), (True, (5.0, 95.0))]:
        _, _, missed, regions, _ = locate(data, idx, start_page=4, x_by_orientation=flag)
        assert missed == ["c1"]
        assert (regions[0][0]["x0"], regions[0][0]["x1"]) == expected
        assert (regions[1][0]["x0"], regions[1][0]["x1"]) == expected
        if flag:
            assert idx.words["x_source"]["portrait"] == [5.0, 2, 95.0, 2]


def test_every_box_on_a_page_uses_the_pages_fixed_x_range():
    idx = lines_index({4: [P1, P2A], 5: [P2B, P3]})
    regions = locate(chunks(P1, P2A + " " + P2B, P3), idx, start_page=4)[3]
    by_page = {}
    for r in (r for chunk in regions for r in chunk):
        by_page.setdefault(r["pdf_page"], set()).add((r["x0"], r["x1"]))
    # one X range per page, wider than the chunk's own words (those span only 5..75 on page 1)
    assert by_page == {1: {(1.0, 99.0)}, 2: {(1.0, 199.0)}}
    assert {r["orientation"] for r in regions[1]} == {"portrait", "landscape"}


def test_consecutive_boxes_on_a_page_do_not_overlap_and_keep_a_gap():
    idx = lines_index({4: [P1, P2A, P2B, P3]})
    regions = locate(chunks(P1, P2A, P2B, P3), idx, start_page=4)[3]
    boxes = [chunk[0] for chunk in regions]
    for a, b in zip(boxes, boxes[1:]):
        assert b["y0"] >= a["y1"] + GAP - 1e-6
    assert boxes[0]["y0"] == -PAD_Y and boxes[0]["y1"] == 10 + PAD_Y  # unchanged when nothing is close


def test_a_line_shared_by_two_chunks_is_split_at_the_midpoint():
    half = len(P2A.split()) // 2
    first, second = " ".join(P2A.split()[:half]), " ".join(P2A.split()[half:])
    idx = lines_index({4: [P1, P2A, P3]})  # P2A is one line, the two chunks both sit on it
    regions = locate(chunks(P1, first, second, P3), idx, start_page=4)[3]
    a, b = regions[1][0], regions[2][0]
    assert a["y1"] < b["y0"] and b["y0"] - a["y1"] >= GAP - 1e-6  # no overlap, GAP apart
    assert a["y0"] < a["y1"] and b["y0"] < b["y1"]  # neither box collapsed


def test_chunk_over_two_pages_gets_one_region_per_page():
    idx = words_index({4: P1 + " " + P2A, 5: P2B + " " + P3})
    starts, ends, _, regions, exact = locate(chunks(P1, P2A + " " + P2B, P3), idx, start_page=4)
    assert [len(r) for r in regions] == [1, 2, 1] and exact == [True, True, True]
    assert [r["page"] for r in regions[1]] == [4, 5]  # the chunk that runs over the page break, one box per page
    assert [r["pdf_page"] for r in regions[1]] == [1, 2]  # physical page index, 1-based
    assert regions[1][0]["page_w"] == 100.0 and regions[1][0]["page_h"] == 200.0


def test_unmatched_chunk_is_estimated_between_its_neighbours_and_flagged():
    idx = words_index({4: P1 + " " + P2A, 5: P2B + " " + P3})
    missing = "טקסט שאינו מופיע בקובץ המקור כלל ולכן לא יימצא"
    _, _, missed, regions, exact = locate(chunks(P1, missing, P3), idx, start_page=4)
    assert missed == ["c1"] and exact == [True, False, True]
    assert regions[1]  # estimated from the text between P1 and P3, not left empty
    assert {r["page"] for r in regions[1]} <= {4, 5}


# nb_changed 2026-11-17: tests for box version 4 (fallback anchors for table rows and for chunks that start with "(א)").
def kinds(*rows: tuple[str, str]) -> pd.DataFrame:
    """Chunks with an explicit kind: rows of (kind, text)."""
    return pd.DataFrame({"chunk_id": [f"c{i}" for i in range(len(rows))], "kind": [k for k, _ in rows], "text": [t for _, t in rows]})


RECORD = "[מגורים]\n- השימושים המותרים במקבץ: " + P2A + "\n- פירוט לדוגמה *: " + P2B


def test_table_row_without_notes_is_found_by_label_uses_and_example_and_covers_the_whole_row():
    # the label alone ("מגורים", 6 letters) is too short to search; the row is: label + uses on line 2, example on line 3
    idx = lines_index({4: [P1, "מגורים " + P2A, P2B, P3]})
    starts, _, missed, regions, exact = locate(kinds(("clause", P1), ("record", RECORD), ("clause", P3)), idx, start_page=4)
    assert missed == [] and exact == [True, True, True]
    row = regions[1][0]
    assert row["y0"] == 20 - PAD_Y and row["y1"] == 40 + 10 + PAD_Y  # from the top of line 2 to the bottom of line 3 (the example)


def test_clause_with_list_marker_matches_when_the_pdf_text_has_no_marker_letter():
    idx = words_index({4: P1 + " " + P2A + " " + P3})
    # "(א)" puts the letter א in front of the text, which the PDF text does not have in that place
    _, _, missed, regions, exact = locate(kinds(("clause", P1), ("clause", "(א) " + P2A), ("clause", P3)), idx, start_page=4)
    assert missed == [] and exact == [True, True, True]


def test_chunk_matched_by_a_fallback_does_not_run_past_the_next_chunk():
    # chunk A = "(א)" + heading (line 2) + a footnote the PDF prints on line 4, BELOW chunk B (line 3, a table row)
    idx = lines_index({4: [P1, P2A, P2B, P3]})
    a = "(א) " + P2A + " " + P3
    _, _, _, regions, _ = locate(kinds(("clause", P1), ("clause", a), ("clause", P2B)), idx, start_page=4)
    # lines are 10 high and 20 apart: A ends with its own line 2 (y 20..30), B is line 3 (y 40..50); no overlap
    assert regions[1][0]["y0"] == 20 - PAD_Y and regions[1][0]["y1"] == 20 + 10 + PAD_Y
    assert regions[2][0]["y0"] == 40 - PAD_Y


# nb_changed 2026-11-17: tests for box version 5 (a record starts at its own cells, which the PDF prints before its notes).
def _row(path: str, cells: str, notes: str) -> str:
    return f"[{path}]\n- תשריט עם גבולות המתחם: {cells}\n- הערות והוראות נוספות(1) (2): - {notes}"


def test_record_after_an_unmatched_chunk_starts_at_its_own_cells_and_the_estimate_shrinks():
    cells_a, cells_c = "אזור ירוק גדול ויפה מאוד", "אזור כחול קטן ומרוחק מאוד"
    row_a, row_c = _row("רובע אחד > אזור א", cells_a, P1), _row("רובע אחד > אזור ג", cells_c, P3)
    missing = "[רובע אחד > אזור ב]\nתיאור שלא מופיע במסמך המקור בכלל ולכן לא יימצא"
    # PDF lines: cells A / notes A (P1) / the cells of the missing row B / cells C / notes C (P3), cells before notes
    idx = lines_index({4: [cells_a, P1, "אזור ב שורה שאינה מופיעה בטקסט", cells_c, P3]})
    regions = locate(kinds(("record", row_a), ("record", missing), ("record", row_c)), idx, start_page=4)[3]
    # C starts at its cells line (line 4, y 60) and not at its notes line (line 5, y 80): the old start would be y 78
    assert regions[2][0]["y0"] == 60 - PAD_Y
    # the estimate for B is only B's own line (y 40..50), not B plus C's cells
    assert regions[1][0]["y1"] <= 50 + PAD_Y + 1e-6


def test_record_does_not_take_the_header_it_shares_with_the_unmatched_chunk_before_it():
    area = "אזור תכנון מערבי גדול"  # the area name: the header of the unmatched area record AND the parent of the row below
    row = _row(f"רובע אחד > {area} > אזור ג", "אזור כחול קטן ומרוחק מאוד", P3)
    missing = f"[רובע אחד > {area}]\nתיאור שלא מופיע במסמך המקור בכלל ולכן לא יימצא"
    idx = lines_index({4: [P1, area, "אזור כחול קטן ומרוחק מאוד", P3]})
    regions = locate(kinds(("clause", P1), ("record", missing), ("record", row)), idx, start_page=4)[3]
    assert regions[1], "the unmatched chunk keeps its estimate (its header line is not taken by the next row)"
    assert regions[2][0]["y0"] == 40 - PAD_Y  # the row starts at its zone cell (line 3), not at the shared area line (line 2)


# nb_changed 2026-11-17: tests for box version 6 (end found with a shorter tail, end at the next chunk, placing unmatched chunks).
P2B_EXTRA = P2B.replace("בשטח", "מאוד בשטח")  # the PDF has one more word near the end than the chunk text


def test_end_is_found_with_a_shorter_tail_when_the_last_30_letters_differ():
    idx = lines_index({4: [P2A, P2B_EXTRA, P3]})
    regions, exact = locate(chunks(P2A + " " + P2B, P3), idx, start_page=4)[3:]
    assert exact == [True, True]  # the last 15 letters are found, so the chunk is located at both ends
    assert regions[0][0]["y0"] == -PAD_Y and regions[0][0]["y1"] == 20 + 10 + PAD_Y  # lines 1 and 2, not line 3


def test_end_not_found_by_any_tail_is_the_start_of_the_next_matched_chunk_and_stays_estimated():
    idx = lines_index({4: [P2A, P2B + " משהו אחר לגמרי", P3]})
    chunk = P2A + " " + P2B + " שאינו קיים בקובץ כלל וכלל לא"  # a tail the PDF does not have
    regions, exact = locate(chunks(chunk, P3), idx, start_page=4)[3:]
    assert exact == [False, True]
    assert regions[0][0]["y1"] == 20 + 10 + PAD_Y  # ends with line 2, where the next chunk starts, not inside line 3


def test_short_chunk_end_is_its_own_length_not_30_letters():
    idx = lines_index({4: ["הועדה רשאית לקבוע", P3]})  # 16 letters: shorter than the 30-letter end snippet
    regions = locate(chunks("הועדה רשאית לקבוע", P3), idx, start_page=4)[3]
    assert regions[0][0]["y1"] == 10 + PAD_Y  # line 1 only; an end 30 letters long would run into line 2


def test_unmatched_record_is_placed_by_its_own_cell_inside_the_gap_and_counts_as_exact():
    cell = "אזור כחול קטן ומרוחק מאוד"
    idx = lines_index({4: [P1, cell, P3]})
    row = f"[רובע אחד > {cell}]\nמאפייני: משהו אחר לחלוטין שלא קיים בקובץ"
    _, _, missed, regions, exact = locate(kinds(("clause", P1), ("record", row), ("clause", P3)), idx, start_page=4)
    assert missed == ["c1"] and exact == [True, True, True]  # not found by the normal matching, placed by its cell
    assert (regions[1][0]["y0"], regions[1][0]["y1"]) == (20 - PAD_Y, 30 + PAD_Y)


def test_run_of_unmatched_records_is_shared_by_what_each_says_not_by_text_length():
    a, b = "אזור ירוק גדול ויפה מאוד", "אזור כחול קטן ומרוחק מאוד"
    idx = lines_index({4: [P1, a, b, P3]})
    rows = [f"[רובע אחד > {x}]\nמאפייני: משהו אחר לחלוטין שלא קיים בקובץ" for x in (a, b)]
    regions, exact = locate(kinds(("clause", P1), ("record", rows[0]), ("record", rows[1]), ("clause", P3)), idx, start_page=4)[3:]
    assert exact == [True] * 4
    assert (regions[1][0]["y0"], regions[1][0]["y1"]) == (20 - PAD_Y, 30 + PAD_Y)  # each on its own line
    assert (regions[2][0]["y0"], regions[2][0]["y1"]) == (40 - PAD_Y, 50 + PAD_Y)


def test_unmatched_chunk_with_no_own_text_in_the_gap_stays_estimated():
    idx = lines_index({4: [P1, "שורה אחרת לגמרי בלי קשר לשום דבר", P3]})
    row = "[רובע אחד > אזור כחול קטן ומרוחק מאוד]\nמאפייני: משהו אחר לחלוטין שלא קיים בקובץ"
    exact = locate(kinds(("clause", P1), ("record", row), ("clause", P3)), idx, start_page=4)[4]
    assert exact == [True, False, True]


def test_record_after_a_matched_chunk_is_not_extended():
    cells_c = "אזור כחול קטן ומרוחק מאוד"
    row_a, row_c = _row("רובע אחד > אזור א", "אזור ירוק גדול ויפה מאוד", P1), _row("רובע אחד > אזור ג", cells_c, P3)
    idx = lines_index({4: ["אזור ירוק גדול ויפה מאוד", P1, cells_c, P3]})
    regions = locate(kinds(("record", row_a), ("record", row_c)), idx, start_page=4)[3]
    assert regions[1][0]["y0"] == 60 - PAD_Y  # unchanged from the notes-only box (notes line is line 4, y 60)


def test_fallback_anchors_are_not_used_for_chunks_that_already_match():
    clause = pd.Series({"kind": "clause", "text": P1})
    assert _alt_anchors(clause, _letters(P1)) == []  # same letters as the normal anchor: nothing else to try
    idx = words_index({4: P1 + " " + P2A})
    assert locate(kinds(("clause", P1)), idx, start_page=4)[2] == []


def test_no_word_table_means_no_regions():
    idx = index({4: P1 + " " + P2A})  # the plain index of the tests above has no word table
    assert locate(chunks(P1), idx, start_page=4)[3] == [[]]
