"""Shared HTTP retrieval dependencies and stable error mapping."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import build_retrieval_service, get_video_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.retrieval_schemas import RetrievalOptions
from galaxy_frog.application.retrieval.search import (
    SearchExecution,
    SearchTranscript,
    TracedRetrievalFailure,
)
from galaxy_frog.application.retrieval.temporal import InvalidAnchor, RetrievalVideoNotFound
from galaxy_frog.db.retrieval_traces import PostgresRetrievalTraces
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.retrieval.errors import TemporalResolutionRequired
from galaxy_frog.domain.retrieval.traces import TraceLimitExceeded

VideoRepositoryDependency = Annotated[SqlAlchemyVideoRepository, Depends(get_video_repository)]


def get_search_service(request: Request, repository: VideoRepositoryDependency) -> SearchTranscript:
    return build_retrieval_service(request, repository)


def get_retrieval_traces(repository: VideoRepositoryDependency) -> PostgresRetrievalTraces:
    return PostgresRetrievalTraces(repository.session)


SearchDependency = Annotated[SearchTranscript, Depends(get_search_service)]
TraceRepositoryDependency = Annotated[PostgresRetrievalTraces, Depends(get_retrieval_traces)]


async def run_search(
    video_id: UUID,
    query: str,
    options: RetrievalOptions,
    repository: SqlAlchemyVideoRepository,
    service: SearchTranscript,
) -> SearchExecution:
    try:
        if await repository.get_video(video_id) is None:
            raise RetrievalVideoNotFound("Video not found.")
        return await service.execute(
            video_id,
            query,
            mode=options.mode,
            limit=options.limit,
            selected_anchor=options.selected_anchor,
            allow_fallback=options.allow_fallback,
        )
    except RetrievalVideoNotFound as exc:
        raise ApiError(
            status_code=404,
            code="VIDEO_NOT_FOUND",
            message="The requested video does not exist.",
            retryable=False,
        ) from exc
    except TracedRetrievalFailure as exc:
        invalid_input = isinstance(exc.cause, (InvalidAnchor, TemporalResolutionRequired))
        integrity = exc.code == "RETRIEVAL_INTEGRITY_FAILED"
        raise ApiError(
            status_code=422 if invalid_input or integrity else 503,
            code=exc.code,
            message="The temporal request could not be resolved."
            if invalid_input
            else "The requested retrieval could not be completed.",
            retryable=not invalid_input,
            suggested_action="Choose a current event anchor or use one trailing timestamp constraint."
            if invalid_input
            else "Inspect the retrieval trace and check the configured providers.",
            details={"trace_id": str(exc.trace.trace_id)},
        ) from exc
    except TraceLimitExceeded as exc:
        raise ApiError(
            status_code=422,
            code="RETRIEVAL_TRACE_LIMIT_EXCEEDED",
            message="The evidence lineage exceeds the bounded trace record size.",
            retryable=False,
            suggested_action="Narrow the query or result limit.",
        ) from exc
    except SQLAlchemyError as exc:
        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Retrieval and its durable trace require the database.",
            retryable=True,
            suggested_action="Start PostgreSQL and apply the current migrations.",
        ) from exc
