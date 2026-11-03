from pipeline.doc_tree import parse_markdown

PLAN = """\
## פרק 3 – אזורי יעוד
### 3.1. הוראות כלליות
3.1.1\tשטחי בנייה
(א) הוראה בכפוף לסעיף 3.1.2 ולפי כללי החישוב.
(1) פריט ראשון.
(2) פריט שני, ראו סעיף 3.1.3.
(ב) הוראה לפי סעיפים 3.1.2 - 3.1.4 וגם סעיף 7.7.7.
3.1.2\tרח"ק
(א) הגדרה ראשונה.
(ב) לפי האמור בסעיף קטן (א) לעיל ובסעיף קטן (ג) להלן.
(ג) הגדרה אחרונה, בהתאם לסעיף 9.6 לתמ"מ 5.
3.1.3\tשימושים
(א) הוראה.
3.1.4\tגובה
(א) הוראה.
"""


def refs(tree, node_id):
    return {(r.target or r.raw, r.status, r.mandatory) for r in tree.nodes[node_id].refs_out}


def test_ids_compose_parent_numbers():
    tree = parse_markdown(PLAN.splitlines())
    assert tree.nodes["3.1.1"].parent == "3.1"
    assert tree.nodes["3.1.1(א)"].parent == "3.1.1"
    assert tree.nodes["3.1.1(א)(2)"].parent == "3.1.1(א)"
    assert tree.nodes["3.1.2(א)"].parent == "3.1.2"  # labels restart under each parent


def test_a_numbered_clause_styled_as_a_heading_is_a_clause():
    tree = parse_markdown(["## פרק 1 – כללי", "### 1.1. שם", "1.1.1\tתכנית זו תקרא תכנית כוללנית.", "1.1.2\tכותרת", "גוף הסעיף"])
    clause, heading = tree.nodes["1.1.1"], tree.nodes["1.1.2"]
    assert (clause.kind, clause.title, clause.text) == ("clause", "", "תכנית זו תקרא תכנית כוללנית.")
    assert heading.kind == "subsection"  # it has a body


def test_absolute_references_carry_the_mandatory_flag():
    tree = parse_markdown(PLAN.splitlines())
    assert ("3.1.2", "internal", True) in refs(tree, "3.1.1(א)")  # "בכפוף ל"
    assert ("3.1.3", "internal", False) in refs(tree, "3.1.1(א)(2)")  # "ראו"
    assert "3.1.1(א)" in tree.nodes["3.1.2"].refs_in


def test_range_expands_to_every_existing_section_and_missing_target_is_dangling():
    tree = parse_markdown(PLAN.splitlines())
    found = refs(tree, "3.1.1(ב)")
    assert {("3.1.2", "internal", False), ("3.1.3", "internal", False), ("3.1.4", "internal", False)} <= found
    assert ("7.7.7", "dangling", False) in found


def test_relative_references_follow_direction_words():
    tree = parse_markdown(PLAN.splitlines())
    targets = {r.target for r in tree.nodes["3.1.2(ב)"].refs_out}
    assert targets == {"3.1.2(א)", "3.1.2(ג)"}  # "לעיל" looks back, "להלן" looks ahead


def test_reference_to_another_plan_is_external():
    tree = parse_markdown(PLAN.splitlines())
    assert [r.status for r in tree.nodes["3.1.2(ג)"].refs_out] == ["external"]
