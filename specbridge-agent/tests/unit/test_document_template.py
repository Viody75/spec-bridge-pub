from __future__ import annotations

import pytest

# Reuse the in-memory repository fixture and helpers
import tests.unit.test_jobs_and_human_edits as helpers
from app.domain.schemas import (
    ArtifactLinks,
    DiagramArtifact,
    DiagramWriterOutput,
    DocTable,
    DocumentWriterOutput,
    ManualRequirementCreate,
    RequirementRef,
    RtmRow,
    StoryWriterOutput,
    TraceabilityRow,
    UseCase,
    UserStory,
    WriterSection,
)
from app.infrastructure.repository import replace_candidate_ids
from app.workflow.document_assembly import TEMPLATE, assemble_document, clean_mermaid
from tests.unit.test_jobs_and_human_edits import (  # noqa: F401
    _persist,
    repository,
)

REQUIREMENTS = [
    RequirementRef(
        requirement_id="FR-01",
        statement="Sistem harus memungkinkan karyawan mengajukan cuti.",
        kind="functional",
        priority="must",
        origin="existing",
    ),
    RequirementRef(
        requirement_id="CAND-001",
        statement="Sistem harus mengirim email saat status cuti berubah.",
        kind="functional",
        priority="should",
        origin="candidate",
    ),
    RequirementRef(
        requirement_id="CAND-002",
        statement="Sesi harus berakhir setelah 15 menit tidak aktif.",
        kind="non_functional",
        priority="must",
        origin="candidate",
    ),
]


def _writer() -> DocumentWriterOutput:
    return DocumentWriterOutput(
        title="SRS Aplikasi Cuti",
        document_type="SRS",
        sections=[
            WriterSection(number="1.1", title="Tujuan", content="Dokumen ini ..."),
            WriterSection(
                number="1.3",
                title="Daftar Istilah",
                tables=[
                    DocTable(
                        columns=["Istilah", "Definisi", "extra"],
                        rows=[
                            ["SRS", "Software Requirement Specification", "x"],
                            ["HR"],
                        ],
                    )
                ],
            ),
            WriterSection(number="9.1", title="Catatan Tambahan", content="Ekstra."),
        ],
        rtm=[],
    )


def _stories() -> StoryWriterOutput:
    return StoryWriterOutput(
        stories=[
            UserStory(
                story_id="S1",
                role="Karyawan",
                requirement_ids=["FR-01", "FR-99"],
                story="Sebagai karyawan, saya ingin mengajukan cuti, sehingga ...",
                acceptance_criteria=["Form tersimpan", "Saldo kurang ditolak"],
            ),
            UserStory(
                story_id="S2",
                role="Atasan",
                requirement_ids=["CAND-001"],
                story="Sebagai atasan, saya ingin diberi tahu, sehingga ...",
            ),
            UserStory(
                story_id="S3",
                role="karyawan",
                requirement_ids=[],
                story="Sebagai karyawan, saya ingin melihat saldo, sehingga ...",
            ),
        ],
        use_cases=[
            UseCase(
                use_case_id="UC1",
                name="Ajukan cuti",
                actors=["Karyawan"],
                story_ids=["S2"],
            ),
        ],
    )


def _diagrams() -> DiagramWriterOutput:
    return DiagramWriterOutput(
        diagrams=[
            DiagramArtifact(
                diagram_id="x",
                kind="activity",
                title="Pengajuan cuti",
                mermaid='```mermaid\nflowchart TD\n  A(("Mulai")) --> B["UC1 ajukan"]\n```',
                requirement_ids=["FR-01"],
            ),
            DiagramArtifact(
                diagram_id="y",
                kind="sequence",
                title="Notifikasi",
                mermaid="sequenceDiagram\n  A->>B: kirim",
                requirement_ids=["CAND-001", "CAND-002"],
            ),
            # Wrong diagram type for the kind: dropped
            DiagramArtifact(
                diagram_id="z",
                kind="class",
                title="Rusak",
                mermaid="flowchart TD\n A-->B",
            ),
            DiagramArtifact(
                diagram_id="c",
                kind="context",
                title="Konteks",
                mermaid="flowchart LR\n K-->S",
                requirement_ids=["FR-01", "CAND-001"],
            ),
        ]
    )


def test_assembly_follows_the_template_and_numbering_rules() -> None:
    doc = assemble_document(_writer(), _stories(), _diagrams(), REQUIREMENTS)
    numbers = [section.number for section in doc.sections]

    assert numbers[: len(TEMPLATE)] == [number for number, *_ in TEMPLATE]
    assert numbers[-1] == "9.1"  # the writer's extra section is kept after the template
    sections = {section.number: section for section in doc.sections}
    assert sections["1.1"].content == "Dokumen ini ..."
    # Template columns win; a missing cell needs confirmation
    glossary = sections["1.3"].tables[0]
    assert glossary.columns == ["Istilah / Singkatan", "Definisi"]
    assert glossary.rows == [
        ["SRS", "Software Requirement Specification"],
        ["HR", "[PERLU KONFIRMASI]"],
    ]
    assert sections["1.2"].content.startswith("Tidak berlaku")
    assert sections["1.4"].tables == []  # no stored sources in this test
    assert sections["3.2"].kind == "functional_requirements"
    assert sections["4"].kind == "traceability"

    # US-<role letter>-<nn>: roles in order of appearance, case-insensitive
    assert [s.story_id for s in doc.user_stories] == ["US-A-01", "US-B-01", "US-A-02"]
    assert doc.registry.role_codes == {"karyawan": "A", "atasan": "B"}
    assert doc.user_stories[0].requirement_ids == ["FR-01"]
    # FR-99 does not exist: dropped and reported
    assert doc.invalid_references == ["US-A-01 → FR-99"]
    assert [t.caption for t in sections["3.3"].tables] == [
        "Role A: Karyawan",
        "Role B: Atasan",
    ]
    assert doc.use_cases[0].use_case_id == "UC-01"
    assert doc.use_cases[0].story_ids == ["US-B-01"]

    assert [d.diagram_id for d in sections["3.5"].diagrams] == ["AD-01"]
    activity = sections["3.5"].diagrams[0]
    assert activity.mermaid.startswith("flowchart TD")  # fences removed
    assert "UC-01 ajukan" in activity.mermaid  # renumbered inside labels
    assert sections["3.6"].diagrams == []
    # A mandatory section whose diagram is missing, not irrelevant
    assert sections["3.6"].content.startswith("[BELUM TERSEDIA]")
    assert [d.diagram_id for d in sections["3.8"].diagrams] == ["CD-01"]


def test_traceability_only_holds_declared_relations() -> None:
    doc = assemble_document(_writer(), _stories(), _diagrams(), REQUIREMENTS)
    rows = {row.requirement_id: row for row in doc.traceability}

    # Non-functional requirements are not rows of the FR matrix
    assert set(rows) == {"FR-01", "CAND-001"}
    assert rows["FR-01"].model_dump() == {
        "requirement_id": "FR-01",
        "us": ["US-A-01"],
        "uc": [],
        "ad": ["AD-01"],
        "cd": ["CD-01"],
        "sq": [],
        "dfd": [],
        # Every FR needs a use case; diagrams nobody flagged are not relevant
        "markers": {"uc": "pending", "sq": "not_applicable", "dfd": "not_applicable"},
    }
    # The use case covers CAND-001 through its story
    assert rows["CAND-001"].uc == ["UC-01"]
    assert rows["CAND-001"].sq == ["SQ-01"]


def test_clean_mermaid_and_candidate_replacement() -> None:
    assert clean_mermaid("```mermaid\nsequenceDiagram\nA->>B: x\n```") == (
        "sequenceDiagram\nA->>B: x"
    )
    value = {"a": ["CAND-001 dan CAND-002", {"b": "CAND-0010"}], "n": 3}
    assert replace_candidate_ids(value, {"CAND-001": "FR-04"}) == {
        "a": ["FR-04 dan CAND-002", {"b": "CAND-0010"}],
        "n": 3,
    }


@pytest.mark.asyncio
async def test_saved_draft_uses_final_ids_and_agent_rtm_links(
    repository,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await repository.add_manual_requirement(
        "mvp",
        ManualRequirementCreate(
            text="Sesi berakhir setelah 15 menit.",
            rationale="Keamanan.",
            actor_id="po-1",
            kind="non_functional",
        ),
    )
    original = helpers._result

    def with_traceability(run_id, plan, **kwargs):
        result = original(run_id, plan, **kwargs)
        result.document.sections[0].content = "Mencakup CAND-001."
        result.document.traceability = [
            TraceabilityRow(requirement_id="CAND-001", us=["US-1-01"], ad=["AD-01"]),
            TraceabilityRow(requirement_id="CAND-002", uc=["UC-01"]),
        ]
        return result

    with monkeypatch.context() as patch:
        patch.setattr(helpers, "_result", with_traceability)
        await _persist(
            repository,
            [
                ("Karyawan dapat mengajukan cuti.", "new", None, None),
                ("Atasan dapat menyetujui cuti.", "new", None, None),
            ],
        )

    workspace = await repository.get_project_workspace("mvp")
    reqs = {req.requirement_id: req for req in workspace.requirements}
    # The manual non-functional item is NF-01; the agents' functional items FR-01/02
    assert set(reqs) == {"NF-01", "FR-01", "FR-02"}
    assert workspace.document.content.sections[0].content == "Mencakup FR-01."
    assert reqs["FR-01"].artifact_links.us == ["US-1-01"]
    assert reqs["FR-01"].artifact_links.ad == ["AD-01"]
    assert reqs["FR-01"].artifact_links_updated_by == "specbridge_agent"
    assert reqs["FR-02"].artifact_links.uc == ["UC-01"]

    # A person's correction is kept when the agents write the next version
    await repository.update_artifact_links(
        "mvp", "FR-01", links=ArtifactLinks(uc=["UC-09"]), actor_id="ba-1"
    )
    await _persist(repository, [("Laporan bulanan diekspor.", "new", None, None)])
    workspace = await repository.get_project_workspace("mvp")
    reqs = {req.requirement_id: req for req in workspace.requirements}
    assert reqs["FR-01"].artifact_links.uc == ["UC-09"]
    assert reqs["FR-01"].artifact_links_updated_by == "ba-1"
    # Agent links that the new version no longer declares are cleared
    assert reqs["FR-02"].artifact_links.uc == []


def test_rtm_row_type_still_validates() -> None:
    # Older drafts without template fields still load
    RtmRow(
        candidate_id="CAND-001",
        requirement_statement="x",
        source_ids=[],
        review_status="approve",
        verification_basis="y",
    )


@pytest.mark.asyncio
async def test_writer_context_for_a_project_that_is_not_stored(repository) -> None:  # noqa: F811
    # Direct workflow runs (eval, playground, A2A) use project ids never created
    context = await repository.get_writer_context("eval-only")
    assert (context.project_name, context.requirements, context.sources) == (
        "eval-only",
        [],
        [],
    )
    assert context.previous.stories == []
