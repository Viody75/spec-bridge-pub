"""Validation report for an SRS draft (section 8 of the SpecBridge SRS rules).

Deterministic checks over the stored document and the project's requirements,
including RTM links a person edited after the draft was written. A FAIL is a
confirmed rule violation and blocks publishing the document as FINAL.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.schemas import (
    DocumentDraftOutput,
    DocumentValidation,
    NfVerification,
    TraceCoverage,
    ValidationCheck,
    ValidationIssue,
    ValidationStatus,
    WorkspaceRequirementView,
)
from app.workflow.document_assembly import (
    NEEDS_CONFIRMATION,
    NOT_YET_AVAILABLE,
    TEMPLATE,
)
from app.workflow.document_items import all_item_keys, rejected_ids, review_of

ID_FORMATS = {
    "FR": re.compile(r"^FR-\d{2,}$"),
    "NF": re.compile(r"^NF-\d{2,}$"),
    "US": re.compile(r"^US-[A-Z]+-\d{2,}$"),
    "UC": re.compile(r"^UC-\d{2,}$"),
    "AD": re.compile(r"^AD-\d{2,}$"),
    "CD": re.compile(r"^CD-\d{2,}$"),
    "SQ": re.compile(r"^SQ-\d{2,}$"),
    "DFD": re.compile(r"^DFD-\d{2,}$"),
}

# Words that make a requirement unverifiable unless a measure is given (RULE-03)
VAGUE_TERMS = (
    "cepat",
    "mudah",
    "sangat aman",
    "user-friendly",
    "ramah pengguna",
    "optimal",
    "efisien",
    "fleksibel",
    "intuitif",
    "responsif",
    "andal",
    "handal",
    "memadai",
    "secepat mungkin",
    "sebaik mungkin",
    "modern",
    "sederhana",
)

AGENT_ACTOR = "specbridge_agent"
LINK_COLUMNS = {
    "us": "US",
    "uc": "UC",
    "ad": "AD",
    "cd": "CD",
    "sq": "SQ",
    "dfd": "DFD",
}


def _status(issues: list[ValidationIssue]) -> ValidationStatus:
    if any(issue.status == "FAIL" for issue in issues):
        return "FAIL"
    return "WARNING" if issues else "PASS"


def _join(items: Iterable[str], limit: int = 8) -> str:
    values = list(items)
    shown = ", ".join(values[:limit])
    return f"{shown} (+{len(values) - limit})" if len(values) > limit else shown


def _is_functional(requirement: WorkspaceRequirementView) -> bool:
    kind = requirement.rationale.get("kind")
    if kind in {"non_functional", "constraint"}:
        return False
    return not requirement.requirement_id.startswith("NF-")


def _effective_links(
    requirement: WorkspaceRequirementView, document: DocumentDraftOutput
) -> tuple[dict[str, list[str]], dict[str, str], bool]:
    """RTM row as shown: a person's edit wins over the agents' row."""
    human = requirement.artifact_links_updated_by not in (None, AGENT_ACTOR)
    links = requirement.artifact_links.model_dump()
    if human:
        return links, {}, True
    row = next(
        (
            item
            for item in document.traceability
            if item.requirement_id == requirement.requirement_id
        ),
        None,
    )
    return links, (row.markers if row else {}), False


def validate_document(
    document: DocumentDraftOutput, requirements: list[WorkspaceRequirementView]
) -> DocumentValidation:
    current = [item for item in requirements if item.review_state != "rejected"]
    functional = [item for item in current if _is_functional(item)]
    non_functional = [item for item in current if not _is_functional(item)]
    # Drafts written before the template have no section numbers
    sections = {
        section.number: section for section in document.sections if section.number
    }
    # A rejected story, use case or diagram no longer counts as part of the document
    rejected = rejected_ids(document)
    stories = {
        story.story_id: story
        for story in document.user_stories
        if story.story_id not in rejected
    }
    use_cases = {
        use_case.use_case_id: use_case
        for use_case in document.use_cases
        if use_case.use_case_id not in rejected
    }
    diagrams = {
        diagram.diagram_id: diagram
        for section in document.sections
        for diagram in section.diagrams
        if diagram.diagram_id not in rejected
    }
    registry = {
        "US": set(stories),
        "UC": set(use_cases),
        **{
            prefix: {d for d in diagrams if d.startswith(f"{prefix}-")}
            for prefix in ("AD", "CD", "SQ", "DFD")
        },
    }
    fr_ids = {item.requirement_id for item in functional}

    # 1. SRS structure
    structure: list[ValidationIssue] = []
    missing = [number for number, *_ in TEMPLATE if number not in sections]
    if missing:
        structure.append(
            ValidationIssue(
                status="FAIL" if len(missing) < len(TEMPLATE) else "WARNING",
                location="Dokumen",
                issue=(
                    "Draft dibuat sebelum template SRS; jalankan ulang analisis."
                    if len(missing) == len(TEMPLATE)
                    else f"Bagian wajib tidak ada: {_join(missing)}."
                ),
            )
        )
    for section in document.sections:
        if section.kind == "traceability":
            continue  # its text explains the markers
        number = section.number or "—"
        text = section.content
        if (
            NOT_YET_AVAILABLE in text
            or NEEDS_CONFIRMATION in text
            or text.startswith("Tidak berlaku")
        ):
            marker = (
                NOT_YET_AVAILABLE
                if NOT_YET_AVAILABLE in text
                else NEEDS_CONFIRMATION
                if NEEDS_CONFIRMATION in text
                else "Tidak berlaku"
            )
            structure.append(
                ValidationIssue(
                    status="WARNING",
                    location=f"{number} {section.title}",
                    issue=f"Bagian bertanda {marker}.",
                )
            )

    # 2. Requirement quality (RULE-03, RULE-14)
    quality: list[ValidationIssue] = []
    for requirement in current:
        lowered = requirement.statement.casefold()
        vague = [
            term
            for term in VAGUE_TERMS
            if re.search(rf"\b{re.escape(term)}\b", lowered)
        ]
        if vague and not re.search(r"\d", lowered):
            quality.append(
                ValidationIssue(
                    status="WARNING",
                    location=requirement.requirement_id,
                    issue=f"Kata tidak terukur tanpa kriteria: {', '.join(vague)}.",
                )
            )
        if requirement.rationale.get("priority_basis") == "assumed":
            quality.append(
                ValidationIssue(
                    status="WARNING",
                    location=requirement.requirement_id,
                    issue="Prioritas belum didukung sumber; perlu konfirmasi.",
                )
            )
    seen: dict[str, str] = {}
    for requirement in current:
        key = re.sub(r"\W+", " ", requirement.statement.casefold()).strip()
        if key in seen:
            quality.append(
                ValidationIssue(
                    status="WARNING",
                    location=f"{seen[key]}, {requirement.requirement_id}",
                    issue="Kebutuhan duplikat dengan rumusan yang sama.",
                )
            )
        seen.setdefault(key, requirement.requirement_id)
    nf_rows = {
        row[0].strip(): row
        for table in (sections["3.10"].tables if "3.10" in sections else [])
        for row in table.rows
        if row
    }
    nf_report: list[NfVerification] = []
    for requirement in non_functional:
        row = nf_rows.get(requirement.requirement_id)
        criterion = row[3].strip() if row and len(row) > 3 else ""
        unverifiable = (
            not criterion
            or NEEDS_CONFIRMATION in criterion
            or criterion.startswith("Belum ditentukan")
        )
        nf_report.append(
            NfVerification(
                nf_id=requirement.requirement_id,
                criterion=criterion or NOT_YET_AVAILABLE,
                status="WARNING" if unverifiable else "PASS",
            )
        )
        if unverifiable:
            quality.append(
                ValidationIssue(
                    status="WARNING",
                    location=requirement.requirement_id,
                    issue="Kebutuhan non-fungsional belum punya kriteria uji yang terukur.",
                )
            )
    pending_review = [
        item.requirement_id for item in current if item.review_state != "approved"
    ]

    # 3. ID consistency (RULE-05)
    identity: list[ValidationIssue] = []
    all_ids = [
        *(item.requirement_id for item in current),
        *stories,
        *use_cases,
        *(d for d in diagrams if not d.startswith(("UCD-", "CLS-"))),
    ]
    for value in all_ids:
        prefix = value.split("-", 1)[0]
        pattern = ID_FORMATS.get(prefix)
        if pattern and not pattern.match(value):
            identity.append(
                ValidationIssue(
                    status="FAIL",
                    location=value,
                    issue="Format ID tidak sesuai aturan penomoran.",
                )
            )
        elif not pattern and not value.startswith("REQ-"):
            identity.append(
                ValidationIssue(
                    status="FAIL", location=value, issue="Jenis ID tidak dikenal."
                )
            )
    duplicates = sorted({value for value in all_ids if all_ids.count(value) > 1})
    if duplicates:
        identity.append(
            ValidationIssue(
                status="FAIL",
                location=_join(duplicates),
                issue="ID dipakai lebih dari sekali.",
            )
        )
    reused = sorted(set(all_ids) & set(document.registry.retired))
    if reused:
        identity.append(
            ValidationIssue(
                status="FAIL",
                location=_join(reused),
                issue="ID yang sudah dipensiunkan dipakai lagi.",
            )
        )
    legacy = [
        item.requirement_id
        for item in current
        if item.requirement_id.startswith("REQ-")
    ]
    if legacy:
        identity.append(
            ValidationIssue(
                status="WARNING",
                location=_join(legacy),
                issue="ID lama (REQ-) belum mengikuti format FR-XX / NF-XX.",
            )
        )

    # 4. Artifact relationships (RULE-12, RULE-16)
    relations: list[ValidationIssue] = []
    for story in stories.values():
        if not story.requirement_ids:
            relations.append(
                ValidationIssue(
                    status="WARNING",
                    location=story.story_id,
                    issue="User story tanpa kebutuhan fungsional (artefak yatim).",
                )
            )
        if not story.acceptance_criteria:
            relations.append(
                ValidationIssue(
                    status="WARNING",
                    location=story.story_id,
                    issue="User story tanpa acceptance criteria.",
                )
            )
        for rid in story.requirement_ids:
            if rid.startswith("CAND-"):
                # A close match still waiting for a person's merge/keep decision
                relations.append(
                    ValidationIssue(
                        status="WARNING",
                        location=story.story_id,
                        issue=f"Merujuk {rid} yang menunggu keputusan trace item.",
                    )
                )
            elif rid not in fr_ids:
                relations.append(
                    ValidationIssue(
                        status="FAIL",
                        location=story.story_id,
                        issue=f"Merujuk {rid} yang tidak ada sebagai kebutuhan fungsional.",
                    )
                )
    for use_case in use_cases.values():
        if not use_case.requirement_ids and not use_case.story_ids:
            relations.append(
                ValidationIssue(
                    status="WARNING",
                    location=use_case.use_case_id,
                    issue="Use case tanpa konteks fungsional (artefak yatim).",
                )
            )
    for diagram in diagrams.values():
        if not diagram.requirement_ids and diagram.kind not in {"class", "context"}:
            relations.append(
                ValidationIssue(
                    status="WARNING",
                    location=diagram.diagram_id,
                    issue="Diagram tidak menyatakan kebutuhan yang digambarkan (artefak yatim).",
                )
            )
    for reference in document.invalid_references:
        relations.append(
            ValidationIssue(
                status="WARNING",
                location=reference.split(" → ")[0],
                issue=f"Rujukan ke ID yang tidak ada dibuang: {reference}.",
            )
        )
    user_roles = {
        row[0].strip().casefold()
        for table in (sections["2.3"].tables if "2.3" in sections else [])
        for row in table.rows
        if row
    }
    if user_roles:
        for role in sorted({story.role for story in stories.values()}):
            if not any(
                role.casefold() in known or known in role.casefold()
                for known in user_roles
            ):
                relations.append(
                    ValidationIssue(
                        status="WARNING",
                        location=f"Role {role}",
                        issue="Role user story tidak tercantum di 2.3 Karakteristik Pengguna.",
                    )
                )

    awaiting = [
        key
        for key in all_item_keys(document)
        if review_of(document, key).state == "pending"
    ]
    if awaiting:
        relations.append(
            ValidationIssue(
                status="WARNING",
                location="Dokumen",
                issue=f"{len(awaiting)} baris tabel/diagram menunggu persetujuan.",
            )
        )

    # 5 and 6. RTM coverage and traceability integrity (RULE-08, 11, 13)
    coverage_issues: list[ValidationIssue] = []
    integrity: list[ValidationIssue] = []
    with_story = with_design = unresolved = 0
    referenced: set[str] = set()
    for requirement in functional:
        links, markers, human = _effective_links(requirement, document)
        if links["us"]:
            with_story += 1
        else:
            coverage_issues.append(
                ValidationIssue(
                    status="WARNING",
                    location=requirement.requirement_id,
                    issue="Belum ada user story yang terhubung.",
                )
            )
        if any(links[column] for column in ("uc", "ad", "cd", "sq", "dfd")):
            with_design += 1
        else:
            coverage_issues.append(
                ValidationIssue(
                    status="WARNING",
                    location=requirement.requirement_id,
                    issue="Belum ada use case atau diagram yang terhubung.",
                )
            )
        unresolved += sum(
            1 for marker in markers.values() if marker != "not_applicable"
        )
        for column, prefix in LINK_COLUMNS.items():
            for value in links[column]:
                referenced.add(value)
                if value not in registry[prefix]:
                    reason = (
                        "sudah ditolak" if value in rejected else "tidak ada di dokumen"
                    )
                    integrity.append(
                        ValidationIssue(
                            status="FAIL",
                            location=f"RTM {requirement.requirement_id}",
                            issue=f"{value} {reason}{' (diubah pengguna)' if human else ''}.",
                        )
                    )
    orphaned = [
        artifact
        for artifact in [
            *stories,
            *use_cases,
            *(d for d in diagrams if not d.startswith(("UCD-", "CLS-"))),
        ]
        if artifact not in referenced
    ]
    if orphaned:
        integrity.append(
            ValidationIssue(
                status="WARNING",
                location=_join(orphaned),
                issue="Artefak tidak dirujuk oleh baris RTM mana pun.",
            )
        )
    total = len(functional)
    coverage = TraceCoverage(
        total_fr=total,
        fr_in_rtm=total,
        fr_with_story=with_story,
        fr_with_design=with_design,
        percent=100.0 if total else 0.0,
        unresolved=unresolved,
        invalid=sum(1 for issue in integrity if issue.status == "FAIL"),
        orphaned=len(orphaned),
    )

    groups = [
        ("SRS Structure", structure, "Semua bagian wajib ada dan terisi."),
        (
            "Requirement Quality",
            quality,
            "Kebutuhan jelas, terukur, dan prioritasnya didukung sumber.",
        ),
        ("ID Consistency", identity, "Semua ID unik dan sesuai aturan penomoran."),
        (
            "Artifact Relationships",
            relations,
            "User story, use case, dan diagram merujuk kebutuhan yang ada.",
        ),
        (
            "RTM Coverage",
            coverage_issues,
            f"{total} dari {total} FR ada di RTM; semua punya user story dan artefak desain.",
        ),
        (
            "Traceability Integrity",
            integrity,
            "Semua ID di RTM ada di dokumen; telusur maju dan mundur konsisten.",
        ),
    ]
    summary = []
    for name, issues, ok in groups:
        status = _status(issues)
        if name == "RTM Coverage":
            finding = (
                f"Cakupan FR {coverage.percent:.0f}% ({total}/{total}); "
                f"{with_story} dengan user story, {with_design} dengan artefak desain."
            )
        elif issues:
            # The same finding on many IDs reads as one line: "Prioritas ... (FR-01, FR-02 +7)"
            grouped: dict[str, list[str]] = {}
            for issue in issues:
                grouped.setdefault(issue.issue.rstrip("."), []).append(issue.location)
            finding = f"{len(issues)} temuan: " + "; ".join(
                f"{text} ({_join(locations, 3)})" for text, locations in grouped.items()
            )
        else:
            finding = ok
        summary.append(ValidationCheck(check=name, status=status, finding=finding))

    clarifications = list(document.open_questions)
    clarifications += [
        f"{issue.location}: {issue.issue}"
        for issue in structure
        if NEEDS_CONFIRMATION in issue.issue
    ]
    for requirement in functional:
        _, markers, _ = _effective_links(requirement, document)
        uncertain = [
            column.upper()
            for column, marker in markers.items()
            if marker == "needs_confirmation"
        ]
        if uncertain:
            clarifications.append(
                f"RTM {requirement.requirement_id}: relasi {', '.join(uncertain)} perlu konfirmasi."
            )
    if pending_review:
        clarifications.append(
            f"Kebutuhan masih draft (belum disetujui): {_join(pending_review)}."
        )

    issues = [*structure, *quality, *identity, *relations, *coverage_issues, *integrity]
    return DocumentValidation(
        summary=summary,
        coverage=coverage,
        issues=issues,
        nf_report=nf_report,
        clarifications=clarifications,
        ready_for_final=not any(issue.status == "FAIL" for issue in issues),
    )
