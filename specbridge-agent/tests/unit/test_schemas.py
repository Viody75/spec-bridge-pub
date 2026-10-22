from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.schemas import (
    AnalysisRequest,
    EvidenceRef,
    NormalizedSource,
    RequirementCandidate,
    TraceDecision,
)


def test_candidate_requires_evidence_or_assumption() -> None:
    with pytest.raises(ValidationError):
        RequirementCandidate(
            candidate_id="CAND-001",
            statement="The service shall retain an audit history.",
            kind="functional",
            priority="must",
            source_basis="inferred",
            claim="Audit history is required.",
            rationale="It supports traceability.",
            role_perspective="business analysis",
            confidence=0.6,
        )


def test_candidate_accepts_source_evidence() -> None:
    candidate = RequirementCandidate(
        candidate_id="CAND-001",
        statement="The service shall retain an audit history.",
        kind="functional",
        priority="must",
        source_basis="explicit",
        claim="Audit history is required.",
        rationale="The source explicitly requests it.",
        role_perspective="business analysis",
        confidence=0.9,
        evidence_refs=[
            EvidenceRef(
                source_id="note-1",
                quote="semua perubahan perlu tercatat",
                relation="supports",
            )
        ],
    )
    assert candidate.evidence_refs[0].source_id == "note-1"


def test_non_new_trace_decision_requires_existing_requirement() -> None:
    with pytest.raises(ValidationError):
        TraceDecision(
            candidate_id="CAND-001",
            classification="updates",
            confidence=0.8,
            rationale="The delivery target changed.",
        )


def test_analysis_request_rejects_duplicate_source_ids() -> None:
    source = NormalizedSource(
        source_id="source-1",
        source_type="pdf",
        title="PO notes",
        content="The system shall record changes.",
    )
    with pytest.raises(ValidationError):
        AnalysisRequest(
            project_id="project-1",
            document_type="PRD",
            sources=[source, source.model_copy()],
        )


def test_workflow_schema_is_json_serializable() -> None:
    schema = AnalysisRequest.model_json_schema()
    assert schema["title"] == "AnalysisRequest"
