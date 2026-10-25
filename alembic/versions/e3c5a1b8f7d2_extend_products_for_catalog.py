"""extend products for catalog fields

Revision ID: e3c5a1b8f7d2
Revises: d1b9a7c4e2f0
Create Date: 2026-10-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3c5a1b8f7d2"
down_revision: Union[str, Sequence[str], None] = "d1b9a7c4e2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_LIST_COLUMNS = ("specifications", "features", "tags", "target_customers", "use_cases")


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "price",
            new_column_name="selling_price",
            existing_type=sa.Numeric(12, 2),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("brand", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("sku", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("image_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("type", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("sub_type", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("cost_price", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(
            sa.Column("discontinued", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
        batch_op.add_column(sa.Column("warranty_months", sa.Integer(), nullable=True))
        for column in JSON_LIST_COLUMNS:
            batch_op.add_column(
                sa.Column(column, sa.JSON(), server_default=sa.text("'[]'"), nullable=False)
            )
        batch_op.add_column(sa.Column("key_benefits", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("limitations", sa.Text(), nullable=True))
        batch_op.create_unique_constraint("uq_products_sku", ["sku"])


def downgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint("uq_products_sku", type_="unique")
        for column in (
            "limitations", "key_benefits", *JSON_LIST_COLUMNS, "warranty_months",
            "discontinued", "cost_price", "sub_type", "type", "image_url", "sku",
            "model", "brand",
        ):
            batch_op.drop_column(column)
        batch_op.alter_column(
            "selling_price",
            new_column_name="price",
            existing_type=sa.Numeric(12, 2),
            existing_nullable=False,
        )
