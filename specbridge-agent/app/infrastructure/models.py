from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSONB().with_variant(JSON(), "sqlite")
VectorType = Vector().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    document_type: Mapped[str] = mapped_column(String(10), nullable=False)
    current_policy_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )

    policies: Mapped[list[ProjectPolicy]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectPolicy(Base, TimestampMixin):
    __tablename__ = "project_policies"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="policies")


class Source(Base, TimestampMixin):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("project_id", "source_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, default=dict, nullable=False
    )


class Requirement(Base, TimestampMixin):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("project_id", "stable_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stable_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    implementation_status: Mapped[str] = mapped_column(
        String(30), default="not_started", nullable=False
    )
    status_updated_by: Mapped[str | None] = mapped_column(String(200))
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # RTM columns edited by a person (user story, use case, diagram IDs)
    artifact_links: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    artifact_links_updated_by: Mapped[str | None] = mapped_column(String(200))

    revisions: Mapped[list[RequirementRevision]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )


class RequirementRevision(Base, TimestampMixin):
    __tablename__ = "requirement_revisions"
    __table_args__ = (UniqueConstraint("requirement_id", "revision"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(VectorType)

    requirement: Mapped[Requirement] = relationship(back_populates="revisions")


class TraceLink(Base, TimestampMixin):
    __tablename__ = "trace_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    requirement_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement_revisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(500), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)


class AnalysisRun(Base, TimestampMixin):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        # One queued or running analysis per project, so revisions never race
        Index(
            "uq_analysis_run_project_active",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    progress: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TraceItem(Base, TimestampMixin):
    """Where the trace linker placed one candidate; `similar` waits for a person."""

    __tablename__ = "trace_items"
    __table_args__ = (UniqueConstraint("run_id", "candidate_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    match_requirement_id: Mapped[str | None] = mapped_column(String(100))
    similarity: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_id: Mapped[str | None] = mapped_column(String(100))
    candidate: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    decision: Mapped[str | None] = mapped_column(String(20))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewFinding(Base, TimestampMixin):
    __tablename__ = "review_findings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    verdict: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)


class Conflict(Base, TimestampMixin):
    __tablename__ = "conflicts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    decision: Mapped[str | None] = mapped_column(Text)
    decision_rationale: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comments: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, default=list, nullable=False
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "document_type"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approval_decision: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False
    )
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    rtm: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, nullable=False)
    section_edits: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
