"""Translate provider-neutral dispatch failures at the HTTP boundary."""

from http import HTTPStatus
from uuid import UUID

from galaxy_frog.api.errors import ApiError
from galaxy_frog.application.ingestion.dispatch import (
    DispatchMessage,
    JobDispatcher,
    JobDispatchError,
)


async def dispatch_ingestion_job(dispatcher: JobDispatcher, job_id: UUID) -> None:
    """Send a wake-up hint while keeping durable job state in PostgreSQL."""

    try:
        await dispatcher.dispatch(DispatchMessage(job_id=job_id))
    except JobDispatchError as exc:
        raise ApiError(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            code="JOB_DISPATCH_UNAVAILABLE",
            message="The ingestion job was queued but its wake-up hint could not be delivered.",
            retryable=True,
            suggested_action="Retry the request; the existing durable job will be reused.",
        ) from exc
