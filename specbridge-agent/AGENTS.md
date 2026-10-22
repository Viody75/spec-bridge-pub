# Coding Agent Guide

## Prerequisites

Install the CLI (one-time):
```bash
uv tool install google-agents-cli
```

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `agents-cli playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Start with 1-2 eval cases, run `agents-cli eval run`, iterate by making changes and rerunning it until satisfied. Expect 5-10+ iterations. Once you have a baseline, reach for `agents-cli eval compare` (regression diffs), `agents-cli eval analyze` (cluster failure modes), and `agents-cli eval optimize` (auto-tune prompts). See the **Evaluation Guide** for metrics, dataset schema, LLM-as-judge config, and common gotchas.

### Phase 4: Pre-Deployment Tests
Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

### Phase 5: Deploy to Dev
**Requires explicit human approval.** Run `agents-cli deploy` only after user confirms. See the **Deployment Guide** for details.

### Phase 6: Production Deployment
Ask the user: Option A (simple single-project) or Option B (full CI/CD pipeline with `agents-cli infra cicd`).

## Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios for your agent |
| `agents-cli eval run` | Run the agent over the eval dataset and grade the traces |
| `agents-cli eval generate` / `agents-cli eval grade` | Decoupled form: produce traces, then grade them |
| `agents-cli eval compare` | Compare two grade-results files (regression check) |
| `agents-cli eval analyze` | Cluster failure modes from grade results |
| `agents-cli eval metric list` | List built-in metrics available in the SDK |
| `agents-cli eval optimize` | Auto-tune agent prompts using eval data |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |

---

## Operational Guidelines for Coding Agents

- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked.
- **Model 404 errors**: Fix `GOOGLE_CLOUD_LOCATION` (e.g., `global` instead of `asia-southeast1`), not the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `agents-cli install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.

## SpecBridge Product Contract

- `app/workflow/pipeline.py` owns the bounded ADK graph; stage and role names are a frontend-visible contract.
- `GET /v1/projects/{project_id}/workspace` is the aggregate read model for the Next.js frontend.
- Agent output is always draft content. Conflict decisions, requirement review, and document publication remain deterministic human API operations.
- Requirement revisions are immutable; edits create a new revision and retain human provenance.
- `/analysis` accepts normalized source JSON. `/uploads` locally extracts PDF, DOCX, UTF-8 TXT, Markdown, and images (PNG/JPEG/WebP) into that same contract; scanned PDF pages and images are read with Gemini OCR (`OCR_ENABLED`).
- The UI uses the background-job endpoints: `/upload-jobs` (several files, one agent run) and `/analysis-jobs` return 202 with a `run_id`; `/analysis/{run_id}/progress` reports the stage. The queue is the `analysis_runs` table (`queued` → `running`), worked by the in-process `AnalysisWorker`; Cloud Run needs `--no-cpu-throttling`. `/uploads` and `/analysis` stay synchronous.
- The trace linker never revises an existing requirement on its own: `updates`, `possibly_conflicts` and close matches become `similar` trace items until a person merges or keeps them (`/trace-items/{id}/decision`).
- People edit RTM artifact links (`/requirements/{id}/artifact-links`) and section bodies (`/document/sections/{index}`); edited sections are kept when the agents write a new version.
- Drafts follow the organisation's SRS template (`app/workflow/document_assembly.py` owns section order, titles and table columns). The Document Writer and Story Writer run in parallel, then the Diagram Writer draws Mermaid diagrams; IDs follow the template's numbering (FR-XX, NF-XX, US-X-YY, UC-XX, AD-XX, CD-XX, SQ-XX, DFD-XX). The traceability matrix is derived only from relations each story, use case and diagram declares; agent links never overwrite a person's RTM edit.
- SRS rules (RULE-01…17 of the team's SRS/RTM agent rules) are enforced in code, not only in prompts: `document_assembly.py` keeps artifact IDs across versions through `DocumentDraftOutput.registry` (letter role codes in US-X-YY, retired IDs never reused), fills 1.4 Referensi from the stored sources, lists every NF in 3.10, and marks empty RTM cells `—` / `[BELUM TERSEDIA]` / `[PERLU KONFIRMASI]`. `app/workflow/validation.py` builds the PASS/WARNING/FAIL report on every workspace read (including people's RTM edits); `publish_document` refuses FINAL while it has a FAIL.
- Every table row and diagram of a draft is a reviewable item (`app/workflow/document_items.py`; keys `story:US-A-01`, `usecase:UC-01`, `diagram:AD-01`, `row:<section>:<first cell>`). People approve/reject/reopen (`POST /document/items/review`) and edit (`PATCH /document/items/{key}`); an edit returns the item to review and re-derives 3.3/3.4 and the RTM. A person's edit wins over later agent versions; rejected stories/use cases/diagrams leave the RTM and the export. FINAL needs every item approved or rejected.
- ADK treats `{name}` in an instruction as session state; never write such placeholders in prompts (`tests/unit/test_prompts.py` guards this).
- Keep browser credentials out of the client bundle. Next.js reaches this API through its server-side proxy using `X-API-Key`.
