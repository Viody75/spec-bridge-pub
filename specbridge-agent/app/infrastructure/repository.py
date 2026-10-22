from __future__ import annotations

import functools
import re
import uuid
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.domain.schemas import (
    AnalysisRequest,
    AnalysisResult,
    AnalysisRunView,
    ArtifactLinks,
    ConflictCommentCreate,
    ConflictDecisionCreate,
    ExistingRequirement,
    ManualRequirementCreate,
    NormalizedSource,
    PersistedConflictRef,
    PersistedResources,
    ProductProjectView,
    ProjectCreate,
    ProjectView,
    ProjectWorkspace,
    RequirementCandidate,
    RequirementRef,
    RequirementTraceView,
    RtmRow,
    RunProgress,
    SectionEdit,
    TraceItemView,
    WorkspaceConflictView,
    WorkspaceDocumentView,
    WorkspaceRequirementView,
    WorkspaceSourceView,
)
from app.infrastructure.database import get_engine, get_session_factory
from app.infrastructure.embeddings import EmbeddingService, get_embedding_service
from app.infrastructure.models import (
    AnalysisRun,
    AuditEvent,
    Base,
    Conflict,
    Document,
    DocumentVersion,
    Project,
    ProjectPolicy,
    Requirement,
    RequirementRevision,
    ReviewFinding,
    Source,
    TraceItem,
    TraceLink,
)

FINISHED_RUN_STATUSES = ("completed", "human_context_required")


class NotFoundError(RuntimeError):
    pass


class ConflictError(RuntimeError):
    pass


class Repository:
    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._sessions = get_session_factory()
        self._embeddings = embedding_service or get_embedding_service()

    async def create_schema(self) -> None:
        engine = get_engine()
        async with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                await connection.exec_driver_sql(
                    "CREATE EXTENSION IF NOT EXISTS vector"
                )
            await connection.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        try:
            async with self._sessions() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def create_project(self, payload: ProjectCreate) -> ProjectView:
        policy = merge_project_policy(payload.policy)
        async with self._sessions() as session, session.begin():
            if await session.get(Project, payload.project_id):
                raise ConflictError(f"project {payload.project_id!r} already exists")
            project = Project(
                id=payload.project_id,
                name=payload.name,
                document_type=payload.document_type,
                current_policy_version=1,
            )
            session.add(project)
            session.add(
                ProjectPolicy(
                    project_id=payload.project_id,
                    version=1,
                    policy=policy,
                )
            )
            await session.flush()
            return ProjectView(
                project_id=project.id,
                name=project.name,
                document_type=project.document_type,
                policy=policy,
                created_at=project.created_at,
            )

    async def list_projects(self) -> list[ProductProjectView]:
        async with self._sessions() as session:
            projects = (
                await session.scalars(
                    select(Project).order_by(Project.created_at.desc())
                )
            ).all()
            return [
                await self._product_project_view(session, item) for item in projects
            ]

    async def get_project_workspace(self, project_id: str) -> ProjectWorkspace:
        async with self._sessions() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise NotFoundError(f"project {project_id!r} does not exist")
            policy = await self._policy_for(session, project)
            project_view = await self._product_project_view(
                session, project, policy=policy
            )

            source_rows = (
                await session.scalars(
                    select(Source)
                    .where(Source.project_id == project_id)
                    .order_by(Source.created_at)
                )
            ).all()
            sources = [
                WorkspaceSourceView(
                    source_id=row.source_key,
                    source_type=row.source_type,
                    title=row.title,
                    author=row.author,
                    occurred_at=row.occurred_at,
                    content=row.content,
                    locator=row.locator,
                    metadata=row.source_metadata,
                    created_at=row.created_at,
                )
                for row in source_rows
            ]

            requirement_rows = (
                await session.execute(
                    select(Requirement, RequirementRevision)
                    .join(
                        RequirementRevision,
                        (RequirementRevision.requirement_id == Requirement.id)
                        & (
                            RequirementRevision.revision == Requirement.current_revision
                        ),
                    )
                    .where(Requirement.project_id == project_id)
                    .order_by(Requirement.stable_id)
                )
            ).all()
            requirements = [
                await self._requirement_view(session, requirement, revision)
                for requirement, revision in requirement_rows
            ]

            conflict_rows = (
                await session.scalars(
                    select(Conflict)
                    .where(Conflict.project_id == project_id)
                    .order_by(Conflict.created_at.desc())
                )
            ).all()
            conflicts = [
                WorkspaceConflictView(
                    record_id=row.id,
                    conflict_id=row.external_id,
                    status=row.status,
                    payload=row.payload,
                    decision=row.decision,
                    decision_rationale=row.decision_rationale,
                    decided_by=row.decided_by,
                    decided_at=row.decided_at,
                    comments=row.comments or [],
                    created_at=row.created_at,
                )
                for row in conflict_rows
            ]

            document_row = await session.scalar(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.document_type == project.document_type,
                )
            )
            document_view = None
            if document_row and document_row.current_version:
                version = await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document_row.id,
                        DocumentVersion.version == document_row.current_version,
                    )
                )
                if version:
                    document_view = self._document_view(document_row, version)

            trace_rows = (
                await session.scalars(
                    select(TraceItem)
                    .where(TraceItem.project_id == project_id)
                    .order_by(TraceItem.created_at, TraceItem.candidate_id)
                )
            ).all()
            trace_items = [
                await self._trace_item_view(session, row) for row in trace_rows
            ]

            run = await session.scalar(
                select(AnalysisRun)
                .where(AnalysisRun.project_id == project_id)
                .order_by(AnalysisRun.created_at.desc())
                .limit(1)
            )
            latest_run = self._run_view(run) if run else None

            return ProjectWorkspace(
                project=project_view,
                policy=policy,
                sources=sources,
                requirements=requirements,
                conflicts=conflicts,
                trace_items=trace_items,
                document=document_view,
                latest_run=latest_run,
            )

    async def get_policy(self, project_id: str) -> dict[str, Any]:
        async with self._sessions() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise NotFoundError(f"project {project_id!r} does not exist")
            return await self._policy_for(session, project)

    async def add_sources(
        self, project_id: str, sources: list[NormalizedSource]
    ) -> int:
        async with self._sessions() as session, session.begin():
            if not await session.get(Project, project_id):
                raise NotFoundError(f"project {project_id!r} does not exist")
            return await self._add_sources(session, project_id, sources)

    async def _add_sources(
        self, session: Any, project_id: str, sources: list[NormalizedSource]
    ) -> int:
        existing_keys = set(
            (
                await session.scalars(
                    select(Source.source_key).where(Source.project_id == project_id)
                )
            ).all()
        )
        inserted = 0
        for item in sources:
            values = item.model_dump(mode="json")
            if item.source_id in existing_keys:
                row = await session.scalar(
                    select(Source).where(
                        Source.project_id == project_id,
                        Source.source_key == item.source_id,
                    )
                )
                if row and row.content != item.content:
                    raise ConflictError(
                        f"source {item.source_id!r} already exists with different content"
                    )
                continue
            session.add(
                Source(
                    project_id=project_id,
                    source_key=item.source_id,
                    source_type=item.source_type,
                    title=item.title,
                    author=item.author,
                    occurred_at=item.occurred_at,
                    content=item.content,
                    locator=values.get("locator"),
                    source_metadata=item.metadata,
                )
            )
            existing_keys.add(item.source_id)
            inserted += 1
        return inserted

    async def add_manual_requirement(
        self, project_id: str, payload: ManualRequirementCreate
    ) -> ExistingRequirement:
        embedding = await self._embeddings.embed(payload.text)
        async with self._sessions() as session, session.begin():
            if not await session.get(Project, project_id):
                raise NotFoundError(f"project {project_id!r} does not exist")
            stable_id = await self._next_requirement_id(
                session, project_id, payload.kind
            )
            requirement = Requirement(
                project_id=project_id, stable_id=stable_id, current_revision=1
            )
            session.add(requirement)
            await session.flush()
            session.add(
                RequirementRevision(
                    requirement_id=requirement.id,
                    revision=1,
                    statement=payload.text,
                    priority=payload.priority,
                    source_type="human_manual",
                    created_by=payload.actor_id,
                    rationale={"rationale": payload.rationale, "kind": payload.kind},
                    embedding=embedding,
                )
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor_id=payload.actor_id,
                    event_type="requirement.manual_created",
                    payload={"requirement_id": stable_id},
                )
            )
            return ExistingRequirement(
                requirement_id=stable_id,
                statement=payload.text,
                revision=1,
                similarity=1,
            )

    async def revise_requirement(
        self,
        project_id: str,
        requirement_id: str,
        *,
        statement: str,
        rationale: str,
        actor_id: str,
        priority: str,
    ) -> ExistingRequirement:
        embedding = await self._embeddings.embed(statement)
        async with self._sessions() as session, session.begin():
            requirement = await session.scalar(
                select(Requirement).where(
                    Requirement.project_id == project_id,
                    Requirement.stable_id == requirement_id,
                )
            )
            if not requirement:
                raise NotFoundError(
                    f"requirement {requirement_id!r} does not exist in project {project_id!r}"
                )
            revision_number = requirement.current_revision + 1
            requirement.current_revision = revision_number
            requirement.status = "draft"
            session.add(
                RequirementRevision(
                    requirement_id=requirement.id,
                    revision=revision_number,
                    statement=statement,
                    priority=priority,
                    source_type="human_manual",
                    created_by=actor_id,
                    rationale={"rationale": rationale},
                    embedding=embedding,
                )
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor_id=actor_id,
                    event_type="requirement.revised",
                    payload={
                        "requirement_id": requirement_id,
                        "revision": revision_number,
                    },
                )
            )
            return ExistingRequirement(
                requirement_id=requirement_id,
                statement=statement,
                revision=revision_number,
                similarity=1,
            )

    async def set_requirement_review(
        self,
        project_id: str,
        requirement_id: str,
        *,
        state: str,
        actor_id: str,
    ) -> WorkspaceRequirementView:
        if state not in {"approved", "rejected"}:
            raise ValueError("review state must be approved or rejected")
        async with self._sessions() as session, session.begin():
            requirement = await self._requirement_row(
                session, project_id, requirement_id
            )
            requirement.status = state
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor_id=actor_id,
                    event_type=f"requirement.{state}",
                    payload={"requirement_id": requirement_id},
                )
            )
        return await self.get_requirement_view(project_id, requirement_id)

    async def set_implementation_status(
        self,
        project_id: str,
        requirement_id: str,
        *,
        state: str,
        actor_id: str,
    ) -> WorkspaceRequirementView:
        if state not in {"not_started", "in_progress", "done", "blocked"}:
            raise ValueError("invalid implementation status")
        async with self._sessions() as session, session.begin():
            requirement = await self._requirement_row(
                session, project_id, requirement_id
            )
            requirement.implementation_status = state
            requirement.status_updated_by = actor_id
            requirement.status_updated_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor_id=actor_id,
                    event_type="requirement.implementation_status_changed",
                    payload={"requirement_id": requirement_id, "state": state},
                )
            )
        return await self.get_requirement_view(project_id, requirement_id)

    async def get_requirement_view(
        self, project_id: str, requirement_id: str
    ) -> WorkspaceRequirementView:
        async with self._sessions() as session:
            requirement = await self._requirement_row(
                session, project_id, requirement_id
            )
            revision = await session.scalar(
                select(RequirementRevision).where(
                    RequirementRevision.requirement_id == requirement.id,
                    RequirementRevision.revision == requirement.current_revision,
                )
            )
            if not revision:
                raise NotFoundError(
                    f"current revision for requirement {requirement_id!r} does not exist"
                )
            return await self._requirement_view(session, requirement, revision)

    async def create_run(
        self,
        request: AnalysisRequest,
        *,
        status: Literal["queued", "running"] = "running",
        with_sources: bool = False,
    ) -> uuid.UUID:
        """Create a run; `queued` hands it to the background worker.

        With `with_sources`, the request's sources are stored in the same
        transaction, so a refused run stores nothing and a worker never sees a
        queued run whose sources are missing.
        """
        now = datetime.now(UTC)
        run = AnalysisRun(
            project_id=request.project_id,
            status=status,
            request_payload=request.model_dump(mode="json"),
            model_name=get_settings().model_name,
            progress=RunProgress(
                stage="queued" if status == "queued" else "extracting",
                updated_at=now,
            ).model_dump(mode="json"),
            attempts=0 if status == "queued" else 1,
            started_at=None if status == "queued" else now,
            heartbeat_at=None if status == "queued" else now,
        )
        try:
            async with self._sessions() as session, session.begin():
                if not await session.get(Project, request.project_id):
                    raise NotFoundError(
                        f"project {request.project_id!r} does not exist"
                    )
                session.add(run)
                await session.flush()
                if with_sources:
                    await self._add_sources(
                        session, request.project_id, request.sources
                    )
                return run.id
        except IntegrityError as exc:
            raise ConflictError(
                f"project {request.project_id!r} already has an active analysis run"
            ) from exc

    async def claim_next_run(self) -> uuid.UUID | None:
        """Move the oldest queued run to `running`; safe with several workers on Postgres."""
        async with self._sessions() as session, session.begin():
            query = (
                select(AnalysisRun)
                .where(AnalysisRun.status == "queued")
                .order_by(AnalysisRun.created_at)
                .limit(1)
            )
            if get_engine().dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            run = await session.scalar(query)
            if not run:
                return None
            now = datetime.now(UTC)
            run.status = "running"
            run.attempts += 1
            run.started_at = now
            run.heartbeat_at = now
            run.progress = RunProgress(
                stage="extracting", percent=2, updated_at=now
            ).model_dump(mode="json")
            return run.id

    async def get_run_request(self, run_id: uuid.UUID) -> AnalysisRequest:
        async with self._sessions() as session:
            run = await session.get(AnalysisRun, run_id)
            if not run:
                raise NotFoundError(f"analysis run {run_id} does not exist")
            return AnalysisRequest.model_validate(run.request_payload)

    async def update_run_progress(
        self, run_id: uuid.UUID, progress: RunProgress
    ) -> None:
        """Store the latest stage; doubles as the worker heartbeat."""
        async with self._sessions() as session, session.begin():
            run = await session.get(AnalysisRun, run_id)
            if run and run.status == "running":
                run.progress = progress.model_dump(mode="json")
                run.heartbeat_at = datetime.now(UTC)

    async def fail_stale_runs(self, stale_after_seconds: float) -> int:
        """Fail runs whose worker stopped sending heartbeats (instance restarted)."""
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        async with self._sessions() as session, session.begin():
            runs = (
                await session.scalars(
                    select(AnalysisRun).where(
                        AnalysisRun.status == "running",
                        AnalysisRun.heartbeat_at < cutoff,
                    )
                )
            ).all()
            for run in runs:
                self._mark_failed(
                    run,
                    "The analysis worker stopped responding. Run the analysis again.",
                )
            return len(runs)

    async def fail_run(self, run_id: uuid.UUID, error: str) -> None:
        async with self._sessions() as session, session.begin():
            run = await session.get(AnalysisRun, run_id)
            if run:
                self._mark_failed(run, error)

    @staticmethod
    def _mark_failed(run: AnalysisRun, error: str) -> None:
        progress = RunProgress.model_validate(run.progress or {})
        progress.message = "Analysis failed"
        progress.updated_at = datetime.now(UTC)
        run.status = "failed"
        run.error = error[:10_000]
        run.progress = progress.model_dump(mode="json")
        run.completed_at = datetime.now(UTC)

    async def get_run(self, project_id: str, run_id: uuid.UUID) -> AnalysisRunView:
        async with self._sessions() as session:
            run = await session.get(AnalysisRun, run_id)
            if not run or run.project_id != project_id:
                raise NotFoundError(f"analysis run {run_id} does not exist")
            return self._run_view(run)

    @staticmethod
    def _run_view(run: AnalysisRun, *, include_result: bool = True) -> AnalysisRunView:
        progress = RunProgress.model_validate(run.progress or {})
        # Runs stored before progress tracking existed
        if run.status in FINISHED_RUN_STATUSES and progress.stage != "done":
            progress = RunProgress(
                stage="done", percent=100, updated_at=run.completed_at
            )
        return AnalysisRunView(
            run_id=run.id,
            project_id=run.project_id,
            status=run.status,
            result=(
                AnalysisResult.model_validate(run.result_payload)
                if include_result and run.result_payload
                else None
            ),
            error=run.error,
            progress=progress,
            source_ids=[
                str(item.get("source_id"))
                for item in (run.request_payload or {}).get("sources", [])
            ],
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    async def find_similar_requirements(
        self, project_id: str, statement: str, limit: int
    ) -> list[ExistingRequirement]:
        embedding = await self._embeddings.embed(statement, task_type="RETRIEVAL_QUERY")
        async with self._sessions() as session:
            base = (
                select(Requirement, RequirementRevision)
                .join(
                    RequirementRevision,
                    (RequirementRevision.requirement_id == Requirement.id)
                    & (RequirementRevision.revision == Requirement.current_revision),
                )
                .where(Requirement.project_id == project_id)
            )
            lexical_rows = (await session.execute(base)).all()
            scored: dict[str, ExistingRequirement] = {}
            for requirement, revision in lexical_rows:
                ratio = SequenceMatcher(
                    None, statement.casefold(), revision.statement.casefold()
                ).ratio()
                scored[requirement.stable_id] = ExistingRequirement(
                    requirement_id=requirement.stable_id,
                    statement=revision.statement,
                    revision=revision.revision,
                    similarity=ratio,
                )

            if embedding is not None and get_engine().dialect.name == "postgresql":
                distance = RequirementRevision.embedding.cosine_distance(embedding)
                vector_query = (
                    select(Requirement, RequirementRevision, distance.label("distance"))
                    .join(
                        RequirementRevision,
                        (RequirementRevision.requirement_id == Requirement.id)
                        & (
                            RequirementRevision.revision == Requirement.current_revision
                        ),
                    )
                    .where(
                        Requirement.project_id == project_id,
                        RequirementRevision.embedding.is_not(None),
                    )
                    .order_by(distance)
                    .limit(limit)
                )
                for requirement, revision, vector_distance in (
                    await session.execute(vector_query)
                ).all():
                    vector_similarity = max(0.0, 1.0 - float(vector_distance))
                    current = scored.get(requirement.stable_id)
                    if current is None or vector_similarity > (current.similarity or 0):
                        scored[requirement.stable_id] = ExistingRequirement(
                            requirement_id=requirement.stable_id,
                            statement=revision.statement,
                            revision=revision.revision,
                            similarity=vector_similarity,
                        )

            return sorted(
                scored.values(), key=lambda item: item.similarity or 0, reverse=True
            )[:limit]

    async def persist_result(
        self, result: AnalysisResult, *, progress: RunProgress | None = None
    ) -> PersistedResources:
        run_id = uuid.UUID(result.run_id)
        candidate_by_id = {
            item.candidate_id: item for item in result.extraction.requirements
        }
        decision_by_id = {item.candidate_id: item for item in result.trace.decisions}
        embeddings = {
            candidate_id: await self._embeddings.embed(candidate.statement)
            for candidate_id, candidate in candidate_by_id.items()
        }

        async with self._sessions() as session, session.begin():
            run = await session.get(AnalysisRun, run_id)
            if not run:
                raise NotFoundError(f"analysis run {result.run_id} does not exist")

            sources = await self._sources_by_key(session, result.project_id)

            settings = get_settings()
            # Candidate → requirement it became (or duplicates); similar ones wait
            placed: dict[str, str] = {}
            retrieval = {
                item.candidate.candidate_id: item.existing_candidates
                for item in result.retrieval
            }
            for candidate_id, candidate in candidate_by_id.items():
                decision = decision_by_id[candidate_id]
                retrieved = retrieval.get(candidate_id, [])
                target_id = decision.existing_requirement_id
                if target_id is None and retrieved:
                    # "new" still needs a person when retrieval found a close match
                    top = max(retrieved, key=lambda item: item.similarity or 0)
                    if (top.similarity or 0) >= settings.similarity_review_threshold:
                        target_id = top.requirement_id
                similarity = next(
                    (
                        item.similarity
                        for item in retrieved
                        if item.requirement_id == target_id
                    ),
                    None,
                )
                target = (
                    await self._requirement_with_revision(
                        session, result.project_id, target_id
                    )
                    if target_id
                    else None
                )

                if target is None:
                    outcome = "added"
                elif decision.classification == "duplicates" and (
                    similarity is None
                    or similarity >= settings.similarity_duplicate_threshold
                ):
                    outcome = "duplicate"
                else:
                    # updates, possible conflicts and close matches wait for a person
                    outcome = "similar"

                requirement_id: str | None = None
                if outcome == "added":
                    requirement_id = await self._create_agent_requirement(
                        session,
                        result.project_id,
                        candidate,
                        sources,
                        embedding=embeddings[candidate_id],
                    )
                elif outcome == "duplicate" and target:
                    requirement, revision = target
                    self._add_trace_links(
                        session,
                        revision.id,
                        candidate,
                        sources,
                        relation_prefix="Duplicate mention",
                    )
                    requirement_id = requirement.stable_id

                if requirement_id:
                    placed[candidate_id] = requirement_id
                session.add(
                    TraceItem(
                        project_id=result.project_id,
                        run_id=run_id,
                        candidate_id=candidate_id,
                        classification=decision.classification,
                        outcome=outcome,
                        match_requirement_id=target[0].stable_id if target else None,
                        similarity=similarity,
                        confidence=decision.confidence,
                        rationale=decision.rationale,
                        requirement_id=requirement_id,
                        candidate=candidate.model_dump(mode="json"),
                    )
                )

            for review in [*result.initial_reviews, *result.rebuttals]:
                for finding in review.findings:
                    session.add(
                        ReviewFinding(
                            run_id=run_id,
                            role=review.role,
                            round_number=review.round,
                            candidate_id=finding.candidate_id,
                            verdict=finding.verdict,
                            severity=finding.severity,
                            payload=finding.model_dump(mode="json"),
                        )
                    )

            conflict_rows = [
                Conflict(
                    project_id=result.project_id,
                    run_id=run_id,
                    external_id=conflict.conflict_id,
                    payload=conflict.model_dump(mode="json"),
                )
                for conflict in result.conflicts.conflicts
            ]
            session.add_all(conflict_rows)
            await session.flush()

            document = await session.scalar(
                select(Document).where(
                    Document.project_id == result.project_id,
                    Document.document_type == result.document.document_type,
                )
            )
            if document is None:
                document = Document(
                    project_id=result.project_id,
                    document_type=result.document.document_type,
                )
                session.add(document)
                await session.flush()
            previous = (
                await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.version == document.current_version,
                    )
                )
                if document.current_version
                else None
            )
            content, section_edits = carry_human_section_edits(
                replace_candidate_ids(result.document.model_dump(mode="json"), placed),
                previous,
            )
            await self._apply_agent_trace_links(
                session, result.project_id, content.get("traceability", [])
            )
            document.current_version += 1
            document_version = DocumentVersion(
                document_id=document.id,
                run_id=run_id,
                version=document.current_version,
                content=content,
                rtm=[row.model_dump(mode="json") for row in result.document.rtm],
                section_edits=section_edits,
            )
            session.add(document_version)

            resources = PersistedResources(
                document_id=document.id,
                document_version=document.current_version,
                conflicts=[
                    PersistedConflictRef(
                        conflict_id=row.external_id,
                        record_id=row.id,
                    )
                    for row in conflict_rows
                ],
            )
            result.resources = resources

            run.status = result.status
            run.result_payload = result.model_dump(mode="json")
            run.progress = (
                progress or RunProgress(stage="done", percent=100)
            ).model_dump(mode="json")
            run.completed_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=result.project_id,
                    actor_id="specbridge_agent",
                    event_type="analysis.completed",
                    payload={"run_id": str(result.run_id), "status": result.status},
                )
            )
            return resources

    async def decide_conflict(
        self, conflict_id: uuid.UUID, payload: ConflictDecisionCreate
    ) -> None:
        async with self._sessions() as session, session.begin():
            conflict = await session.get(Conflict, conflict_id)
            if not conflict:
                raise NotFoundError(f"conflict {conflict_id} does not exist")
            if conflict.status == "resolved":
                raise ConflictError(f"conflict {conflict_id} is already resolved")
            conflict.status = "resolved"
            conflict.decision = payload.decision
            conflict.decision_rationale = payload.rationale
            conflict.decided_by = payload.actor_id
            conflict.decided_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=conflict.project_id,
                    actor_id=payload.actor_id,
                    event_type="conflict.resolved",
                    payload={"conflict_id": str(conflict_id)},
                )
            )

    async def defer_conflict(self, conflict_id: uuid.UUID, actor_id: str) -> None:
        async with self._sessions() as session, session.begin():
            conflict = await session.get(Conflict, conflict_id)
            if not conflict:
                raise NotFoundError(f"conflict {conflict_id} does not exist")
            if conflict.status == "resolved":
                raise ConflictError(f"conflict {conflict_id} is already resolved")
            conflict.status = "deferred"
            session.add(
                AuditEvent(
                    project_id=conflict.project_id,
                    actor_id=actor_id,
                    event_type="conflict.deferred",
                    payload={"conflict_id": str(conflict_id)},
                )
            )

    async def add_conflict_comment(
        self, conflict_id: uuid.UUID, payload: ConflictCommentCreate
    ) -> None:
        async with self._sessions() as session, session.begin():
            conflict = await session.get(Conflict, conflict_id)
            if not conflict:
                raise NotFoundError(f"conflict {conflict_id} does not exist")
            comments = list(conflict.comments or [])
            comments.append(
                {
                    "comment_id": str(uuid.uuid4()),
                    "actor_id": payload.actor_id,
                    "text": payload.text,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            conflict.comments = comments
            session.add(
                AuditEvent(
                    project_id=conflict.project_id,
                    actor_id=payload.actor_id,
                    event_type="conflict.commented",
                    payload={"conflict_id": str(conflict_id)},
                )
            )

    async def publish_document(
        self, document_id: uuid.UUID, actor_id: str, decision_note: str
    ) -> None:
        async with self._sessions() as session, session.begin():
            document = await session.get(Document, document_id)
            if not document:
                raise NotFoundError(f"document {document_id} does not exist")
            open_conflicts = await session.scalar(
                select(func.count(Conflict.id)).where(
                    Conflict.project_id == document.project_id,
                    Conflict.status == "open",
                )
            )
            if open_conflicts:
                raise ConflictError("document cannot be published with open conflicts")
            pending_requirements = await session.scalar(
                select(func.count(Requirement.id)).where(
                    Requirement.project_id == document.project_id,
                    Requirement.status != "approved",
                )
            )
            if pending_requirements:
                raise ConflictError(
                    "document cannot be published until every requirement is approved by a person"
                )
            version = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version == document.current_version,
                )
            )
            if not version:
                raise ConflictError("document has no draft version to publish")
            run = await session.get(AnalysisRun, version.run_id)
            if not run or run.status != "completed":
                raise ConflictError(
                    "document cannot be published until analysis reaches consensus"
                )
            version.published = True
            version.published_by = actor_id
            version.published_at = datetime.now(UTC)
            document.status = "final"
            document.approval_decision = "approved"
            document.decision_note = decision_note
            document.decided_by = actor_id
            document.decided_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=document.project_id,
                    actor_id=actor_id,
                    event_type="document.published",
                    payload={
                        "document_id": str(document.id),
                        "version": document.current_version,
                        "decision_note": decision_note,
                    },
                )
            )

    async def request_document_changes(
        self, document_id: uuid.UUID, actor_id: str, decision_note: str
    ) -> None:
        async with self._sessions() as session, session.begin():
            document = await session.get(Document, document_id)
            if not document:
                raise NotFoundError(f"document {document_id} does not exist")
            document.status = "draft"
            document.approval_decision = "changes_requested"
            document.decision_note = decision_note
            document.decided_by = actor_id
            document.decided_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=document.project_id,
                    actor_id=actor_id,
                    event_type="document.changes_requested",
                    payload={
                        "document_id": str(document.id),
                        "version": document.current_version,
                        "decision_note": decision_note,
                    },
                )
            )

    async def get_writer_context(
        self, project_id: str
    ) -> tuple[str, list[RequirementRef]]:
        """Project name and every current, not rejected requirement for the writers.

        The workflow can also run on its own (eval, playground, A2A) for a project
        that is not stored; it then has no earlier requirements.
        """
        async with self._sessions() as session:
            project = await session.get(Project, project_id)
            if not project:
                return project_id, []
            rows = (
                await session.execute(
                    select(Requirement, RequirementRevision)
                    .join(
                        RequirementRevision,
                        (RequirementRevision.requirement_id == Requirement.id)
                        & (
                            RequirementRevision.revision == Requirement.current_revision
                        ),
                    )
                    .where(
                        Requirement.project_id == project_id,
                        Requirement.status != "rejected",
                    )
                    .order_by(Requirement.stable_id)
                )
            ).all()
            refs = [
                RequirementRef(
                    requirement_id=requirement.stable_id,
                    statement=revision.statement,
                    kind=requirement_kind(requirement.stable_id, revision.rationale),
                    priority=revision.priority,
                    origin="existing",
                )
                for requirement, revision in rows
            ]
            return project.name, refs

    async def decide_trace_item(
        self,
        project_id: str,
        item_id: uuid.UUID,
        *,
        decision: Literal["merged", "kept_new"],
        actor_id: str,
    ) -> TraceItemView:
        """A person settles a `similar` match: link as evidence, or keep as new."""
        async with self._sessions() as session:
            item = await session.get(TraceItem, item_id)
            if not item or item.project_id != project_id:
                raise NotFoundError(f"trace item {item_id} does not exist")
            statement = str(item.candidate.get("statement", ""))
        embedding = (
            await self._embeddings.embed(statement) if decision == "kept_new" else None
        )

        async with self._sessions() as session, session.begin():
            item = await session.get(TraceItem, item_id, with_for_update=True)
            if not item or item.project_id != project_id:
                raise NotFoundError(f"trace item {item_id} does not exist")
            if item.outcome != "similar":
                raise ConflictError("only similar items need a decision")
            if item.decision:
                raise ConflictError(f"trace item {item_id} is already decided")
            candidate = RequirementCandidate.model_validate(item.candidate)
            sources = await self._sources_by_key(session, project_id)

            if decision == "merged":
                target = await self._requirement_with_revision(
                    session, project_id, item.match_requirement_id
                )
                if target is None:
                    raise ConflictError(
                        f"requirement {item.match_requirement_id!r} no longer exists; keep the item as new instead"
                    )
                requirement, revision = target
                self._add_trace_links(
                    session,
                    revision.id,
                    candidate,
                    sources,
                    relation_prefix="Merged by a reviewer",
                )
                item.requirement_id = requirement.stable_id
            else:
                item.requirement_id = await self._create_agent_requirement(
                    session,
                    project_id,
                    candidate,
                    sources,
                    embedding=embedding,
                    kept_new_by=actor_id,
                )
            item.decision = decision
            item.decided_by = actor_id
            item.decided_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor_id=actor_id,
                    event_type=f"trace_item.{decision}",
                    payload={
                        "trace_item_id": str(item.id),
                        "candidate_id": item.candidate_id,
                        "requirement_id": item.requirement_id,
                        "match_requirement_id": item.match_requirement_id,
                    },
                )
            )
            await session.flush()
            return await self._trace_item_view(session, item)

    async def update_artifact_links(
        self,
        project_id: str,
        requirement_id: str,
        *,
        links: ArtifactLinks,
        actor_id: str,
    ) -> WorkspaceRequirementView:
        """A person corrects the RTM row (user story, use case, diagram IDs)."""
        async with self._sessions() as session, session.begin():
            requirement = await self._requirement_row(
                session, project_id, requirement_id
            )
            before = requirement.artifact_links or {}
            after = links.model_dump(mode="json")
            if before != after:
                requirement.artifact_links = after
                requirement.artifact_links_updated_by = actor_id
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor_id=actor_id,
                        event_type="requirement.artifact_links_changed",
                        payload={
                            "requirement_id": requirement_id,
                            "before": before,
                            "after": after,
                        },
                    )
                )
        return await self.get_requirement_view(project_id, requirement_id)

    async def update_section_body(
        self,
        project_id: str,
        section_index: int,
        *,
        content: str,
        actor_id: str,
    ) -> WorkspaceDocumentView:
        """A person rewrites a section body of the current draft.

        A published version is never changed: the edit opens a new draft version.
        The previous text is kept in the audit event.
        """
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id)
            if not project:
                raise NotFoundError(f"project {project_id!r} does not exist")
            document = await session.scalar(
                select(Document).where(
                    Document.project_id == project_id,
                    Document.document_type == project.document_type,
                )
            )
            version = (
                await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.version == document.current_version,
                    )
                )
                if document
                else None
            )
            if not document or not version:
                raise NotFoundError(
                    f"project {project_id!r} has no document yet; run an analysis first"
                )
            sections = list(version.content.get("sections", []))
            if not 0 <= section_index < len(sections):
                raise NotFoundError(f"section {section_index} does not exist")
            before = str(sections[section_index].get("content", ""))
            text = content.strip()
            if text == before.strip():
                return self._document_view(document, version)

            if version.published:
                document.current_version += 1
                version = DocumentVersion(
                    document_id=document.id,
                    run_id=version.run_id,
                    version=document.current_version,
                    content=version.content,
                    rtm=version.rtm,
                    section_edits=dict(version.section_edits or {}),
                )
                session.add(version)
                document.status = "draft"
                document.approval_decision = "pending"

            sections[section_index] = {**sections[section_index], "content": text}
            # Assign new objects so the JSON columns are flagged as changed
            version.content = {**version.content, "sections": sections}
            version.section_edits = {
                **(version.section_edits or {}),
                str(section_index): SectionEdit(
                    actor_id=actor_id, edited_at=datetime.now(UTC)
                ).model_dump(mode="json"),
            }
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor_id=actor_id,
                    event_type="document.section_edited",
                    payload={
                        "document_id": str(document.id),
                        "version": document.current_version,
                        "section_index": section_index,
                        "section_title": sections[section_index].get("title"),
                        "before": before,
                        "after": text,
                    },
                )
            )
            await session.flush()
            return self._document_view(document, version)

    async def _apply_agent_trace_links(
        self, session: Any, project_id: str, rows: list[dict[str, Any]]
    ) -> None:
        """RTM links from the agents' artifacts; a person's correction is never replaced."""
        by_id = {row["requirement_id"]: row for row in rows}
        requirements = (
            await session.scalars(
                select(Requirement).where(Requirement.project_id == project_id)
            )
        ).all()
        for requirement in requirements:
            if requirement.artifact_links_updated_by not in (None, AGENT_ACTOR):
                continue
            row = by_id.get(requirement.stable_id)
            links = (
                ArtifactLinks.model_validate(
                    {key: row.get(key, []) for key in ARTIFACT_LINK_KEYS}
                ).model_dump(mode="json")
                if row
                else {}
            )
            if links != (requirement.artifact_links or {}):
                requirement.artifact_links = links
                requirement.artifact_links_updated_by = AGENT_ACTOR if links else None

    async def _requirement_with_revision(
        self, session: Any, project_id: str, requirement_id: str | None
    ) -> tuple[Requirement, RequirementRevision] | None:
        if not requirement_id:
            return None
        row = (
            await session.execute(
                select(Requirement, RequirementRevision)
                .join(
                    RequirementRevision,
                    (RequirementRevision.requirement_id == Requirement.id)
                    & (RequirementRevision.revision == Requirement.current_revision),
                )
                .where(
                    Requirement.project_id == project_id,
                    Requirement.stable_id == requirement_id,
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def _sources_by_key(self, session: Any, project_id: str) -> dict[str, Source]:
        rows = (
            await session.scalars(select(Source).where(Source.project_id == project_id))
        ).all()
        return {source.source_key: source for source in rows}

    async def _create_agent_requirement(
        self,
        session: Any,
        project_id: str,
        candidate: RequirementCandidate,
        sources: dict[str, Source],
        *,
        embedding: list[float] | None,
        kept_new_by: str | None = None,
    ) -> str:
        requirement = Requirement(
            project_id=project_id,
            stable_id=await self._next_requirement_id(
                session, project_id, candidate.kind
            ),
            current_revision=1,
        )
        session.add(requirement)
        await session.flush()
        rationale = candidate.model_dump(mode="json")
        if kept_new_by:
            rationale["kept_new_by"] = kept_new_by
        revision = RequirementRevision(
            requirement_id=requirement.id,
            revision=1,
            statement=candidate.statement,
            priority=candidate.priority,
            source_type="agent",
            created_by="specbridge_agent",
            rationale=rationale,
            embedding=embedding,
        )
        session.add(revision)
        await session.flush()
        self._add_trace_links(session, revision.id, candidate, sources)
        return requirement.stable_id

    @staticmethod
    def _add_trace_links(
        session: Any,
        revision_id: uuid.UUID,
        candidate: RequirementCandidate,
        sources: dict[str, Source],
        *,
        relation_prefix: str | None = None,
    ) -> None:
        for evidence in candidate.evidence_refs:
            source = sources.get(evidence.source_id)
            if not source:
                continue
            session.add(
                TraceLink(
                    requirement_revision_id=revision_id,
                    source_id=source.id,
                    relation=(
                        f"{relation_prefix} · {evidence.relation}"[:500]
                        if relation_prefix
                        else evidence.relation
                    ),
                    quote=evidence.quote,
                    locator=(
                        evidence.locator.model_dump(mode="json")
                        if evidence.locator
                        else None
                    ),
                    confidence=candidate.confidence,
                )
            )

    async def _trace_item_view(self, session: Any, item: TraceItem) -> TraceItemView:
        match = await self._requirement_with_revision(
            session, item.project_id, item.match_requirement_id
        )
        return TraceItemView(
            item_id=item.id,
            run_id=item.run_id,
            candidate_id=item.candidate_id,
            statement=str(item.candidate.get("statement", "")),
            source_ids=list(
                dict.fromkeys(
                    str(ref.get("source_id"))
                    for ref in item.candidate.get("evidence_refs", [])
                )
            ),
            classification=item.classification,
            outcome=item.outcome,
            match_requirement_id=item.match_requirement_id,
            match_statement=match[1].statement if match else None,
            similarity=item.similarity,
            confidence=item.confidence,
            rationale=item.rationale,
            requirement_id=item.requirement_id,
            decision=item.decision,
            decided_by=item.decided_by,
            decided_at=item.decided_at,
        )

    @staticmethod
    def _document_view(
        document: Document, version: DocumentVersion
    ) -> WorkspaceDocumentView:
        return WorkspaceDocumentView(
            document_id=document.id,
            document_type=document.document_type,
            status=document.status,
            version=version.version,
            content=version.content,
            rtm=[RtmRow.model_validate(item) for item in version.rtm],
            published=version.published,
            published_by=version.published_by,
            published_at=version.published_at,
            approval_decision=document.approval_decision,
            decision_note=document.decision_note,
            decided_by=document.decided_by,
            decided_at=document.decided_at,
            section_edits=version.section_edits or {},
            created_at=version.created_at,
        )

    async def _next_requirement_id(
        self, session: Any, project_id: str, kind: str = "functional"
    ) -> str:
        """FR-XX or NF-XX, as the SRS numbering rules ask."""
        prefix = requirement_prefix(kind)
        existing = (
            await session.scalars(
                select(Requirement.stable_id).where(
                    Requirement.project_id == project_id,
                    Requirement.stable_id.like(f"{prefix}-%"),
                )
            )
        ).all()
        numbers = [
            int(value.split("-", 1)[1])
            for value in existing
            if value.split("-", 1)[1].isdigit()
        ]
        return f"{prefix}-{max(numbers, default=0) + 1:02d}"

    async def _policy_for(self, session: Any, project: Project) -> dict[str, Any]:
        policy = await session.scalar(
            select(ProjectPolicy.policy).where(
                ProjectPolicy.project_id == project.id,
                ProjectPolicy.version == project.current_policy_version,
            )
        )
        return merge_project_policy(policy or {})

    async def _product_project_view(
        self,
        session: Any,
        project: Project,
        *,
        policy: dict[str, Any] | None = None,
    ) -> ProductProjectView:
        active_policy = policy or await self._policy_for(session, project)
        project_meta = active_policy.get("project", {})
        requirement_count = int(
            await session.scalar(
                select(func.count(Requirement.id)).where(
                    Requirement.project_id == project.id
                )
            )
            or 0
        )
        open_conflicts = int(
            await session.scalar(
                select(func.count(Conflict.id)).where(
                    Conflict.project_id == project.id,
                    Conflict.status == "open",
                )
            )
            or 0
        )
        document = await session.scalar(
            select(Document).where(
                Document.project_id == project.id,
                Document.document_type == project.document_type,
            )
        )
        created_date = project.created_at.date().isoformat()
        return ProductProjectView(
            id=project.id,
            name=project.name,
            code=project_meta.get("code") or project.id.upper(),
            description=project_meta.get("description"),
            document_type=project.document_type,
            document_version=document.current_version if document else 0,
            status=document.status if document else "draft",
            requirements=requirement_count,
            open_conflicts=open_conflicts,
            owner_id=project_meta.get("owner_id") or "unassigned",
            approver_id=project_meta.get("approver_id") or "unassigned",
            division=project_meta.get("division") or "Unassigned",
            start_date=project_meta.get("start_date") or created_date,
            target_date=project_meta.get("target_date") or created_date,
            source_types=project_meta.get("source_types") or [],
            active_agents=project_meta.get("active_agents")
            or list(active_policy.get("roles", {})),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    async def _requirement_row(
        self, session: Any, project_id: str, requirement_id: str
    ) -> Requirement:
        requirement = await session.scalar(
            select(Requirement).where(
                Requirement.project_id == project_id,
                Requirement.stable_id == requirement_id,
            )
        )
        if not requirement:
            raise NotFoundError(
                f"requirement {requirement_id!r} does not exist in project {project_id!r}"
            )
        return requirement

    async def _requirement_view(
        self,
        session: Any,
        requirement: Requirement,
        revision: RequirementRevision,
    ) -> WorkspaceRequirementView:
        trace_rows = (
            await session.execute(
                select(TraceLink, Source)
                .join(Source, Source.id == TraceLink.source_id)
                .where(TraceLink.requirement_revision_id == revision.id)
                .order_by(TraceLink.created_at)
            )
        ).all()
        traces = [
            RequirementTraceView(
                source_id=source.source_key,
                quote=trace.quote,
                locator=trace.locator,
                relation=trace.relation,
                confidence=trace.confidence,
            )
            for trace, source in trace_rows
        ]
        return WorkspaceRequirementView(
            requirement_id=requirement.stable_id,
            statement=revision.statement,
            revision=revision.revision,
            priority=revision.priority,
            source_type=revision.source_type,
            created_by=revision.created_by,
            rationale=revision.rationale,
            review_state=requirement.status,
            implementation_status=requirement.implementation_status,
            status_updated_by=requirement.status_updated_by,
            status_updated_at=requirement.status_updated_at,
            traces=traces,
            artifact_links=ArtifactLinks.model_validate(
                requirement.artifact_links or {}
            ),
            artifact_links_updated_by=requirement.artifact_links_updated_by,
            created_at=revision.created_at,
            updated_at=requirement.updated_at,
        )


AGENT_ACTOR = "specbridge_agent"
ARTIFACT_LINK_KEYS = ("us", "uc", "ad", "cd", "sq", "dfd")
CANDIDATE_ID = re.compile(r"\bCAND-[0-9]{3,}\b")


def requirement_prefix(kind: str) -> str:
    return "NF" if kind in {"non_functional", "constraint"} else "FR"


def requirement_kind(stable_id: str, rationale: dict[str, Any]) -> str:
    kind = rationale.get("kind")
    if kind in {"functional", "non_functional", "constraint", "business_rule"}:
        return kind
    return "non_functional" if stable_id.startswith("NF-") else "functional"


def replace_candidate_ids(value: Any, placed: dict[str, str]) -> Any:
    """Swap the run's CAND-NNN placeholders for final requirement IDs, everywhere."""
    if isinstance(value, str):
        return CANDIDATE_ID.sub(
            lambda match: placed.get(match.group(0), match.group(0)), value
        )
    if isinstance(value, list):
        return [replace_candidate_ids(item, placed) for item in value]
    if isinstance(value, dict):
        return {key: replace_candidate_ids(item, placed) for key, item in value.items()}
    return value


def carry_human_section_edits(
    content: dict[str, Any], previous: DocumentVersion | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep sections a person rewrote when the agents write a new version.

    A human-edited section replaces the agent text of the section with the same
    title; one the agents no longer produce is appended so the edit is not lost.
    """
    if previous is None or not previous.section_edits:
        return content, {}
    sections = list(content.get("sections", []))
    previous_sections = previous.content.get("sections", [])
    by_title = {
        str(section.get("title", "")).strip().casefold(): index
        for index, section in enumerate(sections)
    }
    edits: dict[str, Any] = {}
    for key, edit in previous.section_edits.items():
        index = int(key)
        if index >= len(previous_sections):
            continue
        edited = previous_sections[index]
        title = str(edited.get("title", "")).strip().casefold()
        target = by_title.get(title)
        if target is None:
            sections.append({**edited, "candidate_ids": []})
            target = len(sections) - 1
        else:
            sections[target] = {
                **sections[target],
                "content": edited.get("content", ""),
            }
        edits[str(target)] = edit
    return {**content, "sections": sections}, edits


def default_project_policy() -> dict[str, Any]:
    return {
        "standard": "ISO/IEC/IEEE 29148 §5.2.5",
        "roles": {
            "business_analyst": ["necessary", "correct", "complete"],
            "project_manager": ["scope", "schedule", "risk", "value"],
            "developer": ["feasible", "constraints", "what_not_how"],
            "quality_assurance": ["verifiable", "measurable", "unambiguous"],
            "designer": ["usability", "accessibility", "workflow_fit"],
        },
        "max_deliberation_rounds": 2,
        "human_gates": ["conflict_resolution", "document_publication"],
        # Language of requirement statements and of the SRS (template is Indonesian)
        "document_language": "Bahasa Indonesia",
    }


def merge_project_policy(policy: dict[str, Any]) -> dict[str, Any]:
    merged = default_project_policy()
    for key, value in policy.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


@functools.cache
def get_repository() -> Repository:
    return Repository()
