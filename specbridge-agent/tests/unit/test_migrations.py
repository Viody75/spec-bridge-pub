from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.config import get_settings


@pytest.fixture(autouse=True)
def fresh_settings():
    # migrations/env.py reads DATABASE_URL through the cached settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_migrations_upgrade_empty_database_to_head(monkeypatch, tmp_path) -> None:
    database = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    command.upgrade(Config("alembic.ini"), "head")

    with sqlite3.connect(database) as connection:
        requirement_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(requirements)").fetchall()
        }
        conflict_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(conflicts)").fetchall()
        }
        document_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()

    assert {
        "implementation_status",
        "status_updated_by",
        "status_updated_at",
    } <= requirement_columns
    assert "comments" in conflict_columns
    assert {"approval_decision", "decision_note", "decided_by", "decided_at"} <= (
        document_columns
    )
    assert revision == ("20261022_0005",)


def _schema(database) -> tuple[set[str], set[str], set[str]]:
    with sqlite3.connect(database) as connection:
        run_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(analysis_runs)").fetchall()
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(analysis_runs)").fetchall()
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    return run_columns, indexes, tables


def test_jobs_migration_round_trip(monkeypatch, tmp_path) -> None:
    database = tmp_path / "jobs.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, "20261022_0004")
    run_columns, indexes, tables = _schema(database)
    assert "progress" not in run_columns
    assert "uq_analysis_run_project_running" in indexes
    assert "trace_items" not in tables

    command.upgrade(config, "head")
    run_columns, indexes, tables = _schema(database)
    assert {"progress", "attempts", "started_at", "heartbeat_at"} <= run_columns
    assert "uq_analysis_run_project_active" in indexes
    assert "uq_analysis_run_project_running" not in indexes
    assert "trace_items" in tables
