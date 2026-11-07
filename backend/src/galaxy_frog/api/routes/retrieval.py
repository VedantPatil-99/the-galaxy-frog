"""Retrieval-only search and video-scoped durable trace inspection."""

from collections.abc import Callable
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import build_visual_reasoning, get_vision_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.moment_schemas import (
    ExplainMomentRequest,
    ExplainMomentResponse,
    moment_response,
)
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
from galaxy_frog.application.retrieval.explain_moment import ExplainMoment, MomentTextProvider
from galaxy_frog.application.retrieval.moment import InvalidMomentTimestamp, RetrieveMomentEvidence
from galaxy_frog.application.retrieval.temporal import RetrievalVideoNotFound
from galaxy_frog.db.models import OCRBlockRow, VideoFrameRow
from galaxy_frog.db.moment_evidence import SqlAlchemyMomentEvidenceReader
from galaxy_frog.db.retrieval_traces import PostgresRetrievalTraces
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.domain.retrieval.traces import TraceLimitExceeded

router = APIRouter(prefix="/v1/videos", tags=["retrieval"])

VisionRepositoryDependency = Annotated[SqlAlchemyVisionRepository, Depends(get_vision_repository)]


def get_moment_service(request: Request, repository: VideoRepositoryDependency) -> ExplainMoment:
    text = cast(Callable[[], MomentTextProvider], request.app.state.generation_provider_factory)()
    return ExplainMoment(
        evidence=RetrieveMomentEvidence(SqlAlchemyMomentEvidenceReader(repository.session)),
        text=text,
        visual=build_visual_reasoning(request, repository.session),
        traces=PostgresRetrievalTraces(repository.session),
    )


@router.post(
    "/{video_id}/explain-moment",
    response_model=ExplainMomentResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def explain_video_moment(
    video_id: UUID,
    body: ExplainMomentRequest,
    service: Annotated[ExplainMoment, Depends(get_moment_service)],
) -> ExplainMomentResponse:
    try:
        result = await service.execute(
            video_id, body.timestamp_ms, external_permission=body.allow_external_frames
        )
    except RetrievalVideoNotFound as exc:
        raise ApiError(
            status_code=404,
            code="VIDEO_NOT_FOUND",
            message="The requested video does not exist.",
            retryable=False,
        ) from exc
    except InvalidMomentTimestamp as exc:
        raise ApiError(
            status_code=422, code="INVALID_MOMENT_TIMESTAMP", message=str(exc), retryable=False
        ) from exc
    except TraceLimitExceeded as exc:
        raise ApiError(
            status_code=422,
            code="RETRIEVAL_TRACE_LIMIT_EXCEEDED",
            message="The moment evidence exceeds the bounded trace size.",
            retryable=False,
        ) from exc
    except SQLAlchemyError as exc:
        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Moment evidence and its trace require the database.",
            retryable=True,
        ) from exc
    return moment_response(result)


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
    if execution.visual is not None:
        return search_response(execution, body.mode)
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
