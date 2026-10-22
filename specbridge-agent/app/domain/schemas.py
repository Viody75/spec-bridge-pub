from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocumentType = Literal["PRD", "SRS"]
SourceType = Literal[
    "pdf", "docx", "image", "meeting_transcript", "manual_note", "other"
]
RoleName = Literal[
    "business_analyst",
    "project_manager",
    "developer",
    "quality_assurance",
    "designer",
]
Verdict = Literal["approve", "concern", "reject", "not_applicable"]
Severity = Literal["info", "low", "medium", "high", "blocker"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceLocator(StrictModel):
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=1)
    timestamp_start: str | None = None
    timestamp_end: str | None = None
    speaker: str | None = None
    section: str | None = None


class NormalizedSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    source_type: SourceType
    title: str = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    occurred_at: datetime | None = None
    content: str = Field(min_length=1)
    locator: SourceLocator | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectCreate(StrictModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=2, max_length=300)
    document_type: DocumentType = "PRD"
    policy: dict[str, Any] = Field(default_factory=dict)


class ProjectView(ProjectCreate):
    created_at: datetime


class ProductProjectView(StrictModel):
    id: str
    name: str
    code: str
    description: str | None = None
    document_type: DocumentType
    document_version: int = Field(default=0, ge=0)
    status: Literal["draft", "review", "final"] = "draft"
    requirements: int = Field(default=0, ge=0)
    open_conflicts: int = Field(default=0, ge=0)
    owner_id: str
    approver_id: str
    division: str
    start_date: str
    target_date: str
    source_types: list[str] = Field(default_factory=list)
    active_agents: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkspaceSourceView(StrictModel):
    source_id: str
    source_type: SourceType
    title: str
    author: str | None = None
    occurred_at: datetime | None = None
    content: str
    locator: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RequirementTraceView(StrictModel):
    source_id: str
    quote: str
    locator: dict[str, Any] | None = None
    relation: str
    confidence: float = Field(ge=0, le=1)


class WorkspaceRequirementView(StrictModel):
    requirement_id: str
    statement: str
    revision: int = Field(ge=1)
    priority: Literal["must", "should", "could", "wont"]
    source_type: str
    created_by: str
    rationale: dict[str, Any] = Field(default_factory=dict)
    review_state: Literal["draft", "approved", "rejected"]
    implementation_status: Literal["not_started", "in_progress", "done", "blocked"]
    status_updated_by: str | None = None
    status_updated_at: datetime | None = None
    traces: list[RequirementTraceView] = Field(default_factory=list)
    artifact_links: ArtifactLinks = Field(default_factory=lambda: ArtifactLinks())
    artifact_links_updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class ConflictCommentView(StrictModel):
    comment_id: str
    actor_id: str
    text: str
    created_at: datetime


class WorkspaceConflictView(StrictModel):
    record_id: uuid.UUID
    conflict_id: str
    status: Literal["open", "resolved", "deferred"]
    payload: ConflictItem
    decision: str | None = None
    decision_rationale: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    comments: list[ConflictCommentView] = Field(default_factory=list)
    created_at: datetime


class WorkspaceDocumentView(StrictModel):
    document_id: uuid.UUID
    document_type: DocumentType
    status: Literal["draft", "review", "final"]
    version: int = Field(ge=1)
    content: DocumentDraftOutput
    rtm: list[RtmRow]
    published: bool
    published_by: str | None = None
    published_at: datetime | None = None
    approval_decision: Literal["pending", "approved", "changes_requested"] = "pending"
    decision_note: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    # Section index (as a string) -> who rewrote that section body and when
    section_edits: dict[str, SectionEdit] = Field(default_factory=dict)
    validation: DocumentValidation | None = None
    history: list[DocumentHistoryEntry] = Field(default_factory=list)
    created_at: datetime


ValidationStatus = Literal["PASS", "WARNING", "FAIL"]


class ValidationCheck(StrictModel):
    check: str
    status: ValidationStatus
    finding: str


class ValidationIssue(StrictModel):
    status: Literal["WARNING", "FAIL"]
    location: str
    issue: str


class TraceCoverage(StrictModel):
    total_fr: int
    fr_in_rtm: int
    fr_with_story: int
    fr_with_design: int
    percent: float
    unresolved: int
    invalid: int
    orphaned: int


class NfVerification(StrictModel):
    nf_id: str
    criterion: str
    status: ValidationStatus


class DocumentValidation(StrictModel):
    """Validation report (section 8 of the SRS rules), from the stored artifacts."""

    summary: list[ValidationCheck]
    coverage: TraceCoverage
    issues: list[ValidationIssue] = Field(default_factory=list)
    nf_report: list[NfVerification] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)
    # FINAL is only allowed without confirmed failures
    ready_for_final: bool


class DocumentHistoryEntry(StrictModel):
    version: int
    created_at: datetime
    published: bool
    changes: list[str] = Field(default_factory=list)
    changed_sections: list[str] = Field(default_factory=list)
    edited_sections: list[str] = Field(default_factory=list)


class SectionEdit(StrictModel):
    actor_id: str
    edited_at: datetime


class TraceItemView(StrictModel):
    """How the trace linker placed one extracted candidate, and any human decision."""

    item_id: uuid.UUID
    run_id: uuid.UUID
    candidate_id: str
    statement: str
    source_ids: list[str] = Field(default_factory=list)
    classification: Literal["new", "updates", "duplicates", "possibly_conflicts"]
    outcome: Literal["added", "duplicate", "similar"]
    match_requirement_id: str | None = None
    match_statement: str | None = None
    similarity: float | None = Field(default=None, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str
    requirement_id: str | None = None
    decision: Literal["merged", "kept_new"] | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


class ProjectWorkspace(StrictModel):
    project: ProductProjectView
    policy: dict[str, Any]
    sources: list[WorkspaceSourceView] = Field(default_factory=list)
    requirements: list[WorkspaceRequirementView] = Field(default_factory=list)
    conflicts: list[WorkspaceConflictView] = Field(default_factory=list)
    trace_items: list[TraceItemView] = Field(default_factory=list)
    document: WorkspaceDocumentView | None = None
    latest_run: AnalysisRunView | None = None


class RequirementReviewUpdate(StrictModel):
    state: Literal["approved", "rejected"]
    actor_id: str = Field(min_length=1, max_length=200)


class RequirementRevisionUpdate(StrictModel):
    text: str = Field(min_length=5, max_length=10_000)
    rationale: str = Field(min_length=3, max_length=5_000)
    actor_id: str = Field(min_length=1, max_length=200)
    priority: Literal["must", "should", "could", "wont"]


class ArtifactLinks(StrictModel):
    """IDs of the design artifacts a requirement traces to (RTM columns)."""

    us: list[str] = Field(default_factory=list, max_length=50)
    uc: list[str] = Field(default_factory=list, max_length=50)
    ad: list[str] = Field(default_factory=list, max_length=50)
    cd: list[str] = Field(default_factory=list, max_length=50)
    sq: list[str] = Field(default_factory=list, max_length=50)
    dfd: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def normalize_ids(self) -> ArtifactLinks:
        patterns = {
            # Role code: a letter (US-A-01); digits from older drafts still pass
            "us": r"^US-[A-Z0-9]+-\d{2,}$",
            "uc": r"^UC-\d{2,}$",
            "ad": r"^AD-\d{2,}$",
            "cd": r"^CD-\d{2,}$",
            "sq": r"^SQ-\d{2,}$",
            "dfd": r"^DFD-\d{2,}$",
        }
        for key, pattern in patterns.items():
            ids = list(
                dict.fromkeys(
                    value.strip().upper()
                    for value in getattr(self, key)
                    if value.strip()
                )
            )
            wrong = [value for value in ids if not re.match(pattern, value)]
            if wrong:
                raise ValueError(f"{key}: invalid artifact id(s) {', '.join(wrong)}")
            setattr(self, key, ids)
        return self


class ArtifactLinksUpdate(StrictModel):
    links: ArtifactLinks
    actor_id: str = Field(min_length=1, max_length=200)


class SectionBodyUpdate(StrictModel):
    content: str = Field(min_length=1, max_length=50_000)
    actor_id: str = Field(min_length=1, max_length=200)


class TraceItemDecisionCreate(StrictModel):
    decision: Literal["merged", "kept_new"]
    actor_id: str = Field(min_length=1, max_length=200)


class ImplementationStatusUpdate(StrictModel):
    state: Literal["not_started", "in_progress", "done", "blocked"]
    actor_id: str = Field(min_length=1, max_length=200)


class AnalysisRequest(StrictModel):
    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    document_type: DocumentType
    sources: list[NormalizedSource] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_source_ids(self) -> AnalysisRequest:
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique within a request")
        return self


class ManualRequirementCreate(StrictModel):
    text: str = Field(min_length=5, max_length=10_000)
    rationale: str = Field(min_length=3, max_length=5_000)
    actor_id: str = Field(min_length=1, max_length=200)
    priority: Literal["must", "should", "could", "wont"] = "should"
    # non_functional and constraint get an NF-XX id, the rest FR-XX
    kind: Literal["functional", "non_functional", "constraint", "business_rule"] = (
        "functional"
    )


class EvidenceRef(StrictModel):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=5_000)
    locator: SourceLocator | None = None
    relation: str = Field(min_length=1, max_length=500)


class DeliberationItem(StrictModel):
    claim: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    role_perspective: str = Field(min_length=1)
    standard_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    alternatives_considered: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    dissent: list[str] = Field(default_factory=list)


class RequirementCandidate(DeliberationItem):
    candidate_id: str = Field(pattern=r"^CAND-[0-9]{3,}$")
    statement: str = Field(min_length=5)
    kind: Literal["functional", "non_functional", "constraint", "business_rule"]
    priority: Literal["must", "should", "could", "wont"]
    # "stated" only when a source or agreed scheme gives the priority
    priority_basis: Literal["stated", "assumed"] = "assumed"
    source_basis: Literal["explicit", "inferred", "assumption"]

    @model_validator(mode="after")
    def require_evidence_or_assumption(self) -> RequirementCandidate:
        if not self.evidence_refs and not self.assumptions:
            raise ValueError("candidate requires evidence_refs or assumptions")
        return self


class ExtractionOutput(StrictModel):
    requirements: list[RequirementCandidate]
    ignored_context: list[str] = Field(default_factory=list)
    extraction_notes: list[str] = Field(default_factory=list)


class ExistingRequirement(StrictModel):
    requirement_id: str
    statement: str
    revision: int = Field(ge=1)
    similarity: float | None = Field(default=None, ge=0, le=1)


class TraceCandidateSet(StrictModel):
    candidate: RequirementCandidate
    existing_candidates: list[ExistingRequirement] = Field(default_factory=list)


class TraceLinkerInput(StrictModel):
    project_id: str
    candidates: list[TraceCandidateSet]


class TraceDecision(StrictModel):
    candidate_id: str
    classification: Literal["new", "updates", "duplicates", "possibly_conflicts"]
    existing_requirement_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_existing_target(self) -> TraceDecision:
        if self.classification != "new" and not self.existing_requirement_id:
            raise ValueError("non-new trace decisions require existing_requirement_id")
        if self.classification == "new" and self.existing_requirement_id:
            raise ValueError(
                "new trace decisions cannot target an existing requirement"
            )
        return self


class TraceOutput(StrictModel):
    decisions: list[TraceDecision]


class ReviewInput(StrictModel):
    project_id: str
    document_type: DocumentType
    policy: dict[str, Any]
    requirements: list[RequirementCandidate]
    trace_decisions: list[TraceDecision]


class Finding(DeliberationItem):
    finding_id: str = Field(pattern=r"^FIND-[A-Z]{2,4}-[0-9]{3,}$")
    candidate_id: str
    verdict: Verdict
    severity: Severity
    recommended_action: str = Field(min_length=1)


class SpecialistReview(StrictModel):
    role: RoleName
    round: int = Field(ge=1, le=2)
    findings: list[Finding]
    overall_rationale: str = Field(min_length=1)


class RebuttalInput(StrictModel):
    review_input: ReviewInput
    initial_reviews: list[SpecialistReview]


class ModerationInput(StrictModel):
    review_input: ReviewInput
    initial_reviews: list[SpecialistReview]
    rebuttals: list[SpecialistReview]


class ModeratorOutput(StrictModel):
    status: Literal["consensus", "unresolved_dissent", "human_context_required"]
    rationale: str = Field(min_length=1)
    agreed_actions: list[str] = Field(default_factory=list)
    unresolved_dissent: list[str] = Field(default_factory=list)
    human_questions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ConflictAnalysisInput(StrictModel):
    review_input: ReviewInput
    initial_reviews: list[SpecialistReview]
    rebuttals: list[SpecialistReview]
    moderation: ModeratorOutput


class ConflictItem(DeliberationItem):
    conflict_id: str = Field(pattern=r"^CONFLICT-[0-9]{3,}$")
    title: str = Field(min_length=1)
    affected_candidate_ids: list[str] = Field(min_length=1)
    severity: Severity
    positions: list[str] = Field(min_length=2)
    resolution_options: list[str] = Field(min_length=1)


class ConflictOutput(StrictModel):
    conflicts: list[ConflictItem] = Field(default_factory=list)


class RtmRow(StrictModel):
    candidate_id: str
    requirement_statement: str
    source_ids: list[str]
    review_status: Verdict
    conflict_ids: list[str] = Field(default_factory=list)
    verification_basis: str


class DocTable(StrictModel):
    """A table inside a document section, e.g. 1.3 Daftar Istilah."""

    caption: str = Field(default="", max_length=300)
    columns: list[str] = Field(min_length=1, max_length=10)
    rows: list[list[str]] = Field(default_factory=list, max_length=60)
    # One review key per row (set when the draft is assembled; the agents leave it empty)
    item_keys: list[str] = Field(default_factory=list)


DiagramKind = Literal["use_case", "activity", "class", "sequence", "context", "dfd"]


class DiagramArtifact(StrictModel):
    """A simple UML/structured-analysis diagram as Mermaid source."""

    diagram_id: str = Field(min_length=1, max_length=40)
    kind: DiagramKind
    title: str = Field(min_length=1, max_length=300)
    mermaid: str = Field(min_length=1, max_length=20_000)
    description: str = Field(default="", max_length=2_000)
    requirement_ids: list[str] = Field(default_factory=list)


class UserStory(StrictModel):
    story_id: str = Field(min_length=1, max_length=40)
    role: str = Field(min_length=1, max_length=120)
    requirement_ids: list[str] = Field(default_factory=list)
    story: str = Field(min_length=1, max_length=2_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=10)


class UseCase(StrictModel):
    use_case_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=300)
    actors: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    story_ids: list[str] = Field(default_factory=list)


# Empty RTM cell: "—" (not relevant), [BELUM TERSEDIA] (relevant artifact not created
# yet) or [PERLU KONFIRMASI] (relationship cannot be established confidently)
TraceMarker = Literal["not_applicable", "pending", "needs_confirmation"]


class TraceabilityRow(StrictModel):
    """One functional requirement and the artifacts that declare they cover it."""

    requirement_id: str
    us: list[str] = Field(default_factory=list)
    uc: list[str] = Field(default_factory=list)
    ad: list[str] = Field(default_factory=list)
    cd: list[str] = Field(default_factory=list)
    sq: list[str] = Field(default_factory=list)
    dfd: list[str] = Field(default_factory=list)
    markers: dict[str, TraceMarker] = Field(default_factory=dict)


class ArtifactRegistry(StrictModel):
    """IDs issued so far, so an ID survives new versions and is never reused."""

    # Casefolded role name -> role code letter in US-<code>-YY
    role_codes: dict[str, str] = Field(default_factory=dict)
    # "US-A", "UC", "AD", ... -> highest number issued
    counters: dict[str, int] = Field(default_factory=dict)
    retired: list[str] = Field(default_factory=list)


SectionKind = Literal[
    "text", "functional_requirements", "non_functional_requirements", "traceability"
]


class DocumentSection(StrictModel):
    title: str
    content: str
    candidate_ids: list[str] = Field(default_factory=list)
    # Template position such as "3.10"; absent in drafts written before the template
    number: str | None = None
    kind: SectionKind = "text"
    tables: list[DocTable] = Field(default_factory=list)
    diagrams: list[DiagramArtifact] = Field(default_factory=list)


class DocumentDraftOutput(StrictModel):
    title: str
    document_type: DocumentType
    sections: list[DocumentSection]
    rtm: list[RtmRow]
    open_questions: list[str] = Field(default_factory=list)
    user_stories: list[UserStory] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)
    traceability: list[TraceabilityRow] = Field(default_factory=list)
    registry: ArtifactRegistry = Field(default_factory=ArtifactRegistry)
    # References an artifact made to an ID that does not exist; dropped and reported
    invalid_references: list[str] = Field(default_factory=list)
    # Filled when the version is saved: what changed against the previous version
    changes: list[str] = Field(default_factory=list)
    changed_sections: list[str] = Field(default_factory=list)
    # Review of each table row and diagram, keyed by its item key
    item_reviews: dict[str, ItemReview] = Field(default_factory=dict)
    publication_ready: bool = False

    @field_validator("publication_ready")
    @classmethod
    def draft_cannot_be_published(cls, value: bool) -> bool:
        if value:
            raise ValueError("agent-generated documents are always drafts")
        return value


class ItemReview(StrictModel):
    """A person's decision on one table row or diagram of the draft."""

    state: Literal["pending", "approved", "rejected"] = "pending"
    actor_id: str | None = None
    at: datetime | None = None
    note: str | None = None
    # Last person who changed the item's content; their version wins over agents
    edited_by: str | None = None


class ItemReviewCreate(StrictModel):
    action: Literal["approve", "reject", "reopen"]
    actor_id: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2_000)


class ItemBulkReviewCreate(ItemReviewCreate):
    keys: list[str] = Field(min_length=1, max_length=200)


class ItemEdit(StrictModel):
    """New content for one item; only the fields of its kind are used."""

    actor_id: str = Field(min_length=1, max_length=200)
    # Table row
    cells: list[str] | None = None
    # User story
    role: str | None = Field(default=None, max_length=120)
    story: str | None = Field(default=None, max_length=2_000)
    acceptance_criteria: list[str] | None = None
    # Use case
    name: str | None = Field(default=None, max_length=300)
    actors: list[str] | None = None
    story_ids: list[str] | None = None
    # Diagram
    title: str | None = Field(default=None, max_length=300)
    mermaid: str | None = Field(default=None, max_length=20_000)
    description: str | None = Field(default=None, max_length=2_000)
    # Story, use case and diagram
    requirement_ids: list[str] | None = None


class SourceRef(StrictModel):
    """A stored source, for 1.4 Referensi (only sources actually used)."""

    source_id: str
    title: str
    source_type: str
    author: str | None = None
    date: str


class PreviousDiagram(StrictModel):
    diagram_id: str
    kind: DiagramKind
    title: str
    requirement_ids: list[str] = Field(default_factory=list)


class PreviousArtifacts(StrictModel):
    """The current version's artifacts, so their IDs carry over to the next one."""

    stories: list[UserStory] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)
    diagrams: list[PreviousDiagram] = Field(default_factory=list)
    registry: ArtifactRegistry = Field(default_factory=ArtifactRegistry)


class RequirementRef(StrictModel):
    """A requirement the writers may cite: an existing one or a new candidate."""

    requirement_id: str
    statement: str
    kind: Literal["functional", "non_functional", "constraint", "business_rule"]
    priority: Literal["must", "should", "could", "wont"]
    origin: Literal["existing", "candidate"]


class DocumentWriterInput(ConflictAnalysisInput):
    conflicts: ConflictOutput
    project_name: str = ""
    # Every current requirement of the project plus this run's candidates, so each
    # document version is complete rather than covering only the latest upload
    project_requirements: list[RequirementRef] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    # Stories and use cases of the current version: reuse their IDs when unchanged
    previous_stories: list[UserStory] = Field(default_factory=list)
    previous_use_cases: list[UseCase] = Field(default_factory=list)


class WriterSection(StrictModel):
    number: str = Field(pattern=r"^[0-9]+(\.[0-9]+)*$")
    title: str = Field(min_length=1)
    content: str = ""
    tables: list[DocTable] = Field(default_factory=list, max_length=4)
    candidate_ids: list[str] = Field(default_factory=list)


class DocumentWriterOutput(StrictModel):
    """Narrative sections and tables of the template; diagrams come from other agents."""

    title: str
    document_type: DocumentType
    sections: list[WriterSection]
    rtm: list[RtmRow]
    open_questions: list[str] = Field(default_factory=list)
    publication_ready: bool = False

    @field_validator("publication_ready")
    @classmethod
    def draft_cannot_be_published(cls, value: bool) -> bool:
        if value:
            raise ValueError("agent-generated documents are always drafts")
        return value


class StoryWriterOutput(StrictModel):
    # No maxItems here: Vertex AI rejects large limits on arrays of objects in a
    # response schema. The prompt asks for at most 30/15; assembly caps the rest.
    stories: list[UserStory] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)


class DiagramWriterInput(StrictModel):
    project_name: str
    document_type: DocumentType
    policy: dict[str, Any]
    requirements: list[RequirementRef]
    stories: list[UserStory]
    use_cases: list[UseCase]
    # Diagrams of the current version (no Mermaid source): reuse IDs when unchanged
    previous_diagrams: list[PreviousDiagram] = Field(default_factory=list)


class PendingArtifact(StrictModel):
    """A diagram a requirement needs that is not drawn yet, or an uncertain link."""

    requirement_id: str
    kind: DiagramKind
    status: Literal["pending", "needs_confirmation"]
    reason: str = Field(default="", max_length=300)


class DiagramWriterOutput(StrictModel):
    diagrams: list[DiagramArtifact] = Field(default_factory=list, max_length=20)
    pending: list[PendingArtifact] = Field(default_factory=list)


class WorkflowInput(StrictModel):
    run_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    request: AnalysisRequest
    policy: dict[str, Any]


class StageUsage(StrictModel):
    node: str
    model_version: str | None = None
    model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RunUsage(StrictModel):
    duration_ms: int = Field(ge=0)
    model_calls: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    stages: list[StageUsage] = Field(default_factory=list)


class PersistedConflictRef(StrictModel):
    conflict_id: str
    record_id: uuid.UUID


class PersistedResources(StrictModel):
    document_id: uuid.UUID
    document_version: int = Field(ge=1)
    conflicts: list[PersistedConflictRef] = Field(default_factory=list)


class AnalysisResult(StrictModel):
    run_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    project_id: str
    status: Literal["completed", "human_context_required"]
    extraction: ExtractionOutput
    trace: TraceOutput
    initial_reviews: list[SpecialistReview]
    rebuttals: list[SpecialistReview]
    moderation: ModeratorOutput
    conflicts: ConflictOutput
    document: DocumentDraftOutput
    # Existing requirements the hybrid retrieval offered per candidate, with scores
    retrieval: list[TraceCandidateSet] = Field(default_factory=list)
    usage: RunUsage | None = None
    resources: PersistedResources | None = None


class SourceUploadResult(StrictModel):
    source: NormalizedSource
    analysis: AnalysisResult


RunStage = Literal[
    "queued",
    "extracting",
    "trace_linking",
    "reviewing",
    "rebutting",
    "moderating",
    "detecting_conflicts",
    "writing_document",
    "modeling",
    "persisting",
    "done",
]

RUN_STAGES: tuple[RunStage, ...] = (
    "queued",
    "extracting",
    "trace_linking",
    "reviewing",
    "rebutting",
    "moderating",
    "detecting_conflicts",
    "writing_document",
    "modeling",
    "persisting",
    "done",
)


class RunProgress(StrictModel):
    stage: RunStage = "queued"
    percent: int = Field(default=0, ge=0, le=100)
    completed_stages: list[RunStage] = Field(default_factory=list)
    # Specialist agents that have answered in the current review round
    agents_done: list[str] = Field(default_factory=list)
    message: str | None = None
    updated_at: datetime | None = None


class AnalysisRunView(StrictModel):
    run_id: uuid.UUID
    project_id: str
    status: str
    result: AnalysisResult | None = None
    error: str | None = None
    progress: RunProgress = Field(default_factory=RunProgress)
    source_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AnalysisJobAccepted(StrictModel):
    run_id: uuid.UUID
    project_id: str
    status: Literal["queued"]
    sources: list[NormalizedSource] = Field(default_factory=list)


class ConflictDecisionCreate(StrictModel):
    actor_id: str = Field(min_length=1, max_length=200)
    decision: str = Field(min_length=1, max_length=10_000)
    rationale: str = Field(min_length=3, max_length=10_000)


class ConflictDeferCreate(StrictModel):
    actor_id: str = Field(min_length=1, max_length=200)


class ConflictCommentCreate(StrictModel):
    actor_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=5_000)


class PublishDocumentRequest(StrictModel):
    actor_id: str = Field(min_length=1, max_length=200)
    decision_note: str = Field(min_length=3, max_length=10_000)


class RequestDocumentChanges(StrictModel):
    actor_id: str = Field(min_length=1, max_length=200)
    decision_note: str = Field(min_length=3, max_length=10_000)


class HealthResponse(StrictModel):
    status: Literal["ok", "degraded"]
    database_configured: bool
    embedding_configured: bool


class ApiMessage(StrictModel):
    message: str


class SourceBatch(StrictModel):
    sources: list[NormalizedSource] = Field(min_length=1, max_length=50)

    @field_validator("sources")
    @classmethod
    def unique_ids(cls, sources: list[NormalizedSource]) -> list[NormalizedSource]:
        ids = [source.source_id for source in sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        return sources


DocumentDraftOutput.model_rebuild()
WorkspaceRequirementView.model_rebuild()
WorkspaceConflictView.model_rebuild()
WorkspaceDocumentView.model_rebuild()
ProjectWorkspace.model_rebuild()
