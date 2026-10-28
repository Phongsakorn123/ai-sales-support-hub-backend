from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

APPROVAL_STATUSES = ("mock_demo", "draft", "in_review", "approved", "retired")


class Document(Base):
    """Metadata for a supporting document. The Markdown file on disk is the canonical content."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('mock_demo', 'draft', 'in_review', 'approved', 'retired')",
            name="ck_documents_approval_status",
        ),
        CheckConstraint(
            "NOT (is_mock = 1 AND approval_status = 'approved')",
            name="ck_documents_mock_not_approved",
        ),
        Index("ix_documents_product_id", "product_id"),
        Index("ix_documents_document_type", "document_type"),
        Index(
            "uq_documents_mock_product_type",
            "product_id",
            "document_type",
            unique=True,
            sqlite_where=text("is_mock = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    file_path: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_format: Mapped[str] = mapped_column(
        String(16), default="md", server_default=text("'md'"), nullable=False
    )
    version: Mapped[str] = mapped_column(
        String(32), default="1.0", server_default=text("'1.0'"), nullable=False
    )
    approval_status: Mapped[str] = mapped_column(
        String(20),
        default="mock_demo",
        server_default=text("'mock_demo'"),
        nullable=False,
    )
    is_mock: Mapped[bool] = mapped_column(
        default=True, server_default=text("1"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.current_timestamp(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.current_timestamp(),
        nullable=False,
    )
