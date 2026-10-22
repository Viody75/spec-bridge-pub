"""persist Product Owner document decisions

Revision ID: 20261022_0004
Revises: 20261022_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0004"
down_revision: str | None = "20261022_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("documents")
    }
    if "approval_decision" not in existing:
        op.add_column(
            "documents",
            sa.Column(
                "approval_decision",
                sa.String(length=30),
                nullable=False,
                server_default="pending",
            ),
        )
    if "decision_note" not in existing:
        op.add_column("documents", sa.Column("decision_note", sa.Text()))
    if "decided_by" not in existing:
        op.add_column("documents", sa.Column("decided_by", sa.String(length=200)))
    if "decided_at" not in existing:
        op.add_column("documents", sa.Column("decided_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("documents", "decided_at")
    op.drop_column("documents", "decided_by")
    op.drop_column("documents", "decision_note")
    op.drop_column("documents", "approval_decision")
