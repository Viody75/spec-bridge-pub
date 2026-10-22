from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from app.api.demo import router as demo_router
from app.config import get_settings
from app.domain.schemas import (
    AnalysisJobAccepted,
    AnalysisRequest,
    AnalysisResult,
    AnalysisRunView,
    ApiMessage,
    ArtifactLinksUpdate,
    ConflictCommentCreate,
    ConflictDecisionCreate,
    ConflictDeferCreate,
    ExistingRequirement,
    HealthResponse,
    ImplementationStatusUpdate,
    ItemBulkReviewCreate,
    ItemEdit,
    ManualRequirementCreate,
    NormalizedSource,
    ProductProjectView,
    ProjectCreate,
    ProjectView,
    ProjectWorkspace,
    PublishDocumentRequest,
    RequestDocumentChanges,
    RequirementReviewUpdate,
    RequirementRevisionUpdate,
    SectionBodyUpdate,
    SourceBatch,
    SourceUploadResult,
    TraceItemDecisionCreate,
    TraceItemView,
    WorkspaceDocumentView,
    WorkspaceRequirementView,
)
from app.infrastructure.ocr import get_ocr_engine
from app.infrastructure.repository import Repository, get_repository
from app.services import AnalysisService, get_analysis_service
from app.services.source_normalization import normalize_uploaded_source

router = APIRouter()
router.include_router(demo_router)


def repository_dependency() -> Repository:
    return get_repository()


def analysis_service_dependency() -> AnalysisService:
    return get_analysis_service()


RepositoryDep = Annotated[Repository, Depends(repository_dependency)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(analysis_service_dependency)]


@router.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    settings = get_settings()
    database_ok = False
    if settings.database_url:
        database_ok = await get_repository().ping()
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        database_configured=database_ok,
        embedding_configured=bool(settings.text_embedding_model),
    )


v1 = APIRouter(prefix="/v1")


@v1.post("/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    repository: RepositoryDep,
) -> ProjectView:
    return await repository.create_project(payload)


@v1.get("/projects", response_model=list[ProductProjectView])
async def list_projects(repository: RepositoryDep) -> list[ProductProjectView]:
    return await repository.list_projects()


@v1.get("/projects/{project_id}/workspace", response_model=ProjectWorkspace)
async def get_project_workspace(
    project_id: str, repository: RepositoryDep
) -> ProjectWorkspace:
    return await repository.get_project_workspace(project_id)


@v1.post(
    "/projects/{project_id}/sources",
    response_model=ApiMessage,
    status_code=status.HTTP_201_CREATED,
)
async def add_sources(
    project_id: str,
    payload: SourceBatch,
    repository: RepositoryDep,
) -> ApiMessage:
    inserted = await repository.add_sources(project_id, payload.sources)
    return ApiMessage(message=f"{inserted} normalized source(s) added")


@v1.post(
    "/projects/{project_id}/uploads",
    response_model=SourceUploadResult,
)
async def upload_and_analyze_source(
    project_id: str,
    request: Request,
    file: Annotated[UploadFile, File()],
    repository: RepositoryDep,
    service: AnalysisServiceDep,
    author: Annotated[str | None, Form()] = None,
) -> SourceUploadResult:
    """Synchronous single-file analysis; prefer /upload-jobs from the UI."""
    workspace = await repository.get_project_workspace(project_id)
    [source] = await _normalize_uploads([file], author)
    payload = AnalysisRequest(
        project_id=project_id,
        document_type=workspace.project.document_type,
        sources=[source],
    )
    analysis = await service.run(
        payload,
        runner=request.app.state.runner,
        app_name=request.app.state.agent_app_name,
    )
    return SourceUploadResult(source=source, analysis=analysis)


@v1.post(
    "/projects/{project_id}/upload-jobs",
    response_model=AnalysisJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_batch_for_analysis(
    project_id: str,
    files: Annotated[list[UploadFile], File()],
    repository: RepositoryDep,
    service: AnalysisServiceDep,
    author: Annotated[str | None, Form()] = None,
) -> AnalysisJobAccepted:
    """Extract (with OCR where needed) every file, then queue one agent run for all."""
    workspace = await repository.get_project_workspace(project_id)
    sources = await _normalize_uploads(files, author)
    run_id = await service.enqueue(
        AnalysisRequest(
            project_id=project_id,
            document_type=workspace.project.document_type,
            sources=sources,
        )
    )
    return AnalysisJobAccepted(
        run_id=run_id, project_id=project_id, status="queued", sources=sources
    )


@v1.post(
    "/projects/{project_id}/analysis-jobs",
    response_model=AnalysisJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_analysis(
    project_id: str,
    payload: AnalysisRequest,
    service: AnalysisServiceDep,
) -> AnalysisJobAccepted:
    if payload.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="path project_id must match payload project_id",
        )
    run_id = await service.enqueue(payload)
    return AnalysisJobAccepted(
        run_id=run_id, project_id=project_id, status="queued", sources=[]
    )


@v1.post(
    "/projects/{project_id}/requirements/manual",
    response_model=ExistingRequirement,
    status_code=status.HTTP_201_CREATED,
)
async def add_manual_requirement(
    project_id: str,
    payload: ManualRequirementCreate,
    repository: RepositoryDep,
) -> ExistingRequirement:
    return await repository.add_manual_requirement(project_id, payload)


@v1.patch(
    "/projects/{project_id}/requirements/{requirement_id}",
    response_model=ExistingRequirement,
)
async def revise_requirement(
    project_id: str,
    requirement_id: str,
    payload: RequirementRevisionUpdate,
    repository: RepositoryDep,
) -> ExistingRequirement:
    return await repository.revise_requirement(
        project_id,
        requirement_id,
        statement=payload.text,
        rationale=payload.rationale,
        actor_id=payload.actor_id,
        priority=payload.priority,
    )


@v1.patch(
    "/projects/{project_id}/requirements/{requirement_id}/review",
    response_model=WorkspaceRequirementView,
)
async def review_requirement(
    project_id: str,
    requirement_id: str,
    payload: RequirementReviewUpdate,
    repository: RepositoryDep,
) -> WorkspaceRequirementView:
    return await repository.set_requirement_review(
        project_id,
        requirement_id,
        state=payload.state,
        actor_id=payload.actor_id,
    )


@v1.patch(
    "/projects/{project_id}/requirements/{requirement_id}/implementation-status",
    response_model=WorkspaceRequirementView,
)
async def update_implementation_status(
    project_id: str,
    requirement_id: str,
    payload: ImplementationStatusUpdate,
    repository: RepositoryDep,
) -> WorkspaceRequirementView:
    return await repository.set_implementation_status(
        project_id,
        requirement_id,
        state=payload.state,
        actor_id=payload.actor_id,
    )


@v1.post(
    "/projects/{project_id}/analysis",
    response_model=AnalysisResult,
)
async def run_analysis(
    project_id: str,
    payload: AnalysisRequest,
    request: Request,
    service: AnalysisServiceDep,
) -> AnalysisResult:
    if payload.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="path project_id must match payload project_id",
        )
    return await service.run(
        payload,
        runner=request.app.state.runner,
        app_name=request.app.state.agent_app_name,
    )


@v1.get(
    "/projects/{project_id}/analysis/{run_id}",
    response_model=AnalysisRunView,
)
async def get_analysis(
    project_id: str,
    run_id: uuid.UUID,
    repository: RepositoryDep,
) -> AnalysisRunView:
    return await repository.get_run(project_id, run_id)


@v1.get(
    "/projects/{project_id}/analysis/{run_id}/progress",
    response_model=AnalysisRunView,
)
async def get_analysis_progress(
    project_id: str,
    run_id: uuid.UUID,
    repository: RepositoryDep,
) -> AnalysisRunView:
    """Light status for polling: stage and percent, without the full result."""
    run = await repository.get_run(project_id, run_id)
    return run.model_copy(update={"result": None})


@v1.post(
    "/projects/{project_id}/trace-items/{item_id}/decision",
    response_model=TraceItemView,
)
async def decide_trace_item(
    project_id: str,
    item_id: uuid.UUID,
    payload: TraceItemDecisionCreate,
    repository: RepositoryDep,
) -> TraceItemView:
    return await repository.decide_trace_item(
        project_id, item_id, decision=payload.decision, actor_id=payload.actor_id
    )


@v1.patch(
    "/projects/{project_id}/requirements/{requirement_id}/artifact-links",
    response_model=WorkspaceRequirementView,
)
async def update_artifact_links(
    project_id: str,
    requirement_id: str,
    payload: ArtifactLinksUpdate,
    repository: RepositoryDep,
) -> WorkspaceRequirementView:
    return await repository.update_artifact_links(
        project_id, requirement_id, links=payload.links, actor_id=payload.actor_id
    )


@v1.patch(
    "/projects/{project_id}/document/sections/{section_index}",
    response_model=WorkspaceDocumentView,
)
async def update_section_body(
    project_id: str,
    section_index: int,
    payload: SectionBodyUpdate,
    repository: RepositoryDep,
) -> WorkspaceDocumentView:
    return await repository.update_section_body(
        project_id, section_index, content=payload.content, actor_id=payload.actor_id
    )


@v1.post(
    "/projects/{project_id}/document/items/review",
    response_model=WorkspaceDocumentView,
)
async def review_document_items(
    project_id: str,
    payload: ItemBulkReviewCreate,
    repository: RepositoryDep,
) -> WorkspaceDocumentView:
    """Approve, reject or reopen table rows and diagrams (one key or a batch)."""
    return await repository.review_items(
        project_id,
        payload.keys,
        action=payload.action,
        actor_id=payload.actor_id,
        note=payload.note,
    )


@v1.patch(
    "/projects/{project_id}/document/items/{item_key}",
    response_model=WorkspaceDocumentView,
)
async def edit_document_item(
    project_id: str,
    item_key: str,
    payload: ItemEdit,
    repository: RepositoryDep,
) -> WorkspaceDocumentView:
    """Edit a table row, user story, use case or diagram; it goes back to review."""
    return await repository.edit_item(project_id, item_key, payload)


@v1.post("/conflicts/{conflict_id}/decisions", response_model=ApiMessage)
async def decide_conflict(
    conflict_id: uuid.UUID,
    payload: ConflictDecisionCreate,
    repository: RepositoryDep,
) -> ApiMessage:
    await repository.decide_conflict(conflict_id, payload)
    return ApiMessage(message="conflict decision recorded")


@v1.post("/conflicts/{conflict_id}/defer", response_model=ApiMessage)
async def defer_conflict(
    conflict_id: uuid.UUID,
    payload: ConflictDeferCreate,
    repository: RepositoryDep,
) -> ApiMessage:
    await repository.defer_conflict(conflict_id, payload.actor_id)
    return ApiMessage(message="conflict deferred")


@v1.post("/conflicts/{conflict_id}/comments", response_model=ApiMessage)
async def comment_on_conflict(
    conflict_id: uuid.UUID,
    payload: ConflictCommentCreate,
    repository: RepositoryDep,
) -> ApiMessage:
    await repository.add_conflict_comment(conflict_id, payload)
    return ApiMessage(message="conflict comment recorded")


@v1.post("/documents/{document_id}/publish", response_model=ApiMessage)
async def publish_document(
    document_id: uuid.UUID,
    payload: PublishDocumentRequest,
    repository: RepositoryDep,
) -> ApiMessage:
    await repository.publish_document(
        document_id,
        actor_id=payload.actor_id,
        decision_note=payload.decision_note,
    )
    return ApiMessage(message="document published")


@v1.post("/documents/{document_id}/request-changes", response_model=ApiMessage)
async def request_document_changes(
    document_id: uuid.UUID,
    payload: RequestDocumentChanges,
    repository: RepositoryDep,
) -> ApiMessage:
    await repository.request_document_changes(
        document_id,
        actor_id=payload.actor_id,
        decision_note=payload.decision_note,
    )
    return ApiMessage(message="document changes requested")


async def _normalize_uploads(
    files: list[UploadFile], author: str | None
) -> list[NormalizedSource]:
    settings = get_settings()
    if author and len(author) > 300:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="author must contain at most 300 characters",
        )
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="upload at least one file",
        )
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"upload at most {settings.max_upload_files} files per analysis",
        )

    uploads: list[tuple[UploadFile, bytes]] = []
    total = 0
    for file in files:
        try:
            data = await file.read(settings.max_upload_bytes + 1)
        finally:
            await file.close()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{file.filename or 'file'} exceeds the {settings.max_upload_bytes}-byte upload limit",
            )
        total += len(data)
        if total > settings.max_upload_batch_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"the files together exceed the {settings.max_upload_batch_bytes}-byte batch limit",
            )
        uploads.append((file, data))

    ocr = get_ocr_engine()
    return list(
        await asyncio.gather(
            *(
                normalize_uploaded_source(
                    filename=file.filename or "",
                    data=data,
                    content_type=file.content_type,
                    author=author,
                    max_pdf_pages=settings.max_pdf_pages,
                    max_docx_uncompressed_bytes=settings.max_docx_uncompressed_bytes,
                    ocr=ocr,
                    max_ocr_pages=settings.max_ocr_pages,
                )
                for file, data in uploads
            )
        )
    )


router.include_router(v1)
