# AI Sales Support Hub — Backend

FastAPI + SQLite backend. API prefix: `/api/v1`. Interactive docs: `/docs`.

## Supporting documents

> **All seeded documents are MOCK DEMO DATA** generated from product catalog fields. They are
> not official, reviewed or approved documents (`is_mock = true`, `approval_status = mock_demo`).
> Search is plain keyword matching, **not** semantic/RAG search.

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

| Method | Path | Purpose |
|---|---|---|
| GET | `/documents/search?q=&product_id=&document_type=&limit=&offset=` | Keyword search over title, description and file text |
| GET | `/documents/{document_id}` | Metadata and file links |
| GET | `/documents/{document_id}/file[?download=true]` | The actual Markdown (inline or attachment) |
| GET | `/products/{product_id}/documents` | Documents for a product |

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8081/api/v1/documents/search?q=warranty&document_type=datasheet&limit=5"
curl -H "Authorization: Bearer $TOKEN" http://localhost:8081/api/v1/documents/DOC-0001/file
curl -H "Authorization: Bearer $TOKEN" -OJ "http://localhost:8081/api/v1/documents/DOC-0001/file?download=true"
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
minimum RAM with deterministic rules, validated against the real catalog values, and retrieves matching
in-stock products and short excerpts of their linked Markdown documents with ordinary queries. The
model then ranks them and explains why. Names, prices, stock and document metadata always come from
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
curl -X POST http://localhost:8081/api/v1/ai/product-suggestions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"requirements": "A laptop for video editing under 50000 THB with at least 32GB RAM", "max_products": 3, "include_out_of_stock": false}'
```

Errors: 422 invalid input, 503 AI not configured. Model failures degrade to the labelled fallback above.
Search is keyword-based; there is no vector search.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests/unit
```
