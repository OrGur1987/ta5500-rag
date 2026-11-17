import numpy as np
import pytest

from rag import config, retrieval
from synthetic_index import CHUNKS, HOTEL, MIXED, NEW_HUB, make


def via(result):
    return {h["chunk_id"]: h["via"] for h in result.hits}


def test_location_is_read_from_the_query_text(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    loc = ret.locate("מה הרח\"ק במוקד תעסוקה חדש באזור 703?", None, None)
    assert (loc["areas"], loc["zone_types"], loc["source"]) == (["703"], [NEW_HUB], "text")
    assert ret.locate("מה ההוראות במבואות יפו", None, None)["areas"] == ["703"]  # by area name
    none = ret.locate("מהם השטחים המותרים", None, None)  # 'שטחים' contains the 2-letter zone title 'ים'
    assert (none["zone_types"], none["source"]) == ([], "none")


def test_explicit_filters_win_over_the_text(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    loc = ret.search("מה יש באזור 703?", areas=["502"]).location
    assert (loc["areas"], loc["source"]) == (["502"], "filters")


def test_filters_take_the_records_of_each_requested_zone_type_per_area(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    hits = via(ret.search("מה ההוראות?", areas=["703"], zone_types=[NEW_HUB, MIXED]))
    assert hits["5.4[1]"] == hits["5.4[2]"] == "location"  # new employment hub and mixed zone of area 703
    assert "5.4[3]" not in hits  # another planning area
    assert hits["3.4"] == "zone"  # the zone section of the mixed zone
    assert hits["3.3"] == "general"  # the general instructions of the employment category
    assert hits["5.4"] == "notes"  # the notes of the table come with its records


def test_lists_of_areas_and_zone_types_are_all_served(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    hits = via(ret.search("מה ההוראות?", areas=["703", "502"], zone_types=[HOTEL]))
    assert hits["5.4[3]"] == "location"  # hotel-oriented record of 502
    assert {"5.4[1]", "5.4[2]"} <= set(hits)  # 703 has none, so its most similar records stand in
    assert hits["3.7.2"] == "zone"


def test_a_row_of_a_split_table_brings_its_section(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.1.2[2]": 1})
    hit = {h["chunk_id"]: h for h in ret.search("שאלה").hits}["3.1.2"]
    assert (hit["via"], hit["from_chunk"]) == ("section", "3.1.2[2]")


def test_references_are_followed_when_mandatory_or_relevant(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1, "5.3.2": 0.9})
    hits = {h["chunk_id"]: h for h in ret.search("שאלה").hits}
    assert hits["3.1.2"]["via"] == "ref" and hits["3.1.2"]["mandatory"]  # followed although unrelated to the question
    assert hits["5.3.2"]["via"] == "ref" and not hits["5.3.2"]["mandatory"]  # similar enough
    assert "4.4.2" not in hits  # optional and unrelated


def test_context_is_hebrew_with_citations_and_plain_hyphens(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    result = ret.search("מה ההוראות?", areas=["703"], zone_types=[NEW_HUB])
    assert (result.band, result.band_label) == ("high", "התאמה גבוהה")  # an explicit location that matched
    context = ret.context(result)
    assert "[1] סעיף 3.3.5" in context and "[3] טבלה 5.4 - אזור תכנון 703 מבואות יפו" in context  # structural matches come first
    assert "סעיף 3.3.5" in context and "עמוד 30" in context
    assert "–" not in context and "5.4[1]" not in context  # no long dashes, no internal chunk ids


def test_a_question_nothing_matches_is_graded_low(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {None: 1})
    result = ret.search("שאלה שאין לה מענה")
    assert (result.band, result.band_label) == ("low", "התאמה נמוכה")
    assert config.LABELS["context"]["low_note"] in ret.context(result)


def test_a_chunk_that_merged_neighbouring_sections_cites_both(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    assert ret.citation("3.10") == "סעיפים 3.10, 3.11"
    assert ret.citation("3.4") == "סעיף 3.4"  # a section and its own sub-section cite the section only
    assert ret.citation("3.1.2(ב)") == "סעיף 3.1.2(ב)-(ג)"  # small sibling clauses share a chunk and are both cited


def test_a_short_title_is_a_zone_type_by_id_and_sub_sections_of_3_8_are_zone_types(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    assert ret.resolve_zone("3.6.2") == ["3.6.2"]  # 'ים' is too short to be found in free text, not to be named by id
    assert ret.resolve_zone("3.8") == ["3.8.2"]  # the category stands for its types; 3.8.1 כללי is not one
    hits = via(ret.search("מה ההוראות?", zone_types=["3.8.2"]))
    assert (hits["3.8.2"], hits["3.8"]) == ("zone", "general")


@pytest.mark.parametrize("zone", ["3.10", "3.11"])
def test_categories_without_sub_types_are_zone_types_and_share_their_chunk(tmp_path, monkeypatch, zone):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    assert via(ret.search("מה ההוראות?", zone_types=[zone]))["3.10"] == "zone"


def test_a_category_filter_stands_for_its_types(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    result = ret.search("מה ההוראות?", areas=["703"], zone_types=["3.3"])  # 3.3 = employment zones
    assert result.location["zone_types"] == [NEW_HUB]  # 3.3.1 כללי is not a type
    hits = via(result)
    assert hits["5.4[1]"] == "location" and "5.4[2]" not in hits
    assert ret.resolve_zone("אזורי תעסוקה") == [NEW_HUB]  # the category by name


def test_general_words_in_a_question_do_not_select_a_zone_type(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    assert ret.locate("מהו הכללי בתכנית", None, None)["zone_types"] == []


def test_a_sub_area_filter_narrows_to_its_record_in_any_common_form(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    for value in ("703ה", "703 ה'", "703-ה", "‏703ה׳", "ה703"):
        loc = ret.locate("", [value], None)
        assert (loc["areas"], loc["subareas"]) == (["703"], ["703ה"])
    hits = via(ret.search(areas=["703ה"], zone_types=[NEW_HUB]))  # the named sub-area record, plus the area's rows of the zone type
    assert hits["5.4[2]"] == hits["5.4[1]"] == "location"
    assert "5.4[1]" not in via(ret.search(areas=["703ה"]))  # without a zone type only the sub-area's own record
    assert "5.4[1]" in via(ret.search(areas=["703ה", "703"]))  # a plain code beside it asks for the whole area
    both = via(ret.search(areas=["703ה", "703ו"]))  # several sub-areas of one area each return their own record
    assert both["5.4[2]"] == both["5.4[8]"] == "location" and "5.4[1]" not in both
    assert ret.subareas == [{"code": "502ב", "area": "502", "name": "מרקם בנוי לשימור"},
                            {"code": "703ה", "area": "703", "name": "אזור מעורב"},
                            {"code": "703ו", "area": "703", "name": "מרקם חדש"}]


def test_entity_filters_name_table_rows_by_code_or_label(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    rows = lambda value: {ret.chunks.at[c, "area"] for c in ret.resolve_entity(value)}
    assert rows("ר־83") == rows("ר83") == {"703", "502"}  # the uncoded 'רח' בן צבי' of 502 takes the code of the same street
    assert ret.resolve_entity("רחוב בן צבי") == ret.resolve_entity("ר-83")  # by name
    narrowed = via(ret.search(areas=["703"], entities=["ר-83"]))
    assert narrowed["5.4[4]"] == "location" and "5.4[1]" not in narrowed  # the area is narrowed to the street's own row
    alone = ret.search(entities=["ר-83", "ר-99"])
    assert {h["chunk_id"] for h in alone.hits if h["via"] == "location"} == {"5.4[4]", "5.4[5]"}  # no area needed
    assert (alone.location["entities_not_found"], alone.band) == (["ר-99"], "high")
    assert config.LABELS["context"]["entities_missing"] in ret.context(alone)


def test_a_row_label_matches_as_a_phrase_and_is_found_in_free_text(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    assert ret.resolve_entity("מרקם בנוי לשימור") == ["5.4[6]", "5.4[7]"]  # the exact label and the one inside a longer label
    asked = ret.search("היכן יש בעיר מרקם בנוי לשימור")
    assert {h["chunk_id"] for h in asked.hits if h["via"] == "location"} == {"5.4[6]", "5.4[7]"}  # no area, yet every row
    assert (asked.location["entities"], asked.band) == (["מרקם בנוי לשימור"], "high")
    assert ret.locate("שורות במרקם בנוי לשימור", None, None)["entities"] == ["מרקם בנוי לשימור"]  # a Hebrew prefix is allowed


def test_a_heading_named_by_number_leads_and_a_large_one_is_cut(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.1.2": 1}, zone_max_tokens=0, zone_k=0)  # a chapter is too big to return whole
    asked = ret.search("מה כתוב בפרק 4?")
    assert asked.location["headings"] == ["4"]
    assert asked.hits[0]["chunk_id"] == "4" and via(asked)["4"] == "heading"  # the chapter's own chunk leads
    assert "4.2" not in via(asked)  # its sections are left out; the outline lists them
    numbered = ret.locate("מה כתוב בסעיף 3.3.5", None, None)  # the number of a zone type names the zone type
    assert (numbered["zone_types"], numbered["headings"]) == ([NEW_HUB], [])


def test_the_headings_filter_returns_a_section_and_reports_unknown_values(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    result = ret.search(headings=["4.2", "סעיף 4.2", "דיור בהישג יד", "9.9", "3.1.2(ג)"])
    assert [h["chunk_id"] for h in result.hits if h["via"] == "heading"] == ["4.2", "3.1.2(ב)"]  # a clause names the chunk that holds it
    assert (result.location["headings_not_found"], result.band) == (["9.9"], "high")


def test_the_outline_is_a_tree_of_headings_with_sizes(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    four = next(c for c in ret.outline() if c["id"] == "4")
    assert "4.2" in [c["id"] for c in four["children"]] and four["citation"] == "פרק 4" and four["chunks"] > 1
    assert ret.outline("4", depth=1)[0]["children"] == []
    section = ret.outline("דיור בהישג יד")[0]  # a node by its full title
    assert (section["id"], section["citation"], section["tokens"] > 0) == ("4.2", "סעיף 4.2", True)
    with pytest.raises(ValueError):
        ret.outline("9.9")


def test_a_row_name_in_another_form_is_linked_by_similarity(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {None: 1})  # the question's vector is the axis no chunk has
    axis = np.zeros(len(CHUNKS) + 1, dtype=np.float32)
    axis[-1] = 1
    name = "מרקם בנוי לשימור"
    monkeypatch.setattr(retrieval, "embed", lambda texts, model: np.vstack([axis if t.endswith(name) else 0 * axis for t in texts]))
    plural = ret.search("היכן יש בעיר מרקמים בנויים לשימור")  # no exact match: the string differs
    assert plural.location["linked"] == [name]
    assert {h["chunk_id"] for h in plural.hits if h["via"] == "location"} == {"5.4[6]", "5.4[7]"}
    assert ret.search("היכן יש בעיר מרקם בנוי לשימור").location["linked"] == []  # an exact match needs no linking


def test_areas_without_a_table_record_are_reported(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    result = ret.search("מה ההוראות?", areas=["205"], zone_types=[NEW_HUB])
    assert result.location["areas_without_records"] == ["205"]
    assert not any(h["via"] == "location" for h in result.hits)
    context = ret.context(result)
    assert "205" in context and config.LABELS["context"]["no_record"] in context


def test_a_capped_zone_scope_is_reported_unless_there_is_no_question_to_rank_by(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1}, zone_max_tokens=0, zone_k=0)
    asked = ret.search("מה ההוראות?", zone_types=[NEW_HUB])
    assert [o["chunk_id"] for o in asked.omitted] == ["3.3"]  # 3.3.5 itself was found by the search
    assert "(3.3)" in ret.context(asked)
    plain = ret.search(areas=["703"], zone_types=[NEW_HUB])
    assert plain.omitted == [] and via(plain)["3.3"] == "general"


def test_general_instructions_and_table_notes_follow_the_hits_they_qualify(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.4": 1, "3.3": 0.1})  # the general chunk scores far below the others
    order = [h["chunk_id"] for h in ret.search("מה ההוראות?", areas=["703"], zone_types=[NEW_HUB]).hits]
    assert order.index("3.3") == order.index("3.3.5") + 1
    assert order.index("5.4") == order.index("5.4[1]") + 1


def test_in_free_text_the_best_match_leads_and_the_named_zone_follows_it(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.1.2": 1, "3.3.5": 0.5})
    order = [h["chunk_id"] for h in ret.search("מה יש במוקד תעסוקה חדש?").hits]
    assert order[:3] == ["3.1.2", "3.3.5", "3.3"]  # best search hit, then the zone section and its general instructions


def note_axes(monkeypatch, axes):
    """Makes each note text a unit vector along one chunk's axis, so its similarity to that chunk is 1 and to the others 0."""
    ids = [c["chunk_id"] for c in CHUNKS]

    def fake(texts, model):
        out = np.zeros((len(texts), len(ids) + 1), dtype=np.float32)
        for k, text in enumerate(texts):
            if text in axes:
                out[k, ids.index(axes[text])] = 1
        return out

    monkeypatch.setattr(retrieval, "embed", fake)


def test_focus_notes_add_chunks_after_all_other_hits_and_never_displace_them(tmp_path, monkeypatch):
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    note_axes(monkeypatch, {"דיור": "4.2", "שימור": "4.4.2"})
    plain = [h["chunk_id"] for h in ret.search("שאלה").hits]
    notes = [{"text": "דיור", "layer": "שכבה א", "priority": 2, "weight": 1.0},
             {"text": "שימור", "layer": "שכבה ב", "priority": 2, "weight": 1.0},
             {"text": "ללא התאמה", "layer": "שכבה ג", "priority": 1, "weight": 1.0}]  # similar to nothing: below the gate
    result = ret.search("שאלה", focus_notes=notes)
    ids = [h["chunk_id"] for h in result.hits]
    assert ids[:len(plain)] == plain  # the other hits come first, unchanged
    added = {h["chunk_id"]: h for h in result.hits if h["via"] == "focus_note"}
    assert set(added) == {"4.2", "4.4.2"} and ids[len(plain):] == list(added)
    assert "דיור" in added["4.2"]["reason"]  # the reason carries the note text, which names the layer


def test_focus_notes_respect_priority_the_budget_and_the_per_layer_cap(tmp_path, monkeypatch):
    note = lambda text, layer, priority: {"text": text, "layer": layer, "priority": priority, "weight": 1.0}
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1}, focus_max_tokens=50)  # room for one chunk of 50 tokens
    note_axes(monkeypatch, {"דיור": "4.2", "שימור": "4.4.2"})
    result = ret.search("שאלה", focus_notes=[note("דיור", "א", 2), note("שימור", "ב", 1)])
    assert [h["chunk_id"] for h in result.hits if h["via"] == "focus_note"] == ["4.4.2"]  # the more important layer takes the budget
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1}, focus_per_layer=1)
    note_axes(monkeypatch, {"דיור": "4.2", "שימור": "4.4.2"})
    result = ret.search("שאלה", focus_notes=[note("דיור", "א", 2), note("שימור", "א", 2)])
    assert len([h for h in result.hits if h["via"] == "focus_note"]) == 1  # one layer adds at most `focus_per_layer`


def test_a_layer_code_resolves_to_the_longest_plan_section_prefix_with_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FOCUS_CODES", [{"layer": "axes", "code": "3619", "heading": "3.4.2"},
                                                {"layer": "x", "code": "זים", "heading": "9.9"}])
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1})
    assert ret.resolve_note_section("l", "382") == ret.resolve_note_section("l", "3821") == "3.8.2"  # item 1 of 3.8.2
    assert ret.resolve_note_section("l", "310") == "3.10" and ret.resolve_note_section("l", "34") == "3.4"
    assert ret.resolve_note_section("axes", "3619") == "3.4.2"  # an override beats the digits
    assert ret.resolve_note_section("x", "זים") is None  # an override to a heading the plan lacks
    assert ret.resolve_note_section("l", "999") is None and ret.resolve_note_section("l", "abc") is None


def test_notes_with_a_plan_code_bring_the_section_and_the_rest_is_listed_to_ask_for(tmp_path, monkeypatch):
    note = lambda text, code, priority: {"text": text, "layer": "שכבה", "code": code, "priority": priority, "weight": 1.0}
    ret = make(tmp_path, monkeypatch, {"3.3.5": 1}, focus_min_tokens=50, focus_max_tokens=50)  # room for one chunk
    result = ret.search("שאלה", focus_notes=[note("דיור", "42", 2), note("דרכים", "382", 1), note("דרכים נוספות", "3821", 1)])
    assert [h["chunk_id"] for h in result.hits if h["via"] == "focus_note"] == ["3.8.2"]  # priority 1 first; both notes on 3.8.2 together
    assert [(m["heading"], m["chunk_ids"]) for m in result.focus_more] == [("4.2", ["4.2"])]  # what did not fit
    assert "[כותרת 4.2]" in ret.context(result)
