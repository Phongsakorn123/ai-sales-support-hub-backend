"""Local multilingual product embeddings stored in the application's SQLite database."""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from collections.abc import Sequence

from fastembed import TextEmbedding
from sqlalchemy import select
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.backend.models.document import Document
from app.backend.models.product import Product
from app.backend.models.product_embedding import ProductEmbedding
from app.backend.services import documents as document_service
from app.core.config import settings

logger = logging.getLogger(__name__)
_model: TextEmbedding | None = None
_model_lock = threading.Lock()
MAX_DOCUMENT_CHARS = 3000
MAX_DOCUMENTS = 5
INDEX_FORMAT = "normalized-v1"


def index_model_name() -> str:
    return f"{settings.semantic_embedding_model}:{INDEX_FORMAT}"


def _embedding_model() -> TextEmbedding:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = TextEmbedding(
                    model_name=settings.semantic_embedding_model,
                    cache_dir=str(settings.semantic_embedding_cache_dir),
                )
    return _model


def embed_text(text: str) -> list[float]:
    """Generate one normalized local vector; no catalog text leaves this process."""
    vector = next(iter(_embedding_model().embed([text])))
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        raise ValueError("The embedding model returned an empty vector")
    return [value / norm for value in values]


def product_text(db: Session, product: Product) -> str:
    """Build retrieval text from customer-facing fields only; never include cost or secrets."""
    parts = [
        product.name,
        product.brand,
        product.model,
        product.category,
        product.type,
        product.sub_type,
        product.description,
        product.key_benefits,
        product.limitations,
        *(f"{item['key']}: {item['value']}" for item in product.specifications),
        *product.features,
        *product.tags,
        *product.target_customers,
        *product.use_cases,
    ]
    docs = db.scalars(
        select(Document)
        .where(Document.product_id == product.id)
        .order_by(Document.id)
        .limit(MAX_DOCUMENTS)
    ).all()
    for document in docs:
        parts.extend([document.title, document.description])
        try:
            parts.append(
                document_service.read_document_text(document)[:MAX_DOCUMENT_CHARS]
            )
        except document_service.DocumentFileError:
            logger.info(
                "Skipping unreadable product document during embedding (product_id=%s)",
                product.id,
            )
    return "\n".join(str(part).strip() for part in parts if part and str(part).strip())


def sync_product_embedding(db: Session, product: Product) -> None:
    """Upsert a current vector after a product write; caller handles failures without failing CRUD."""
    text = product_text(db, product)
    model_name = index_model_name()

    digest = hashlib.sha256(
        f"{INDEX_FORMAT}\0{model_name}\0{text}".encode("utf-8")
    ).hexdigest()
    existing = db.get(ProductEmbedding, product.id)
    if (
        existing
        and existing.content_hash == digest
        and existing.model_name == model_name
    ):
        return
    vector = embed_text(text)
    if existing:
        existing.model_name = model_name
        existing.content_hash = digest
        existing.vector = vector
    else:
        db.add(
            ProductEmbedding(
                product_id=product.id,
                model_name=model_name,
                content_hash=digest,
                vector=vector,
            )
        )
    db.commit()


def try_sync_product_embedding(db: Session, product: Product) -> bool:
    try:
        sync_product_embedding(db, product)
        return True
    except Exception as error:
        db.rollback()
        try:
            db.execute(
                delete(ProductEmbedding).where(
                    ProductEmbedding.product_id == product.id
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        logger.warning(
            "Product embedding sync failed (product_id=%s, error=%s)",
            product.id,
            type(error).__name__,
        )
        return False


def semantic_search(
    db: Session, query: str, *, limit: int, eligible_ids: set[int] | None = None
) -> tuple[list[tuple[int, float]], int]:
    """Return (product_id, cosine score) rows and the count of usable indexed products."""
    query_vector = embed_text(query)
    rows = db.scalars(
        select(ProductEmbedding).where(
            ProductEmbedding.model_name == index_model_name()
        )
    ).all()
    scored: list[tuple[int, float]] = []
    usable = 0
    for row in rows:
        if eligible_ids is not None and row.product_id not in eligible_ids:
            continue
        try:
            vector = [float(value) for value in row.vector]
            if len(vector) != len(query_vector) or not vector:
                continue
            usable += 1
            score = sum(left * right for left, right in zip(query_vector, vector))
            scored.append((row.product_id, score))
        except (TypeError, ValueError):
            continue
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:limit], usable
