"""Builds the SRS in the SpecBridge template from the writer agents' output.

The agents write content; this module owns structure. Section order and titles,
artifact IDs (US-X-YY, UC-XX, AD-XX, CD-XX, SQ-XX, DFD-XX), the references table
and the traceability matrix are deterministic:

- IDs come from a registry kept with the document: an artifact that still exists
  keeps its ID, a new one gets the next number, and a retired ID is never reused.
- The matrix only holds relations an artifact declared. An empty cell says why it
  is empty: not relevant (—), not created yet ([BELUM TERSEDIA]) or uncertain
  ([PERLU KONFIRMASI]).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.schemas import (
    ArtifactRegistry,
    DiagramArtifact,
    DiagramKind,
    DiagramWriterOutput,
    DocTable,
    DocumentDraftOutput,
    DocumentSection,
    DocumentWriterOutput,
    PreviousArtifacts,
    RequirementRef,
    SectionKind,
    SourceRef,
    StoryWriterOutput,
    TraceabilityRow,
    TraceMarker,
    UseCase,
    UserStory,
)

# (number, title, kind, table columns the writer fills; None = generated here)
TEMPLATE: list[tuple[str, str, SectionKind, list[str] | None]] = [
    ("1", "PENDAHULUAN", "text", []),
    ("1.1", "Tujuan", "text", []),
    ("1.2", "Lingkup Masalah", "text", []),
    ("1.3", "Daftar Istilah", "text", ["Istilah / Singkatan", "Definisi"]),
    ("1.4", "Referensi", "text", []),
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

REFERENCE_COLUMNS = ["No.", "Judul / Sumber", "Versi / Tanggal", "Tautan / Keterangan"]

# Sections the writer agent fills (text and tables); 1.4 is listed for its text only
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

# RTM column -> diagram kind
RTM_DIAGRAM_COLUMNS: dict[str, DiagramKind] = {
    "ad": "activity",
    "cd": "context",
    "sq": "sequence",
    "dfd": "dfd",
}

FUNCTIONAL_KINDS = {"functional", "business_rule"}
MAX_STORIES = 40
MAX_USE_CASES = 20
NEEDS_CONFIRMATION = "[PERLU KONFIRMASI]"
NOT_YET_AVAILABLE = "[BELUM TERSEDIA]"
NOT_APPLICABLE = (
    "Tidak berlaku: sumber yang diunggah belum memuat informasi untuk bagian ini."
)
DIAGRAM_NOT_AVAILABLE = (
    f"{NOT_YET_AVAILABLE} Informasi pada sumber belum cukup untuk membuat diagram ini "
    "tanpa mengarang desain."
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
    mapping = {old: new for old, new in mapping.items() if old != new}
    if not mapping:
        return text
    pattern = re.compile(
        r"(?<![A-Za-z0-9-])("
        + "|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True))
        + r")(?![A-Za-z0-9])"
    )
    return pattern.sub(lambda match: mapping[match.group(1)], text)


def _role_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


class IdAllocator:
    """Hands out IDs from the registry: keep a previous ID, or issue the next one."""

    def __init__(self, registry: ArtifactRegistry) -> None:
        self.registry = registry.model_copy(deep=True)
        self.used: set[str] = set()

    def role_code(self, role: str) -> str:
        key = role.strip().casefold()
        if key not in self.registry.role_codes:
            taken = set(self.registry.role_codes.values())
            index = 0
            while _role_letter(index) in taken:
                index += 1
            self.registry.role_codes[key] = _role_letter(index)
        return self.registry.role_codes[key]

    def take(self, prefix: str, previous: str | None, allowed: set[str]) -> str:
        if previous and previous in allowed and previous not in self.used:
            number = previous.rsplit("-", 1)[-1]
            if number.isdigit():
                self.registry.counters[prefix] = max(
                    self.registry.counters.get(prefix, 0), int(number)
                )
            self.used.add(previous)
            return previous
        self.registry.counters[prefix] = self.registry.counters.get(prefix, 0) + 1
        new_id = f"{prefix}-{self.registry.counters[prefix]:02d}"
        self.used.add(new_id)
        return new_id


def _known(
    owner: str, ids: Iterable[str], allowed: set[str], invalid: list[str]
) -> list[str]:
    kept: list[str] = []
    for item in dict.fromkeys(ids):
        if item in allowed:
            kept.append(item)
        else:
            invalid.append(f"{owner} → {item}")
    return kept


def renumber_stories(
    stories: StoryWriterOutput,
    functional_ids: set[str],
    ids: IdAllocator,
    previous: PreviousArtifacts,
    invalid: list[str],
) -> tuple[list[UserStory], list[UseCase], dict[str, str]]:
    """US-<role code>-<nn> and UC-<nn>, keeping previous IDs. Returns an old→new map."""
    previous_story = {story.story_id: story for story in previous.stories}
    mapping: dict[str, str] = {}
    renumbered: list[UserStory] = []
    for story in stories.stories[:MAX_STORIES]:
        code = ids.role_code(story.role)
        prefix = f"US-{code}"
        same_role = {
            story_id
            for story_id, old in previous_story.items()
            if old.role.strip().casefold() == story.role.strip().casefold()
        }
        new_id = ids.take(prefix, story.story_id, same_role)
        mapping[story.story_id] = new_id
        renumbered.append(
            story.model_copy(
                update={
                    "story_id": new_id,
                    # A user story implements functional requirements only
                    "requirement_ids": _known(
                        new_id, story.requirement_ids, functional_ids, invalid
                    ),
                }
            )
        )
    previous_cases = {use_case.use_case_id for use_case in previous.use_cases}
    use_cases: list[UseCase] = []
    for use_case in stories.use_cases[:MAX_USE_CASES]:
        new_id = ids.take("UC", use_case.use_case_id, previous_cases)
        mapping[use_case.use_case_id] = new_id
        use_cases.append(use_case.model_copy(update={"use_case_id": new_id}))
    story_ids = {story.story_id for story in renumbered}
    use_cases = [
        use_case.model_copy(
            update={
                "requirement_ids": _known(
                    use_case.use_case_id,
                    use_case.requirement_ids,
                    functional_ids,
                    invalid,
                ),
                "story_ids": _known(
                    use_case.use_case_id,
                    (mapping.get(item, item) for item in use_case.story_ids),
                    story_ids,
                    invalid,
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
    ids: IdAllocator,
    previous: PreviousArtifacts,
    invalid: list[str],
) -> list[DiagramArtifact]:
    diagrams: list[DiagramArtifact] = []
    for diagram in output.diagrams:
        source = clean_mermaid(diagram.mermaid)
        if not source.split(maxsplit=1)[0].startswith(MERMAID_HEADERS[diagram.kind]):
            # Unusable source: the frontend would only show an error
            continue
        _, prefix, _ = DIAGRAM_SECTIONS[diagram.kind]
        same_kind = {d.diagram_id for d in previous.diagrams if d.kind == diagram.kind}
        new_id = ids.take(prefix, diagram.diagram_id, same_kind)
        diagrams.append(
            diagram.model_copy(
                update={
                    "diagram_id": new_id,
                    # Use case and story IDs inside labels follow the renumbering
                    "mermaid": _replace_ids(source, story_mapping),
                    "description": _replace_ids(diagram.description, story_mapping),
                    "requirement_ids": _known(
                        new_id, diagram.requirement_ids, allowed_requirements, invalid
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
    pending: DiagramWriterOutput | None = None,
) -> list[TraceabilityRow]:
    story_reqs = {story.story_id: set(story.requirement_ids) for story in stories}
    open_items = {
        (item.requirement_id, item.kind): item.status
        for item in (pending.pending if pending else [])
    }
    rows: list[TraceabilityRow] = []
    for requirement in requirements:
        if requirement.kind not in FUNCTIONAL_KINDS:
            continue
        rid = requirement.requirement_id
        links: dict[str, list[str]] = {
            "us": [story.story_id for story in stories if rid in story.requirement_ids],
            # A use case covers a requirement directly, or through one of its stories
            "uc": [
                use_case.use_case_id
                for use_case in use_cases
                if rid in use_case.requirement_ids
                or any(
                    rid in story_reqs.get(story, set()) for story in use_case.story_ids
                )
            ],
        }
        for column, kind in RTM_DIAGRAM_COLUMNS.items():
            links[column] = [
                diagram.diagram_id
                for diagram in diagrams
                if diagram.kind == kind and rid in diagram.requirement_ids
            ]
        markers: dict[str, TraceMarker] = {}
        for column, ids in links.items():
            if ids:
                continue
            if column in {"us", "uc"}:
                # Every functional requirement needs a story and a use case
                markers[column] = "pending"
            else:
                markers[column] = open_items.get(
                    (rid, RTM_DIAGRAM_COLUMNS[column]), "not_applicable"
                )
        rows.append(TraceabilityRow(requirement_id=rid, markers=markers, **links))
    return rows


def story_tables(stories: list[UserStory], ids: IdAllocator) -> list[DocTable]:
    codes = {code: role for role, code in ids.registry.role_codes.items()}
    roles: dict[str, list[UserStory]] = {}
    for story in stories:
        roles.setdefault(story.story_id.split("-")[1], []).append(story)
    tables = []
    for code in sorted(roles, key=lambda c: (len(c), c)):
        items = sorted(roles[code], key=lambda s: section_sort_key(s.story_id))
        tables.append(
            DocTable(
                caption=f"Role {code}: {items[0].role or codes.get(code, code)}",
                columns=["US-ID", "FR-ID Terkait", "User Story", "Acceptance Criteria"],
                rows=[
                    [
                        story.story_id,
                        ", ".join(story.requirement_ids) or NEEDS_CONFIRMATION,
                        story.story,
                        "\n".join(
                            f"{index}. {item}"
                            for index, item in enumerate(
                                story.acceptance_criteria, start=1
                            )
                        )
                        or NOT_YET_AVAILABLE,
                    ]
                    for story in items
                ],
            )
        )
    return tables


def use_case_table(use_cases: list[UseCase]) -> list[DocTable]:
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
                    ", ".join(use_case.actors) or NEEDS_CONFIRMATION,
                    ", ".join([*use_case.requirement_ids, *use_case.story_ids])
                    or NEEDS_CONFIRMATION,
                ]
                for use_case in sorted(use_cases, key=lambda u: u.use_case_id)
            ],
        )
    ]


def _references_table(sources: list[SourceRef]) -> list[DocTable]:
    """Only the sources the analysis actually read, as the rules require."""
    if not sources:
        return []
    return [
        DocTable(
            caption="",
            columns=REFERENCE_COLUMNS,
            rows=[
                [
                    str(index),
                    source.title,
                    source.date,
                    ", ".join(
                        part
                        for part in (
                            f"Sumber {source.source_type}",
                            f"oleh {source.author}" if source.author else "",
                            f"ID {source.source_id}",
                        )
                        if part
                    ),
                ]
                for index, source in enumerate(sources, start=1)
            ],
        )
    ]


def _with_every_nf(
    tables: list[DocTable], requirements: list[RequirementRef]
) -> list[DocTable]:
    """Every non-functional requirement appears in 3.10; a row the writer left out
    gets its statement and [PERLU KONFIRMASI] instead of an invented criterion."""
    columns = next(c for n, _, _, c in TEMPLATE if n == "3.10") or []
    table = tables[0] if tables else DocTable(columns=columns, rows=[])
    listed = {row[0].strip() for row in table.rows if row}
    missing = [
        [
            item.requirement_id,
            NEEDS_CONFIRMATION,
            item.statement,
            f"{NEEDS_CONFIRMATION} Kriteria belum ditentukan",
        ]
        for item in requirements
        if item.kind not in FUNCTIONAL_KINDS and item.requirement_id not in listed
    ]
    if not missing:
        return tables
    return [table.model_copy(update={"rows": [*table.rows, *missing]}), *tables[1:]]


def _fit_table(table: DocTable, columns: list[str]) -> DocTable:
    """Template columns win; rows are padded or cut to the same width."""
    width = len(columns)
    rows = [
        [str(cell) for cell in row[:width]]
        + [NEEDS_CONFIRMATION] * (width - len(row[:width]))
        for row in table.rows
        if any(str(cell).strip() for cell in row)
    ]
    return DocTable(caption=table.caption, columns=columns, rows=rows)


def assemble_document(
    writer: DocumentWriterOutput,
    stories: StoryWriterOutput,
    diagrams: DiagramWriterOutput,
    requirements: list[RequirementRef],
    *,
    sources: list[SourceRef] | None = None,
    previous: PreviousArtifacts | None = None,
) -> DocumentDraftOutput:
    previous = previous or PreviousArtifacts()
    allowed = {item.requirement_id for item in requirements}
    functional = {
        item.requirement_id for item in requirements if item.kind in FUNCTIONAL_KINDS
    }
    ids = IdAllocator(previous.registry)
    invalid: list[str] = []
    user_stories, use_cases, mapping = renumber_stories(
        stories, functional, ids, previous, invalid
    )
    drawn = renumber_diagrams(diagrams, allowed, mapping, ids, previous, invalid)
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
        if number == "1.4":
            tables = _references_table(sources or [])
        elif number == "3.3":
            tables = story_tables(user_stories, ids)
            content = (
                "User story dikelompokkan per role dengan format “Sebagai [role], saya "
                "ingin [tujuan], sehingga [manfaat].” Setiap user story merujuk minimal "
                "satu kebutuhan fungsional."
                if user_stories
                else f"{NOT_YET_AVAILABLE} Belum ada user story yang didukung sumber."
            )
        elif number == "3.4":
            tables = use_case_table(use_cases)
        elif number == "3.10":
            tables = _with_every_nf(tables, requirements)
        if columns is None and number not in {"3.3", "4"}:
            kind_for_section = next(
                diagram_kind
                for diagram_kind, (section_number, _, _) in DIAGRAM_SECTIONS.items()
                if section_number == number
            )
            section_diagrams = [d for d in drawn if d.kind == kind_for_section]
            if not section_diagrams:
                # A mandatory section; the diagram is missing, not irrelevant
                content = DIAGRAM_NOT_AVAILABLE
        if number == "4":
            content = (
                "Hubungan kebutuhan fungsional dengan user story, use case, dan "
                "diagram. Hanya relasi yang dinyatakan oleh artefaknya yang dicantumkan. "
                "“—”: tidak relevan; [BELUM TERSEDIA]: artefak relevan belum dibuat; "
                "[PERLU KONFIRMASI]: relasi belum dapat dipastikan."
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

    current = (
        {story.story_id for story in user_stories}
        | {use_case.use_case_id for use_case in use_cases}
        | {diagram.diagram_id for diagram in drawn}
    )
    earlier = (
        {story.story_id for story in previous.stories}
        | {use_case.use_case_id for use_case in previous.use_cases}
        | {diagram.diagram_id for diagram in previous.diagrams}
    )
    ids.registry.retired = sorted(
        (set(ids.registry.retired) | (earlier - current)) - current,
        key=lambda value: (value.rsplit("-", 1)[0], section_sort_key(value)),
    )

    draft = DocumentDraftOutput(
        title=writer.title,
        document_type=writer.document_type,
        sections=sections,
        rtm=writer.rtm,
        open_questions=writer.open_questions,
        user_stories=user_stories,
        use_cases=use_cases,
        traceability=build_traceability(
            requirements, user_stories, use_cases, drawn, diagrams
        ),
        registry=ids.registry,
        invalid_references=invalid,
    )
    # Late import: document_items builds on this module
    from app.workflow.document_items import assign_item_keys

    assign_item_keys(draft)
    return draft
