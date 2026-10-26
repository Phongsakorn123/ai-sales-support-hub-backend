"""Persisted local semantic vectors for product retrieval."""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class ProductEmbedding(Base):
    __tablename__ = "product_embeddings"
    __table_args__ = (Index("ix_product_embeddings_model_name", "model_name"),)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.current_timestamp(),
        nullable=False,
    )
