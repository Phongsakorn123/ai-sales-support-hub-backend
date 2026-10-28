"""SQLAlchemy persistence operations for supporting-document metadata."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.models.document import Document


def list_search_candidates(
    db: Session, *, product_id: int | None, document_type: str | None
) -> list[Document]:
    statement = select(Document).order_by(Document.id)
    if product_id is not None:
        statement = statement.where(Document.product_id == product_id)
    if document_type:
        statement = statement.where(Document.document_type == document_type)
    return list(db.scalars(statement))


def get_document(db: Session, document_id: str) -> Document | None:
    return db.get(Document, document_id)


def list_product_documents(
    db: Session, product_id: int, *, limit: int, offset: int
) -> list[Document]:
    statement = (
        select(Document)
        .where(Document.product_id == product_id)
        .order_by(Document.id)
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement))
