from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.config import get_settings
from app.domain.schemas import (
    AnalysisRequest,
    ManualRequirementCreate,
    NormalizedSource,
    ProjectCreate,
)
from app.infrastructure.database import get_engine, reset_database_caches
from app.infrastructure.embeddings import get_embedding_service
from app.infrastructure.repository import ConflictError, Repository, get_repository


@pytest_asyncio.fixture
async def repository(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Repository]:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.delenv("TEXT_EMBEDDING_MODEL", raising=False)
    get_settings.cache_clear()
    get_embedding_service.cache_clear()
    get_repository.cache_clear()
    reset_database_caches()
    repo = Repository()
    await repo.create_schema()
    yield repo
    await get_engine().dispose()
    get_repository.cache_clear()
    get_embedding_service.cache_clear()
    reset_database_caches()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_manual_requirement_preserves_human_provenance(
    repository: Repository,
) -> None:
    await repository.create_project(
        ProjectCreate(project_id="mvp", name="MVP", document_type="PRD")
    )
    created = await repository.add_manual_requirement(
        "mvp",
        ManualRequirementCreate(
            text="The Product Owner can add a draft requirement manually.",
            rationale="Human input must remain available.",
            actor_id="po-1",
            priority="must",
        ),
    )
    assert created.requirement_id == "FR-01"
    assert created.revision == 1


@pytest.mark.asyncio
async def test_only_one_active_run_per_project(repository: Repository) -> None:
    await repository.create_project(
        ProjectCreate(project_id="mvp", name="MVP", document_type="PRD")
    )
    request = AnalysisRequest(
        project_id="mvp",
        document_type="PRD",
        sources=[
            NormalizedSource(
                source_id="note-1",
                source_type="manual_note",
                title="PO note",
                content="Users need a visible status.",
            )
        ],
    )
    await repository.create_run(request)
    with pytest.raises(ConflictError):
        await repository.create_run(request)


@pytest.mark.asyncio
async def test_workspace_projection_and_human_requirement_lifecycle(
    repository: Repository,
) -> None:
    await repository.create_project(
        ProjectCreate(
            project_id="mvp",
            name="MVP",
            document_type="PRD",
            policy={
                "project": {
                    "code": "MVP",
                    "owner_id": "pm-1",
                    "approver_id": "po-1",
                    "division": "Product",
                    "target_date": "2026-12-31",
                }
            },
        )
    )
    await repository.add_sources(
        "mvp",
        [
            NormalizedSource(
                source_id="note-1",
                source_type="manual_note",
                title="PO note",
                content="Users need a visible status.",
            )
        ],
    )
    await repository.add_manual_requirement(
        "mvp",
        ManualRequirementCreate(
            text="The Product Owner can add a requirement.",
            rationale="Human input must be preserved.",
            actor_id="po-1",
            priority="must",
        ),
    )

    revised = await repository.revise_requirement(
        "mvp",
        "FR-01",
        statement="The Product Owner can add a draft requirement.",
        rationale="Clarified that the item starts as a draft.",
        actor_id="ba-1",
        priority="must",
    )
    assert revised.revision == 2

    await repository.set_requirement_review(
        "mvp", "FR-01", state="approved", actor_id="ba-1"
    )
    await repository.set_implementation_status(
        "mvp", "FR-01", state="in_progress", actor_id="dev-1"
    )

    workspace = await repository.get_project_workspace("mvp")
    assert workspace.project.code == "MVP"
    assert workspace.project.requirements == 1
    assert workspace.sources[0].source_id == "note-1"
    assert workspace.requirements[0].revision == 2
    assert workspace.requirements[0].review_state == "approved"
    assert workspace.requirements[0].implementation_status == "in_progress"
    assert workspace.requirements[0].source_type == "human_manual"

    projects = await repository.list_projects()
    assert projects[0].open_conflicts == 0
    assert projects[0].requirements == 1
