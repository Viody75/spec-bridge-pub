from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from google.adk.events.event import Event, NodeInfo
from pydantic import ValidationError
from sqlalchemy import select

from app.config import get_settings
from app.domain.schemas import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactLinks,
    ConflictOutput,
    DocumentDraftOutput,
    DocumentSection,
    EvidenceRef,
    ExistingRequirement,
    ExtractionOutput,
    ManualRequirementCreate,
    ModeratorOutput,
    NormalizedSource,
    ProjectCreate,
    RequirementCandidate,
    RtmRow,
    RunProgress,
    TraceCandidateSet,
    TraceDecision,
    TraceOutput,
)
from app.infrastructure.database import get_engine, reset_database_caches
from app.infrastructure.embeddings import get_embedding_service
from app.infrastructure.models import DocumentVersion
from app.infrastructure.repository import ConflictError, Repository, get_repository
from app.services.analysis import ProgressTracker
from app.services.jobs import AnalysisWorker


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
    await repo.create_project(
        ProjectCreate(project_id="mvp", name="MVP", document_type="SRS")
    )
    yield repo
    await get_engine().dispose()
    get_repository.cache_clear()
    get_embedding_service.cache_clear()
    reset_database_caches()
    get_settings.cache_clear()


def _request(source_id: str = "note-1") -> AnalysisRequest:
    return AnalysisRequest(
        project_id="mvp",
        document_type="SRS",
        sources=[
            NormalizedSource(
                source_id=source_id,
                source_type="manual_note",
                title="PO note",
                content="Users reset passwords by email. Sessions end after 15 minutes.",
            )
        ],
    )


def _candidate(number: int, statement: str) -> RequirementCandidate:
    return RequirementCandidate(
        candidate_id=f"CAND-{number:03d}",
        statement=statement,
        kind="functional",
        priority="should",
        source_basis="explicit",
        claim=statement,
        rationale="Stated in the note.",
        role_perspective="business analysis",
        confidence=0.9,
        evidence_refs=[
            EvidenceRef(source_id="note-1", quote=statement, relation="supports")
        ],
    )


def _result(
    run_id: uuid.UUID,
    plan: list[tuple[str, str, str | None, float | None]],
    *,
    section_title: str = "Functional requirements",
) -> AnalysisResult:
    """plan: (statement, classification, existing requirement id, similarity)."""
    candidates = [_candidate(i + 1, item[0]) for i, item in enumerate(plan)]
    return AnalysisResult(
        run_id=str(run_id),
        project_id="mvp",
        status="completed",
        extraction=ExtractionOutput(requirements=candidates),
        trace=TraceOutput(
            decisions=[
                TraceDecision(
                    candidate_id=candidate.candidate_id,
                    classification=classification,
                    existing_requirement_id=(
                        existing if classification != "new" else None
                    ),
                    confidence=0.8,
                    rationale="Compared with existing requirements.",
                )
                for candidate, (_, classification, existing, _) in zip(
                    candidates, plan, strict=True
                )
            ]
        ),
        initial_reviews=[],
        rebuttals=[],
        moderation=ModeratorOutput(
            status="consensus", rationale="All agree.", confidence=0.9
        ),
        conflicts=ConflictOutput(),
        document=DocumentDraftOutput(
            title="SRS",
            document_type="SRS",
            sections=[
                DocumentSection(
                    title=section_title,
                    content="Agent text.",
                    candidate_ids=[c.candidate_id for c in candidates],
                )
            ],
            rtm=[
                RtmRow(
                    candidate_id=c.candidate_id,
                    requirement_statement=c.statement,
                    source_ids=["note-1"],
                    review_status="approve",
                    verification_basis="Test",
                )
                for c in candidates
            ],
        ),
        retrieval=[
            TraceCandidateSet(
                candidate=candidate,
                existing_candidates=(
                    [
                        ExistingRequirement(
                            requirement_id=existing,
                            statement="existing",
                            revision=1,
                            similarity=similarity,
                        )
                    ]
                    if existing
                    else []
                ),
            )
            for candidate, (_, _, existing, similarity) in zip(
                candidates, plan, strict=True
            )
        ],
    )


async def _seed_existing(repository: Repository) -> None:
    await repository.add_manual_requirement(
        "mvp",
        ManualRequirementCreate(
            text="Users can reset their password by email.",
            rationale="Support load.",
            actor_id="po-1",
        ),
    )


async def _persist(repository: Repository, plan, **kwargs) -> uuid.UUID:
    request = _request()
    await repository.add_sources("mvp", request.sources)
    run_id = await repository.create_run(request)
    await repository.persist_result(_result(run_id, plan, **kwargs))
    return run_id


# ---------- queue ----------


@pytest.mark.asyncio
async def test_queued_run_is_claimed_once_and_reports_progress(
    repository: Repository,
) -> None:
    run_id = await repository.create_run(_request(), status="queued")
    queued = await repository.get_run("mvp", run_id)
    assert queued.status == "queued"
    assert queued.progress.stage == "queued"
    assert queued.source_ids == ["note-1"]

    # A queued run blocks a second one, like a running run does
    with pytest.raises(ConflictError):
        await repository.create_run(_request("note-2"))

    assert await repository.claim_next_run() == run_id
    assert await repository.claim_next_run() is None

    await repository.update_run_progress(
        run_id, RunProgress(stage="reviewing", percent=43, agents_done=["designer"])
    )
    running = await repository.get_run("mvp", run_id)
    assert running.status == "running"
    assert running.progress.stage == "reviewing"
    assert running.progress.agents_done == ["designer"]
    assert running.started_at is not None


@pytest.mark.asyncio
async def test_stale_running_run_is_failed(repository: Repository) -> None:
    run_id = await repository.create_run(_request(), status="queued")
    await repository.claim_next_run()

    assert await repository.fail_stale_runs(stale_after_seconds=-1) == 1

    failed = await repository.get_run("mvp", run_id)
    assert failed.status == "failed"
    assert "stopped responding" in (failed.error or "")


@pytest.mark.asyncio
async def test_worker_runs_claimed_job(repository: Repository) -> None:
    class FakeService:
        def __init__(self) -> None:
            self.executed: list[uuid.UUID] = []

        async def execute_claimed(self, run_id, *, runner, app_name):
            self.executed.append(run_id)

    service = FakeService()
    worker = AnalysisWorker(
        runner=object(), app_name="app", service=service, repository=repository
    )
    run_id = await repository.create_run(_request(), status="queued")

    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert service.executed == [run_id]


def test_progress_tracker_follows_workflow_nodes() -> None:
    tracker = ProgressTracker()

    def event(path: str, output=None) -> Event:
        return Event(
            author=path.rsplit("/", 1)[-1].split("@")[0],
            node_info=NodeInfo(path=path),
            output=output,
        )

    assert tracker.observe(event("specbridge_agent@1/trace_linker@1"))
    assert tracker.progress.stage == "trace_linking"
    assert tracker.observe(event("specbridge_agent@1/review1_designer@1", output={}))
    assert tracker.progress.stage == "reviewing"
    assert tracker.progress.agents_done == ["designer"]
    # Late events from an earlier stage never move progress back
    assert not tracker.observe(event("specbridge_agent@1/extractor@1"))
    assert tracker.progress.completed_stages == ["extracting", "trace_linking"]


# ---------- trace linker human decisions ----------


@pytest.mark.asyncio
async def test_trace_placement_and_human_decisions(repository: Repository) -> None:
    await _seed_existing(repository)
    await _persist(
        repository,
        [
            ("Sessions end after 15 minutes of inactivity.", "new", None, None),
            ("Users can reset the password by email.", "duplicates", "FR-01", 0.96),
            (
                "Users reset the password with an email link.",
                "updates",
                "FR-01",
                0.82,
            ),
            ("A reset email is sent to the user.", "new", "FR-01", 0.74),
        ],
    )

    workspace = await repository.get_project_workspace("mvp")
    items = {item.candidate_id: item for item in workspace.trace_items}
    assert items["CAND-001"].outcome == "added"
    assert items["CAND-001"].requirement_id == "FR-02"
    assert items["CAND-002"].outcome == "duplicate"
    assert items["CAND-002"].requirement_id == "FR-01"
    assert items["CAND-003"].outcome == "similar"
    assert (
        items["CAND-003"].match_statement == "Users can reset their password by email."
    )
    assert items["CAND-004"].outcome == "similar"
    assert items["CAND-004"].similarity == pytest.approx(0.74)
    # Nothing is created or revised for similar items until a person decides
    assert [req.requirement_id for req in workspace.requirements] == [
        "FR-01",
        "FR-02",
    ]
    existing = workspace.requirements[0]
    assert existing.revision == 1
    assert [trace.relation for trace in existing.traces] == [
        "Duplicate mention · supports"
    ]

    merged = await repository.decide_trace_item(
        "mvp", items["CAND-003"].item_id, decision="merged", actor_id="ba-1"
    )
    assert merged.decision == "merged"
    assert merged.requirement_id == "FR-01"
    kept = await repository.decide_trace_item(
        "mvp", items["CAND-004"].item_id, decision="kept_new", actor_id="ba-1"
    )
    assert kept.requirement_id == "FR-03"

    with pytest.raises(ConflictError):
        await repository.decide_trace_item(
            "mvp", items["CAND-004"].item_id, decision="merged", actor_id="ba-1"
        )
    with pytest.raises(ConflictError):
        await repository.decide_trace_item(
            "mvp", items["CAND-001"].item_id, decision="merged", actor_id="ba-1"
        )

    workspace = await repository.get_project_workspace("mvp")
    reqs = {req.requirement_id: req for req in workspace.requirements}
    assert reqs["FR-01"].revision == 1
    assert any(
        t.relation.startswith("Merged by a reviewer") for t in reqs["FR-01"].traces
    )
    assert reqs["FR-03"].statement == "A reset email is sent to the user."
    assert reqs["FR-03"].rationale["kept_new_by"] == "ba-1"


# ---------- RTM artifact links ----------


@pytest.mark.asyncio
async def test_artifact_links_are_normalized_and_stored(repository: Repository) -> None:
    await _seed_existing(repository)

    updated = await repository.update_artifact_links(
        "mvp",
        "FR-01",
        links=ArtifactLinks(us=["us-1-02", "US-1-02"], uc=["UC-02"]),
        actor_id="ba-1",
    )

    assert updated.artifact_links.us == ["US-1-02"]
    assert updated.artifact_links.uc == ["UC-02"]
    assert updated.artifact_links.dfd == []
    assert updated.artifact_links_updated_by == "ba-1"


def test_artifact_links_reject_wrong_format() -> None:
    with pytest.raises(ValidationError, match="invalid artifact id"):
        ArtifactLinks(uc=["UC-2"])


# ---------- section body ----------


@pytest.mark.asyncio
async def test_section_edit_survives_reanalysis_and_reopens_published(
    repository: Repository,
) -> None:
    await _persist(repository, [("Sessions end after 15 minutes.", "new", None, None)])

    edited = await repository.update_section_body(
        "mvp", 0, content="Human wording of the section.", actor_id="ba-1"
    )
    assert edited.version == 1
    assert edited.content.sections[0].content == "Human wording of the section."
    assert edited.section_edits["0"].actor_id == "ba-1"

    with pytest.raises(Exception, match="section 3 does not exist"):
        await repository.update_section_body("mvp", 3, content="x", actor_id="ba-1")

    # A new agent run keeps the person's text for the same section title
    await _persist(repository, [("Audit log keeps changes.", "new", None, None)])
    workspace = await repository.get_project_workspace("mvp")
    assert workspace.document.version == 2
    assert (
        workspace.document.content.sections[0].content
        == "Human wording of the section."
    )
    assert workspace.document.section_edits["0"].actor_id == "ba-1"

    # Editing a published version opens a new draft version instead
    async with repository._sessions() as session, session.begin():
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == workspace.document.document_id,
                DocumentVersion.version == workspace.document.version,
            )
        )
        version.published = True
    reopened = await repository.update_section_body(
        "mvp", 0, content="After publication.", actor_id="po-1"
    )
    assert reopened.version == 3
    assert reopened.status == "draft"
    assert reopened.content.sections[0].content == "After publication."
