"""HTTP service around the retriever: search, the list of known locations, and health.

Environment: RAG_DATA_DIR (index bundle folder), RAG_EMBED_BASE_URL (embedding server), RAG_API_KEY (optional;
when set, /v1 calls need an X-API-Key header), RAG_CORS_ORIGINS (optional, comma-separated browser origins).
"""
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from rag import config
from rag.embed import EmbeddingError
from rag.retrieval import Retriever, norm

STARTUP_TIMEOUT_S = 60  # the first request may have to load the embedding model


class Utf8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"  # clients such as Windows PowerShell 5 assume Latin-1 without it


class SearchRequest(BaseModel):
    question: str | None = Field(
        None, description="The user's question in Hebrew. May be left out when filters are given, e.g. a plan "
                          "border from the GIS with no typed question.")
    areas: list[str] | None = Field(
        None, description="Planning-area codes of table 5.4, e.g. ['703', '502'], or sub-areas such as '101ה' (also "
                          "accepted as \"101 ה'\" or '101-ה'; a plain code beside it asks for the whole area). When given "
                          "they replace the location detected in the question text. An empty list counts as not given.",
        examples=[["703"], ["101ה"]])
    zone_types: list[str] | None = Field(
        None, description="Zone types (land-use zones): section ids such as '3.3.5' or zone names such as "
                          "'מוקד תעסוקה חדש'. Several may be given. An empty list counts as not given.",
        examples=[["3.3.5", "3.4.1"]])
    entities: list[str] | None = Field(
        None, description="Rows of table 5.4 by their first column: a street or special-space code such as 'ר-12' (also "
                          "written 'ר12' or 'ר־12'), a lettered sub-area such as '101ה', or a label from `/v1/locations` "
                          "(a street name, a symbol, a zone-type row). They narrow the requested areas to their own rows and "
                          "are returned even when no area is given. An empty list counts as not given.",
        examples=[["ר-83"], ["101ה", "ר-12"]])
    headings: list[str] | None = Field(
        None, description="Chapters and sections of the plan by number ('4', '4.3', 'סעיף 4.3') or by full title. A small heading "
                          "returns all its chunks; a large one its own chunk and a few more (without a question, the first ones in "
                          "document order). `GET /v1/contents` lists the headings to name.",
        examples=[["4.3"]])
    context: bool = Field(True, description="Also return `context`, the ready-made Hebrew text block for the assistant.")


class Location(BaseModel):
    areas: list[str]
    areas_without_records: list[str] = Field(
        description="Requested planning areas that have no record in table 5.4, i.e. no special provisions "
                    "(e.g. an area that is only a cemetery); only the general rules of chapters 3-4 apply.")
    subareas: list[str]
    zone_types: list[str] = Field(description="Zone-type section ids.")
    entities: list[str] = Field(description="The entity filters that were given.")
    entities_not_found: list[str] = Field(description="Entity filters that match no row of table 5.4.")
    headings: list[str] = Field(description="Chapters and sections named in the question or the filter, by number or by title (node ids such as '4' or '4.3').")
    headings_not_found: list[str] = Field(description="Heading filters that match no chapter or section.")
    linked: list[str] = Field(description="Row names matched by similarity, when the question writes a name in another form (the plural).")
    source: str = Field(description="'filters', 'text' (detected in the question) or 'none'.")


class Chunk(BaseModel):
    chunk_id: str = Field(description="Stable within an index build; pass it to GET /v1/chunk to fetch the chunk.")
    citation: str = Field(description="How to cite it, in Hebrew, e.g. 'סעיף 3.3.2(ב)' or 'טבלה 5.4 - אזור תכנון 703 ...'.")
    page_start: int | None
    page_end: int | None
    heading_path: list[str]
    text: str


class Hit(Chunk):
    score: float = Field(description="Cosine similarity to the question (0-1); not a probability.")
    via: str = Field(description="Why it was returned: search, location, zone, general (general instructions of the zone category), notes (notes of table 5.4), section or ref.")
    reason: str = Field(description="`via` in Hebrew.")
    followed_from: str | None = Field(None, description="Citation of the hit this one was reached from (via section/ref).")
    mandatory: bool | None = Field(None, description="For ref hits: the reference is worded as binding.")


class Omitted(BaseModel):
    chunk_id: str
    citation: str
    tokens: int


class Section(BaseModel):
    id: str = Field(description="Node id, usable in the `headings` filter, e.g. '4.3'.")
    title: str
    citation: str
    chunks: int = Field(description="How many chunks it holds.")
    tokens: int


class Contents(Section):
    children: list["Contents"] = Field(description="The headings directly inside it.")


class SearchResponse(BaseModel):
    band: str = Field(description="Match level: high, medium or low.")
    band_label: str = Field(description="`band` in Hebrew.")
    top_score: float
    location: Location
    hits: list[Hit]
    omitted: list[Omitted] = Field(
        description="Chunks of a requested zone type (or its general instructions) left out because the section is "
                    "large and a question ranked the rest. Fetch them with GET /v1/chunk when relevant.")
    context: str | None = None


class Entity(BaseModel):
    code: str = Field(description="'101ה' for a sub-area, 'ר-12' for a street or special space; empty for the other types.")
    type: str = Field(description="subarea, street, symbol, area (a whole-area row), zone_type or named.")
    label: str = Field(description="The row's first column, as written in the plan.")
    areas: list[str] = Field(description="Planning areas whose table has this row.")
    rows: int = Field(description="How many rows of table 5.4 a filter by this entity returns (every row whose label contains it).")


class GisCode(BaseModel):
    layer: str = Field(description="The GIS layer the code belongs to: zones (EzorYeudCode) or foci (TypeCode).")
    code: str
    key: str = Field(description="The /v1/search filter to put `value` in: zone_types or headings.")
    value: str
    title: str = Field(description="The plan's title for `value`; empty when the index has no such section.")
    note: str = Field(description="Where the GIS layer and the plan disagree (name, wording, merged, section, uncertain); empty when they agree.")


class LocationsResponse(BaseModel):
    areas: list[dict[str, str]] = Field(description="Planning areas of table 5.4: code and name.")
    subareas: list[dict[str, str]] = Field(
        description="Lettered sub-areas of table 5.4 (e.g. '101ה'): `code` (usable as an `areas` filter), `area` and `name`.")
    entities: list[Entity] = Field(
        description="Every kind of row of table 5.4, usable as an `entities` filter by `code` or, when the code is empty, by `label`.")
    zone_types: list[dict[str, str]] = Field(
        description="Zone types (the sub-sections of chapter 3): id, title and `category`, the id of its category.")
    zone_categories: list[dict[str, str]] = Field(
        description="The chapter-3 categories (3.2-3.12). A category id as a `zone_types` filter stands for all its types.")
    gis_codes: list[GisCode] = Field(
        description="How codes of the plan's GIS layers map to filters: put `value` in the `key` filter of /v1/search.")


def _page(value) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _chunk_fields(ret: Retriever, cid: str) -> dict:
    row = ret.chunks.loc[cid]
    return {"chunk_id": cid, "citation": ret.citation(cid), "page_start": _page(row.get("page_start")),
            "page_end": _page(row.get("page_end")), "heading_path": list(row["heading_path"]), "text": row["text"]}


def _warm(retriever: Retriever) -> bool:
    normal = config.SERVER["timeout_s"]
    config.SERVER["timeout_s"] = STARTUP_TIMEOUT_S
    try:
        retriever.warm_up()
        return True
    except EmbeddingError:
        return False
    finally:
        config.SERVER["timeout_s"] = normal


def create_app(retriever: Retriever | None = None) -> FastAPI:
    api_key = os.environ.get("RAG_API_KEY")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.retriever = retriever or Retriever(config.DATA_DIR)
        app.state.ready = _warm(app.state.retriever)
        yield

    app = FastAPI(
        title="ta5500-rag", version="1.0.0", lifespan=lifespan, default_response_class=Utf8JSONResponse,
        description="Retrieval over the Tel Aviv master plan (תא/5500). Returns the plan text relevant to a Hebrew "
                    "question and/or to the planning areas and zone types of a plan border.")
    origins = [o.strip() for o in os.environ.get("RAG_CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    def check_key(x_api_key: str | None = Header(None)) -> None:
        if api_key and not secrets.compare_digest(x_api_key or "", api_key):
            raise HTTPException(401, "missing or wrong X-API-Key")

    @app.get("/demo", include_in_schema=False)
    def demo():
        return HTMLResponse((Path(__file__).parent / "demo.html").read_text(encoding="utf-8"))

    @app.get("/health", summary="Readiness: 200 once the index is loaded and the embedding server answers")
    def health():
        ret: Retriever = app.state.retriever
        if not app.state.ready:
            app.state.ready = _warm(ret)
        if not app.state.ready:
            raise HTTPException(503, "embedding server unavailable")
        return {"status": "ok", "model": ret.m["name"], "chunks": len(ret.chunks)}

    @app.post("/v1/search", response_model=SearchResponse, dependencies=[Depends(check_key)],
              summary="Find the plan text relevant to a question and/or a plan border's planning areas and zone types")
    def search(req: SearchRequest):
        ret: Retriever = app.state.retriever
        try:
            result = ret.search(req.question, req.areas or None, req.zone_types or None, req.entities or None, req.headings or None)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except EmbeddingError as e:
            raise HTTPException(503, "embedding server unavailable") from e
        hits = []
        for h in result.hits:
            source = h.get("from_chunk")
            hits.append(Hit(
                **_chunk_fields(ret, h["chunk_id"]), score=h["score"], via=h["via"], reason=h["reason"],
                followed_from=ret.citation(source) if source else None, mandatory=h.get("mandatory")))
        return SearchResponse(
            band=result.band, band_label=result.band_label, top_score=result.top_score,
            location=Location(**{k: result.location[k] for k in Location.model_fields}), hits=hits,
            omitted=result.omitted, context=ret.context(result) if req.context else None)

    @app.get("/v1/chunk", response_model=Chunk, dependencies=[Depends(check_key)],
             summary="Fetch one chunk by the id given in a search response (hits or omitted)")
    def chunk(id: str):
        ret: Retriever = app.state.retriever
        if id not in ret.chunks.index:
            raise HTTPException(404, "unknown chunk id")
        return Chunk(**_chunk_fields(ret, id))

    @app.get("/v1/contents", response_model=list[Contents], dependencies=[Depends(check_key)],
             summary="The plan's table of contents, to learn its wording before searching",
             description="The chapters, or the subtree of `node` (a number such as '4.3' or a full title), as a tree down `depth` "
                         "levels (2 by default: the chapters and their sections). Each heading has its `id`, which the `headings` filter of /v1/search accepts, "
                         "its citation, and its size in chunks and tokens. This is structure, not plan text.")
    def contents(node: str | None = None, depth: int = Query(2, ge=1)):
        try:
            return app.state.retriever.outline(node, depth)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/v1/locations", response_model=LocationsResponse, dependencies=[Depends(check_key)],
             summary="Planning-area codes and zone types the search understands, for building filters",
             description="Pass `q` to resolve a name first: every list is narrowed to the entries whose name contains the "
                         "phrase, e.g. q=\u05de\u05e8\u05e7\u05dd \u05d1\u05e0\u05d5\u05d9 \u05dc\u05e9\u05d9\u05de\u05d5\u05e8. Use the returned `code`, or the `label` when the code is empty, as a filter in /v1/search.")
    def locations(q: str | None = None):
        ret: Retriever = app.state.retriever
        areas = ret.chunks.loc[ret.chunks["area"].notna(), ["area", "area_name"]].drop_duplicates("area").sort_values("area")
        by_id = lambda kv: [int(p) for p in kv[0].split(".")]
        found = lambda *texts: not q or norm(q) in norm(" ".join(texts))  # the resolver: names that contain the phrase
        titles = {"zone_types": {**ret.zone_category_title, **ret.zone_title}, "headings": ret.heading_title}
        gis = [GisCode(**{"note": "", **g}, title=titles[g["key"]].get(g["value"], "")) for g in config.GIS_CODES]
        return LocationsResponse(
            areas=[{"code": a, "name": n or ""} for a, n in areas.itertuples(index=False) if found(a, n or "")],
            subareas=[s for s in ret.subareas if found(s["code"], s["name"])],
            entities=[Entity(**{k: e[k] for k in Entity.model_fields}) for e in sorted(
                ret.entities.values(), key=lambda e: (e["type"], e["code"] or "~", e["label"])) if found(e["code"], e["label"])],
            zone_types=[{"id": i, "title": t, "category": next((c for c, m in ret.zone_members.items() if i in m), "")}
                        for i, t in sorted(ret.zone_title.items(), key=by_id) if found(i, t)],
            zone_categories=[{"id": c, "title": t} for c, t in sorted(ret.zone_category_title.items(), key=by_id) if found(c, t)],
            gis_codes=[g for g in gis if found(g.code, g.value, g.title)])

    return app


app = create_app()
