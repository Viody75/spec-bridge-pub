"""persist conflict comments

Revision ID: 20261022_0003
Revises: 20261022_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0003"
down_revision: str | None = "20261022_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("conflicts")
    }
    if "comments" not in existing:
        op.add_column(
            "conflicts",
            sa.Column("comments", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_column("conflicts", "comments")
