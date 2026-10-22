"""SRS rules: stable IDs, RTM markers, references, validation report and gates."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.schemas import (
    ArtifactLinks,
    DiagramArtifact,
    DiagramWriterOutput,
    DocTable,
    DocumentDraftOutput,
    DocumentSection,
    DocumentWriterOutput,
    PendingArtifact,
    PreviousArtifacts,
    PreviousDiagram,
    RequirementRef,
    SourceRef,
    StoryWriterOutput,
    UseCase,
    UserStory,
    WorkspaceRequirementView,
    WriterSection,
)
from app.infrastructure.repository import ConflictError, describe_changes
from app.workflow.document_assembly import assemble_document
from app.workflow.validation import validate_document

# Reuse the in-memory repository fixture and helpers
from tests.unit.test_jobs_and_human_edits import (  # noqa: F401
    _persist,
    repository,
)

FR = [
    RequirementRef(
        requirement_id=f"FR-0{n}",
        statement=f"Sistem harus menjalankan fungsi {n}.",
        kind="functional",
        priority="must",
        origin="existing",
    )
    for n in (1, 2, 3)
]
NF = RequirementRef(
    requirement_id="NF-01",
    statement="Sistem harus mengakhiri sesi setelah 15 menit tidak aktif.",
    kind="non_functional",
    priority="must",
    origin="existing",
)


def _writer(
    nf_criterion: str = "Sesi berakhir pada menit ke-15",
) -> DocumentWriterOutput:
    return DocumentWriterOutput(
        title="SRS",
        document_type="SRS",
        sections=[
            WriterSection(
                number="2.3",
                title="Karakteristik Pengguna",
                tables=[
                    DocTable(columns=["Role", "K", "N"], rows=[["Karyawan", "x", "y"]])
                ],
            ),
            WriterSection(
                number="3.10",
                title="Kebutuhan Non-Fungsionalitas",
                tables=[
                    DocTable(
                        columns=["NF-ID", "Kategori", "Kebutuhan", "Kriteria"],
                        rows=[["NF-01", "Keamanan", "Sesi berakhir", nf_criterion]],
                    )
                ],
            ),
        ],
        rtm=[],
    )


def _story(story_id: str, *reqs: str, role: str = "Karyawan") -> UserStory:
    return UserStory(
        story_id=story_id,
        role=role,
        requirement_ids=list(reqs),
        story=f"Sebagai {role.lower()}, saya ingin ..., sehingga ...",
        acceptance_criteria=["Diberikan ..., ketika ..., maka ..."],
    )


def _activity(diagram_id: str, *reqs: str) -> DiagramArtifact:
    return DiagramArtifact(
        diagram_id=diagram_id,
        kind="activity",
        title=f"Skenario {diagram_id}",
        mermaid='flowchart TD\n A(("Mulai")) --> B(("Selesai"))',
        requirement_ids=list(reqs),
    )


def _previous(doc: DocumentDraftOutput) -> PreviousArtifacts:
    return PreviousArtifacts(
        stories=doc.user_stories,
        use_cases=doc.use_cases,
        diagrams=[
            PreviousDiagram(
                diagram_id=d.diagram_id,
                kind=d.kind,
                title=d.title,
                requirement_ids=d.requirement_ids,
            )
            for s in doc.sections
            for d in s.diagrams
        ],
        registry=doc.registry,
    )


def test_ids_survive_versions_and_retired_ids_are_never_reused() -> None:
    first = assemble_document(
        _writer(),
        StoryWriterOutput(
            stories=[_story("S1", "FR-01"), _story("S2", "FR-02")],
            use_cases=[UseCase(use_case_id="UC1", name="Ajukan", story_ids=["S1"])],
        ),
        DiagramWriterOutput(
            diagrams=[_activity("a", "FR-01"), _activity("b", "FR-02")]
        ),
        FR,
    )
    assert [s.story_id for s in first.user_stories] == ["US-A-01", "US-A-02"]
    assert [d.diagram_id for s in first.sections for d in s.diagrams] == [
        "AD-01",
        "AD-02",
    ]

    # Next version: US-A-02 and AD-02 are unchanged; US-A-01 and AD-01 are gone
    second = assemble_document(
        _writer(),
        StoryWriterOutput(
            stories=[_story("US-A-02", "FR-02"), _story("S9", "FR-03")],
            use_cases=[
                UseCase(use_case_id="UC-01", name="Ajukan", story_ids=["US-A-02"])
            ],
        ),
        DiagramWriterOutput(
            diagrams=[_activity("AD-02", "FR-02"), _activity("new", "FR-03")]
        ),
        FR,
        previous=_previous(first),
    )
    assert [s.story_id for s in second.user_stories] == ["US-A-02", "US-A-03"]
    assert second.use_cases[0].use_case_id == "UC-01"
    assert [d.diagram_id for s in second.sections for d in s.diagrams] == [
        "AD-02",
        "AD-03",
    ]
    assert set(second.registry.retired) == {"US-A-01", "AD-01"}

    # A retired number is never handed out again, even when the agent asks for it
    third = assemble_document(
        _writer(),
        StoryWriterOutput(stories=[_story("US-A-01", "FR-01"), _story("S1", "FR-02")]),
        DiagramWriterOutput(),
        FR,
        previous=_previous(second),
    )
    assert [s.story_id for s in third.user_stories] == ["US-A-04", "US-A-05"]

    # A story with a different role cannot take over another role's ID
    other_role = assemble_document(
        _writer(),
        StoryWriterOutput(stories=[_story("US-A-02", "FR-02", role="Atasan")]),
        DiagramWriterOutput(),
        FR,
        previous=_previous(first),
    )
    assert other_role.user_stories[0].story_id == "US-B-01"


def test_rtm_markers_and_references_from_sources() -> None:
    doc = assemble_document(
        _writer(),
        StoryWriterOutput(stories=[_story("S1", "FR-01")]),
        DiagramWriterOutput(
            diagrams=[_activity("a", "FR-01")],
            pending=[
                PendingArtifact(
                    requirement_id="FR-01", kind="sequence", status="pending"
                ),
                PendingArtifact(
                    requirement_id="FR-02", kind="activity", status="needs_confirmation"
                ),
            ],
        ),
        [*FR, NF],
        sources=[
            SourceRef(
                source_id="note-1",
                title="Notulen kick-off",
                source_type="manual_note",
                author="po-1",
                date="2026-10-20",
            )
        ],
    )
    rows = {row.requirement_id: row for row in doc.traceability}
    assert rows["FR-01"].markers == {
        "uc": "pending",
        "cd": "not_applicable",
        "sq": "pending",
        "dfd": "not_applicable",
    }
    assert rows["FR-02"].markers["us"] == "pending"
    assert rows["FR-02"].markers["ad"] == "needs_confirmation"
    assert "NF-01" not in rows  # RULE-14: no NF in the FR matrix

    references = next(s for s in doc.sections if s.number == "1.4").tables[0]
    assert references.rows == [
        [
            "1",
            "Notulen kick-off",
            "2026-10-20",
            "Sumber manual_note, oleh po-1, ID note-1",
        ]
    ]


def _requirement(
    rid: str,
    statement: str = "Sistem harus menyimpan pengajuan.",
    *,
    kind: str = "functional",
    links: ArtifactLinks | None = None,
    links_by: str | None = "specbridge_agent",
    priority_basis: str = "stated",
    review: str = "approved",
) -> WorkspaceRequirementView:
    now = datetime.now(UTC)
    return WorkspaceRequirementView(
        requirement_id=rid,
        statement=statement,
        revision=1,
        priority="must",
        source_type="agent",
        created_by="specbridge_agent",
        rationale={"kind": kind, "priority_basis": priority_basis},
        review_state=review,
        implementation_status="not_started",
        artifact_links=links or ArtifactLinks(),
        artifact_links_updated_by=links_by,
        created_at=now,
        updated_at=now,
    )


def _validated_doc(nf_criterion: str = "Sesi berakhir pada menit ke-15"):
    return assemble_document(
        _writer(nf_criterion),
        StoryWriterOutput(
            stories=[_story("S1", "FR-01")],
            use_cases=[
                UseCase(use_case_id="UC1", name="Ajukan", requirement_ids=["FR-01"])
            ],
        ),
        DiagramWriterOutput(diagrams=[_activity("a", "FR-01")]),
        [FR[0], NF],
    )


def test_validation_report_passes_a_consistent_draft() -> None:
    doc = _validated_doc()
    report = validate_document(
        doc,
        [
            _requirement(
                "FR-01", links=ArtifactLinks(us=["US-A-01"], uc=["UC-01"], ad=["AD-01"])
            ),
            _requirement(
                "NF-01",
                doc.sections[0].content or "Sesi berakhir",
                kind="non_functional",
            ),
        ],
    )
    checks = {check.check: check.status for check in report.summary}
    assert list(checks) == [
        "SRS Structure",
        "Requirement Quality",
        "ID Consistency",
        "Artifact Relationships",
        "RTM Coverage",
        "Traceability Integrity",
    ]
    assert checks["ID Consistency"] == "PASS"
    assert checks["Traceability Integrity"] == "PASS"
    assert checks["RTM Coverage"] == "PASS"
    assert report.coverage.model_dump() == {
        "total_fr": 1,
        "fr_in_rtm": 1,
        "fr_with_story": 1,
        "fr_with_design": 1,
        "percent": 100.0,
        "unresolved": 0,
        "invalid": 0,
        "orphaned": 0,
    }
    assert report.nf_report[0].status == "PASS"
    assert report.ready_for_final


def test_validation_flags_broken_links_vague_wording_and_missing_criteria() -> None:
    doc = _validated_doc(nf_criterion="[PERLU KONFIRMASI] Kriteria belum ditentukan")
    report = validate_document(
        doc,
        [
            # A person linked a use case that does not exist
            _requirement(
                "FR-01",
                "Sistem harus cepat menampilkan status.",
                links=ArtifactLinks(us=["US-A-01"], uc=["UC-09"]),
                links_by="ba-1",
                priority_basis="assumed",
            ),
            _requirement("NF-01", "Sesi berakhir.", kind="non_functional"),
        ],
    )
    checks = {check.check: check.status for check in report.summary}
    assert checks["Traceability Integrity"] == "FAIL"
    assert checks["Requirement Quality"] == "WARNING"
    assert not report.ready_for_final
    issues = {(issue.location, issue.issue) for issue in report.issues}
    assert ("RTM FR-01", "UC-09 tidak ada di dokumen (diubah pengguna).") in issues
    assert any("cepat" in text for loc, text in issues if loc == "FR-01")
    assert any("Prioritas" in text for loc, text in issues if loc == "FR-01")
    assert report.nf_report[0].status == "WARNING"


def test_validation_of_a_draft_written_before_the_template() -> None:
    legacy = DocumentDraftOutput(
        title="Old",
        document_type="SRS",
        sections=[DocumentSection(title="Requirements", content="...")],
        rtm=[],
    )
    report = validate_document(legacy, [_requirement("REQ-001")])
    checks = {check.check: check.status for check in report.summary}
    assert checks["SRS Structure"] == "WARNING"
    assert checks["ID Consistency"] == "WARNING"  # REQ- ids from before FR/NF


def test_describe_changes_lists_new_and_retired_ids() -> None:
    before = {
        "sections": [
            {"number": "1.1", "content": "A"},
            {"number": "1.2", "content": "B"},
        ],
        "user_stories": [{"story_id": "US-A-01"}],
    }
    after = {
        "sections": [
            {"number": "1.1", "content": "A"},
            {"number": "1.2", "content": "C"},
        ],
        "user_stories": [{"story_id": "US-A-02"}],
    }
    changes, changed = describe_changes(after, before, ["FR-04"], {})
    assert changes == [
        "Kebutuhan baru: FR-04.",
        "Artefak baru: US-A-02.",
        "Artefak tidak lagi dipakai (ID dipensiunkan): US-A-01.",
    ]
    assert changed == ["1.2"]
    first, _ = describe_changes(after, None, [], {})
    assert first == ["Draft pertama dibuat oleh agent SpecBridge."]


@pytest.mark.asyncio
async def test_publication_is_blocked_while_validation_fails(repository) -> None:  # noqa: F811
    await _persist(repository, [("Karyawan dapat mengajukan cuti.", "new", None, None)])
    await repository.set_requirement_review(
        "mvp", "FR-01", state="approved", actor_id="ba-1"
    )
    await repository.update_artifact_links(
        "mvp", "FR-01", links=ArtifactLinks(uc=["UC-09"]), actor_id="ba-1"
    )
    workspace = await repository.get_project_workspace("mvp")
    assert workspace.document.validation is not None
    assert not workspace.document.validation.ready_for_final
    assert (
        workspace.document.history[0].changes[0]
        == "Draft pertama dibuat oleh agent SpecBridge."
    )

    with pytest.raises(ConflictError, match="validation fails"):
        await repository.publish_document(
            workspace.document.document_id, actor_id="po-1", decision_note="Setuju."
        )


def test_every_nf_is_listed_in_3_10() -> None:
    other = RequirementRef(
        requirement_id="NF-02",
        statement="Sistem harus memakai SSO perusahaan.",
        kind="constraint",
        priority="must",
        origin="existing",
    )
    doc = assemble_document(
        _writer(), StoryWriterOutput(), DiagramWriterOutput(), [FR[0], NF, other]
    )
    rows = next(s for s in doc.sections if s.number == "3.10").tables[0].rows
    assert [row[0] for row in rows] == ["NF-01", "NF-02"]
    # Left out by the writer: listed with what is known, the rest needs confirmation
    assert rows[1] == [
        "NF-02",
        "[PERLU KONFIRMASI]",
        "Sistem harus memakai SSO perusahaan.",
        "[PERLU KONFIRMASI] Kriteria belum ditentukan",
    ]
