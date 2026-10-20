"""Retrieval-only search and video-scoped durable trace inspection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from galaxy_frog.api.dependencies import get_vision_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.retrieval import (
    SearchDependency,
    TraceRepositoryDependency,
    VideoRepositoryDependency,
    run_search,
)
from galaxy_frog.api.retrieval_schemas import (
    RetrievalTraceResponse,
    SearchRequest,
    SearchResponse,
    VisualFrameResponse,
    search_response,
    trace_response,
)
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.db.models import OCRBlockRow, VideoFrameRow
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository

router = APIRouter(prefix="/v1/videos", tags=["retrieval"])

VisionRepositoryDependency = Annotated[SqlAlchemyVisionRepository, Depends(get_vision_repository)]


@router.post(
    "/{video_id}/search",
    response_model=SearchResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def search_video(
    video_id: UUID,
    body: SearchRequest,
    repository: VideoRepositoryDependency,
    service: SearchDependency,
    vision_repository: VisionRepositoryDependency,
) -> SearchResponse:
    execution = await run_search(video_id, body.query, body, repository, service)
    frames = await vision_repository.get_frames(video_id, only_kept=True)
    visual_frames: list[VisualFrameResponse] = []
    if frames:
        ocr_blocks = await vision_repository.get_ocr_blocks(video_id)
        ocr_by_frame: dict[UUID, list[OCRBlockRow]] = {}
        for block in ocr_blocks:
            ocr_by_frame.setdefault(block.frame_id, []).append(block)

        window = execution.result.window
        groups = execution.result.groups
        matching_frames: list[VideoFrameRow] = []

        for frame in frames:
            frame_blocks = ocr_by_frame.get(frame.id, [])
            query_lower = body.query.lower()
            text_matches = any(query_lower in b.text.lower() for b in frame_blocks)
            time_matches = (
                window.overlaps(frame.start_ms, frame.end_ms)
                if window is not None
                else any(
                    max(frame.start_ms, g.start_ms) < min(frame.end_ms, g.end_ms) for g in groups
                )
            )
            if time_matches or text_matches:
                matching_frames.append(frame)

        for frame in matching_frames:
            blocks = ocr_by_frame.get(frame.id, [])
            combined_text = "\n".join(b.text for b in blocks) if blocks else None
            confidences = [b.confidence for b in blocks if b.confidence is not None]
            avg_confidence = sum(confidences) / len(confidences) if confidences else None
            visual_frames.append(
                VisualFrameResponse(
                    frame_id=frame.id,
                    sequence=frame.sequence,
                    start_ms=frame.start_ms,
                    end_ms=frame.end_ms,
                    image_url=f"/v1/videos/{video_id}/frames/{frame.sequence}/image",
                    ocr_text=combined_text,
                    confidence=avg_confidence,
                    is_duplicate=frame.is_duplicate,
                )
            )

    return search_response(execution, body.mode, visual_frames=visual_frames)


@router.get(
    "/{video_id}/retrieval-traces/{trace_id}",
    response_model=RetrievalTraceResponse,
    responses={404: {"model": ErrorResponse}},
)
async def read_retrieval_trace(
    video_id: UUID, trace_id: UUID, traces: TraceRepositoryDependency
) -> RetrievalTraceResponse:
    trace = await traces.get(video_id, trace_id)
    if trace is None:
        raise ApiError(
            status_code=404,
            code="RETRIEVAL_TRACE_NOT_FOUND",
            message="The retrieval trace does not exist for this video.",
            retryable=False,
        )
    return trace_response(trace)
