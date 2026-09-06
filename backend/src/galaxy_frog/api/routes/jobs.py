"""Durable ingestion status, event, retry, and cancellation endpoints."""

from http import HTTPStatus
from typing import Annotated, Any, Never
from uuid import UUID

from fastapi import APIRouter, Depends

from galaxy_frog.api.dependencies import get_ingestion_repository, get_job_dispatcher
from galaxy_frog.api.dispatching import dispatch_ingestion_job
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.job_schemas import (
    IngestionEventsResponse,
    IngestionJobResponse,
    ingestion_event_response,
    ingestion_job_response,
)
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.application.ingestion.dispatch import JobDispatcher
from galaxy_frog.db.ingestion_repository import (
    IngestionRepositoryError,
    PostgresIngestionRepository,
)
from galaxy_frog.domain.ingestion.models import IngestionJob

router = APIRouter(prefix="/v1/jobs", tags=["ingestion"])

RepositoryDependency = Annotated[PostgresIngestionRepository, Depends(get_ingestion_repository)]
DispatcherDependency = Annotated[JobDispatcher, Depends(get_job_dispatcher)]
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    int(HTTPStatus.NOT_FOUND): {"model": ErrorResponse},
    int(HTTPStatus.CONFLICT): {"model": ErrorResponse},
    int(HTTPStatus.SERVICE_UNAVAILABLE): {"model": ErrorResponse},
}


async def _job_or_404(repository: PostgresIngestionRepository, job_id: UUID) -> IngestionJob:
    job = await repository.get(job_id)
    if job is None:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="INGESTION_JOB_NOT_FOUND",
            message="The requested ingestion job does not exist.",
            retryable=False,
        )
    return job


def _raise_transition_error(error: IngestionRepositoryError) -> Never:
    raise ApiError(
        status_code=HTTPStatus.CONFLICT,
        code="INGESTION_JOB_TRANSITION_REJECTED",
        message="The requested ingestion job transition is not currently allowed.",
        retryable=False,
        suggested_action="Refresh the job and retry only when its current state allows this action.",
    ) from error


@router.get(
    "/{job_id}",
    response_model=IngestionJobResponse,
    responses=ERROR_RESPONSES,
)
async def get_job(
    job_id: UUID,
    repository: RepositoryDependency,
) -> IngestionJobResponse:
    """Return the current durable projection for one ingestion job."""

    return ingestion_job_response(await _job_or_404(repository, job_id))


@router.get(
    "/{job_id}/events",
    response_model=IngestionEventsResponse,
    responses=ERROR_RESPONSES,
)
async def get_job_events(
    job_id: UUID,
    repository: RepositoryDependency,
) -> IngestionEventsResponse:
    """Return append-only job events in their persisted sequence order."""

    await _job_or_404(repository, job_id)
    events = await repository.list_events(job_id)
    return IngestionEventsResponse(
        job_id=job_id,
        events=[ingestion_event_response(event) for event in events],
    )


@router.post(
    "/{job_id}/retry",
    response_model=IngestionJobResponse,
    responses=ERROR_RESPONSES,
)
async def retry_job(
    job_id: UUID,
    repository: RepositoryDependency,
    dispatcher: DispatcherDependency,
) -> IngestionJobResponse:
    """Requeue an eligible retryable failure from its persisted checkpoint."""

    await _job_or_404(repository, job_id)
    try:
        job = await repository.retry(job_id)
    except IngestionRepositoryError as exc:
        _raise_transition_error(exc)
    await dispatch_ingestion_job(dispatcher, job.job_id)
    return ingestion_job_response(job)


@router.post(
    "/{job_id}/cancel",
    response_model=IngestionJobResponse,
    responses=ERROR_RESPONSES,
)
async def cancel_job(
    job_id: UUID,
    repository: RepositoryDependency,
) -> IngestionJobResponse:
    """Persist a cancellation request or immediately cancel queued work."""

    await _job_or_404(repository, job_id)
    try:
        job = await repository.request_cancellation(job_id)
    except IngestionRepositoryError as exc:
        _raise_transition_error(exc)
    return ingestion_job_response(job)
