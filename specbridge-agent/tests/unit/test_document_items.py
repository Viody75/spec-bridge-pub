"""Reviewable items: every table row and diagram can be approved, rejected and edited."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.domain.schemas import (
    DiagramWriterOutput,
    DocTable,
    DocumentDraftOutput,
    ItemEdit,
    ItemReview,
    StoryWriterOutput,
    UseCase,
    WriterSection,
)
from app.infrastructure.models import DocumentVersion
from app.infrastructure.repository import ConflictError
from app.workflow.document_assembly import assemble_document
from app.workflow.document_items import (
    all_item_keys,
    apply_item_edit,
    carry_reviews,
    refresh_derived,
    review_of,
)
from tests.unit.test_jobs_and_human_edits import (  # noqa: F401
    _request,
    _result,
    repository,
)
from tests.unit.test_srs_rules import FR, NF, _activity, _story, _writer


def _draft(stories=None, diagrams=None, extra_sections=()):
    writer = _writer()
    writer.sections += [
        WriterSection(
            number="2.2",
            title="Fungsi Produk",
            tables=[
                DocTable(
                    columns=["No.", "Fungsi Utama", "Ringkasan"],
                    rows=[["1", "Pengajuan cuti", "a"], ["2", "Pengajuan cuti", "b"]],
                )
            ],
        ),
        *extra_sections,
    ]
    return assemble_document(
        writer,
        StoryWriterOutput(
            stories=stories or [_story("S1", "FR-01"), _story("S2", "FR-02")],
            use_cases=[UseCase(use_case_id="UC1", name="Ajukan", story_ids=["S1"])],
        ),
        DiagramWriterOutput(diagrams=diagrams or [_activity("a", "FR-01")]),
        [*FR, NF],
    )


def test_every_row_and_diagram_has_a_stable_key() -> None:
    doc = _draft()
    keys = all_item_keys(doc)
    assert "story:US-A-01" in keys and "story:US-A-02" in keys
    assert "usecase:UC-01" in keys
    assert "diagram:AD-01" in keys
    assert "row:3.10:nf-01" in keys
    # "No." tables are keyed by the next column; equal names get a suffix
    assert "row:2.2:pengajuan-cuti" in keys and "row:2.2:pengajuan-cuti-2" in keys
    assert all(review_of(doc, key).state == "pending" for key in keys)


def test_edits_follow_the_rules() -> None:
    doc = _draft()
    functional = {"FR-01", "FR-02", "FR-03"}
    every = functional | {"NF-01"}

    def edit(key, **fields):
        apply_item_edit(
            doc,
            key,
            ItemEdit(actor_id="ba-1", **fields),
            functional_ids=functional,
            requirement_ids=every,
        )

    with pytest.raises(ValueError, match="not an existing functional requirement"):
        edit("story:US-A-01", requirement_ids=["NF-01"])
    with pytest.raises(ValueError, match="at least one functional requirement"):
        edit("story:US-A-01", requirement_ids=[])
    with pytest.raises(ValueError, match="must start with"):
        edit("diagram:AD-01", mermaid="sequenceDiagram\n A->>B: x")
    with pytest.raises(ValueError, match="has 4 cells"):
        edit("row:3.10:nf-01", cells=["NF-01", "x"])

    edit(
        "story:US-A-01",
        story="Sebagai karyawan, saya ingin melihat saldo.",
        requirement_ids=["FR-03"],
    )
    edit(
        "diagram:AD-01",
        mermaid='```mermaid\nflowchart LR\n A["x"] --> B["y"]\n```',
        requirement_ids=["FR-02"],
    )
    edit("row:3.10:nf-01", cells=["NF-01", "Keamanan", "Sesi berakhir", "Menit ke-15"])
    story = doc.user_stories[0]
    assert (story.story_id, story.requirement_ids) == ("US-A-01", ["FR-03"])
    diagram = next(d for s in doc.sections for d in s.diagrams)
    assert diagram.mermaid == 'flowchart LR\n A["x"] --> B["y"]'


def test_rejected_artifacts_leave_the_matrix() -> None:
    doc = _draft()
    rows = {row.requirement_id: row for row in doc.traceability}
    assert rows["FR-01"].us == ["US-A-01"] and rows["FR-01"].ad == ["AD-01"]

    doc.item_reviews["story:US-A-01"] = ItemReview(state="rejected")
    doc.item_reviews["diagram:AD-01"] = ItemReview(state="rejected")
    refresh_derived(doc, [*FR, NF])
    rows = {row.requirement_id: row for row in doc.traceability}
    assert rows["FR-01"].us == [] and rows["FR-01"].markers["us"] == "pending"
    assert rows["FR-01"].ad == []
    # The rejected story stays visible in 3.3 (marked rejected in the UI)
    assert any("story:US-A-01" in t.item_keys for s in doc.sections for t in s.tables)


def test_reviews_and_human_edits_carry_into_the_next_version() -> None:
    previous = _draft()
    previous.item_reviews["row:3.10:nf-01"] = ItemReview(
        state="approved", actor_id="ba-1"
    )
    previous.item_reviews["row:2.2:pengajuan-cuti"] = ItemReview(
        state="approved", actor_id="ba-1"
    )
    apply_item_edit(
        previous,
        "story:US-A-02",
        ItemEdit(actor_id="po-1", story="Ditulis ulang oleh PO."),
        functional_ids={"FR-01", "FR-02"},
        requirement_ids={"FR-01", "FR-02"},
    )
    previous.item_reviews["story:US-A-02"] = ItemReview(
        state="pending", edited_by="po-1"
    )

    # The agents' next version: NF row unchanged, 2.2 row rewritten, US-A-02 dropped
    new = _draft(stories=[_story("US-A-01", "FR-01")])
    table = next(s for s in new.sections if s.number == "2.2").tables[0]
    table.rows[0] = ["1", "Pengajuan cuti", "rumusan baru"]
    carry_reviews(new, previous)

    assert review_of(new, "row:3.10:nf-01").state == "approved"
    reworded = review_of(new, "row:2.2:pengajuan-cuti")
    assert reworded.state == "pending" and "Diubah agent" in (reworded.note or "")
    # The person's story comes back with their wording
    kept = next(s for s in new.user_stories if s.story_id == "US-A-02")
    assert kept.story == "Ditulis ulang oleh PO."
    assert review_of(new, "story:US-A-02").edited_by == "po-1"


async def _persist_template(repo, project_id: str = "mvp") -> uuid.UUID:
    request = _request()
    await repo.add_sources(project_id, request.sources)
    run_id = await repo.create_run(request)
    result = _result(
        run_id,
        [
            ("Karyawan dapat mengajukan cuti.", "new", None, None),
            ("Atasan dapat menyetujui cuti.", "new", None, None),
        ],
    )
    # A template draft whose artifacts refer to the run's candidates
    candidates = _draft(
        stories=[_story("S1", "FR-01"), _story("S2", "FR-02")],
        diagrams=[_activity("a", "FR-01")],
    )
    text = (
        json.dumps(candidates.model_dump(mode="json"))
        .replace('"FR-01"', '"CAND-001"')
        .replace('"FR-02"', '"CAND-002"')
    )
    result.document = DocumentDraftOutput.model_validate_json(text)
    await repo.persist_result(result)
    return run_id


@pytest.mark.asyncio
async def test_item_review_and_edit_through_the_repository(repository) -> None:  # noqa: F811
    await _persist_template(repository)
    workspace = await repository.get_project_workspace("mvp")
    reqs = {r.requirement_id: r for r in workspace.requirements}
    assert reqs["FR-01"].artifact_links.us == ["US-A-01"]

    # Moving the story to FR-02 updates the matrix and sends the story back to review
    view = await repository.edit_item(
        "mvp",
        "story:US-A-01",
        ItemEdit(actor_id="ba-1", requirement_ids=["FR-02"]),
    )
    assert view.content.item_reviews["story:US-A-01"].edited_by == "ba-1"
    reqs = {
        r.requirement_id: r
        for r in (await repository.get_project_workspace("mvp")).requirements
    }
    assert reqs["FR-01"].artifact_links.us == []
    assert reqs["FR-02"].artifact_links.us == ["US-A-01", "US-A-02"]

    with pytest.raises(ValueError):
        await repository.edit_item(
            "mvp", "story:US-A-01", ItemEdit(actor_id="ba-1", requirement_ids=["FR-99"])
        )

    # FINAL needs every row and diagram approved
    for rid in reqs:
        await repository.set_requirement_review(
            "mvp", rid, state="approved", actor_id="ba-1"
        )
    document_id = view.document_id
    with pytest.raises(ConflictError, match="await approval"):
        await repository.publish_document(
            document_id, actor_id="po-1", decision_note="Setuju."
        )

    keys = all_item_keys(view.content)
    approved = await repository.review_items(
        "mvp", keys, action="approve", actor_id="ba-1"
    )
    assert all(review_of(approved.content, key).state == "approved" for key in keys)
    await repository.publish_document(
        document_id, actor_id="po-1", decision_note="Setuju."
    )

    # Editing a published document opens a new draft version
    reopened = await repository.edit_item(
        "mvp",
        "row:3.10:nf-01",
        ItemEdit(
            actor_id="po-1", cells=["NF-01", "Keamanan", "Sesi berakhir", "Menit ke-15"]
        ),
    )
    assert reopened.version == 2 and not reopened.published
    async with repository._sessions() as session:
        published = await session.scalar(
            select(DocumentVersion).where(DocumentVersion.version == 1)
        )
        assert published.published
