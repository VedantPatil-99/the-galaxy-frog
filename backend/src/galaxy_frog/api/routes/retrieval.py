"""Retrieval-only search and video-scoped durable trace inspection."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import (
    build_retrieval_service,
    build_visual_reasoning,
    get_video_repository,
    get_vision_repository,
)
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.moment_schemas import (
    ExplainMomentRequest,
    ExplainMomentResponse,
    moment_response,
)
from galaxy_frog.api.provider_execution import (
    RoutedTextGeneration,
    api_provider_run,
    provider_api_error,
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
from galaxy_frog.application.provider_policy import ProviderFailure
from galaxy_frog.application.retrieval.explain_moment import ExplainMoment, MomentTextProvider
from galaxy_frog.application.retrieval.moment import InvalidMomentTimestamp, RetrieveMomentEvidence
from galaxy_frog.application.retrieval.temporal import RetrievalVideoNotFound
from galaxy_frog.db.models import OCRBlockRow, VideoFrameRow
from galaxy_frog.db.moment_evidence import SqlAlchemyMomentEvidenceReader
from galaxy_frog.db.retrieval_traces import PostgresRetrievalTraces
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.domain.providers import ProviderOperation
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
    request: Request,
) -> ExplainMomentResponse:
    trace_id = uuid4()
    try:
        if request.app.state.database_engine is not None:
            async with asynccontextmanager(get_video_repository)(request) as repository:
                run = await api_provider_run(
                    request,
                    repository,
                    operation=ProviderOperation.MOMENT,
                    selection=body.provider_selection,
                    video_id=video_id,
                    run_id=trace_id,
                )
                assert run is not None
                local = cast(
                    Callable[[], MomentTextProvider], request.app.state.generation_provider_factory
                )()
                routed = ExplainMoment(
                    evidence=RetrieveMomentEvidence(
                        SqlAlchemyMomentEvidenceReader(repository.session)
                    ),
                    text=RoutedTextGeneration(run, local),
                    visual=build_visual_reasoning(request, repository.session, run),
                    traces=PostgresRetrievalTraces(repository.session),
                )
                return moment_response(
                    await routed.execute(
                        video_id,
                        body.timestamp_ms,
                        trace_id=trace_id,
                        allow_fallback=not run.policy.evaluation,
                    )
                )
        if body.provider_selection is not None:
            raise ApiError(
                status_code=503,
                code="DATABASE_UNAVAILABLE",
                message="Provider policy requires PostgreSQL.",
                retryable=True,
            )
        result = await service.execute(video_id, body.timestamp_ms, external_permission=False)
    except ProviderFailure as exc:
        raise provider_api_error(exc, trace_id) from exc
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
    request: Request,
) -> SearchResponse:
    trace_id = uuid4()
    run = await api_provider_run(
        request,
        repository,
        operation=ProviderOperation.SEARCH,
        selection=body.provider_selection,
        video_id=video_id,
        run_id=trace_id,
    )
    execution = await run_search(
        video_id,
        body.query,
        body,
        repository,
        build_retrieval_service(request, repository, run) if run else service,
        trace_id=trace_id if run else None,
        policy_strict=bool(run and run.policy.evaluation),
    )
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
            if window is not None:
                time_matches = (
                    frame.capture_ms is not None
                    and frame.capture_ms >= window.start_ms
                    and (window.end_ms is None or frame.capture_ms < window.end_ms)
                )
                if not time_matches:
                    continue
            else:
                time_matches = any(
                    max(frame.start_ms, g.start_ms) < min(frame.end_ms, g.end_ms) for g in groups
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
                    capture_ms=frame.capture_ms,
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
