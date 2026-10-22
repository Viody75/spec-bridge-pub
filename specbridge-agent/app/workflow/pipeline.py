from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.models import Gemini
from google.adk.workflow import JoinNode, RetryConfig, Workflow
from google.genai import types

from app.config import get_settings
from app.domain.schemas import (
    AnalysisResult,
    ConflictAnalysisInput,
    ConflictOutput,
    DiagramWriterInput,
    DiagramWriterOutput,
    DocumentWriterInput,
    DocumentWriterOutput,
    ExtractionOutput,
    ModerationInput,
    ModeratorOutput,
    RebuttalInput,
    RequirementRef,
    ReviewInput,
    RoleName,
    SpecialistReview,
    StoryWriterOutput,
    TraceCandidateSet,
    TraceLinkerInput,
    TraceOutput,
    WorkflowInput,
)
from app.infrastructure.repository import get_repository
from app.workflow.document_assembly import assemble_document
from app.workflow.prompts import (
    CONFLICT_PROMPT,
    DIAGRAM_WRITER_PROMPT,
    DOCUMENT_WRITER_PROMPT,
    EXTRACTOR_PROMPT,
    MODERATOR_PROMPT,
    STORY_WRITER_PROMPT,
    TRACE_LINKER_PROMPT,
    initial_review_prompt,
    rebuttal_prompt,
)

ROLES: tuple[RoleName, ...] = (
    "business_analyst",
    "project_manager",
    "developer",
    "quality_assurance",
    "designer",
)

STRUCTURED_OUTPUT_RETRY = RetryConfig(
    max_attempts=2,
    initial_delay=0.25,
    max_delay=0.25,
    backoff_factor=1.0,
    jitter=0.0,
    exceptions=["ValidationError"],
)


def _model(model_name: str) -> Gemini:
    return Gemini(
        model=model_name,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


def _agent(
    *,
    name: str,
    model_name: str,
    instruction: str,
    input_schema: type,
    output_schema: type,
    output_key: str,
    max_output_tokens: int | None = None,
) -> LlmAgent:
    settings = get_settings()
    return LlmAgent(
        name=name,
        retry_config=STRUCTURED_OUTPUT_RETRY,
        model=_model(model_name),
        instruction=instruction,
        input_schema=input_schema,
        output_schema=output_schema,
        output_key=output_key,
        include_contents="none",
        mode="single_turn",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=max_output_tokens or settings.model_max_output_tokens,
        ),
    )


def prepare_workflow_input(node_input: WorkflowInput) -> Event:
    payload = node_input.model_dump(mode="json")
    return Event(
        output=payload,
        actions=EventActions(state_delta={"workflow_input": payload}),
    )


async def build_trace_input(
    node_input: ExtractionOutput,
    workflow_input: dict[str, Any],
) -> Event:
    workflow = WorkflowInput.model_validate(workflow_input)
    repository = get_repository()
    candidates: list[TraceCandidateSet] = []
    for candidate in node_input.requirements:
        existing = await repository.find_similar_requirements(
            workflow.request.project_id,
            candidate.statement,
            get_settings().retrieval_top_k,
        )
        candidates.append(
            TraceCandidateSet(candidate=candidate, existing_candidates=existing)
        )
    payload = TraceLinkerInput(
        project_id=workflow.request.project_id,
        candidates=candidates,
    )
    return Event(
        output=payload,
        actions=EventActions(
            state_delta={"trace_input": payload.model_dump(mode="json")}
        ),
    )


def build_review_input(
    node_input: TraceOutput,
    workflow_input: dict[str, Any],
    extraction: dict[str, Any],
    trace_input: dict[str, Any],
) -> Event:
    workflow = WorkflowInput.model_validate(workflow_input)
    extracted = ExtractionOutput.model_validate(extraction)
    candidate_ids = {item.candidate_id for item in extracted.requirements}
    decision_ids = [item.candidate_id for item in node_input.decisions]
    if (
        len(decision_ids) != len(set(decision_ids))
        or set(decision_ids) != candidate_ids
    ):
        raise ValueError("Trace Linker must return exactly one decision per candidate")
    retrieved = TraceLinkerInput.model_validate(trace_input)
    allowed_targets = {
        item.candidate.candidate_id: {
            existing.requirement_id for existing in item.existing_candidates
        }
        for item in retrieved.candidates
    }
    for decision in node_input.decisions:
        if (
            decision.existing_requirement_id
            and decision.existing_requirement_id
            not in allowed_targets.get(decision.candidate_id, set())
        ):
            raise ValueError(
                "Trace Linker selected an existing requirement not retrieved"
            )
    payload = ReviewInput(
        project_id=workflow.request.project_id,
        document_type=workflow.request.document_type,
        policy=workflow.policy,
        requirements=extracted.requirements,
        trace_decisions=node_input.decisions,
    )
    return Event(
        output=payload,
        actions=EventActions(
            state_delta={"review_input": payload.model_dump(mode="json")}
        ),
    )


def _collect_reviews(
    outputs: dict[str, Any],
    *,
    round_number: int,
    review_input: ReviewInput,
) -> list[SpecialistReview]:
    expected_candidates = {item.candidate_id for item in review_input.requirements}
    reviews: list[SpecialistReview] = []
    for role in ROLES:
        key = f"review{round_number}_{role}"
        if key not in outputs:
            raise ValueError(f"Missing output from {key}")
        review = SpecialistReview.model_validate(outputs[key])
        if review.role != role or review.round != round_number:
            raise ValueError(f"{key} returned the wrong role or round")
        finding_ids = [item.candidate_id for item in review.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError(f"{key} returned duplicate candidate findings")
        if set(finding_ids) != expected_candidates:
            raise ValueError(f"{key} must review every candidate exactly once")
        reviews.append(review)
    return reviews


def collect_initial_reviews(
    node_input: dict[str, Any], review_input: dict[str, Any]
) -> Event:
    review = ReviewInput.model_validate(review_input)
    reviews = _collect_reviews(node_input, round_number=1, review_input=review)
    payload = RebuttalInput(review_input=review, initial_reviews=reviews)
    return Event(
        output=payload,
        actions=EventActions(
            state_delta={
                "initial_reviews": [item.model_dump(mode="json") for item in reviews]
            }
        ),
    )


def collect_rebuttals(
    node_input: dict[str, Any],
    review_input: dict[str, Any],
    initial_reviews: list[dict[str, Any]],
) -> Event:
    review = ReviewInput.model_validate(review_input)
    rebuttals = _collect_reviews(node_input, round_number=2, review_input=review)
    payload = ModerationInput(
        review_input=review,
        initial_reviews=[
            SpecialistReview.model_validate(item) for item in initial_reviews
        ],
        rebuttals=rebuttals,
    )
    return Event(
        output=payload,
        actions=EventActions(
            state_delta={
                "rebuttals": [item.model_dump(mode="json") for item in rebuttals]
            }
        ),
    )


def build_conflict_input(
    node_input: ModeratorOutput,
    review_input: dict[str, Any],
    initial_reviews: list[dict[str, Any]],
    rebuttals: list[dict[str, Any]],
) -> Event:
    payload = ConflictAnalysisInput(
        review_input=ReviewInput.model_validate(review_input),
        initial_reviews=[
            SpecialistReview.model_validate(item) for item in initial_reviews
        ],
        rebuttals=[SpecialistReview.model_validate(item) for item in rebuttals],
        moderation=node_input,
    )
    return Event(
        output=payload,
        actions=EventActions(
            state_delta={"conflict_input": payload.model_dump(mode="json")}
        ),
    )


async def build_document_input(
    node_input: ConflictOutput,
    conflict_input: dict[str, Any],
) -> Event:
    conflict_context = ConflictAnalysisInput.model_validate(conflict_input)
    project_id = conflict_context.review_input.project_id
    project_name, existing = await get_repository().get_writer_context(project_id)
    candidates = [
        RequirementRef(
            requirement_id=item.candidate_id,
            statement=item.statement,
            kind=item.kind,
            priority=item.priority,
            origin="candidate",
        )
        for item in conflict_context.review_input.requirements
    ]
    payload = DocumentWriterInput(
        **conflict_context.model_dump(),
        conflicts=node_input,
        project_name=project_name,
        project_requirements=[*existing, *candidates],
    )
    return Event(
        output=payload,
        actions=EventActions(
            state_delta={"document_input": payload.model_dump(mode="json")}
        ),
    )


def build_diagram_input(
    node_input: dict[str, Any],
    document_input: dict[str, Any],
) -> Event:
    for key in ("document_writer", "story_writer"):
        if key not in node_input:
            raise ValueError(f"Missing output from {key}")
    stories = StoryWriterOutput.model_validate(node_input["story_writer"])
    context = DocumentWriterInput.model_validate(document_input)
    payload = DiagramWriterInput(
        project_name=context.project_name,
        document_type=context.review_input.document_type,
        policy=context.review_input.policy,
        requirements=context.project_requirements,
        stories=stories.stories,
        use_cases=stories.use_cases,
    )
    return Event(output=payload)


def finalize_result(
    node_input: DiagramWriterOutput,
    workflow_input: dict[str, Any],
    extraction: dict[str, Any],
    trace: dict[str, Any],
    initial_reviews: list[dict[str, Any]],
    rebuttals: list[dict[str, Any]],
    moderation: dict[str, Any],
    conflicts: dict[str, Any],
    trace_input: dict[str, Any],
    document: dict[str, Any],
    stories: dict[str, Any],
    document_input: dict[str, Any],
) -> Event:
    workflow = WorkflowInput.model_validate(workflow_input)
    writer = DocumentWriterOutput.model_validate(document)
    moderated = ModeratorOutput.model_validate(moderation)
    conflict_output = ConflictOutput.model_validate(conflicts)
    candidate_ids = {
        item.candidate_id
        for item in ExtractionOutput.model_validate(extraction).requirements
    }
    rtm_candidate_ids = [item.candidate_id for item in writer.rtm]
    if (
        len(rtm_candidate_ids) != len(set(rtm_candidate_ids))
        or set(rtm_candidate_ids) != candidate_ids
    ):
        raise ValueError(
            "Document Writer must include every candidate exactly once in RTM"
        )
    if any(
        not set(conflict.affected_candidate_ids).issubset(candidate_ids)
        for conflict in conflict_output.conflicts
    ):
        raise ValueError("Conflict Detector referenced an unknown candidate")
    all_reviews = [
        *[SpecialistReview.model_validate(item) for item in initial_reviews],
        *[SpecialistReview.model_validate(item) for item in rebuttals],
    ]
    has_blocker = any(
        finding.severity == "blocker" and finding.verdict in {"concern", "reject"}
        for review in all_reviews
        for finding in review.findings
    )
    status = (
        "completed"
        if moderated.status == "consensus"
        and not conflict_output.conflicts
        and not has_blocker
        else "human_context_required"
    )
    result = AnalysisResult(
        run_id=workflow.run_id,
        project_id=workflow.request.project_id,
        status=status,
        extraction=ExtractionOutput.model_validate(extraction),
        trace=TraceOutput.model_validate(trace),
        initial_reviews=all_reviews[: len(ROLES)],
        rebuttals=all_reviews[len(ROLES) :],
        moderation=moderated,
        conflicts=conflict_output,
        document=assemble_document(
            writer,
            StoryWriterOutput.model_validate(stories),
            node_input,
            DocumentWriterInput.model_validate(document_input).project_requirements,
        ),
        retrieval=TraceLinkerInput.model_validate(trace_input).candidates,
    )
    return Event(
        output=result,
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(text=json.dumps(result.model_dump(mode="json")))
            ],
        ),
    )


def build_workflow(model_name: str) -> Workflow:
    extractor = _agent(
        name="extractor",
        model_name=model_name,
        instruction=EXTRACTOR_PROMPT,
        input_schema=WorkflowInput,
        output_schema=ExtractionOutput,
        output_key="extraction",
    )
    trace_linker = _agent(
        name="trace_linker",
        model_name=model_name,
        instruction=TRACE_LINKER_PROMPT,
        input_schema=TraceLinkerInput,
        output_schema=TraceOutput,
        output_key="trace",
    )
    initial_agents = tuple(
        _agent(
            name=f"review1_{role}",
            model_name=model_name,
            instruction=initial_review_prompt(role),
            input_schema=ReviewInput,
            output_schema=SpecialistReview,
            output_key=f"review1_{role}_state",
        )
        for role in ROLES
    )
    rebuttal_agents = tuple(
        _agent(
            name=f"review2_{role}",
            model_name=model_name,
            instruction=rebuttal_prompt(role),
            input_schema=RebuttalInput,
            output_schema=SpecialistReview,
            output_key=f"review2_{role}_state",
        )
        for role in ROLES
    )
    initial_join = JoinNode(name="join_initial_reviews")
    rebuttal_join = JoinNode(name="join_rebuttals")
    moderator = _agent(
        name="moderator",
        model_name=model_name,
        instruction=MODERATOR_PROMPT,
        input_schema=ModerationInput,
        output_schema=ModeratorOutput,
        output_key="moderation",
    )
    conflict_detector = _agent(
        name="conflict_detector",
        model_name=model_name,
        instruction=CONFLICT_PROMPT,
        input_schema=ConflictAnalysisInput,
        output_schema=ConflictOutput,
        output_key="conflicts",
    )
    settings = get_settings()
    document_writer = _agent(
        name="document_writer",
        model_name=model_name,
        instruction=DOCUMENT_WRITER_PROMPT,
        input_schema=DocumentWriterInput,
        output_schema=DocumentWriterOutput,
        output_key="document",
        max_output_tokens=settings.artifact_max_output_tokens,
    )
    # Runs beside the document writer: user stories, acceptance criteria, use cases
    story_writer = _agent(
        name="story_writer",
        model_name=model_name,
        instruction=STORY_WRITER_PROMPT,
        input_schema=DocumentWriterInput,
        output_schema=StoryWriterOutput,
        output_key="stories",
        max_output_tokens=settings.artifact_max_output_tokens,
    )
    writers_join = JoinNode(name="join_writers")
    diagram_writer = _agent(
        name="diagram_writer",
        model_name=model_name,
        instruction=DIAGRAM_WRITER_PROMPT,
        input_schema=DiagramWriterInput,
        output_schema=DiagramWriterOutput,
        output_key="diagrams",
        max_output_tokens=settings.artifact_max_output_tokens,
    )

    return Workflow(
        name="specbridge_agent",
        description="Bounded multi-agent requirements analysis and draft generation.",
        input_schema=WorkflowInput,
        output_schema=AnalysisResult,
        max_concurrency=5,
        timeout=get_settings().workflow_timeout_seconds,
        edges=[
            ("START", prepare_workflow_input),
            (prepare_workflow_input, extractor),
            (extractor, build_trace_input),
            (build_trace_input, trace_linker),
            (trace_linker, build_review_input),
            (build_review_input, initial_agents),
            (initial_agents, initial_join),
            (initial_join, collect_initial_reviews),
            (collect_initial_reviews, rebuttal_agents),
            (rebuttal_agents, rebuttal_join),
            (rebuttal_join, collect_rebuttals),
            (collect_rebuttals, moderator),
            (moderator, build_conflict_input),
            (build_conflict_input, conflict_detector),
            (conflict_detector, build_document_input),
            (build_document_input, (document_writer, story_writer)),
            ((document_writer, story_writer), writers_join),
            (writers_join, build_diagram_input),
            (build_diagram_input, diagram_writer),
            (diagram_writer, finalize_result),
        ],
    )
