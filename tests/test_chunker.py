from types import SimpleNamespace

from pipeline.chunker import Chunker
from pipeline.doc_tree import parse_markdown


class WordTokenizer:
    """One token per word, so sizes in the tests are easy to read."""

    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=text.split())


def chunk(markdown: str, min_tokens: int, max_tokens: int):
    nodes = parse_markdown(markdown.splitlines()).to_frame()
    return Chunker(nodes, WordTokenizer(), min_tokens, max_tokens).chunks()


def words(n: int, label: str = "מילה") -> str:
    return " ".join([label] * n)


def test_small_subtree_is_one_chunk_with_breadcrumb():
    df = chunk(f"## פרק 3 – אזורי יעוד\n### 3.1. כללי\n3.1.1\tסעיף\n(א) {words(5)}\n(ב) {words(5)}\n", 1, 100)
    row = df[df["node_ids"].map(lambda ids: "3.1.1(א)" in ids)].iloc[0]
    assert row["node_ids"][-1] == "3.1.1(ב)"
    assert row["embed_text"].startswith(" > ".join(row["heading_path"]))
    assert row["tokens"] == len(row["embed_text"].split())


def test_large_section_splits_at_children_and_small_siblings_merge():
    md = (f"## פרק 3 – אזורי יעוד\n### 3.1. כללי\n"
          f"3.1.1\tא\n(א) {words(30)}\n"
          f"3.1.2\tב\n(א) {words(3)}\n"
          f"3.1.3\tג\n(א) {words(3)}\n")
    df = chunk(md, min_tokens=10, max_tokens=40)
    holding = lambda node: df[df["node_ids"].map(lambda ids: node in ids)].iloc[0]["node_ids"]
    assert "3.1.2" not in holding("3.1.1(א)")  # the 33-word section was split at its children
    assert "3.1.3" in holding("3.1.2")  # two small siblings share a chunk


def test_table_records_stay_atomic_and_get_their_location():
    md = ("## פרק 5 – אזורי תכנון\n### 5.4. טבלה\n"
          "[רובע 7 > 703 מבואות יפו > 703 ה' אזור מעורב]\n- רח\"ק מרבי (1): 5\n\n"
          "[רובע 7 > 703 מבואות יפו > מוקד תעסוקה חדש]\n- רח\"ק מרבי (1): 14\n\n"
          "[רובע 1 > 103 תכנית ל']\nהמרחב עליו חלות ההוראות: רח' איינשטיין | תשריט עם גבולות המתחם: תשריט עיצוב עירוני\n")
    df = chunk(md, min_tokens=100, max_tokens=20)  # each record fits alone; min_tokens would merge anything not atomic
    records = df[df["kind"] == "record"]
    assert len(records) == 3
    first = records.iloc[0]
    assert (first["district"], first["area"], first["area_name"], first["zone"]) == (
        "רובע 7", "703", "מבואות יפו", "703 ה' אזור מעורב")
    assert records.iloc[1]["zone"] == "מוקד תעסוקה חדש"
    assert records.iloc[2]["zone"] == "רח' איינשטיין"  # a short row keeps its first column on one line


def test_glossary_lines_become_one_definition_chunk_each():
    terms = "\n".join(f"מונח{i}: הגדרה של מונח {i}" for i in range(6))
    df = chunk(f"## פרק 1 – כללי\n### 1.5. הגדרות\n{terms}\n", min_tokens=1, max_tokens=10)
    assert (df["kind"] == "definition").sum() == 6


def test_references_follow_the_chunk_that_holds_them():
    md = ("## פרק 3 – אזורי יעוד\n### 3.1. כללי\n3.1.1\tא\n(א) בכפוף לסעיף 3.1.2 כאן.\n3.1.2\tב\n(א) תוכן.\n")
    df = chunk(md, 1, 100)
    refs = [r for refs in df["refs_out"] for r in refs]
    assert any(r["target"] == "3.1.2" and r["mandatory"] for r in refs)
