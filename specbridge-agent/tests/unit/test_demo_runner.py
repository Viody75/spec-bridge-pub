from __future__ import annotations

import os

import pytest

from scripts.run_demo import prepare_environment


def test_prepare_environment_prompts_for_database_and_generates_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SPECBRIDGE_API_KEY", raising=False)

    api_key, generated = prepare_environment(
        env_file=tmp_path / "missing.env",
        prompt=lambda _message: "postgresql://user:password@localhost/db",
    )

    assert os.environ["DATABASE_URL"] == "postgresql://user:password@localhost/db"
    assert os.environ["SPECBRIDGE_API_KEY"] == api_key
    assert os.environ["TEXT_EMBEDDING_MODEL"] == "gemini-embedding-001"
    assert os.environ["WORKFLOW_TIMEOUT_SECONDS"] == "600"
    assert len(api_key) >= 24
    assert generated is True


def test_prepare_environment_preserves_configured_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///configured.db")
    monkeypatch.setenv("SPECBRIDGE_API_KEY", "configured-key")

    api_key, generated = prepare_environment(
        env_file=tmp_path / "missing.env",
        prompt=lambda _message: pytest.fail("prompt should not be called"),
    )

    assert api_key == "configured-key"
    assert generated is False
