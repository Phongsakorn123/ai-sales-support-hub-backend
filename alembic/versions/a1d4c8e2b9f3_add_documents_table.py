"""add documents table for supporting document metadata

Revision ID: a1d4c8e2b9f3
Revises: f7a2b9c4d6e1
Create Date: 2026-10-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1d4c8e2b9f3"
down_revision: Union[str, Sequence[str], None] = "f7a2b9c4d6e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=16), server_default=sa.text("'md'"), nullable=False),
        sa.Column("version", sa.String(length=32), server_default=sa.text("'1.0'"), nullable=False),
        sa.Column(
            "approval_status", sa.String(length=20), server_default=sa.text("'mock_demo'"), nullable=False
        ),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(
            "approval_status IN ('mock_demo', 'draft', 'in_review', 'approved', 'retired')",
            name="ck_documents_approval_status",
        ),
        sa.CheckConstraint(
            "NOT (is_mock = 1 AND approval_status = 'approved')",
            name="ck_documents_mock_not_approved",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_path"),
    )
    op.create_index("ix_documents_product_id", "documents", ["product_id"])
    op.create_index("ix_documents_document_type", "documents", ["document_type"])
    op.create_index(
        "uq_documents_mock_product_type",
        "documents",
        ["product_id", "document_type"],
        unique=True,
        sqlite_where=sa.text("is_mock = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_mock_product_type", table_name="documents")
    op.drop_index("ix_documents_document_type", table_name="documents")
    op.drop_index("ix_documents_product_id", table_name="documents")
    op.drop_table("documents")
