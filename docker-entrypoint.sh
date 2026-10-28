#!/bin/sh
set -eu

if [ -z "${JWT_SECRET_KEY:-}" ]; then
  secret_file=/app/data/.jwt_secret
  if [ ! -s "$secret_file" ]; then
    umask 077
    python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$secret_file"
  fi
  JWT_SECRET_KEY=$(cat "$secret_file")
  export JWT_SECRET_KEY
fi

echo "Applying database migrations..."
alembic upgrade head

echo "Seeding the product catalog..."
python -m scripts.seed_products
python -m scripts.seed_consumer_catalog

echo "Creating demo supporting documents..."
python -m scripts.seed_documents
python -m scripts.seed_reference_documents

echo "Building the local product embedding index..."
if ! python -m scripts.reindex_product_embeddings; then
  echo "Embedding index is unavailable; the API will use keyword fallback until indexing succeeds."
fi

exec "$@"
