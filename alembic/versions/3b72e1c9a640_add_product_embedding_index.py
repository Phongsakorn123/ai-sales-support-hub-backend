"""add local product embedding index

Revision ID: 3b72e1c9a640
Revises: a1d4c8e2b9f3
Create Date: 2026-10-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3b72e1c9a640"
down_revision: Union[str, Sequence[str], None] = "a1d4c8e2b9f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_embeddings",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id"),
    )
    op.create_index("ix_product_embeddings_model_name", "product_embeddings", ["model_name"])


def downgrade() -> None:
    op.drop_index("ix_product_embeddings_model_name", table_name="product_embeddings")
    op.drop_table("product_embeddings")
