# ta5500-rag: RAG over the Tel Aviv master plan (תא/5500)

RAG (retrieval-augmented generation): find the relevant plan text by embedding similarity and structure, then give it to an LLM (large language model) as context. This repository turns the plan (Word + PDF) into a searchable index and a retrieval module. The AI assistant is **not connected yet**; see [Status](#status) and [Required next steps](#required-next-steps).

## Status

| Area | State |
|---|---|
| Word → Markdown → document tree with cross-reference graph | Done |
| Chunking (556 chunks), page numbers from the PDF | Done (101 chunks have approximate pages) |
| Embeddings (bge-m3 F16 via Lemonade), stale-embedding check | Done |
| Retrieval: location-aware search, table grouping, reference expansion, match band, Hebrew context block | Done, evaluated on our own question set |
| Tests (56 unit, 6 integration) | Done |
| HTTP service and Docker Compose (service + llama.cpp embedder; same evaluation results as with Lemonade) | Done, runs locally |
| Demo page for trying Hebrew questions and filters by hand (`/demo`) | Done |
| Evaluation set (82 questions) and `python -m rag.evaluate` | Done; written by us, small, partly tuned on |
| Assistant integration, GIS-to-plan code mapping, answer-level evaluation | **Not started** |

Key results (`python -m rag.evaluate`, bge-m3 F16; Hit = every gold chunk is among the returned chunks):

| Question type | Plain similarity (top 5) | Retriever, location from text | Retriever, explicit filters |
|---|---|---|---|
| general (15) | 0.93 | 1.00 | 1.00 |
| planning area, table 5.4 (13) | 1.00 | 1.00 | 1.00 |
| cross-reference (23) | 1.00 (referenced clause reached: 17%) | 1.00 (reached: 96%) | 1.00 (reached: 96%) |
| plan border + generic question, one zone/area (6) | 0 | 0 | 1.00 |
| plan border + generic question, lists of zones/areas or a zone category (6) | 0 | 0 | 1.00 |
| no answer in the plan (3) | – | all graded low match | all graded low match |
| spelling variants מירבי / רחק (4) | 1.00 | 1.00 | 1.00 |

The retriever returns about 10 chunks and 2,000–3,000 tokens per question (plain top 5: about 1,000). Read these numbers with the caveats under [Evaluation](#evaluation).

## Repository layout

```
ta5500-rag/
  models.yml           settings: model registry, chunk limits, server, retrieval parameters, Hebrew labels
  eval_questions.yml   evaluation set
  pipeline/            build stages: convert, docx_numbered, table_text, doc_tree, chunker, pdf_pages, preprocess (entry point)
  rag/                 runtime code shared with the build: config, embed, retrieval, evaluate
  tests/               unit and integration tests
  local_resources/     source documents and built artifacts (git-ignored)
  bundle/              the index served by the containers: chunks, nodes and embeddings parquet files
  service/             HTTP service (FastAPI), the `/demo` test page and the Dockerfile
  scripts/             dev-up.ps1: starts Docker Desktop if needed and brings the stack up (Windows dev machines)
  docker-compose.yml   the service plus a llama.cpp embedding server
```

`pipeline` may import `rag`, never the reverse, so the service image needs only `rag/` and the index bundle. Run everything from the repository root as modules (`python -m pipeline.preprocess`, `python -m rag.evaluate`), not as scripts. The Azure DevOps repository is `ta5500-rag` (renamed from `preprocessing`).

## Environment setup

Dependencies are in `requirements.txt`, installed into a local virtual environment (`.venv`, git-ignored):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`pypandoc_binary` bundles Pandoc. The embedding step needs a running Lemonade Server (see [Embedding model](#embedding-model)). On Windows PowerShell 5, write Hebrew files with an editor or Python: `Set-Content` / `Add-Content` corrupt Hebrew or add a BOM, and console output needs `$env:PYTHONIOENCODING='utf-8'`.

## Pipeline

`python -m pipeline.preprocess plan.docx plan.pdf [--model KEY] [--start-page 4] [--from STAGE]` runs the stages in order and writes the artifacts to `local_resources/`. `--from` resumes at a stage whose earlier artifacts already exist. The other modules are libraries that `pipeline/preprocess.py` calls.

| Stage | Module | Output |
|---|---|---|
| `markdown` | `pipeline/convert.py` (python-docx engine, `docx_numbered.py`, `table_text.py`) | `ta5500_pydocx.md` |
| `tree` | `pipeline/doc_tree.py` | `nodes.parquet` (tree, references) |
| `chunks` | `pipeline/chunker.py` | `chunks.parquet` |
| `pages` | `pipeline/pdf_pages.py` | adds `page_start` / `page_end` to `chunks.parquet` |
| `embeddings` | `rag/embed.py` | `embeddings_<model>.parquet` |

Settings (model registry, chunk limits, server URL, retrieval parameters, Hebrew labels) are in `models.yml`, loaded by `rag/config.py`. Parquet files carry provenance in their metadata (`config.read_meta`): the chunks file records its tokenizer and limits, the embeddings file its model, dimension and prefixes.

When the plan changes: edit the Word file, accept all tracked changes in a copy, export that copy to PDF, and rerun the pipeline. Embeddings are reused for chunks whose text did not change. Keep a tracked-changes copy of the docx for documentation.

## 1. Word → Markdown

Pandoc drops Word auto-numbering, so the `pydocx` engine rebuilds clause numbers ("4.2.3", "(א)") from the numbering levels with python-docx. Details that matter:
- Tracked changes are read as accepted (`table_text.paragraph_text`, which also skips text boxes).
- Empty numbered paragraphs (`2.5.2`, `4.1(ד)`) are kept as parent nodes when clauses follow them; stray empty headings are dropped.
- Source errors found through the reference check were fixed in the Word file by the plan owner.
- Accepted oddities: `4.10` has an unnumbered Heading 4 (`4.10/p1`); `((א))` double-parenthesis labels are normalized.

## 2. Document tree and cross-references

The plan is a containment tree (root → chapter → section → subsection → clause, plus the table `5.4` records) and a separate set of reference edges that may cross chapters. Ids compose the parent numbers (`3.1.1(ה)(1)`), since Hebrew labels restart under each parent. The unnumbered intro chapter is `root/p1`; records are `5.4[n]`.

Current build: 1,240 nodes (7 chapters, 40 sections, 91 subsections, 842 clauses, 259 records; 40 numbered clauses that the source styles as headings but that have no text of their own, such as `1.1.1`, are clauses); 256 internal references (148 mandatory), 8 external, 0 dangling.
- Hubs (most referenced): `3.6.1` (14), `3.1` (9), `5.4` (7), then `3.1.7`, `3.4.3`, `3.5.1`, `3.3.1` (5 each).
- Benign cycles: `5.3.2 ↔ 5.4`, `3.1.6 ↔ 3.5.1`, `3.6.1 ↔ 3.6.2…3.6.12` (range reference). Expansion keeps a visited set.

Reference handling:
- Forms: `סעיף`/`סעיפים`/`פרק`/`פרקים` + dotted number with optional sub-labels; lists (`3.8.7 (א) ו-(ב)`) and ranges (`3.6.2 - 3.6.12`) expand to every existing node in between.
- Relative references ("בסעיף קטן (א) לעיל", `ג(1)-ג(3)`) resolve outwards from the referring clause through its enclosing scopes; "לעיל" keeps earlier clauses, "להלן" later ones.
- External references (תמ"א, תמ"מ, תא/NNNN) are stored with status `external` and no target. Dangling references are reported at build time, never dropped silently.
- **Mandatory flag:** a regex on the wording around the reference ("בכפוף", "על אף האמור", "יגבר", "בהתאם לקבוע/לאמור", "יחולו", "למרות האמור"). It is a first pass, widened once after the evaluation showed missed references; review the hits when the plan changes.

## 3. Chunks

- Subtrees up to 500 tokens (bge-m3 tokenizer, limits in `models.yml`) become one chunk; larger ones are split into their children; small siblings under 40 tokens are merged. Records of table 5.4 and glossary definitions (1.5) are atomic; oversize leaves are split at lines and sentences, never mid-sentence.
- `embed_text` is the heading path (breadcrumb) plus the text; `text` is kept separately for display.
- Result: 556 chunks (record 259, clause 117, subsection 69, definition 64, section 31, intro 10, chapter 6); median 125 tokens, 5 over 500 (max 531), 11 under 40.

Chunk record fields: `chunk_id`, `node_ids`, `section_id`, `kind`, `binding` (false for the non-binding intro), `heading_path`, `text`, `embed_text`, `tokens`, `refs_out` (target, status, mandatory), `page_start`, `page_end`, and for table 5.4 records `district`, `area` (code), `area_name`, `zone` (the row's first column, "המרחב עליו חלות ההוראות": the last part of the record title, or the first field of the one-line form the converter gives short rows).

## 4. Page numbers

The `pages` stage matches each chunk's Hebrew letters against the text of the exported PDF, in document order, and adds `page_start` / `page_end` (printed page numbers; `--start-page 4` skips the table of contents).
- 101 of 556 chunks (mostly scrambled table text) could not be matched and inherit the previous chunk's page, so their pages are approximate; the ids are printed.
- The PDF must be exported after accepting tracked changes, or the text will not match. The PDF's table of contents is stale for chapter 4 numbering; it is skipped.

## Embedding model

- **bge-m3** (multilingual, handles Hebrew) through Lemonade Server at `http://localhost:13305/api/v1`. Registry key `bge-m3-gguf-F16` (`CompendiumLabs/bge-m3-gguf` F16, 1.08 GB, dense 1024-dimension vectors, normalized, cosine similarity). GGUF builds give dense embeddings only.
- Planned comparison, not done yet: `ggml-org/bge-m3-Q8_0-GGUF` (605 MB). Avoid rerankers (a different model type), `USER-bge-m3` (Russian fine-tune) and Q4 variants.
- **Lemonade setup:** register the model with the `embeddings` label only (a model deploys in exactly one mode), and save `llamacpp_args: "-b 2048 -ub 2048"` for it; the default physical batch of 512 rejects chunks of about 530 tokens.
- Embeddings are keyed by `chunk_id` and a hash of the embedded text; only changed chunks are re-embedded. `embed.load_index` refuses embeddings that do not match the chunks, and the stage refuses chunks built with a different tokenizer.

**Runtime requirement:** the chunk vectors are built offline, but every user question must be embedded at query time with the same model (bge-m3), so the deployment needs that model available to the retrieval code. It does not have to be Lemonade or a separate server:
- Any server with an OpenAI-style `/embeddings` endpoint serving bge-m3 (Lemonade, `llama-server`, text-embeddings-inference, Ollama): only `server.base_url` in `models.yml` changes.
- In-process in the retrieval service (e.g. `llama-cpp-python` with the same GGUF, same engine and file as now, so vectors should match the index): needs a second backend behind `embed.embed()`, not written yet. Ships a 1 GB model file; CPU is enough for one short query per request.
- A different embedding model: re-embed everything and re-evaluate Hebrew quality; the `models.yml` registry supports it.
- Whichever is chosen, embed the evaluation questions with it and compare with the current results first (vectors from different builds are close but not identical).

**Recommendation for deployment:**
1. **In-process in the retrieval service, with threads** (`llama-cpp-python`, same GGUF): fewest moving parts, no network hop, same engine as the index. Needs a Python service, about 1 GB RAM per process (so threads in one process, not many processes), and a lock around the model object. The backend is not written yet.
2. **`llama-server` in a container as a sidecar** (same engine and GGUF, same OpenAI-style endpoint, so no code change): when the service is not Python, when several service replicas share one embedding node, or when the team prefers container operations.
- Lemonade is for development; it wraps the same llama.cpp and is not needed in production.

**Load:** measured on the dev machine (Lemonade), one search (embed + retrieve) takes about 45 ms, and 8 parallel requests give about 50 searches per second with a p95 of about 170 ms. About 40 ms of a search is the embedding call; the rest is negligible, and the index (about 5 MB, read-only) is shared by all requests, so the service is stateless and scales by replication. Repeat the measurement on the deployment host. Generating the answer is the assistant's job outside this backend; this backend only decides how much context it returns (`budget_tokens`, about 2,000-3,000 tokens per question).

**Resilience (`embed.py`):** each embedding request has a short timeout (`server.timeout_s`, 3 s; a normal request takes about 40 ms) and one retry after a refused or reset connection, but not after a timeout, so a stalled server costs about 3 s (a refused connection on Windows loopback takes about 2 s per attempt, 5 s with the retry; on Linux it fails at once). Any failure raises `EmbeddingError` so the caller can answer gracefully. Call `Retriever.warm_up()` at service start: the embedding model may need several seconds to load on the first request (and again after an idle unload in Lemonade), which the short timeout would otherwise cut off. At most `server.max_concurrent` requests run at once from a process, the rest queue. Query vectors are cached by text (repeated generic plan-border questions skip the server).

## Retrieval

`retrieval.Retriever(out_dir).search(query, areas=None, zone_types=None)` returns a `Result`: `hits`, `location`, `top_score`, `band` (`high/medium/low`) and `band_label` (Hebrew: התאמה גבוהה / בינונית / נמוכה). Each hit has `chunk_id`, `citation` (Hebrew, e.g. `סעיף 3.3.2(ב)`, `טבלה 5.4 - אזור תכנון 703 מבואות יפו, …`), `score`, `via` (`search`, `location`, `zone`, `general`, `notes`, `heading`, `section`, `ref`) with a Hebrew `reason`, `tokens`, `pages`, and for references `from_chunk`, `mandatory`, `depth`. `Retriever.context(result)` renders the Hebrew text block for the assistant: match level, detected areas and zone types, then each chunk numbered with its citation, pages and reason, heading path and text. Chunk ids stay internal.

How it works:
1. **Location:** explicit filters win. `areas` are planning-area codes (`"703"`) or lettered sub-areas (`"101ה"`, also read from `"101 ה'"`, `"101-ה"` and `"ה101"`, with bidi marks and any quote character ignored); a sub-area narrows the area to its own record before zone types are matched, and a plain code beside it asks for the whole area. `entities` name other rows of table 5.4 by their first column: a street or special-space code (`"ר-83"`, also written `ר83` or `ר־83`), a sub-area, or a label (a street name, a symbol, a zone-type row). Every row is typed (`subarea`, `street`, `symbol`, `area`, `zone_type`, `named`), a street written without its code takes the code of the same street elsewhere in the table (רח' איינשטיין is ר-12), and the same code or label under several areas is one entity. Entities narrow the requested areas to their own rows and are returned even when no area is given; a value that matches no row is reported in `entities_not_found`. A label matches as a whole phrase inside any row's label (Hebrew prefixes allowed), so `"מרקם בנוי לשימור"` returns all 7 rows that contain it, while a code (`ר-83`, `101ה`) matches exactly. A selection by entity is never capped, and a match counts as `high`. Streets, symbols and other named rows (not zone-type rows, which are reached through zone types) that are written in the question text, in a name of at least 6 letters, are applied as entity filters automatically; when no exact match is found, a name written in another form (the plural `מרקמים בנויים`) is linked by similarity: the question is compared with the embeddings of the row names (computed once, 39 names), and the best name is applied if it scores at least `link_min` (0.68) and leads the next name by `link_margin` (0.03); the result is listed in `location.linked`. `GET /v1/locations?q=...` remains the explicit way to resolve a name. `zone_types` are zone-section ids (`"3.3.5"`), category ids (`"3.3"`, all its types) or names; lists are allowed. Without filters, areas and zone types are detected in the query text (area codes, area names with Hebrew prefixes, sub-area names that occur in one area, zone-type titles). A requested area with no table 5.4 record (205, mostly a cemetery) is reported in `areas_without_records` and in the context block, so the assistant can say that no special provisions exist.

   **Zone model:** chapter 3 has 11 categories (sections 3.2-3.12). The zone types are the sub-sections of 3.2-3.7 (minus the "כללי" sections) and every category without such sub-sections as a type of its own (3.8-3.12; 3.10 and 3.11 share one chunk, cited as `סעיפים 3.10, 3.11`).
2. **Search:** the question is first normalised to the plan's spelling (מירבי → מרבי, רחק / רח״ק → רח"ק), as is the text of every chunk before it is embedded (`rag.embed.normalize_for_embedding`); the shown text is never changed. A zone type named in the question by its title comes right after the best match, even in free text. A chapter or section named by number in the question (`פרק 4`, `סעיף 4.3`; the number of a zone type names the zone type) or in the `headings` filter (by number or full title) comes first, with the match counted as `high` (`via` `heading`): all of it if up to 1,500 tokens, else its own chunk and its two most similar chunks (without a question, the first ones in document order). A title in free text is not detected: the assistant reads `GET /v1/contents` to learn the plan's wording, then passes the heading to the filter. A heading too large to return whole (chapter 4 has 57 chunks) is never returned whole; the `headings` filter (`["4.3"]`) asks for one of its sections. Clauses and table 5.4 records are searched separately, because the 259 records would otherwise crowd out the clauses. With an area, each area returns the records of the requested zone types (or the most similar ones); without one, at most 2 records. Each zone type returns its section and the "כללי" instructions of its category (e.g. 3.3.1 for 3.3.5), whole if up to 1,500 tokens; when a question is given, a larger scope is cut to its 2 most similar chunks and the rest is listed in `omitted`, while a call with filters only (no question to rank by) always gets the whole scope, and runs no similarity search at all: each requested area returns the records of the requested zone types (the whole area table when none match or no zone type is given), and no area-less records are added. Any table 5.4 record also brings the notes (1) and (2) that precede the table.
3. **Table rows:** a row of a split clause table (e.g. 3.1.2) pulls in the rest of its small section.
4. **References:** followed like a reader would. Mandatory ones always; others while their similarity to the question is above a gate (0.55, rising 0.03 per level), never out of hub chunks, within a token budget; depth 6 is only a backstop (in the evaluation almost all followed references are at depth 1–2).
5. **Match band:** from the best direct similarity (high at 0.65, medium at 0.58). An explicit location that matched records or zone sections counts as `high`, since a plan border with a generic question has no wording to match.

Parameters and Hebrew labels are in `models.yml` (`retrieval`, `labels`). Table 5.4 overrides chapters 3 and 4 and points back at the general rules; expansion follows those references.

## Service and Docker

`docker compose up -d --build` starts two containers: `embedder` (the llama.cpp server with the bge-m3 GGUF, downloaded into a volume on the first start, about 1 GB, CPU only) and `service` (FastAPI, port 8000, reading the index from `bundle/`). Interactive documentation with a try-it page: `http://localhost:8000/docs` (from another machine on the network, `http://<host>:8000/docs`). `docker compose down` stops them; both containers restart by themselves after a crash or a reboot (`restart: unless-stopped`) as long as the Docker engine starts at boot. On a Windows dev machine, `.\scripts\dev-up.ps1` starts Docker Desktop if it is not running, runs the command above and waits until the service is healthy. Needs Docker Desktop (WSL 2 on Windows; on a server, plain Docker Engine) and about 2-3 GB of free memory. A cold start from a fresh clone with nothing downloaded (the 1.2 GB llama.cpp image, the 1 GB model and the service image build of about 650 MB) took about 100 s on a home connection; later starts take seconds. The service sends its JSON as UTF-8 with an explicit charset, so Hebrew decodes correctly in every client, including Windows PowerShell 5. The built image is named `ta5500-rag-service` (`RAG_SERVICE_IMAGE` overrides it).

| Endpoint | Purpose |
|---|---|
| `POST /v1/search` | Body: `question` (Hebrew, optional when filters are given), `areas` (planning-area codes), `zone_types` (zone-section ids like `3.3.5`, or zone names), `entities` (rows of table 5.4: a code like `ר-83` or `101ה`, or a label), `headings` (chapters and sections by number, `4.3`, or by full title, as listed by `/v1/contents`), `context` (default true). Returns `band`, `band_label` (Hebrew), `top_score`, `location` (what was used and whether it came from filters or the text), `hits` (`chunk_id`, `citation`, `page_start`, `page_end`, `score`, `via`, `reason`, `heading_path`, `text`, `followed_from`, `mandatory`), `omitted` (`chunk_id`, `citation`, `tokens` of zone chunks left out for size), and the Hebrew `context` block, which also lists the omitted chunks. Empty lists count as not given. 422 without a question or filters; 503 when the embedding server is down. |
| `GET /v1/chunk?id=...` | One chunk by a `chunk_id` from a search response (`hits` or `omitted`): `citation`, pages, `heading_path`, `text`. 404 for an unknown id. |
| `GET /v1/contents` | The table of contents as a tree: `id` (usable in the `headings` filter), `title`, `citation`, `chunks`, `tokens`, `children`. `?node=` (a number or full title) selects a subtree and `?depth=` limits the levels; 404 for an unknown node. Structure only, not plan text. |
| `GET /v1/locations` | The planning-area codes, the lettered sub-areas (`code` such as `101ה`, usable as an `areas` filter), the rows of table 5.4 by type (`entities`: `code`, `type`, `label`, `areas`, `rows`, usable as an `entities` filter; `?q=` narrows every list to the names that contain a phrase, to resolve a name before filtering), zone types (with their category) and zone categories the filters accept (for building a UI or the GIS mapping). |
| `GET /health` | 200 once the index is loaded and the embedding server answers (the service warms up at start); 503 otherwise. |
| `GET /demo` | A test page in right-to-left Hebrew for trying the search by hand: a question box, one picker for planning areas (with their sub-areas) and table rows (streets, symbols, zone-type rows), and one for zone types (filled from `/v1/locations`, a category stands for all its types), an API-key field, and the results with match level, detected location, each hit's citation, pages, score and text, plus the exact context block the assistant would receive. A browser is all it needs; the page is not part of the API contract and is not listed in the OpenAPI spec. |

Settings by environment variable: `RAG_API_KEY` (when set, `/v1` calls need an `X-API-Key` header; put it in a `.env` file next to `docker-compose.yml`), `RAG_CORS_ORIGINS` (comma-separated, only if a browser calls the service directly; keep the key out of frontend code), `RAG_DATA_DIR` and `RAG_EMBED_BASE_URL` (set by Compose). Chunk ids never leave the service; the UI gets citations.

`bundle/` holds the three index files (`chunks.parquet`, `nodes.parquet`, `embeddings_<model>.parquet`) copied from `local_resources/` after a build, so that anyone can run the stack from a clone of the repository. They are a matching set: the service refuses to start on embeddings that do not match the chunks. Copy all three together and commit them in one commit, mentioning the plan version. Keeping the bundle in git is a deliberate choice: it is small (about 3.4 MB), changes only when the plan or the chunking changes, and makes setup a single clone. Revisit it if the bundle grows past roughly 10-20 MB, changes often, or several plan versions must be kept (then publish it as a versioned artifact instead). A teammate only needs Docker; running the build stages or Lemonade is not required.

Verified: the evaluation run against the containerized embedder gives the same Hit, recall, MRR and bands as with Lemonade (a few borderline hits differ by one chunk, e.g. 10.5 instead of 10.6 chunks per question). Run it again after changing the llama.cpp image, the model or the GGUF.

## Evaluation

`python -m rag.evaluate [-v]` runs `eval_questions.yml` (84 questions: 15 general, 13 planning area, 23 cross-reference, 12 plan-border cases with `filters` (including an area with no record, zone types that share a chunk and a category filter), 4 spelling variants, 4 table-row entity filters, 4 listing questions, 6 chapter and section headings, 3 no-answer) in three modes: plain similarity, location detected from the text, explicit filters. Gold = node ids whose chunk must be returned; `via_ref` = nodes reachable only through a reference. Metrics: Hit, recall, MRR, reference reach, chunks and tokens returned, match band. `--mode text` and `--category listing heading` narrow a run; query vectors are cached in `.eval_cache.pkl` (git-ignored), so a rerun needs the embedding server only for new or edited questions (`--fresh` rebuilds the cache).

Caveats:
- We wrote the questions from the plan's own wording, and the mandatory-reference cues were widened after the first run missed references, so the figures are optimistic and not an independent test.
- The `filters` mode takes the gold chunk's area as the filter (oracle) for questions without explicit `filters`; real GIS values will bring naming mismatches.
- The match-band thresholds are uncalibrated: only 3 no-answer questions (all graded low), and the lowest answerable top score (0.52) is below the highest no-answer one (0.56) in plain similarity.
- Retrieval only: no assistant answer has been evaluated.

## Tests

`pytest -m "not integration"` runs 56 unit tests in a few seconds, with no embedding server and no real plan: `tests/test_doc_tree.py` (ids, reference forms, mandatory flag, ranges, relative, external, dangling), `test_chunker.py` (size limits, merging, atomic records, 5.4 location columns, definitions), `test_pdf_pages.py` (page matching, inheritance, spans), `test_embed.py` (reuse of unchanged vectors, stale/mismatch checks, failure handling, cache) and `test_retrieval.py` (a synthetic index: location from text vs filters, lists, table-row grouping, reference expansion, bands, the Hebrew context). `pytest -m integration` (6 tests) runs the real evaluation on the built artifacts and needs the embedding server; it skips itself otherwise. Use it after rebuilding the plan or changing the model. Run `pytest` from the repository root with the virtual environment active (settings in `pytest.ini`).

## Required next steps

1. **Try it by hand and collect real questions.** Use the demo page (`/demo`): pick planning areas and zone types or type a Hebrew question, and judge the results; add the useful and the failing questions (including plan-border calls with no question) to `eval_questions.yml`.
2. **Agree the assistant-retrieval interface with the assistant team.** The service implements `POST /v1/search` (see Service and Docker; the OpenAPI spec at `/docs` is the contract). Remaining: agree the request and response shape with them, decide whether the assistant uses it as an OpenAPI function-calling tool or through an MCP wrapper (a thin layer over the same API, about 50 lines, to be decided once we know whether their runtime supports MCP), and handle a 503 (embedding server down) in the caller.
3. **Map GIS layers to plan codes.** The planning-area layer must yield the area codes used in table 5.4 (`101`–`907`); the zone layer must yield zone-type ids (`3.2.4`, `3.7.2`…) or names. Decide how to treat several intersecting areas/zones, small overlaps, and zones with no table 5.4 record. Table rows need identifiers too: find out which identifier each layer returns for streets, special planning spaces and urban-renewal spaces (an `ר-NN` code, a name, a polygon id) and adapt the `entities` matching; rows without an `ר-` code are reachable only by label.
4. **Decide the question flow.** Does the assistant pass the user's question as is or rewrite it into a standalone Hebrew query? Evaluate both, and add questions in the assistant's real phrasing, plus plan-border calls with no question. A paraphrase of a heading (`מה ההנחיות לעריכת תכנית מפורטת` for chapter 4, whose title is `הוראות כלליות להכנת תכניות`) is not found reliably in free text (evaluation case h05): the chapter chunk is mostly the body of 4.1, so other chunks that share more words rank above it. The assistant is expected to read `/v1/contents` and requery with the plan's wording.
5. **Write the assistant instructions:** answer only from the context; cite by the given citation and page; say so when the plan has no answer; state uncertainty on `התאמה נמוכה`; remember that table 5.4 special provisions override the general chapters; follow mandatory references.
6. **Evaluate answers end to end:** correctness, correct citations and pages, refusal on no-answer questions (manual review or an LLM judge), on questions that did not shape the retriever.
7. **Calibrate the gate and bands** on a larger set with more no-answer and out-of-scope questions; consider the margin between section groups, not single chunks.
8. **Retrieval improvements, if the end-to-end results call for them:** compare F16 with Q8_0, add a reranker as a second stage, fuzzier location detection, citations that name the first sub-clause for chunks covering a whole section.
9. **Improve the approximate pages** (101 chunks), for example by matching table text more robustly.
10. **Operations:** record the plan version in the bundle (plan version, export date, hashes of the docx and PDF, git commit, e.g. a `manifest.json` in `bundle/`), automate the rebuild when the plan changes, and log queries and the returned context for review. The `embeddings` stage needs the embedding model: run the pipeline on a machine that has Lemonade or the `embedder` container (`RAG_EMBED_BASE_URL`); earlier stages can run anywhere.
11. **Host it beyond a laptop.** The containers run anywhere with Docker. Open: the organization's server (Docker, about 2-3 GB RAM, a reachable address, one instance always running so the model stays loaded), an API key and network access rules, and building and publishing the service image from a pipeline if a registry is wanted (Azure DevOps can build and release but does not run services and has no free container registry; GitHub's `ghcr.io` is free for public images; App Service's free tier is too small for the 1 GB model).
