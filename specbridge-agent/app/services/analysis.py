from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from google.adk.events.event import Event
from google.adk.runners import Runner
from google.genai import types
from pydantic import ValidationError

from app.config import get_settings
from app.domain.schemas import (
    RUN_STAGES,
    AnalysisRequest,
    AnalysisResult,
    RunProgress,
    RunStage,
    RunUsage,
    StageUsage,
    WorkflowInput,
)
from app.infrastructure.repository import Repository, get_repository

logger = logging.getLogger(__name__)

# Workflow node name -> public stage. Node names come from app/workflow/pipeline.py.
NODE_STAGES: dict[str, RunStage] = {
    "prepare_workflow_input": "extracting",
    "extractor": "extracting",
    "build_trace_input": "trace_linking",
    "trace_linker": "trace_linking",
    "build_review_input": "reviewing",
    "join_initial_reviews": "reviewing",
    "collect_initial_reviews": "rebutting",
    "join_rebuttals": "rebutting",
    "collect_rebuttals": "moderating",
    "moderator": "moderating",
    "build_conflict_input": "detecting_conflicts",
    "conflict_detector": "detecting_conflicts",
    "build_document_input": "writing_document",
    "document_writer": "writing_document",
    "story_writer": "writing_document",
    "join_writers": "modeling",
    "build_diagram_input": "modeling",
    "diagram_writer": "modeling",
    "finalize_result": "modeling",
}

STAGE_PERCENT: dict[RunStage, int] = {
    "queued": 0,
    "extracting": 8,
    "trace_linking": 22,
    "reviewing": 35,
    "rebutting": 55,
    "moderating": 75,
    "detecting_conflicts": 82,
    "writing_document": 85,
    "modeling": 92,
    "persisting": 97,
    "done": 100,
}

STAGE_MESSAGES: dict[RunStage, str] = {
    "queued": "Waiting for a free analysis worker",
    "extracting": "Extractor is finding candidate requirements",
    "trace_linking": "Trace linker is matching candidates to existing requirements",
    "reviewing": "Five specialist agents are reviewing",
    "rebutting": "Specialist agents are answering each other",
    "moderating": "Moderator is weighing agreement and dissent",
    "detecting_conflicts": "Conflict detector is checking contradictions",
    "writing_document": "Document and story writers are drafting the SRS and user stories",
    "modeling": "Diagram writer is drawing UML diagrams for the traceability matrix",
    "persisting": "Saving requirements, trace links and the draft",
    "done": "Analysis finished",
}

ROLE_NAMES = (
    "business_analyst",
    "project_manager",
    "developer",
    "quality_assurance",
    "designer",
)
HEARTBEAT_SECONDS = 20.0


def _node_names(event: Event) -> list[str]:
    """Bare node names on the event path, e.g. "wf@1/review1_designer@1"."""
    path = event.node_info.path if event.node_info else ""
    names = [segment.split("@", 1)[0] for segment in path.split("/") if segment]
    if event.author:
        names.append(event.author)
    return names


class ProgressTracker:
    """Turns workflow events into the public progress contract."""

    def __init__(self) -> None:
        self.progress = RunProgress(
            stage="extracting",
            percent=STAGE_PERCENT["extracting"],
            message=STAGE_MESSAGES["extracting"],
            updated_at=datetime.now(UTC),
        )

    def observe(self, event: Event) -> bool:
        """Apply one event; True when the visible progress changed."""
        changed = False
        for name in _node_names(event):
            stage = NODE_STAGES.get(name)
            if name.startswith("review1_"):
                stage = "reviewing"
            elif name.startswith("review2_"):
                stage = "rebutting"
            if stage and self._advance(stage):
                changed = True
            role = name.split("_", 1)[1] if name[:8] in {"review1_", "review2_"} else ""
            if (
                role in ROLE_NAMES
                and (event.output is not None or event.is_final_response())
                and role not in self.progress.agents_done
                and self.progress.stage == stage
            ):
                self.progress.agents_done.append(role)
                base = STAGE_PERCENT[self.progress.stage]
                self.progress.percent = base + 4 * len(self.progress.agents_done)
                changed = True
        if changed:
            self.progress.updated_at = datetime.now(UTC)
        return changed

    def set_stage(self, stage: RunStage) -> RunProgress:
        self._advance(stage)
        self.progress.updated_at = datetime.now(UTC)
        return self.progress

    def _advance(self, stage: RunStage) -> bool:
        current = RUN_STAGES.index(self.progress.stage)
        target = RUN_STAGES.index(stage)
        if target <= current:
            return False
        self.progress.completed_stages = list(RUN_STAGES[1:target])
        self.progress.stage = stage
        self.progress.percent = STAGE_PERCENT[stage]
        self.progress.message = STAGE_MESSAGES[stage]
        self.progress.agents_done = []
        return True


class AnalysisService:
    def __init__(self, repository: Repository | None = None) -> None:
        self._repository = repository or get_repository()
        self._on_enqueued: Callable[[], None] | None = None

    def set_enqueue_listener(self, listener: Callable[[], None] | None) -> None:
        """The background worker registers here to wake up on new jobs."""
        self._on_enqueued = listener

    async def run(
        self,
        request: AnalysisRequest,
        *,
        runner: Runner,
        app_name: str,
    ) -> AnalysisResult:
        """Synchronous analysis: the caller waits for the whole workflow."""
        run_id = await self._create_run_with_sources(request, status="running")
        return await self._execute(run_id, request, runner=runner, app_name=app_name)

    async def enqueue(self, request: AnalysisRequest) -> uuid.UUID:
        """Store the sources, queue the run and return at once."""
        run_id = await self._create_run_with_sources(request, status="queued")
        if self._on_enqueued:
            self._on_enqueued()
        return run_id

    async def _create_run_with_sources(
        self, request: AnalysisRequest, *, status: Literal["queued", "running"]
    ) -> uuid.UUID:
        self._validate_size(request)
        return await self._repository.create_run(
            request, status=status, with_sources=True
        )

    async def execute_claimed(
        self, run_id: uuid.UUID, *, runner: Runner, app_name: str
    ) -> AnalysisResult:
        """Run a job the worker has already moved to `running`."""
        try:
            request = await self._repository.get_run_request(run_id)
        except Exception as exc:
            await self._repository.fail_run(run_id, type(exc).__name__)
            raise
        return await self._execute(run_id, request, runner=runner, app_name=app_name)

    async def _execute(
        self,
        run_id: uuid.UUID,
        request: AnalysisRequest,
        *,
        runner: Runner,
        app_name: str,
    ) -> AnalysisResult:
        tracker = ProgressTracker()
        heartbeat = asyncio.create_task(self._heartbeat(run_id, tracker))
        try:
            await self._repository.update_run_progress(run_id, tracker.progress)
            policy = await self._repository.get_policy(request.project_id)
            workflow_input = WorkflowInput(
                run_id=str(run_id),
                request=request,
                policy=policy,
            )
            user_id = f"project:{request.project_id}"
            session = await runner.session_service.create_session(
                app_name=app_name,
                user_id=user_id,
            )
            message = types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=json.dumps(workflow_input.model_dump(mode="json"))
                    )
                ],
            )
            result: AnalysisResult | None = None
            started_at = time.monotonic()
            usage_by_node: dict[str, StageUsage] = {}
            async with asyncio.timeout(get_settings().workflow_timeout_seconds + 5):
                async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session.id,
                    new_message=message,
                ):
                    if tracker.observe(event):
                        await self._repository.update_run_progress(
                            run_id, tracker.progress
                        )
                    if event.usage_metadata:
                        node = event.node_info.path if event.node_info else event.author
                        stage = usage_by_node.setdefault(
                            node or "unknown",
                            StageUsage(
                                node=node or "unknown",
                                model_version=event.model_version,
                            ),
                        )
                        stage.model_calls += 1
                        stage.prompt_tokens += (
                            event.usage_metadata.prompt_token_count or 0
                        )
                        stage.output_tokens += (
                            event.usage_metadata.candidates_token_count or 0
                        )
                        stage.total_tokens += (
                            event.usage_metadata.total_token_count or 0
                        )
                    if event.output is None:
                        continue
                    try:
                        result = AnalysisResult.model_validate(event.output)
                    except ValidationError:
                        continue
            if result is None:
                raise RuntimeError("workflow completed without a valid AnalysisResult")
            stages = list(usage_by_node.values())
            result.usage = RunUsage(
                duration_ms=int((time.monotonic() - started_at) * 1000),
                model_calls=sum(stage.model_calls for stage in stages),
                prompt_tokens=sum(stage.prompt_tokens for stage in stages),
                output_tokens=sum(stage.output_tokens for stage in stages),
                total_tokens=sum(stage.total_tokens for stage in stages),
                stages=stages,
            )
            await self._repository.update_run_progress(
                run_id, tracker.set_stage("persisting")
            )
            # The heartbeat keeps publishing "persisting" until the result is stored
            done = tracker.progress.model_copy(deep=True)
            done.completed_stages = list(RUN_STAGES[1:-1])
            done.stage = "done"
            done.percent = STAGE_PERCENT["done"]
            done.message = STAGE_MESSAGES["done"]
            done.updated_at = datetime.now(UTC)
            await self._repository.persist_result(result, progress=done)
            return result
        except Exception as exc:
            await self._repository.fail_run(run_id, _public_error(exc))
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, run_id: uuid.UUID, tracker: ProgressTracker) -> None:
        """Keep the run fresh while a single model call takes a long time."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await self._repository.update_run_progress(run_id, tracker.progress)
            except Exception:
                logger.warning("analysis heartbeat failed for run %s", run_id)

    def _validate_size(self, request: AnalysisRequest) -> None:
        settings = get_settings()
        if len(request.sources) > settings.max_sources_per_run:
            raise ValueError(
                f"analysis accepts at most {settings.max_sources_per_run} sources"
            )
        total_characters = sum(len(source.content) for source in request.sources)
        if total_characters > settings.max_normalized_characters:
            raise ValueError(
                "normalized source content exceeds the configured character limit"
            )


def _public_error(exc: Exception) -> str:
    """Error text safe to show in the UI: no source content, no secrets."""
    if isinstance(exc, TimeoutError):
        return "The analysis took longer than the workflow time limit."
    return type(exc).__name__


@functools.cache
def get_analysis_service() -> AnalysisService:
    return AnalysisService()
