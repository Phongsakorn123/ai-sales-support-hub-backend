"""add username to users

Revision ID: d1b9a7c4e2f0
Revises: c826f22a32bf
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1b9a7c4e2f0"
down_revision: Union[str, Sequence[str], None] = "c826f22a32bf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))

    users = sa.table("users", sa.column("id", sa.Integer), sa.column("username", sa.String))
    connection = op.get_bind()
    existing_ids = connection.execute(sa.select(users.c.id)).scalars().all()

    # Primary-key-based names are deterministic and unique for existing accounts.
    for user_id in existing_ids:
        connection.execute(
            sa.update(users)
            .where(users.c.id == user_id)
            .values(username=f"user-{user_id}")
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "username",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_unique_constraint("uq_users_username", ["username"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_username", type_="unique")
        batch_op.drop_column("username")
