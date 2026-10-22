from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.fast_api_app import app
from app.infrastructure.database import reset_database_caches
from app.infrastructure.repository import get_repository


def test_health_is_public_and_business_routes_require_api_key(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SPECBRIDGE_API_KEY", "test-secret")
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'security.db'}"
    )
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    get_settings.cache_clear()
    get_repository.cache_clear()
    reset_database_caches()
    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        demo = client.get("/demo")
        unauthorized = client.post("/v1/projects", json={})
        authenticated = client.post(
            "/v1/projects",
            headers={"X-API-Key": "test-secret"},
            json={},
        )
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert demo.status_code == 200
    assert "SpecBridge E2E Demo" in demo.text
    assert demo.headers["cache-control"] == "no-store"
    assert unauthorized.status_code == 401
    assert authenticated.status_code == 422
    get_repository.cache_clear()
    reset_database_caches()
    get_settings.cache_clear()
