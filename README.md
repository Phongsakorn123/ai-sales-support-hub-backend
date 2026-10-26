# AI Sales Support Hub — Backend

FastAPI + SQLite backend. API prefix: `/api/v1`. Interactive docs: `/docs`.

## Quick start with Docker Compose

The Compose file builds and starts both the backend and its sibling frontend repository.
Keep `ai-sales-support-hub-backend` and `ai-sales-support-hub-frontend` as sibling directories.
From the backend repository, prepare local configuration and start the services:

```bash
cp .env.example .env
# Edit .env: set a unique JWT_SECRET_KEY; set OPENROUTER_API_KEY and OPENROUTER_MODEL for AI ranking.
docker compose up --build -d
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.seed_products
docker compose exec backend python -m scripts.seed_consumer_catalog
docker compose exec backend python -m scripts.seed_documents
docker compose exec backend python -m scripts.reindex_product_embeddings
```

The frontend is at <http://localhost:3001>; the backend is at <http://localhost:8002>, with
Swagger UI at <http://localhost:8002/docs> and health check at <http://localhost:8002/health>.
The initial embedding build downloads the configured multilingual model and may take several
minutes. SQLite, model cache, and document files persist under the backend `data/` and
`storage/documents/` directories. Seed commands are safe to rerun; they skip existing catalog
SKUs and document records. Do not use `--reset-products` against a database you need to preserve.

For API-only local development, create a virtual environment, install `requirements.txt`, copy
`.env.example` to `.env`, set `DATABASE_PATH` and `DOCUMENT_STORAGE_DIR` as needed, then run
`alembic upgrade head`, the seed commands above, and `uvicorn app.main:app --reload --port 8000`.
When running the frontend outside Compose against that local server, point both frontend API
base URLs at `http://localhost:8000/api/v1`.

## Supporting documents

> **All seeded documents are MOCK DEMO DATA** generated from product catalog fields. They are
> not official, reviewed or approved documents (`is_mock = true`, `approval_status = mock_demo`).
> The documents endpoint's text search is plain keyword matching; product recommendations use the
> separate hybrid retrieval described below.

### Storage and metadata

- Markdown files live in `storage/documents/` (override with `DOCUMENT_STORAGE_DIR`), named by
  document ID: `DOC-0001.md`.
- SQLite table `documents` holds metadata only (`id`, `product_id` → `products.id`, `title`,
  `document_type`, `description`, `file_path`, `file_format`, `version`, `approval_status`,
  `is_mock`, timestamps). `file_path` is relative to the storage directory. The Markdown file is
  the canonical content.
- Deleting a product deletes its document rows (FK `ON DELETE CASCADE`); the `.md` files stay on disk.
- Document types: `datasheet`, `product_manual`, `compatibility_guide`, `brochure`, and
  `validation_report` (blank demo checklist, Laboratory/Monitoring products only).
  `publication` is intentionally not generated because it cannot be grounded in catalog data.

### Set up and seed

```bash
.venv/bin/alembic upgrade head                  # creates the documents table
.venv/bin/python -m scripts.seed_documents --dry-run
.venv/bin/python -m scripts.seed_documents
```

The seed is idempotent: it creates only missing records/files, never overwrites or deletes files,
restores a missing file for an existing record, and reports created/skipped/failed counts. Run it
again after adding products to generate their documents.

### API (Bearer token required)

| Method | Path                                                             | Purpose                                              |
| ------ | ---------------------------------------------------------------- | ---------------------------------------------------- |
| GET    | `/documents/search?q=&product_id=&document_type=&limit=&offset=` | Keyword search over title, description and file text |
| GET    | `/documents/{document_id}`                                       | Metadata and file links                              |
| GET    | `/documents/{document_id}/file[?download=true]`                  | The actual Markdown (inline or attachment)           |
| GET    | `/products/{product_id}/documents`                               | Documents for a product                              |

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8002/api/v1/documents/search?q=warranty&document_type=datasheet&limit=5"
curl -H "Authorization: Bearer $TOKEN" http://localhost:8002/api/v1/documents/DOC-0001/file
curl -H "Authorization: Bearer $TOKEN" -OJ "http://localhost:8002/api/v1/documents/DOC-0001/file?download=true"
```

Files are only served for existing records, and the resolved path must stay inside the storage
directory. Clients never supply paths.

### Adding or updating documents safely

1. Add: put a new `DOC-NNNN.md` (next unused number) in `storage/documents/` and insert a
   `documents` row with `file_path = 'DOC-NNNN.md'` and a valid `product_id`. Set `is_mock = 0` only
   for genuine documents, and never mark a mock document approved.
2. Update: edit the `.md` file in place. Search picks up changes automatically (cache keyed by
   mtime/size). Bump `version` and `updated_at` in the row if appropriate.
3. Back up `data/app.db` before manual database edits.

## AI product suggestions

`POST /api/v1/ai/product-suggestions` (Bearer token required) turns a customer's natural-language
requirements into grounded product recommendations. The code lives in `app/ai_agent/` and calls the
existing product and document services; the Core Backend does not import it.

Configuration (in `.env`, never commit real values): `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
(required, no default), `OPENROUTER_BASE_URL`, `OPENROUTER_TIMEOUT_SECONDS`. Without a key and model
the endpoint returns 503. The LLM is called through LangChain (`ChatOpenAI` against OpenRouter), so
requirement text and selected catalog fields are sent to OpenRouter; `cost_price` never is.

Workflow (one model call per request): the backend parses budget, currency, category, brand and a
minimum RAM with deterministic rules, validated against the real catalog values, then retrieves candidates
with keyword and local embedding search plus RRF. It loads short excerpts of their linked Markdown documents.
The model then ranks them and explains why. Names, prices, stock and document metadata always come from
the database, and any product or document ID the model invents is discarded. Seeded documents are mock
demo data and are labeled as such in every response.

Exact matches and alternatives: the backend checks each candidate against the hard constraints it can
verify from catalog data (category, preferred brand, stock, budget, minimum RAM) and sets
`match_type` on every recommendation: `exact_match` (all met), `insufficient_data` (nothing failed
but something, such as an unlisted RAM size, could not be verified) or `alternative` (a requirement
is not met). Each item lists `satisfied_requirements`, `unmet_requirements` and
`unverified_requirements`. Relaxed constraints only produce labelled alternatives, never exact
matches; alternatives priced above 1.5x the budget are left out; out-of-stock products stay excluded
unless `include_out_of_stock` is true. Results are ordered exact, then unverified, then alternatives,
then by fewest unmet requirements, closeness to budget and RAM, relevance and ID. The model can reorder
within a type and add reasons, but cannot change a classification. `max_products` is an upper bound.

HTTP 429 from OpenRouter, including a 429 reported inside an HTTP 200 error body, is retried at most
twice (about 5 s, then 10 s, or `Retry-After` capped at 20 s), within a 55 s total budget that stays
under Nginx's default 60 s `proxy_read_timeout`. If the model still fails, the response is 200 with
`ranking_source: "catalog_fallback"`: the same deterministic classification and order, with no AI
ranking or reasoning.

```bash
curl -X POST http://localhost:8002/api/v1/ai/product-suggestions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"requirements": "A laptop for video editing under 50000 THB with at least 32GB RAM", "max_products": 3, "include_out_of_stock": false}'
```

Errors: 422 invalid input, 503 AI not configured. Model failures degrade to the labelled fallback above.
Product recommendations use hybrid retrieval: exact keyword matching over product names, brands, categories,
specifications, features and use cases is fused with cosine-ranked local multilingual embeddings. The default
embedding model is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions); FastEmbed
runs it locally with ONNX Runtime, so product text is not sent to an embedding API. The persisted SQLite
`product_embeddings` table stores vectors separately from catalog rows. This keeps the current Docker/SQLite
architecture simple; vector ranking is an exhaustive scan and is appropriate for the current small catalog.
For very large catalogs, move the same vectors to an ANN vector store such as Qdrant.

Keyword and semantic ranks are combined with weighted Reciprocal Rank Fusion:
`sum(weight / (rrf_k + rank))`. Defaults are `k=60` and weight `1.0` for each source. This avoids adding raw
keyword and cosine scores with different scales. The backend still evaluates stock, discontinued status,
category, brand, budget/currency and minimum RAM from database facts after retrieval; semantic similarity
cannot change those verdicts. Existing labelled-alternative and budget-ceiling behavior is preserved.

Install the additive migration, then backfill the current catalog once:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.reindex_product_embeddings --dry-run
.venv/bin/python -m scripts.reindex_product_embeddings
```

The first backfill downloads the model into `data/fastembed` (persistent in the existing Compose `./data`
mount). Product creation and updates synchronously refresh their vector after the catalog commit; product
deletion cascades its vector row. If the local model is unavailable, product CRUD remains successful and
stale vectors are removed. Search then returns safe keyword results and includes `retrieval_mode="keyword_fallback"`
plus a limitation explaining the degraded mode. Once the model is available, re-run the reindex command.
Run reindex after manually editing/adding product documents, because those files have no write API; documents
are included in product vectors when available. No database volume is cleared by this migration or command.

Docker setup from the backend repository (Compose starts both services):

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.reindex_product_embeddings --dry-run
docker compose exec backend python -m scripts.reindex_product_embeddings
```

`retrieval_mode` is `hybrid` only when semantic retrieval has usable indexed vectors; if model inference fails
or no vectors are indexed, the API explicitly reports `keyword_fallback` instead of claiming hybrid search.
The evaluation fixtures and actual local-model comparison are in `evaluation/`:

```bash
.venv/bin/python -m scripts.reindex_product_embeddings
.venv/bin/python -m evaluation.run_hybrid_eval
```

The evaluation uses six representative English and Thai queries with known relevant fixture products and
reports Recall@3 and MRR@3 for keyword-only and fused rankings. On 2026-10-26 with the default model, the
measured scores were keyword Recall@3 0.500 / MRR@3 0.500 and hybrid Recall@3 1.000 / MRR@3 0.806.
The fixture is a reproducible smoke-sized evaluation, not a production relevance benchmark.

## Authentication and API overview

Register at `POST /api/v1/auth/register` and obtain a bearer token from
`POST /api/v1/auth/login`. Protected product, document, and AI suggestion endpoints require
`Authorization: Bearer <token>`. Login tokens expire after the configured one-hour lifetime.
Use `/docs` or `/openapi.json` for the current request and response schemas. Product endpoints
support listing/filtering, detail retrieval, create, update, and delete; product image files are
served from the configured static product directory.

## Tests

The default suite contains 10 essential backend regression cases and is the suite run in CI.

```bash
.venv/bin/python -m unittest discover -s tests/unit
```

The broader existing suite is preserved under `tests/extended` and can be run separately:

```bash
.venv/bin/python -m unittest discover -s tests/extended
```

The current checked-in suites contain 17 unit tests and 142 extended tests. Run both before
submitting changes. Backend CI runs the unit suite; the extended suite is available for broader
regression coverage.

## Security workflows

The frontend repository has a GitHub Actions workflow for Semgrep, Trivy dependency scanning,
and IaC scanning. This repository includes backend test CI and a DAST workflow using OWASP ZAP.
These workflows scan configured targets and dependencies; they do not replace deployment-specific
security review. Keep `.env`, database files, product uploads, and model caches out of commits.
