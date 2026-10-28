"""SQLAlchemy persistence operations for the local semantic index."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.backend.models.product_embedding import ProductEmbedding


def get_product_embedding(
    db: Session, product_id: int
) -> ProductEmbedding | None:
    return db.get(ProductEmbedding, product_id)


def save_product_embedding(
    db: Session,
    *,
    product_id: int,
    model_name: str,
    content_hash: str,
    vector: list[float],
    existing: ProductEmbedding | None,
) -> None:
    if existing is None:
        existing = ProductEmbedding(product_id=product_id)
        db.add(existing)
    existing.model_name = model_name
    existing.content_hash = content_hash
    existing.vector = vector
    db.commit()


def delete_product_embedding(db: Session, product_id: int) -> None:
    db.execute(
        delete(ProductEmbedding).where(ProductEmbedding.product_id == product_id)
    )
    db.commit()


def list_product_embeddings(
    db: Session, model_name: str
) -> list[ProductEmbedding]:
    return list(
        db.scalars(
            select(ProductEmbedding).where(
                ProductEmbedding.model_name == model_name
            )
        ).all()
    )
