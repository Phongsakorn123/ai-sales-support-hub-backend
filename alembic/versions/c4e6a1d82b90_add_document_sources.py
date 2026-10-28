"""add external publisher source metadata to documents

Revision ID: c4e6a1d82b90
Revises: 3b72e1c9a640
Create Date: 2026-10-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "c4e6a1d82b90"
down_revision: Union[str, Sequence[str], None] = "3b72e1c9a640"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_name", sa.String(length=120), nullable=True))
    op.add_column("documents", sa.Column("source_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "source_url")
    op.drop_column("documents", "source_name")
