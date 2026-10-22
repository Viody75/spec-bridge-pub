"""Reviewable items of a draft: every table row and every diagram.

Each item has a stable key, so a person's review and edits survive new versions:

- ``story:US-A-01`` and ``usecase:UC-02`` for rows of 3.3 and 3.4 (the row is
  rendered from the user story or use case, which is what gets edited);
- ``diagram:AD-01`` for a diagram;
- ``row:<section>:<first cell>`` for any other table row (2.3, 3.10, ...).

A person's edit sends the item back to review and wins over later agent rewrites.
Rejected stories, use cases and diagrams leave the traceability matrix.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.schemas import (
    DiagramArtifact,
    DocTable,
    DocumentDraftOutput,
    ItemEdit,
    ItemReview,
    RequirementRef,
    TraceabilityRow,
    UseCase,
    UserStory,
)
from app.workflow.document_assembly import (
    DIAGRAM_SECTIONS,
    MERMAID_HEADERS,
    IdAllocator,
    build_traceability,
    clean_mermaid,
    story_tables,
    use_case_table,
)

STORY_SECTION = "3.3"
USE_CASE_SECTION = "3.4"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40]


def _row_keys(number: str, table: DocTable, table_index: int) -> list[str]:
    # Tables whose first column is a running number are keyed by the next column
    column = (
        1
        if len(table.columns) > 1
        and table.columns[0].strip().casefold().startswith("no")
        else 0
    )
    keys: list[str] = []
    for row in table.rows:
        cell = row[column] if len(row) > column else ""
        base = f"row:{number}{f'.t{table_index + 1}' if table_index else ''}:{_slug(cell) or 'baris'}"
        key, suffix = base, 2
        while key in keys:
            key, suffix = f"{base}-{suffix}", suffix + 1
        keys.append(key)
    return keys


def assign_item_keys(doc: DocumentDraftOutput) -> None:
    """Give every row a key; rows that already have one keep it (edits do not rename)."""
    for section in doc.sections:
        if not section.number:
            continue
        for index, table in enumerate(section.tables):
            if section.number == STORY_SECTION:
                table.item_keys = [f"story:{row[0]}" for row in table.rows]
            elif section.number == USE_CASE_SECTION:
                table.item_keys = [f"usecase:{row[0]}" for row in table.rows]
            elif len(table.item_keys) != len(table.rows):
                table.item_keys = _row_keys(section.number, table, index)


def all_item_keys(doc: DocumentDraftOutput) -> list[str]:
    keys = [
        key
        for section in doc.sections
        for table in section.tables
        for key in table.item_keys
    ]
    keys += [
        f"diagram:{diagram.diagram_id}"
        for section in doc.sections
        for diagram in section.diagrams
    ]
    return list(dict.fromkeys(keys))


def review_of(doc: DocumentDraftOutput, key: str) -> ItemReview:
    return doc.item_reviews.get(key) or ItemReview()


def rejected_ids(doc: DocumentDraftOutput) -> set[str]:
    """IDs of rejected stories, use cases and diagrams."""
    return {
        key.split(":", 1)[1]
        for key, review in doc.item_reviews.items()
        if review.state == "rejected" and not key.startswith("row:")
    }


def _find_row(doc: DocumentDraftOutput, key: str) -> tuple[DocTable, int] | None:
    for section in doc.sections:
        for table in section.tables:
            if key in table.item_keys:
                return table, table.item_keys.index(key)
    return None


def _find_diagram(
    doc: DocumentDraftOutput, diagram_id: str
) -> tuple[list[DiagramArtifact], int] | None:
    for section in doc.sections:
        for index, diagram in enumerate(section.diagrams):
            if diagram.diagram_id == diagram_id:
                return section.diagrams, index
    return None


def item_snapshot(doc: DocumentDraftOutput, key: str) -> Any:
    """Comparable content of an item, or None when the draft does not have it."""
    kind, _, ident = key.partition(":")
    if kind == "story":
        story = next((s for s in doc.user_stories if s.story_id == ident), None)
        return story.model_dump() if story else None
    if kind == "usecase":
        case = next((u for u in doc.use_cases if u.use_case_id == ident), None)
        return case.model_dump() if case else None
    if kind == "diagram":
        found = _find_diagram(doc, ident)
        return found[0][found[1]].model_dump() if found else None
    found_row = _find_row(doc, key)
    if not found_row:
        return None
    table, index = found_row
    return {"columns": table.columns, "cells": table.rows[index]}


def _check_ids(owner: str, ids: list[str], allowed: set[str], what: str) -> list[str]:
    unknown = [item for item in ids if item not in allowed]
    if unknown:
        raise ValueError(f"{owner}: {', '.join(unknown)} is not an existing {what}")
    return list(dict.fromkeys(ids))


def apply_item_edit(
    doc: DocumentDraftOutput,
    key: str,
    edit: ItemEdit,
    *,
    functional_ids: set[str],
    requirement_ids: set[str],
) -> None:
    """Change one item's content; raises ValueError for content the rules do not allow."""
    kind, _, ident = key.partition(":")
    if kind == "story":
        index = next(
            (i for i, s in enumerate(doc.user_stories) if s.story_id == ident), None
        )
        if index is None:
            raise LookupError(key)
        story = doc.user_stories[index]
        reqs = (
            story.requirement_ids
            if edit.requirement_ids is None
            else edit.requirement_ids
        )
        if not reqs:
            raise ValueError(
                f"{ident}: a user story must reference at least one functional requirement"
            )
        doc.user_stories[index] = story.model_copy(
            update={
                "role": (edit.role or story.role).strip(),
                "story": (edit.story or story.story).strip(),
                "acceptance_criteria": [
                    item.strip()
                    for item in (
                        story.acceptance_criteria
                        if edit.acceptance_criteria is None
                        else edit.acceptance_criteria
                    )
                    if item.strip()
                ],
                "requirement_ids": _check_ids(
                    ident, reqs, functional_ids, "functional requirement"
                ),
            }
        )
    elif kind == "usecase":
        index = next(
            (i for i, u in enumerate(doc.use_cases) if u.use_case_id == ident), None
        )
        if index is None:
            raise LookupError(key)
        case = doc.use_cases[index]
        stories = {s.story_id for s in doc.user_stories}
        doc.use_cases[index] = case.model_copy(
            update={
                "name": (edit.name or case.name).strip(),
                "actors": [
                    a.strip()
                    for a in (case.actors if edit.actors is None else edit.actors)
                    if a.strip()
                ],
                "requirement_ids": _check_ids(
                    ident,
                    case.requirement_ids
                    if edit.requirement_ids is None
                    else edit.requirement_ids,
                    functional_ids,
                    "functional requirement",
                ),
                "story_ids": _check_ids(
                    ident,
                    case.story_ids if edit.story_ids is None else edit.story_ids,
                    stories,
                    "user story",
                ),
            }
        )
    elif kind == "diagram":
        found = _find_diagram(doc, ident)
        if not found:
            raise LookupError(key)
        diagrams, index = found
        diagram = diagrams[index]
        source = (
            diagram.mermaid if edit.mermaid is None else clean_mermaid(edit.mermaid)
        )
        if not source or not source.split(maxsplit=1)[0].startswith(
            MERMAID_HEADERS[diagram.kind]
        ):
            expected = " or ".join(MERMAID_HEADERS[diagram.kind])
            raise ValueError(f"{ident}: Mermaid source must start with {expected}")
        diagrams[index] = diagram.model_copy(
            update={
                "title": (edit.title or diagram.title).strip(),
                "description": diagram.description
                if edit.description is None
                else edit.description.strip(),
                "mermaid": source,
                "requirement_ids": _check_ids(
                    ident,
                    diagram.requirement_ids
                    if edit.requirement_ids is None
                    else edit.requirement_ids,
                    requirement_ids,
                    "requirement",
                ),
            }
        )
    else:
        found_row = _find_row(doc, key)
        if not found_row:
            raise LookupError(key)
        table, index = found_row
        if edit.cells is None or len(edit.cells) != len(table.columns):
            raise ValueError(f"a row of this table has {len(table.columns)} cells")
        table.rows[index] = [cell.strip() for cell in edit.cells]


def refresh_derived(
    doc: DocumentDraftOutput, requirements: list[RequirementRef]
) -> None:
    """Rebuild what is derived from stories, use cases and diagrams (RULE-15).

    3.3 and 3.4 tables follow the stories and use cases; the matrix leaves out
    rejected artifacts. A diagram marker ([BELUM TERSEDIA] / [PERLU KONFIRMASI])
    stays while its cell is still empty.
    """
    ids = IdAllocator(doc.registry)
    for section in doc.sections:
        if section.number == STORY_SECTION and doc.user_stories:
            section.tables = story_tables(doc.user_stories, ids)
        elif section.number == USE_CASE_SECTION:
            section.tables = use_case_table(doc.use_cases)
    rejected = rejected_ids(doc)
    stories = [s for s in doc.user_stories if s.story_id not in rejected]
    use_cases = [u for u in doc.use_cases if u.use_case_id not in rejected]
    diagrams = [
        d
        for section in doc.sections
        for d in section.diagrams
        if d.diagram_id not in rejected
    ]
    previous = {row.requirement_id: row.markers for row in doc.traceability}
    rows: list[TraceabilityRow] = []
    for row in build_traceability(requirements, stories, use_cases, diagrams):
        kept = previous.get(row.requirement_id, {})
        row.markers = {
            column: kept.get(column, marker) if marker == "not_applicable" else marker
            for column, marker in row.markers.items()
        }
        rows.append(row)
    doc.traceability = rows
    assign_item_keys(doc)


def _put(doc: DocumentDraftOutput, key: str, snapshot: dict[str, Any]) -> None:
    """Place a person's version of an item into a draft (replace or re-add)."""
    kind, _, ident = key.partition(":")
    if kind == "story":
        story = UserStory.model_validate(snapshot)
        index = next(
            (i for i, s in enumerate(doc.user_stories) if s.story_id == ident), None
        )
        if index is None:
            doc.user_stories.append(story)
        else:
            doc.user_stories[index] = story
    elif kind == "usecase":
        case = UseCase.model_validate(snapshot)
        index = next(
            (i for i, u in enumerate(doc.use_cases) if u.use_case_id == ident), None
        )
        if index is None:
            doc.use_cases.append(case)
        else:
            doc.use_cases[index] = case
    elif kind == "diagram":
        diagram = DiagramArtifact.model_validate(snapshot)
        found = _find_diagram(doc, ident)
        if found:
            found[0][found[1]] = diagram
        else:
            number = DIAGRAM_SECTIONS[diagram.kind][0]
            section = next((s for s in doc.sections if s.number == number), None)
            if section is not None:
                section.diagrams.append(diagram)
    else:
        found_row = _find_row(doc, key)
        if found_row:
            table, index = found_row
            if len(snapshot["cells"]) == len(table.columns):
                table.rows[index] = list(snapshot["cells"])
            return
        number = key.split(":")[1].split(".t")[0]
        section = next((s for s in doc.sections if s.number == number), None)
        if section is None:
            return
        target = next(
            (t for t in section.tables if t.columns == snapshot["columns"]), None
        )
        if target is None:
            target = DocTable(columns=snapshot["columns"], rows=[], item_keys=[])
            section.tables.append(target)
        target.rows.append(list(snapshot["cells"]))
        target.item_keys.append(key)


def carry_reviews(new: DocumentDraftOutput, previous: DocumentDraftOutput) -> None:
    """Bring people's reviews and edits into a new agent version.

    An edited item keeps the person's content (re-added if the agents dropped it).
    An unedited item keeps its review while the agents left it unchanged; an item
    the agents changed goes back to pending.
    """
    for key, review in previous.item_reviews.items():
        before = item_snapshot(previous, key)
        if before is None:
            continue
        if review.edited_by:
            _put(new, key, before)
            new.item_reviews[key] = review
            continue
        after = item_snapshot(new, key)
        if after is None:
            continue
        if after == before:
            new.item_reviews[key] = review
        elif review.state != "pending":
            new.item_reviews[key] = ItemReview(
                note=f"Diubah agent setelah {'disetujui' if review.state == 'approved' else 'ditolak'}; perlu ditinjau ulang."
            )
