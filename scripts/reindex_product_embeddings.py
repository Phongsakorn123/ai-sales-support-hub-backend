"""Backfill or refresh local product embeddings without changing catalog records.

Run after `alembic upgrade head` and whenever documents are added or edited manually:
    python -m scripts.reindex_product_embeddings
"""

import argparse
import sys

from app.ai_agent.services.embeddings import try_sync_product_embedding
from app.backend.services.products import list_products
from app.db.session import SessionLocal


def reindex_products(db, products, *, dry_run: bool = False) -> tuple[int, int, int]:
    total = len(products)
    if dry_run:
        return 0, 0, total
    indexed = failed = 0
    for product in products:
        if try_sync_product_embedding(db, product):
            indexed += 1
        else:
            failed += 1
    return indexed, failed, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report catalog size without generating embeddings.",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        products = list_products(db, limit=5000, offset=0)
        indexed, failed, total = reindex_products(db, products, dry_run=args.dry_run)
    if args.dry_run:
        print(f"Would index {total} product(s).")
        return 0
    print(f"Reindexed {indexed}/{total} product(s); failed: {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
