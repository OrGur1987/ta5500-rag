import pytest
from fastapi.testclient import TestClient

import service.app
from rag.embed import EmbeddingError
from service.app import create_app
from synthetic_index import make


@pytest.fixture
def ret(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_API_KEY", raising=False)
    return make(tmp_path, monkeypatch, {"3.3.5": 1})


@pytest.fixture
def client(ret):
    with TestClient(create_app(ret)) as c:
        yield c


def test_a_plan_border_without_a_question_returns_cited_hits_and_context(client):
    response = client.post("/v1/search", json={"areas": ["703"], "zone_types": ["3.3.5"]})
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    body = response.json()
    assert (body["band"], body["band_label"], body["location"]["source"]) == ("high", "התאמה גבוהה", "filters")
    record = next(h for h in body["hits"] if h["via"] == "location")
    assert record["citation"].startswith("טבלה 5.4 - אזור תכנון 703") and record["page_start"] == 30
    assert not any(h["via"] == "search" for h in body["hits"])  # no question, so no similarity search
    first = body["hits"][0]
    assert first["text"] and first["chunk_id"] and body["omitted"] == []
    fetched = client.get("/v1/chunk", params={"id": first["chunk_id"]}).json()
    assert fetched["text"] == first["text"] and fetched["citation"] == first["citation"]
    assert client.get("/v1/chunk", params={"id": "nope"}).status_code == 404
    assert "התאמה גבוהה" in body["context"]


def test_location_is_detected_in_the_question_unless_filters_are_given(client):
    text = client.post("/v1/search", json={"question": "מה יש באזור 703?"}).json()
    assert (text["location"]["source"], text["location"]["areas"]) == ("text", ["703"])
    empty = client.post("/v1/search", json={"question": "שאלה", "areas": [], "zone_types": []}).json()
    assert empty["location"]["source"] == "none"  # empty lists count as not given


def test_followed_references_name_the_citation_they_came_from(client):
    hits = client.post("/v1/search", json={"question": "שאלה", "context": False}).json()
    ref = next(h for h in hits["hits"] if h["via"] == "ref" and h["mandatory"])
    assert ref["followed_from"] == "סעיף 3.3.5"
    assert client.post("/v1/search", json={"question": "שאלה", "context": False}).json()["context"] is None


def test_a_request_with_neither_question_nor_filters_is_rejected(client):
    assert client.post("/v1/search", json={}).status_code == 422


def test_api_key_is_required_when_configured(ret, monkeypatch):
    monkeypatch.setenv("RAG_API_KEY", "secret")
    with TestClient(create_app(ret)) as c:
        assert c.post("/v1/search", json={"question": "שאלה"}).status_code == 401
        assert c.post("/v1/search", json={"question": "שאלה"}, headers={"X-API-Key": "secret"}).status_code == 200
        assert c.get("/health").status_code == 200  # the readiness probe stays open


def test_embedding_outage_is_a_503_and_health_reports_it(ret, monkeypatch):
    def down(*args, **kwargs):
        raise EmbeddingError("down")

    monkeypatch.setattr(service.app, "_warm", lambda retriever: False)
    with TestClient(create_app(ret)) as c:
        assert c.get("/health").status_code == 503
        monkeypatch.setattr(ret, "search", down)
        assert c.post("/v1/search", json={"question": "שאלה"}).status_code == 503


def test_locations_list_what_the_filters_accept(client):
    body = client.get("/v1/locations").json()
    assert {"code": "703", "name": "מבואות יפו"} in body["areas"]
    assert [s["code"] for s in body["subareas"]] == ["502ב", "703ה", "703ו"]
    assert {"id": "3.3.5", "title": "מוקד תעסוקה חדש", "category": "3.3"} in body["zone_types"]
    assert {"id": "3.4.2", "title": "ציר מעורב", "category": "3.4"} in body["zone_types"]
    assert {"id": "3.3", "title": "אזורי תעסוקה"} in body["zone_categories"]
    assert {"layer": "foci", "code": "335", "key": "zone_types", "value": "3.3.5", "title": "מוקד תעסוקה חדש", "note": ""} in body["gis_codes"]
    assert next(g for g in body["gis_codes"] if g["code"] == "3617")["note"] == "name"  # the layer's code differs from the plan number
    assert client.get("/health").json()["chunks"] == 23
    street = next(e for e in body["entities"] if e["code"] == "ר-83")
    assert (street["type"], street["label"], street["areas"]) == ("street", "ר-83 רחוב בן צבי", ["703", "502"])
    found = client.post("/v1/search", json={"entities": ["ר־83", "ר-99"]}).json()["location"]
    assert (found["entities"], found["entities_not_found"]) == (["ר־83", "ר-99"], ["ר-99"])
    resolved = client.get("/v1/locations", params={"q": "בן צבי"}).json()  # the resolver narrows every list to names containing the phrase
    assert [(e["code"], e["rows"]) for e in resolved["entities"]] == [("ר-83", 2)]
    assert resolved["areas"] == [] and resolved["zone_types"] == []


def test_a_heading_filter_returns_its_chunk_and_reports_unknown_values(client):
    body = client.post("/v1/search", json={"headings": ["סעיף 4.2", "9.9"]}).json()
    assert [h["via"] for h in body["hits"] if h["via"] == "heading"] == ["heading"]
    assert (body["location"]["headings"], body["location"]["headings_not_found"]) == (["4.2"], ["9.9"])


def test_contents_returns_the_heading_tree_or_a_subtree(client):
    chapters = client.get("/v1/contents").json()
    assert "4" in [c["id"] for c in chapters] and all(c["children"] is not None for c in chapters)
    subtree = client.get("/v1/contents", params={"node": "4", "depth": 1}).json()
    assert (subtree[0]["id"], subtree[0]["children"]) == ("4", [])
    assert client.get("/v1/contents", params={"node": "9.9"}).status_code == 404


def test_the_demo_page_is_served_in_right_to_left_hebrew(client):
    page = client.get("/demo")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'dir="rtl"' in page.text and "/v1/search" in page.text and "/v1/locations" in page.text
    assert "/demo" not in client.get("/openapi.json").text  # a tool, not part of the API contract


def test_an_area_without_a_table_record_is_reported_not_ignored(client):
    body = client.post("/v1/search", json={"areas": ["205"], "zone_types": ["3.3.5"]}).json()
    assert body["location"]["areas_without_records"] == ["205"]
