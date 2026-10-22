"""background analysis jobs, trace-item decisions and human document edits

Revision ID: 20261022_0005
Revises: 20261022_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261022_0005"
down_revision: str | None = "20261022_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The initial migration creates tables from the current model metadata, so a
# brand-new database may already have everything below; each step is idempotent.


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _json_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=False,
        server_default=sa.text("'{}'"),
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    runs = _columns("analysis_runs")
    if "progress" not in runs:
        op.add_column("analysis_runs", _json_column("progress"))
    if "attempts" not in runs:
        op.add_column(
            "analysis_runs",
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        )
    if "started_at" not in runs:
        op.add_column(
            "analysis_runs", sa.Column("started_at", sa.DateTime(timezone=True))
        )
    if "heartbeat_at" not in runs:
        op.add_column(
            "analysis_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True))
        )

    # One queued *or* running analysis per project (was: running only)
    indexes = {index["name"] for index in inspector.get_indexes("analysis_runs")}
    if "uq_analysis_run_project_running" in indexes:
        op.drop_index("uq_analysis_run_project_running", table_name="analysis_runs")
    if "uq_analysis_run_project_active" not in indexes:
        op.create_index(
            "uq_analysis_run_project_active",
            "analysis_runs",
            ["project_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('queued', 'running')"),
            sqlite_where=sa.text("status IN ('queued', 'running')"),
        )

    requirements = _columns("requirements")
    if "artifact_links" not in requirements:
        op.add_column("requirements", _json_column("artifact_links"))
    if "artifact_links_updated_by" not in requirements:
        op.add_column(
            "requirements",
            sa.Column("artifact_links_updated_by", sa.String(length=200)),
        )

    if "section_edits" not in _columns("document_versions"):
        op.add_column("document_versions", _json_column("section_edits"))

    if not inspector.has_table("trace_items"):
        op.create_table(
            "trace_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(length=64),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "run_id",
                sa.Uuid(),
                sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("candidate_id", sa.String(length=100), nullable=False),
            sa.Column("classification", sa.String(length=30), nullable=False),
            sa.Column("outcome", sa.String(length=20), nullable=False),
            sa.Column("match_requirement_id", sa.String(length=100)),
            sa.Column("similarity", sa.Float()),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("requirement_id", sa.String(length=100)),
            sa.Column(
                "candidate",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
            ),
            sa.Column("decision", sa.String(length=20)),
            sa.Column("decided_by", sa.String(length=200)),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("run_id", "candidate_id"),
        )


def downgrade() -> None:
    op.drop_table("trace_items")
    op.drop_column("document_versions", "section_edits")
    op.drop_column("requirements", "artifact_links_updated_by")
    op.drop_column("requirements", "artifact_links")
    op.drop_index("uq_analysis_run_project_active", table_name="analysis_runs")
    op.create_index(
        "uq_analysis_run_project_running",
        "analysis_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )
    op.drop_column("analysis_runs", "heartbeat_at")
    op.drop_column("analysis_runs", "started_at")
    op.drop_column("analysis_runs", "attempts")
    op.drop_column("analysis_runs", "progress")
