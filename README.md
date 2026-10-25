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

## Tests

```bash
.venv/bin/python -m unittest discover -s tests/unit
```
