"""Builds the SRS in the SpecBridge template from the writer agents' output.

The agents write content; this module owns structure. Section order and titles,
artifact IDs (US-X-YY, UC-XX, AD-XX, CD-XX, SQ-XX, DFD-XX) and the traceability
matrix are deterministic, so every draft follows the template and the matrix only
holds relations an artifact declared, never invented ones.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.schemas import (
    DiagramArtifact,
    DiagramKind,
    DiagramWriterOutput,
    DocTable,
    DocumentDraftOutput,
    DocumentSection,
    DocumentWriterOutput,
    RequirementRef,
    SectionKind,
    StoryWriterOutput,
    TraceabilityRow,
    UseCase,
    UserStory,
)

# (number, title, kind, table columns the writer fills; None = generated here)
TEMPLATE: list[tuple[str, str, SectionKind, list[str] | None]] = [
    ("1", "PENDAHULUAN", "text", []),
    ("1.1", "Tujuan", "text", []),
    ("1.2", "Lingkup Masalah", "text", []),
    ("1.3", "Daftar Istilah", "text", ["Istilah / Singkatan", "Definisi"]),
    (
        "1.4",
        "Referensi",
        "text",
        ["No.", "Judul / Sumber", "Versi / Tanggal", "Tautan / Keterangan"],
    ),
    ("1.5", "Deskripsi Umum", "text", []),
    ("2", "DESKRIPSI KESELURUHAN", "text", []),
    ("2.1", "Perspektif Produk", "text", []),
    ("2.2", "Fungsi Produk", "text", ["No.", "Fungsi Utama", "Ringkasan"]),
    (
        "2.3",
        "Karakteristik Pengguna",
        "text",
        ["Pengguna / Role", "Karakteristik", "Kebutuhan Utama"],
    ),
    ("2.4", "Batasan Sistem", "text", ["No.", "Batasan", "Dampak / Keterangan"]),
    (
        "2.5",
        "Asumsi dan Ketergantungan",
        "text",
        ["Jenis (Asumsi/Ketergantungan)", "Uraian", "Dampak jika tidak terpenuhi"],
    ),
    (
        "2.6",
        "Stakeholder Map",
        "text",
        ["Stakeholder", "Pengaruh", "Kepentingan", "Strategi Pelibatan"],
    ),
    ("3", "KEBUTUHAN KHUSUS", "text", []),
    (
        "3.1",
        "Kebutuhan Antarmuka Eksternal",
        "text",
        [
            "ID (jika digunakan)",
            "Jenis Antarmuka",
            "Deskripsi / Persyaratan",
            "Sistem Eksternal / Catatan",
        ],
    ),
    ("3.2", "Kebutuhan Fungsionalitas", "functional_requirements", []),
    ("3.3", "User Story dan Acceptance Criteria", "text", None),
    ("3.4", "Use Case Diagram", "text", None),
    ("3.5", "Activity Diagram", "text", None),
    ("3.6", "Class Diagram", "text", None),
    ("3.7", "Sequence Diagram", "text", None),
    ("3.8", "Context Diagram", "text", None),
    ("3.9", "Data Flow Diagram Level 1", "text", None),
    (
        "3.10",
        "Kebutuhan Non-Fungsionalitas",
        "non_functional_requirements",
        ["NF-ID", "Kategori", "Kebutuhan Non-Fungsional", "Kriteria / Ukuran Uji"],
    ),
    (
        "3.11",
        "Atribut Sistem Perangkat Lunak",
        "text",
        ["Atribut", "Kebutuhan / Target", "Cara Verifikasi"],
    ),
    ("4", "TRACEABILITY MATRIX", "traceability", None),
]

# Sections the writer agent fills (text and tables)
WRITER_SECTIONS = [
    (number, title, columns)
    for number, title, _, columns in TEMPLATE
    if columns is not None and "." in number
]

DIAGRAM_SECTIONS: dict[DiagramKind, tuple[str, str, str]] = {
    # kind: (section number, id prefix, caption prefix)
    "use_case": ("3.4", "UCD", "Use Case Diagram"),
    "activity": ("3.5", "AD", "Activity Diagram"),
    "class": ("3.6", "CLS", "Class Diagram"),
    "sequence": ("3.7", "SQ", "Sequence Diagram"),
    "context": ("3.8", "CD", "Context Diagram"),
    "dfd": ("3.9", "DFD", "DFD Level 1"),
}

MERMAID_HEADERS: dict[DiagramKind, tuple[str, ...]] = {
    "use_case": ("flowchart", "graph"),
    "activity": ("flowchart", "graph", "stateDiagram"),
    "class": ("classDiagram",),
    "sequence": ("sequenceDiagram",),
    "context": ("flowchart", "graph"),
    "dfd": ("flowchart", "graph"),
}

FUNCTIONAL_KINDS = {"functional", "business_rule"}
MAX_STORIES = 40
MAX_USE_CASES = 20
NOT_APPLICABLE = (
    "Tidak berlaku: sumber yang diunggah belum memuat informasi untuk bagian ini."
)


def section_sort_key(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in number.split(".") if part.isdigit())


def clean_mermaid(source: str) -> str:
    """Strip Markdown fences the model sometimes adds around Mermaid source."""
    text = source.strip()
    text = re.sub(r"^```(?:mermaid)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _replace_ids(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    pattern = re.compile(
        r"(?<![A-Za-z0-9-])("
        + "|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True))
        + r")(?![A-Za-z0-9])"
    )
    return pattern.sub(lambda match: mapping[match.group(1)], text)


def _known(ids: Iterable[str], allowed: set[str]) -> list[str]:
    return list(dict.fromkeys(item for item in ids if item in allowed))


def renumber_stories(
    stories: StoryWriterOutput, allowed_requirements: set[str]
) -> tuple[list[UserStory], list[UseCase], dict[str, str]]:
    """US-<role code>-<nn> in order of appearance; UC-<nn>. Returns an old→new map."""
    role_codes: dict[str, int] = {}
    per_role: dict[int, int] = {}
    mapping: dict[str, str] = {}
    renumbered: list[UserStory] = []
    for story in stories.stories[:MAX_STORIES]:
        role_key = story.role.strip().casefold()
        code = role_codes.setdefault(role_key, len(role_codes) + 1)
        per_role[code] = per_role.get(code, 0) + 1
        new_id = f"US-{code}-{per_role[code]:02d}"
        mapping[story.story_id] = new_id
        renumbered.append(
            story.model_copy(
                update={
                    "story_id": new_id,
                    "requirement_ids": _known(
                        story.requirement_ids, allowed_requirements
                    ),
                }
            )
        )
    use_cases: list[UseCase] = []
    for index, use_case in enumerate(stories.use_cases[:MAX_USE_CASES], start=1):
        new_id = f"UC-{index:02d}"
        mapping[use_case.use_case_id] = new_id
        use_cases.append(use_case.model_copy(update={"use_case_id": new_id}))
    story_ids = {story.story_id for story in renumbered}
    use_cases = [
        use_case.model_copy(
            update={
                "requirement_ids": _known(
                    use_case.requirement_ids, allowed_requirements
                ),
                "story_ids": _known(
                    (mapping.get(item, item) for item in use_case.story_ids),
                    story_ids,
                ),
            }
        )
        for use_case in use_cases
    ]
    return renumbered, use_cases, mapping


def renumber_diagrams(
    output: DiagramWriterOutput,
    allowed_requirements: set[str],
    story_mapping: dict[str, str],
) -> list[DiagramArtifact]:
    counters: dict[str, int] = {}
    diagrams: list[DiagramArtifact] = []
    for diagram in output.diagrams:
        source = clean_mermaid(diagram.mermaid)
        if not source.split(maxsplit=1)[0].startswith(MERMAID_HEADERS[diagram.kind]):
            # Unusable source: the frontend would only show an error
            continue
        _, prefix, _ = DIAGRAM_SECTIONS[diagram.kind]
        counters[prefix] = counters.get(prefix, 0) + 1
        diagrams.append(
            diagram.model_copy(
                update={
                    "diagram_id": f"{prefix}-{counters[prefix]:02d}",
                    # Use case and story IDs inside labels follow the renumbering
                    "mermaid": _replace_ids(source, story_mapping),
                    "description": _replace_ids(diagram.description, story_mapping),
                    "requirement_ids": _known(
                        diagram.requirement_ids, allowed_requirements
                    ),
                }
            )
        )
    return diagrams


def build_traceability(
    requirements: list[RequirementRef],
    stories: list[UserStory],
    use_cases: list[UseCase],
    diagrams: list[DiagramArtifact],
) -> list[TraceabilityRow]:
    story_reqs = {story.story_id: set(story.requirement_ids) for story in stories}
    rows: list[TraceabilityRow] = []
    for requirement in requirements:
        if requirement.kind not in FUNCTIONAL_KINDS:
            continue
        rid = requirement.requirement_id
        us = [story.story_id for story in stories if rid in story.requirement_ids]
        # A use case covers a requirement directly, or through one of its stories
        uc = [
            use_case.use_case_id
            for use_case in use_cases
            if rid in use_case.requirement_ids
            or any(rid in story_reqs.get(story, set()) for story in use_case.story_ids)
        ]
        by_kind = {
            kind: [
                diagram.diagram_id
                for diagram in diagrams
                if diagram.kind == kind and rid in diagram.requirement_ids
            ]
            for kind in ("activity", "context", "sequence", "dfd")
        }
        rows.append(
            TraceabilityRow(
                requirement_id=rid,
                us=us,
                uc=uc,
                ad=by_kind["activity"],
                cd=by_kind["context"],
                sq=by_kind["sequence"],
                dfd=by_kind["dfd"],
            )
        )
    return rows


def _story_tables(stories: list[UserStory]) -> list[DocTable]:
    roles: dict[str, list[UserStory]] = {}
    for story in stories:
        roles.setdefault(story.story_id.split("-")[1], []).append(story)
    tables = []
    for code, items in roles.items():
        tables.append(
            DocTable(
                caption=f"Role {code}: {items[0].role}",
                columns=["US-ID", "FR-ID Terkait", "User Story", "Acceptance Criteria"],
                rows=[
                    [
                        story.story_id,
                        ", ".join(story.requirement_ids) or "—",
                        story.story,
                        "\n".join(
                            f"{index}. {item}"
                            for index, item in enumerate(
                                story.acceptance_criteria, start=1
                            )
                        )
                        or "—",
                    ]
                    for story in items
                ],
            )
        )
    return tables


def _use_case_table(use_cases: list[UseCase]) -> list[DocTable]:
    if not use_cases:
        return []
    return [
        DocTable(
            caption="Daftar Use Case",
            columns=["UC-ID", "Use Case", "Aktor", "FR-ID / US-ID Terkait"],
            rows=[
                [
                    use_case.use_case_id,
                    use_case.name,
                    ", ".join(use_case.actors) or "—",
                    ", ".join([*use_case.requirement_ids, *use_case.story_ids]) or "—",
                ]
                for use_case in use_cases
            ],
        )
    ]


def _fit_table(table: DocTable, columns: list[str]) -> DocTable:
    """Template columns win; rows are padded or cut to the same width."""
    width = len(columns)
    rows = [
        [str(cell) for cell in row[:width]] + ["—"] * (width - len(row[:width]))
        for row in table.rows
        if any(str(cell).strip() for cell in row)
    ]
    return DocTable(caption=table.caption, columns=columns, rows=rows)


def assemble_document(
    writer: DocumentWriterOutput,
    stories: StoryWriterOutput,
    diagrams: DiagramWriterOutput,
    requirements: list[RequirementRef],
) -> DocumentDraftOutput:
    allowed = {item.requirement_id for item in requirements}
    user_stories, use_cases, mapping = renumber_stories(stories, allowed)
    drawn = renumber_diagrams(diagrams, allowed, mapping)
    written = {section.number: section for section in writer.sections}

    sections: list[DocumentSection] = []
    for number, title, kind, columns in TEMPLATE:
        source = written.get(number)
        content = source.content.strip() if source else ""
        tables: list[DocTable] = []
        section_diagrams: list[DiagramArtifact] = []
        if columns:
            tables = [
                _fit_table(table, columns)
                for table in (source.tables if source else [])
            ]
        if number == "3.3":
            tables = _story_tables(user_stories)
            content = (
                "User story dikelompokkan per role dengan format “Sebagai [role], saya "
                "ingin [tujuan], sehingga [manfaat].”"
                if user_stories
                else NOT_APPLICABLE
            )
        elif number == "3.4":
            tables = _use_case_table(use_cases)
        if columns is None and number not in {"3.3", "4"}:
            kind_for_section = next(
                diagram_kind
                for diagram_kind, (section_number, _, _) in DIAGRAM_SECTIONS.items()
                if section_number == number
            )
            section_diagrams = [d for d in drawn if d.kind == kind_for_section]
            if not section_diagrams and not tables:
                content = NOT_APPLICABLE
        if number == "4":
            content = (
                "Hubungan kebutuhan fungsional dengan user story, use case, dan "
                "diagram. Hanya relasi yang dinyatakan oleh artefaknya yang dicantumkan; "
                "sel tanpa relasi ditandai “—”."
            )
        # Headings have no text; 3.2 and 3.10 list the requirements themselves
        is_heading = "." not in number
        if (
            kind == "text"
            and not is_heading
            and not content
            and not tables
            and not section_diagrams
        ):
            content = NOT_APPLICABLE
        sections.append(
            DocumentSection(
                number=number,
                title=title,
                kind=kind,
                content=content,
                tables=tables,
                diagrams=section_diagrams,
                candidate_ids=source.candidate_ids if source else [],
            )
        )

    # Sections the writer added beyond the template stay, after it
    template_numbers = {number for number, *_ in TEMPLATE}
    extra = sorted(
        (s for s in writer.sections if s.number not in template_numbers),
        key=lambda s: section_sort_key(s.number),
    )
    sections.extend(
        DocumentSection(
            number=s.number,
            title=s.title,
            content=s.content,
            tables=s.tables,
            candidate_ids=s.candidate_ids,
        )
        for s in extra
    )

    return DocumentDraftOutput(
        title=writer.title,
        document_type=writer.document_type,
        sections=sections,
        rtm=writer.rtm,
        open_questions=writer.open_questions,
        user_stories=user_stories,
        use_cases=use_cases,
        traceability=build_traceability(requirements, user_stories, use_cases, drawn),
    )
