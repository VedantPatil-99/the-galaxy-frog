"""Private notebook endpoints with verified ownership on every operation."""

from hashlib import sha256
from time import perf_counter
from typing import Annotated, cast
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError

from galaxy_frog.adapters.workspace_documents import (
    DocumentProcessingTimeout,
    DocumentProcessingUnavailable,
    WorkspaceDocumentProcessor,
    document_original,
    store_document,
)
from galaxy_frog.adapters.workspace_previews import (
    PreviewStorageUnavailable,
    PreviewUnavailable,
    read_private_preview,
    read_supabase_preview,
)
from galaxy_frog.api.dependencies import get_workspace_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.transcript_projection import transcript_evidence
from galaxy_frog.api.workspace_answers import (
    WorkspaceAnswerRequest,
    WorkspaceAnswerResponse,
    WorkspaceAnswerTimings,
    validated_workspace_blocks,
    workspace_answer_passages,
    workspace_evidence_id,
)
from galaxy_frog.api.workspace_auth import (
    get_verified_workspace_creator,
    prevent_workspace_caching,
    workspace_settings,
)
from galaxy_frog.api.workspace_schemas import (
    NotebookListResponse,
    WorkspaceDocumentListResponse,
    WorkspaceFrameResponse,
    WorkspaceSearchQuery,
    WorkspaceSearchResponse,
    WorkspaceSessionResponse,
    WorkspaceSourceListResponse,
    WorkspaceStatusResponse,
    WorkspaceTextSourceListResponse,
    WorkspaceTranscriptResponse,
    WorkspaceVideoSourceResponse,
    workspace_search_hit,
)
from galaxy_frog.application.provider_registry import probe_ollama_models
from galaxy_frog.config import Settings
from galaxy_frog.db.models import VideoFrameRow, VideoRow
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.db.workspace import WorkspaceRepository
from galaxy_frog.db.workspace_documents import DocumentImport, WorkspaceDocumentRepository
from galaxy_frog.db.workspace_page_ocr import WorkspacePageOCRRepository
from galaxy_frog.db.workspace_search import search_workspace_sources
from galaxy_frog.db.workspace_sources import WorkspaceSourceRepository
from galaxy_frog.db.workspace_text import WorkspaceTextRepository
from galaxy_frog.domain.generation.models import GenerationProviderError
from galaxy_frog.domain.generation.workspace_answer import (
    WorkspaceAnswerDraftInvalid,
    WorkspaceAnswerProvider,
)
from galaxy_frog.domain.retrieval.pipeline import QueryKind
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.videos.models import VideoSourceKind
from galaxy_frog.domain.workspace import (
    Notebook,
    NotebookDraft,
    NotebookNotFound,
    VerifiedWorkspaceIdentity,
)
from galaxy_frog.domain.workspace_documents import (
    DOCUMENT_MAX_BYTES,
    DOCUMENT_MAX_PAGES,
    DocumentNotFound,
    DocumentRejected,
    DocumentStorageUnavailable,
    WorkspaceDocumentSection,
    WorkspaceDocumentSource,
    document_filename,
)
from galaxy_frog.domain.workspace_ocr import (
    PageOCRFailed,
    PageOCROutcomeUnknown,
    WorkspacePageOCR,
    WorkspacePageOCRConsent,
    WorkspacePageOCRProvider,
)
from galaxy_frog.domain.workspace_text import (
    WorkspaceTextDraft,
    WorkspaceTextNotFound,
    WorkspaceTextRejected,
    WorkspaceTextVersion,
    prepare_workspace_text,
)

router = APIRouter(
    prefix="/v1/workspace",
    tags=["workspace"],
    dependencies=[Depends(prevent_workspace_caching)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
)
Creator = Annotated[VerifiedWorkspaceIdentity, Depends(get_verified_workspace_creator)]
Repository = Annotated[WorkspaceRepository, Depends(get_workspace_repository)]
_MAX_PASTE_REQUEST_BYTES = 512 * 1024


def notebook_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="NOTEBOOK_NOT_FOUND",
        message="The requested notebook is unavailable.",
        retryable=False,
    )


@router.post("/notebooks/{notebook_id}/search")
async def search_notebook_sources(
    notebook_id: UUID, query: WorkspaceSearchQuery, identity: Creator, repository: Repository
) -> WorkspaceSearchResponse:
    try:
        await repository.get_notebook(identity.account_id, notebook_id)
    except NotebookNotFound:
        raise notebook_not_found() from None
    started = perf_counter()
    rows = await search_workspace_sources(
        repository.session, identity.account_id, notebook_id, query.query
    )
    query_ms = (perf_counter() - started) * 1_000
    return WorkspaceSearchResponse(
        query_ms=round(query_ms, 3), hits=tuple(workspace_search_hit(row) for row in rows)
    )


@router.post(
    "/notebooks/{notebook_id}/answers",
    responses={code: {"model": ErrorResponse} for code in (409, 422, 502)},
)
async def answer_notebook_question(
    notebook_id: UUID,
    body: WorkspaceAnswerRequest,
    request: Request,
    identity: Creator,
    repository: Repository,
) -> WorkspaceAnswerResponse:
    try:
        await repository.get_notebook(identity.account_id, notebook_id)
    except NotebookNotFound:
        raise notebook_not_found() from None
    if analyze_query(body.question).kind == QueryKind.TEMPORAL:
        raise ApiError(
            status_code=422,
            code="WORKSPACE_TEMPORAL_SCOPE_REQUIRED",
            message="This question needs a resolved video time or event. Choose a time-specific video workflow.",
            retryable=False,
        )

    started = perf_counter()
    rows = await search_workspace_sources(
        repository.session, identity.account_id, notebook_id, body.query
    )
    hits = {workspace_evidence_id(hit): hit for hit in map(workspace_search_hit, rows)}
    if any(key not in hits for key in body.evidence_ids):
        raise ApiError(
            status_code=409,
            code="WORKSPACE_EVIDENCE_SELECTION_STALE",
            message="Search again and select passages from the current results.",
            retryable=False,
        )
    evidence = tuple(hits[key] for key in body.evidence_ids)
    passages = workspace_answer_passages(evidence)
    await repository.session.rollback()
    evidence_ms = (perf_counter() - started) * 1_000

    started = perf_counter()
    settings = workspace_settings(request)
    models = await probe_ollama_models(settings.ollama_base_url)
    configured = settings.generation_model
    revision = models.get(configured) or models.get(f"{configured}:latest")
    if not revision or settings.generation_model_revision not in {None, revision}:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_ANSWER_MODEL_NOT_READY",
            message="Start your configured local Ollama model and verify its revision.",
            retryable=False,
        )
    provider = cast("WorkspaceAnswerProvider", request.app.state.generation_provider_factory())
    try:
        generated = await provider.generate_workspace_answer(body.question, passages)
        generation_ms = (perf_counter() - started) * 1_000
        started = perf_counter()
        blocks = validated_workspace_blocks(generated, passages, evidence)
    except GenerationProviderError:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_ANSWER_UNAVAILABLE",
            message="The local Ollama model could not complete the answer.",
            retryable=True,
        ) from None
    except WorkspaceAnswerDraftInvalid:
        raise ApiError(
            status_code=502,
            code="WORKSPACE_ANSWER_UNVERIFIED",
            message="The model returned an answer with evidence that could not be verified.",
            retryable=False,
        ) from None
    return WorkspaceAnswerResponse(
        model=generated.model,
        blocks=blocks,
        metrics=generated.metrics,
        timings=WorkspaceAnswerTimings(
            evidence_ms=round(evidence_ms, 3),
            generation_ms=round(generation_ms, 3),
            validation_ms=round((perf_counter() - started) * 1_000, 3),
        ),
    )


@router.get("/status")
async def workspace_status(request: Request) -> WorkspaceStatusResponse:
    settings = workspace_settings(request)
    required = settings.workspace_auth_required
    return WorkspaceStatusResponse(
        mode="workspace" if required else "local",
        authentication_configured=(
            settings.supabase_url is not None and settings.supabase_publishable_key is not None
        ),
        creator_access_configured=bool(settings.workspace_creator_emails),
        legacy_resources_available=not required,
    )


@router.get("/session")
async def workspace_session(identity: Creator, repository: Repository) -> WorkspaceSessionResponse:
    account = await repository.register_account(identity.account_id)
    await repository.session.commit()
    return WorkspaceSessionResponse(account=account, email=identity.email)


@router.get("/notebooks")
async def list_notebooks(
    identity: Creator, repository: Repository, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> NotebookListResponse:
    await repository.register_account(identity.account_id)
    notebooks = await repository.list_notebooks(identity.account_id, limit=limit)
    await repository.session.commit()
    return NotebookListResponse(notebooks=notebooks)


@router.post("/notebooks", status_code=201)
async def create_notebook(
    draft: NotebookDraft, identity: Creator, repository: Repository
) -> Notebook:
    await repository.register_account(identity.account_id)
    notebook = await repository.create_notebook(identity.account_id, draft)
    await repository.session.commit()
    return notebook


@router.get("/notebooks/{notebook_id}")
async def get_notebook(notebook_id: UUID, identity: Creator, repository: Repository) -> Notebook:
    try:
        return await repository.get_notebook(identity.account_id, notebook_id)
    except NotebookNotFound:
        raise notebook_not_found() from None


@router.patch("/notebooks/{notebook_id}")
async def rename_notebook(
    notebook_id: UUID, draft: NotebookDraft, identity: Creator, repository: Repository
) -> Notebook:
    try:
        notebook = await repository.rename_notebook(identity.account_id, notebook_id, draft)
    except NotebookNotFound:
        raise notebook_not_found() from None
    await repository.session.commit()
    return notebook


@router.post(
    "/notebooks/{notebook_id}/text-sources",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": WorkspaceTextDraft.model_json_schema()}},
        }
    },
    responses={code: {"model": ErrorResponse} for code in (413, 415, 422)},
)
async def paste_notebook_text(
    notebook_id: UUID, request: Request, identity: Creator, repository: Repository
) -> WorkspaceTextVersion:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ApiError(
            status_code=415,
            code="WORKSPACE_TEXT_MEDIA_TYPE",
            message="Send the pasted text as a JSON document.",
            retryable=False,
        )
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_PASTE_REQUEST_BYTES:
            raise ApiError(
                status_code=413,
                code="WORKSPACE_TEXT_REQUEST_TOO_LARGE",
                message="The paste request exceeds the supported size.",
                retryable=False,
            )
        body.extend(chunk)
    try:
        draft = WorkspaceTextDraft.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_input=False, include_url=False)) from None

    try:
        version = await WorkspaceTextRepository(repository.session).save(
            identity.account_id, notebook_id, draft
        )
    except NotebookNotFound:
        raise notebook_not_found() from None
    except WorkspaceTextRejected as exc:
        raise ApiError(
            status_code=422,
            code="WORKSPACE_TEXT_REJECTED",
            message=str(exc),
            retryable=False,
        ) from None
    await repository.session.commit()
    return version


@router.get("/notebooks/{notebook_id}/text-sources")
async def list_notebook_text(
    notebook_id: UUID,
    identity: Creator,
    repository: Repository,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> WorkspaceTextSourceListResponse:
    try:
        sources = await WorkspaceTextRepository(repository.session).list_sources(
            identity.account_id, notebook_id, after=after, limit=limit + 1
        )
    except NotebookNotFound:
        raise notebook_not_found() from None
    return WorkspaceTextSourceListResponse(
        sources=sources[:limit],
        next_after=sources[limit - 1].source_id if len(sources) > limit else None,
    )


@router.get("/notebooks/{notebook_id}/text-sources/{source_id}/versions/{version_id}")
async def get_notebook_text_version(
    notebook_id: UUID,
    source_id: UUID,
    version_id: UUID,
    identity: Creator,
    repository: Repository,
) -> WorkspaceTextVersion:
    try:
        return await WorkspaceTextRepository(repository.session).get_version(
            identity.account_id, notebook_id, source_id, version_id
        )
    except WorkspaceTextNotFound:
        raise _source_not_found() from None


def _document_error(
    error: DocumentRejected
    | DocumentProcessingTimeout
    | DocumentProcessingUnavailable
    | DocumentStorageUnavailable,
) -> ApiError:
    if isinstance(error, DocumentRejected):
        return ApiError(
            status_code=422, code="WORKSPACE_DOCUMENT_REJECTED", message=str(error), retryable=False
        )
    if isinstance(error, DocumentProcessingTimeout):
        return ApiError(
            status_code=504,
            code="WORKSPACE_DOCUMENT_TIMEOUT",
            message="The document exceeded the processing time limit.",
            retryable=False,
        )
    code, message = (
        ("WORKSPACE_DOCUMENT_STORAGE_UNAVAILABLE", "Private document storage is unavailable.")
        if isinstance(error, DocumentStorageUnavailable)
        else ("WORKSPACE_DOCUMENT_PROCESSOR_UNAVAILABLE", "Document processing is unavailable.")
    )
    return ApiError(status_code=503, code=code, message=message, retryable=True)


def _document_processor(request: Request) -> WorkspaceDocumentProcessor:
    return cast(WorkspaceDocumentProcessor, request.app.state.workspace_document_processor)


@router.post(
    "/notebooks/{notebook_id}/documents",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    responses={code: {"model": ErrorResponse} for code in (413, 415, 422, 504)},
)
async def import_notebook_document(
    notebook_id: UUID,
    request: Request,
    identity: Creator,
    repository: Repository,
    filename: Annotated[str, Query(min_length=1, max_length=160)],
) -> WorkspaceDocumentSource:
    try:
        await repository.get_notebook(identity.account_id, notebook_id)
        await repository.session.rollback()
        name, format = document_filename(filename)
    except NotebookNotFound:
        raise notebook_not_found() from None
    except DocumentRejected as exc:
        raise _document_error(exc) from None
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/octet-stream":
        raise ApiError(
            status_code=415,
            code="WORKSPACE_DOCUMENT_MEDIA_TYPE",
            message="Send the original file bytes as application/octet-stream.",
            retryable=False,
        )
    body = bytearray()
    digest = sha256()
    async for chunk in request.stream():
        if len(body) + len(chunk) > DOCUMENT_MAX_BYTES:
            raise ApiError(
                status_code=413,
                code="WORKSPACE_DOCUMENT_TOO_LARGE",
                message="Choose a file of 8 MiB or less.",
                retryable=False,
            )
        digest.update(chunk)
        body.extend(chunk)
    data = bytes(body)
    file_sha256 = digest.hexdigest()
    try:
        content = await _document_processor(request).extract(data, format)
        document = await WorkspaceDocumentRepository(repository.session).save(
            identity.account_id,
            notebook_id,
            DocumentImport(name, format, file_sha256, len(data), content),
        )
        await store_document(
            workspace_settings(request).workspace_document_root, notebook_id, file_sha256, data
        )
        await repository.session.commit()
    except NotebookNotFound:
        raise notebook_not_found() from None
    except (
        DocumentRejected,
        DocumentProcessingTimeout,
        DocumentProcessingUnavailable,
        DocumentStorageUnavailable,
    ) as exc:
        raise _document_error(exc) from None
    return document


@router.get("/notebooks/{notebook_id}/documents")
async def list_notebook_documents(
    notebook_id: UUID,
    identity: Creator,
    repository: Repository,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> WorkspaceDocumentListResponse:
    try:
        sources = await WorkspaceDocumentRepository(repository.session).list_sources(
            identity.account_id, notebook_id, after=after, limit=limit + 1
        )
    except NotebookNotFound:
        raise notebook_not_found() from None
    return WorkspaceDocumentListResponse(
        sources=sources[:limit],
        next_after=sources[limit - 1].source_id if len(sources) > limit else None,
    )


@router.get(
    "/notebooks/{notebook_id}/documents/{source_id}/versions/{version_id}/sections/{section_number}"
)
async def get_document_section(
    notebook_id: UUID,
    source_id: UUID,
    version_id: UUID,
    section_number: Annotated[int, Path(ge=1, le=DOCUMENT_MAX_PAGES)],
    identity: Creator,
    repository: Repository,
) -> WorkspaceDocumentSection:
    try:
        return await WorkspaceDocumentRepository(repository.session).section(
            identity.account_id, notebook_id, source_id, version_id, section_number
        )
    except DocumentNotFound:
        raise _source_not_found() from None


@router.get(
    "/notebooks/{notebook_id}/documents/{source_id}/versions/{version_id}/pages/{page_number}/image",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
        422: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def get_document_page_image(
    notebook_id: UUID,
    source_id: UUID,
    version_id: UUID,
    page_number: Annotated[int, Path(ge=1, le=DOCUMENT_MAX_PAGES)],
    request: Request,
    identity: Creator,
    repository: Repository,
) -> Response:
    try:
        digest = await WorkspaceDocumentRepository(repository.session).pdf_digest(
            identity.account_id, notebook_id, source_id, version_id, page_number
        )
        await repository.session.rollback()
        original = await document_original(
            workspace_settings(request).workspace_document_root, notebook_id, digest
        )
        preview = await _document_processor(request).preview(original, page_number)
    except DocumentNotFound:
        raise _source_not_found() from None
    except (
        DocumentRejected,
        DocumentProcessingTimeout,
        DocumentProcessingUnavailable,
        DocumentStorageUnavailable,
    ) as exc:
        raise _document_error(exc) from None
    return Response(
        preview,
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def _authorized_ocr_page(
    notebook_id: UUID,
    source_id: UUID,
    version_id: UUID,
    page_number: Annotated[int, Path(ge=1, le=DOCUMENT_MAX_PAGES)],
    identity: Creator,
    repository: Repository,
) -> WorkspaceDocumentSection:
    section = await get_document_section(
        notebook_id, source_id, version_id, page_number, identity, repository
    )
    if section.page_number is None:
        raise _source_not_found()
    return section


OCRPage = Annotated[WorkspaceDocumentSection, Depends(_authorized_ocr_page)]
_PAGE_OCR_PATH = (
    "/notebooks/{notebook_id}/documents/{source_id}/versions/{version_id}/pages/{page_number}/ocr"
)


def _page_ocr_provider(request: Request) -> WorkspacePageOCRProvider | None:
    return cast("WorkspacePageOCRProvider | None", request.app.state.workspace_page_ocr_provider)


@router.get(_PAGE_OCR_PATH)
async def get_page_ocr(page: OCRPage, request: Request, repository: Repository) -> WorkspacePageOCR:
    ledger = WorkspacePageOCRRepository(repository.session, page)
    return await ledger.read(available=_page_ocr_provider(request) is not None)


@router.post(
    _PAGE_OCR_PATH,
    responses={code: {"model": ErrorResponse} for code in (422, 504)},
)
async def start_page_ocr(
    notebook_id: UUID,
    page: OCRPage,
    request: Request,
    identity: Creator,
    repository: Repository,
    _consent: WorkspacePageOCRConsent,
) -> WorkspacePageOCR:
    provider = _page_ocr_provider(request)
    ledger = WorkspacePageOCRRepository(repository.session, page)
    receipt = await ledger.read(available=provider is not None)
    if provider is None or receipt.status != "ready":
        return receipt

    try:
        digest = await WorkspaceDocumentRepository(repository.session).pdf_digest(
            identity.account_id, notebook_id, page.source_id, page.version_id, page.section_number
        )
        await repository.session.rollback()
        original = await document_original(
            workspace_settings(request).workspace_document_root, notebook_id, digest
        )
        png = await _document_processor(request).preview(original, page.section_number)
    except DocumentNotFound:
        raise _source_not_found() from None
    except (
        DocumentRejected,
        DocumentProcessingTimeout,
        DocumentProcessingUnavailable,
        DocumentStorageUnavailable,
    ) as exc:
        raise _document_error(exc) from None

    if not await ledger.reserve(identity.account_id):
        return await ledger.read(available=True)

    try:
        result = await provider.extract(png, record_job=ledger.record_job)
        if not result.text.strip():
            return await ledger.finish("no_text")
        evidence = prepare_workspace_text(result.text)
    except PageOCROutcomeUnknown:
        return await ledger.finish("unknown")
    except PageOCRFailed, WorkspaceTextRejected:
        return await ledger.finish("failed")
    return await ledger.finish("succeeded", evidence)


def _source_response(video: VideoRow) -> WorkspaceVideoSourceResponse:
    kind = VideoSourceKind(video.source_kind)
    return WorkspaceVideoSourceResponse(
        video_id=video.id,
        source_kind=kind,
        title=video.title,
        duration_ms=video.duration_ms,
        canonical_url=video.canonical_url if kind is VideoSourceKind.YOUTUBE else None,
    )


def _source_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="WORKSPACE_SOURCE_NOT_FOUND",
        message="The requested notebook source is unavailable.",
        retryable=False,
    )


async def _authorized_source(
    notebook_id: UUID, video_id: UUID, identity: Creator, repository: Repository
) -> VideoRow:
    video = await WorkspaceSourceRepository(repository.session).get_video(
        identity.account_id, notebook_id, video_id
    )
    if video is None:
        raise _source_not_found()
    return video


NotebookSource = Annotated[VideoRow, Depends(_authorized_source)]


@router.get("/notebooks/{notebook_id}/sources")
async def list_notebook_sources(
    notebook_id: UUID,
    identity: Creator,
    repository: Repository,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> WorkspaceSourceListResponse:
    try:
        videos = await WorkspaceSourceRepository(repository.session).list_videos(
            identity.account_id, notebook_id, after=after, limit=limit + 1
        )
    except NotebookNotFound:
        raise notebook_not_found() from None
    return WorkspaceSourceListResponse(
        sources=tuple(_source_response(video) for video in videos[:limit]),
        next_after=videos[limit - 1].id if len(videos) > limit else None,
    )


@router.get("/notebooks/{notebook_id}/sources/{video_id}/transcript")
async def read_notebook_transcript(
    video: NotebookSource, repository: Repository
) -> WorkspaceTranscriptResponse:
    record = await SqlAlchemyVideoRepository(repository.session).read_transcript(video)
    evidence = transcript_evidence(record)
    return WorkspaceTranscriptResponse(
        source=_source_response(video),
        cues=evidence.cues,
        retrieval_units=evidence.retrieval_units,
        transcription=evidence.transcription,
    )


@router.get("/notebooks/{notebook_id}/sources/{video_id}/frames")
async def list_notebook_frames(
    video: NotebookSource, repository: Repository
) -> tuple[WorkspaceFrameResponse, ...]:
    frames = await SqlAlchemyVisionRepository(repository.session).get_frames(
        video.id, only_kept=True
    )
    return tuple(
        WorkspaceFrameResponse(
            frame_id=frame.id,
            sequence=frame.sequence,
            start_ms=frame.start_ms,
            end_ms=frame.end_ms,
            capture_ms=frame.capture_ms,
        )
        for frame in frames
    )


@router.get(
    "/notebooks/{notebook_id}/sources/{video_id}/frames/{sequence}/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}, "description": "Private JPEG preview."}},
)
async def read_notebook_frame_image(
    video: NotebookSource,
    repository: Repository,
    request: Request,
    sequence: Annotated[int, Path(ge=0, le=2_147_483_647)],
) -> Response:
    frame = await SqlAlchemyVisionRepository(repository.session).get_frame(video.id, sequence)
    try:
        if frame is None:
            raise PreviewUnavailable
        content = await _read_preview(workspace_settings(request), frame)
    except PreviewUnavailable:
        raise ApiError(
            status_code=404,
            code="WORKSPACE_PREVIEW_NOT_FOUND",
            message="The requested source preview is unavailable.",
            retryable=False,
        ) from None
    except PreviewStorageUnavailable:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_PREVIEW_STORAGE_UNAVAILABLE",
            message="Private preview storage is unavailable.",
            retryable=True,
            suggested_action="Check the configured storage credentials and private bucket.",
        ) from None
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


async def _read_preview(settings: Settings, frame: VideoFrameRow) -> bytes:
    if settings.workspace_preview_storage == "local":
        return await read_private_preview(
            settings.vision_workspace_root, frame.video_id, frame.image_path
        )
    origin = settings.supabase_url
    key = settings.supabase_storage_secret_key
    if origin is None or key is None:
        raise PreviewStorageUnavailable
    async with httpx.AsyncClient(timeout=4) as client:
        return await read_supabase_preview(
            client, origin, key.get_secret_value(), frame.video_id, frame.id
        )
