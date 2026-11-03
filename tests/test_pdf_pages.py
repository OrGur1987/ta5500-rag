from pipeline.pdf_pages import PageIndex, _letters, locate
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
    starts, ends, missed = locate(chunks(P1, P2A + " " + P2B, P3), idx, start_page=4)
    assert (starts, ends) == ([4, 4, 5], [4, 5, 5])
    assert missed == []


def test_unmatched_chunk_inherits_the_previous_page():
    idx = index({4: P1 + " " + P2A, 5: P2B + " " + P3})
    starts, _, missed = locate(chunks(P1, "טקסט שאינו מופיע בקובץ המקור כלל ולכן לא יימצא", P3), idx, start_page=4)
    assert missed == ["c1"]
    assert starts == [4, 4, 5]


def test_start_page_skips_earlier_pages():
    idx = index({3: P1, 4: P2A, 5: P2B})
    starts, _, missed = locate(chunks(P2A), idx, start_page=4)
    assert (starts, missed) == ([4], [])
