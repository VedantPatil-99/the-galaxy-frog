"""Private notebook endpoints with verified ownership on every operation."""

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response

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
from galaxy_frog.api.workspace_auth import (
    get_verified_workspace_creator,
    prevent_workspace_caching,
    workspace_settings,
)
from galaxy_frog.api.workspace_schemas import (
    NotebookListResponse,
    WorkspaceFrameResponse,
    WorkspaceSessionResponse,
    WorkspaceSourceListResponse,
    WorkspaceStatusResponse,
    WorkspaceTranscriptResponse,
    WorkspaceVideoSourceResponse,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.models import VideoFrameRow, VideoRow
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.db.workspace import WorkspaceRepository
from galaxy_frog.db.workspace_sources import WorkspaceSourceRepository
from galaxy_frog.domain.videos.models import VideoSourceKind
from galaxy_frog.domain.workspace import (
    Notebook,
    NotebookDraft,
    NotebookNotFound,
    VerifiedWorkspaceIdentity,
)

router = APIRouter(
    prefix="/v1/workspace",
    tags=["workspace"],
    dependencies=[Depends(prevent_workspace_caching)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
)
Creator = Annotated[VerifiedWorkspaceIdentity, Depends(get_verified_workspace_creator)]
Repository = Annotated[WorkspaceRepository, Depends(get_workspace_repository)]


def _notebook_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="NOTEBOOK_NOT_FOUND",
        message="The requested notebook is unavailable.",
        retryable=False,
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
        raise _notebook_not_found() from None


@router.patch("/notebooks/{notebook_id}")
async def rename_notebook(
    notebook_id: UUID, draft: NotebookDraft, identity: Creator, repository: Repository
) -> Notebook:
    try:
        notebook = await repository.rename_notebook(identity.account_id, notebook_id, draft)
    except NotebookNotFound:
        raise _notebook_not_found() from None
    await repository.session.commit()
    return notebook


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
        raise _notebook_not_found() from None
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
