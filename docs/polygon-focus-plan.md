# Polygon focus: development plan

> **STAGE: dev.** Breaking changes are allowed, but prefer non-breaking ones: new request fields are optional, and existing behaviour of `/v1/search`, `/v1/locations` and the MCP tools stays unchanged when they are absent.

Status: draft for discussion. Covers the RAG repo (`ta5500-rag`) and the UI repo (`ArcGIS-JavaScript-AI-Component-main`).

## Goal

The user draws a polygon, selects one in the map (from any layer) or uploads one (zipped shapefile or KML). The polygon is intersected with a few configured map layers, the record codes are extracted, and they are used as RAG filters and to focus the chat on that polygon. A toggle switches the focus on and off:

| Polygon | Focus toggle | Focus active |
|---|---|---|
| none | any | no |
| set | off | no |
| set | on | yes |

## Decisions

1. **Intersection parsing is client-side, in a plain function, independent of the assistant.** The filters must land in the right query keys deterministically; the LLM is not asked to copy them. The UI needs no new endpoint:
   - raw values come from `queryFeatures` on the configured layers;
   - a pure function `resolveFocusFilters(rawValues, locations)` maps them to the `/v1/search` keys, using `GET /v1/locations` (planning-area codes, zone-type ids and names, table 5.4 entities), fetched once and cached.
2. **Target keys stay as the API already has them**, one list per key:
   ```json
   { "areas": ["703"], "zone_types": ["3.3.5", "3.4.1"], "entities": ["ר-83"] }
   ```
   `entities` is not split into street, symbol and label keys. The backend already types each row (`subarea`, `street`, `symbol`, `area`, `zone_type`, `named`) and matches codes exactly and labels as whole phrases, so a split would add API surface and no precision.
3. **Layer config is a file, keyed by `serviceUrl + layerId`, not by map title.** The same sublayer appears many times in the TA5500 map under different titles, so titles cannot identify a layer. The service is queried directly, ignoring map filters and visibility.
4. **One state source for focus:** `polygon | null` and `focusEnabled`; `isFocused = polygon && focusEnabled`. The intersect result is derived from the polygon.
5. **The focus filters are merged into the `search_plan` call by the agent code**, not by the model. A key the model passed itself (from the user's wording) is kept; missing keys are filled from the focus.

## Configured layers and fields (first version)

All five layers are in ITM (wkid 2039) and live in three hosted services, identified by `serviceUrl + layerId`. The same field names carry different meanings per layer, so mappings are per layer.

| Layer (service / layerId) | Field | Target key |
|---|---|---|
| אזורי ייעוד תכנוניים (`TA5500_פולי9גונים1` / 128) | `EzorYeudCode` (קוד אזור ייעוד); `EzorYeud` (סוג אזור ייעוד) for display | `zone_types`, resolved by code |
| אזור תכנון ומספרו (`TA5500_פולי9גונים1` / 122) | `NumEzorTihnun` (מספר אזור תכנון) | `areas` |
| מוקדי פיתוח ושימוש עירוניים (`TA5500_נק` / 124) | `TypeCode` (קוד סימון); `TypeName` (שם סימון) for display | `zone_types` or `entities`, resolved by code |
| מרחבי תכנון (`TA5500_פולי9גונים2` / 145) | `mis_ezor` (מספר אזור, a sub-area code such as `702ב`); `shem_ezor` (שם אזור) for display | `areas` (sub-area codes are accepted there) |
| הוראות רחוב מיוחדות (`TA5500_קווים` / 146) | `mis_ezor` (קוד רחוב, `ר-31`); `shem_ezor` (שם רחוב) for display | `entities` |

## B0 findings (GIS values versus `GET /v1/locations`)

- **Planning areas:** 45 of the 46 GIS values of `NumEzorTihnun` match an area code. Only `205` is GIS-only; the service reports requested areas without a table 5.4 record in `areas_without_records`, so sending it is harmless.
- **Planning spaces:** `mis_ezor` matches RAG sub-area codes (117 of 118), so it goes to `areas`, not `entities`. `TypeName` is constant ("מרחב להתחדשות עירונית") and is not used as a filter. `shem_ezor` is display only. One value contains `\r\n`; trim values.
- **Streets:** `mis_ezor` matches street entity codes exactly (15 of 20). The other 5 (`ר-61`, `ר-71`, `ר-73`, `ר-81`, `ר-85`) are not in table 5.4; they come back in `entities_not_found`. Match by code, never by name: RAG labels include the code and a "רחוב" prefix.
- **Zone types do not match by name:** only 14 of 24 distinct `EzorYeud` names equal a RAG title exactly (the rest differ in wording, for example "פארק החוף" versus "אזור פארק החוף"). `EzorYeudCode` (310, 322, 323, 324, …) is the plan section number without dots (322 = 3.2.2), but not all codes map to a section id (for example 3620, 391, 465), so it needs an explicit mapping table.
- **Development foci:** only 3 of 8 `TypeName` values match an entity label; the others ("כיכר עירונית", "מרכז תפעול עירוני", …) are RAG zone types. Resolve by `TypeCode` with the same kind of table.

**Consequence:** zone types and development foci need a code-to-target table. Decision: keep it in the RAG repo (`models.yml`, next to the plan knowledge) and expose it as a new top-level `gis_codes` list in `GET /v1/locations` (additive, non-breaking), so the client resolves everything from one cached response. Each entry gives the filter key and value to use, because some GIS codes point at plan sections that are not zone types and must go to `headings` (a `zone_types` filter on such a section returns nothing). A RAG test asserts that every mapped id exists in the plan.

## GIS code mapping (draft)

Entry shape: `layer`, `code`, `key` (`zone_types` or `headings`), `value`. Marks:
- **ok**: code, name and plan agree.
- **name**: the code does not match the plan section number, matched by name.
- **section**: the plan section is not a zone type; mapped through `headings`.
- **merged**: several GIS codes for one plan type.
- **wording**: only the wording differs slightly.

Layer אזורי ייעוד תכנוניים (`EzorYeudCode`):

| Code | GIS name | Key | Value | Mark |
|---|---|---|---|---|
| 310 | אזור פעילות מיוחד | `zone_types` | 3.10 | ok |
| 311 | אזור לתכנון בעתיד | `zone_types` | 3.11 | ok |
| 322, 323, 324, 325 | residential types | `zone_types` | 3.2.2, 3.2.3, 3.2.4, 3.2.5 | ok |
| 332, 333, 334 | employment types | `zone_types` | 3.3.2, 3.3.3, 3.3.4 | ok |
| 341 | אזור מעורב | `zone_types` | 3.4.1 | ok |
| 352 | אזור מוסדות ציבור עירוניים | `zone_types` | 3.5.2 | wording |
| 3610 | שטח פתוח עירוני | `zone_types` | 3.6.10 | ok |
| 3617 | בית עלמין | `zone_types` | 3.6.18 | name (code is one below the plan number) |
| 362 | ים | `zone_types` | 3.6.2 | ok |
| 3620 | בתחום הים / שובר גלים | `zone_types` | 3.6.2 | name (no plan number 3.6.20; the plan mentions שוברי גלים in 3.6.2) |
| 363 | חוף הים | `zone_types` | 3.6.3 | wording |
| 364 | פארק החוף | `zone_types` | 3.6.4 | wording |
| 366 | מעגנות | `zone_types` | 3.6.6 | wording |
| 368 | שמורת טבע ימית | `zone_types` | 3.6.8 | ok |
| 369 | שטח פתוח מטרופוליטני | `zone_types` | 3.6.9 | ok |
| 382 | דרך מהירה ודרך פרברית מהירה | `zone_types` | 3.8.2 | ok (דרכים) |
| 391 | שצ"פ ומתקן הנדסי | `zone_types` | 3.9 | merged (3.9 has no sub-sections) |
| 392 | שטח למתקנים הנדסיים | `zone_types` | 3.9 | merged (3.9 has no sub-sections) |
| 465 | נחל / תעלת ניקוז מוסדרת | `headings` | 4.6 | section, uncertain (a test search for drainage found 4.8.4 first; revisit) |

Layer מוקדי פיתוח ושימוש עירוניים (`TypeCode`):

| Code | GIS name | Key | Value | Mark |
|---|---|---|---|---|
| 335 | מוקד תעסוקה חדש | `zone_types` | 3.3.5 | ok |
| 353 | מוסד ציבורי עירוני חדש | `zone_types` | 3.5.3 | ok |
| 354 | מרכז תפעול עירוני | `zone_types` | 3.5.4 | ok |
| 365 | מוקד פיתוח חופי | `zone_types` | 3.6.5 | ok |
| 3612 | כיכר עירונית | `zone_types` | 3.6.12 | ok |
| 3618 | אזור לספורט | `zone_types` | 3.6.19 | name (code is one below the plan number) |
| 461, 462 | אתר טבע עירוני (סימבול) | `headings` | 4.7.1 | section (two codes for one name) |

Mismatches to fix in the layers some day: the shifted codes 3617 and 3618 (a clause was added to the plan, the codes were not updated); the code 3620, which has no plan number; codes 461, 462 and 465 that point at chapter 4 sections which are not zone types; the duplicate name for 461 and 462; two codes (391, 392) for one plan type.

Notes from the checks:
- `headings` returns the whole section when it is small. "שובר גלים" appears in three chunks (3.6.2, 3.6.6(ב) and table 5.4 record 92); a plain question search returns all three with a low match band (top score 0.40), while `zone_types: ["3.6.2"]` reaches 3.6.2 directly.
- Backend changes made for this mapping (tests and the evaluation unchanged): zone types now cover the sub-sections of 3.2-3.12, including 3.6.2 (a title shorter than 4 letters is a type by id, not found in free text) and 3.8.2-3.8.10; merged sibling clauses are cited as a range (`4.7.1(ב)-(ג)`); the `headings` filter accepts clause ids (`4.7.1(ג)`).
- Sub-areas and entities name rows; the area's rows of the requested zone types come beside them, not instead of them. With `areas: ["703ה"]` and `zone_types: ["3.3.5"]` the answer holds the 703ה record and the area-wide "מוקד תעסוקה חדש" row. This is why the UI sends a sub-area code without the plain area code.


## Secondary layers: `focus_notes`

Layers beyond the five are not hard filters, but their intersect results still drive retrieval, so plan text relevant to the polygon reaches the assistant. The five layers must never be crowded out by the rest, hence two tiers:

| Tier | Layers | Reaches the RAG as | Precedence |
|---|---|---|---|
| Primary | The five configured layers | Hard filters: `areas`, `zone_types`, `entities` | Applied first, behaviour unchanged |
| Secondary | Every other layer | `focus_notes`: short texts, each embedded and searched on its own | Fills only a reserved share of the result |

`focus_notes` is an optional list on `/v1/search` (additive, non-breaking). Each note: `text` (for example "שכבה X: ערך Y"), `layer` (label for the reason), `priority` (from the layer config, 1 = higher) and `weight` (overlap share, 0-1). `/v1/search` is stateless, so the client sends the notes with every search call (a few KB after capping); no server-side focus session.

Rules that keep the important results first:

1. **Fill order:** filter chunks, then the question's own search hits (both as today), then note hits. A note hit never displaces an earlier one.
2. **Separate budget:** note hits get at most about 25% of `budget_tokens` and at most 2 chunks per layer. Both values live in `models.yml` (`retrieval`).
3. **Similarity gate:** a note contributes only chunks scoring above a minimum (start from the reference-following gate, 0.55). A layer with nothing relevant adds nothing.
4. **Ranking among notes:** score first, then `priority`, then `weight`.
5. **Own embeddings:** each note is a separate query (one batched embedding call, cached by text). Notes are never merged into the question text, which would dilute both.
6. **Dedupe and reason:** chunks already returned are skipped; each note hit has `via: focus_note` and a Hebrew `reason` naming the layer and value.

Deferred: an optional per-layer `heading` hint (a plan section the layer relates to) that would bias its notes toward that section. Start with embedding similarity only.

## Backend (`ta5500-rag`)

- **B0. Discovery (done).** Findings are in the section above. Result: zone types and development foci need a GIS-code mapping table.
- **B1. Coverage check (done for zone types and streets).** The code table is in `models.yml` (`gis_codes`) and served as `gis_codes` in `GET /v1/locations`; an integration test asserts that every mapped value exists in the plan. Still to confirm with real values: street codes (`ר-31`) and sub-area codes (`702ב`) as filters, and long lists. `entities_not_found` already reports misses. Add a server-side cap on list sizes only if a large list degrades results.
- **B2. `focus_notes` retrieval (done):** field on `SearchRequest`, `Retriever._add_focus_notes` with its own budget, gate and per-layer cap, `via: focus_note` hits, parameters in `models.yml` (`focus_min` 0.55 set on ten hand-written notes; revisit with real selections).
- **B3. MCP wrapper (done):** `focus_notes` is in the `search_plan` schema and passed through; its description tells the model not to fill it in. The UI agent must also remove it from the schema it shows the model.
- **B4. Tests (done):** unit tests for placement after all other hits, the gate, priority, the budget and the per-layer cap; service tests for validation; 8 `focus` questions in `eval_questions.yml` (sub-areas of one area, streets, GIS codes mapped to `zone_types` and `headings`, a clause id, secondary-layer notes), all passing in filters mode.
- **B5. Docs (done):** README, endpoint table and Retrieval step 6.

## Frontend (UI repo)

- **F1. Config** `src/config/focusLayers.ts` (typed): per layer `serviceUrl`, `layerId`, label and the fields with their target key.
- **F2. Focus state** hook `usePolygonFocus`.
- **F3. Polygon sources**, one small module each:
  - draw, with the SDK's sketch component;
  - select, by clicking a feature of any layer except the basemap;
  - upload: zipped shapefile and KML parsed in the browser (new dependencies, for example `shpjs` and a KML-to-GeoJSON converter), with a file size limit and a CRS check from the `.prj`.
- **F4. Intersect service.** `queryFeatures` with the polygon per configured layer: `returnGeometry: false`, configured fields only, paginated; geometry reprojected to the service's spatial reference; slivers below a minimum overlap ignored; values ordered by overlap share and capped per key.
- **F5. `resolveFocusFilters`**, a pure function (raw values + locations) returning `{ areas, zone_types, entities, notFound }`.
- **F6. Widget.** Panel with draw, select, upload, clear, the focus toggle, and chips with the resolved areas, zone types and entities. Hebrew, gender-neutral wording. The polygon is shown as a map graphic.
- **F7. Agent integration.** `McpPassthroughAgent` merges the focus filters into the `search_plan` arguments when `isFocused` (model-provided keys win), and adds a short focus note to the prompt.
- **F8. QA.** Unit tests for the pure functions (layer matching, raw values to filters, focus derivation) and a manual end-to-end run on the TA5500 map.

## Open questions

1. Scope of "focus the chat": RAG filters only for v1, or also a geometry filter for the map data-exploration agent? (v1 proposal: RAG only.)
2. Upload with several features: union them or require exactly one? Is adding the parsing libraries acceptable?
3. ~~Which table 5.4 concept does "שם אזור" map to?~~ Answered by B0: display only; the code field goes to `areas`.
4. Conflict policy confirmation: explicit filters from the user's question win over the focus.
