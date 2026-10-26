"""normalize product collections into child tables

Revision ID: f7a2b9c4d6e1
Revises: e3c5a1b8f7d2
Create Date: 2026-10-25
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a2b9c4d6e1"
down_revision: Union[str, Sequence[str], None] = "e3c5a1b8f7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# table name -> (source JSON column on products, value column type)
SIMPLE_TABLES = {
    "product_features": ("features", sa.Text()),
    "product_tags": ("tags", sa.String(length=100)),
    "product_target_customers": ("target_customers", sa.String(length=255)),
    "product_use_cases": ("use_cases", sa.String(length=255)),
}
JSON_COLUMNS = ("specifications", "features", "tags", "target_customers", "use_cases")


def _child_table(name: str, *columns: sa.Column) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *columns,
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index(f"ix_{name}_product_id", name, ["product_id"])


def _load(raw) -> list:
    if raw is None:
        return []
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, list) else []


def upgrade() -> None:
    _child_table(
        "product_specifications",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
    )
    for table, (_, value_type) in SIMPLE_TABLES.items():
        _child_table(table, sa.Column("value", value_type, nullable=False))
    op.create_index("ix_product_tags_value", "product_tags", ["value"])

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, specifications, features, tags, target_customers, use_cases FROM products"
        )
    ).mappings()
    for row in rows.all():
        for position, item in enumerate(_load(row["specifications"])):
            connection.execute(
                sa.text(
                    "INSERT INTO product_specifications (product_id, key, value, position) "
                    "VALUES (:product_id, :key, :value, :position)"
                ),
                {
                    "product_id": row["id"],
                    "key": str(item.get("key", "")),
                    "value": str(item.get("value", "")),
                    "position": position,
                },
            )
        for table, (column, value_type) in SIMPLE_TABLES.items():
            child_table = sa.table(
                table,
                sa.column("product_id", sa.Integer()),
                sa.column("value", value_type),
                sa.column("position", sa.Integer()),
            )
            for position, value in enumerate(_load(row[column])):
                connection.execute(
                    child_table.insert(),
                    {"product_id": row["id"], "value": str(value), "position": position},
                )

    # Inactive legacy products become discontinued so their unavailability is preserved.
    connection.execute(sa.text("UPDATE products SET discontinued = 1 WHERE is_active = 0"))

    with op.batch_alter_table("products") as batch_op:
        for column in JSON_COLUMNS:
            batch_op.drop_column(column)
        batch_op.drop_column("is_active")


def downgrade() -> None:
    raise NotImplementedError("This migration is forward-only.")
