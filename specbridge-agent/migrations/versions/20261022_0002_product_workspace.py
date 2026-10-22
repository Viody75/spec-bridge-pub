"""Add product workspace fields used by the frontend.

Revision ID: 20261022_0002
Revises: 20261021_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20261022_0002"
down_revision = "20261021_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The initial MVP migration creates tables from the current model metadata.
    # Existing installations need these columns; a brand-new installation may
    # already have them, so additions must be idempotent.
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("requirements")
    }
    if "implementation_status" not in existing:
        op.add_column(
            "requirements",
            sa.Column(
                "implementation_status",
                sa.String(length=30),
                nullable=False,
                server_default="not_started",
            ),
        )
    if "status_updated_by" not in existing:
        op.add_column(
            "requirements", sa.Column("status_updated_by", sa.String(length=200))
        )
    if "status_updated_at" not in existing:
        op.add_column(
            "requirements",
            sa.Column("status_updated_at", sa.DateTime(timezone=True)),
        )


def downgrade() -> None:
    op.drop_column("requirements", "status_updated_at")
    op.drop_column("requirements", "status_updated_by")
    op.drop_column("requirements", "implementation_status")
