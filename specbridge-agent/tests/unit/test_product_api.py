from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.fast_api_app import app
from app.infrastructure.database import reset_database_caches
from app.infrastructure.embeddings import get_embedding_service
from app.infrastructure.repository import get_repository
from app.services import get_analysis_service


def test_product_workspace_api_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SPECBRIDGE_API_KEY", "test-secret")
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'product-api.db'}"
    )
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    monkeypatch.delenv("TEXT_EMBEDDING_MODEL", raising=False)
    get_settings.cache_clear()
    get_embedding_service.cache_clear()
    get_repository.cache_clear()
    get_embedding_service.cache_clear()
    reset_database_caches()
    headers = {"X-API-Key": "test-secret"}

    with TestClient(app) as client:
        created = client.post(
            "/v1/projects",
            headers=headers,
            json={
                "project_id": "mvp",
                "name": "MVP",
                "document_type": "SRS",
                "policy": {
                    "project": {
                        "code": "MVP",
                        "owner_id": "pm-1",
                        "approver_id": "po-1",
                        "division": "Product",
                        "target_date": "2026-12-31",
                    }
                },
            },
        )
        assert created.status_code == 201

        requirement = client.post(
            "/v1/projects/mvp/requirements/manual",
            headers=headers,
            json={
                "text": "The Product Owner can add a requirement manually.",
                "rationale": "Human input remains part of the controlled draft.",
                "actor_id": "po-1",
                "priority": "must",
            },
        )
        assert requirement.status_code == 201
        requirement_id = requirement.json()["requirement_id"]

        reviewed = client.patch(
            f"/v1/projects/mvp/requirements/{requirement_id}/review",
            headers=headers,
            json={"state": "approved", "actor_id": "ba-1"},
        )
        assert reviewed.status_code == 200

        delivery = client.patch(
            f"/v1/projects/mvp/requirements/{requirement_id}/implementation-status",
            headers=headers,
            json={"state": "in_progress", "actor_id": "dev-1"},
        )
        assert delivery.status_code == 200

        workspace = client.get("/v1/projects/mvp/workspace", headers=headers)
        assert workspace.status_code == 200
        body = workspace.json()
        assert body["project"]["code"] == "MVP"
        assert body["requirements"][0]["review_state"] == "approved"
        assert body["requirements"][0]["implementation_status"] == "in_progress"

        projects = client.get("/v1/projects", headers=headers)
        assert projects.status_code == 200
        assert projects.json()[0]["requirements"] == 1

    get_repository.cache_clear()
    reset_database_caches()
    get_settings.cache_clear()


def test_batch_upload_queues_one_job_and_human_edit_routes(
    monkeypatch, tmp_path
) -> None:
    from app.api import routes

    class FakeOcr:
        async def transcribe(self, data: bytes, *, mime_type: str) -> str:
            return "Whiteboard: approval within 2 days"

    monkeypatch.setenv("SPECBRIDGE_API_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    monkeypatch.setenv("AUTO_CREATE_SCHEMA", "true")
    # Keep the job queued: this test checks the contract, not the agents
    monkeypatch.setenv("ANALYSIS_WORKER_ENABLED", "false")
    monkeypatch.delenv("TEXT_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(routes, "get_ocr_engine", lambda: FakeOcr())
    get_settings.cache_clear()
    get_embedding_service.cache_clear()
    get_repository.cache_clear()
    get_analysis_service.cache_clear()
    reset_database_caches()
    headers = {"X-API-Key": "test-secret"}

    with TestClient(app) as client:
        created = client.post(
            "/v1/projects",
            headers=headers,
            json={"project_id": "mvp", "name": "MVP", "document_type": "SRS"},
        )
        assert created.status_code == 201, created.text
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        accepted = client.post(
            "/v1/projects/mvp/upload-jobs",
            headers=headers,
            data={"author": "po-1"},
            files=[
                ("files", ("notes.txt", b"Users need a status page.", "text/plain")),
                (
                    "files",
                    ("minutes.md", b"# Meeting\nExport to PDF.", "text/markdown"),
                ),
                ("files", ("whiteboard.png", png, "image/png")),
            ],
        )
        assert accepted.status_code == 202, accepted.text
        job = accepted.json()
        assert job["status"] == "queued"
        assert [s["source_type"] for s in job["sources"]] == [
            "manual_note",
            "manual_note",
            "image",
        ]

        progress = client.get(
            f"/v1/projects/mvp/analysis/{job['run_id']}/progress", headers=headers
        )
        assert progress.status_code == 200
        assert progress.json()["progress"]["stage"] == "queued"
        assert len(progress.json()["source_ids"]) == 3

        # One active job per project
        again = client.post(
            "/v1/projects/mvp/upload-jobs",
            headers=headers,
            files=[("files", ("more.txt", b"Another note.", "text/plain"))],
        )
        assert again.status_code == 409

        workspace = client.get("/v1/projects/mvp/workspace", headers=headers).json()
        assert len(workspace["sources"]) == 3
        assert workspace["latest_run"]["status"] == "queued"

        requirement = client.post(
            "/v1/projects/mvp/requirements/manual",
            headers=headers,
            json={
                "text": "Users can see the status of a request.",
                "rationale": "From the notes.",
                "actor_id": "ba-1",
            },
        ).json()
        links = client.patch(
            f"/v1/projects/mvp/requirements/{requirement['requirement_id']}/artifact-links",
            headers=headers,
            json={"links": {"uc": ["uc-02"], "sq": ["SQ-01"]}, "actor_id": "ba-1"},
        )
        assert links.status_code == 200
        assert links.json()["artifact_links"]["uc"] == ["UC-02"]
        wrong = client.patch(
            f"/v1/projects/mvp/requirements/{requirement['requirement_id']}/artifact-links",
            headers=headers,
            json={"links": {"uc": ["use case 2"]}, "actor_id": "ba-1"},
        )
        assert wrong.status_code == 422

        no_document = client.patch(
            "/v1/projects/mvp/document/sections/0",
            headers=headers,
            json={"content": "Text", "actor_id": "ba-1"},
        )
        assert no_document.status_code == 404
        unknown_item = client.post(
            "/v1/projects/mvp/trace-items/00000000-0000-0000-0000-000000000000/decision",
            headers=headers,
            json={"decision": "merged", "actor_id": "ba-1"},
        )
        assert unknown_item.status_code == 404

    get_repository.cache_clear()
    reset_database_caches()
    get_settings.cache_clear()
    get_analysis_service.cache_clear()
