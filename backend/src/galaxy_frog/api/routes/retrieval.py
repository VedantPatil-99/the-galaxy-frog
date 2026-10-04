"""Retrieval-only search and video-scoped durable trace inspection."""

from uuid import UUID

from fastapi import APIRouter

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
    search_response,
    trace_response,
)
from galaxy_frog.api.schemas import ErrorResponse

router = APIRouter(prefix="/v1/videos", tags=["retrieval"])


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
) -> SearchResponse:
    execution = await run_search(video_id, body.query, body, repository, service)
    return search_response(execution, body.mode)


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
