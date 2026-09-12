"""Translate provider-neutral dispatch failures at the HTTP boundary."""

import logging
from http import HTTPStatus
from uuid import UUID

from galaxy_frog.api.errors import ApiError
from galaxy_frog.application.ingestion.dispatch import (
    DispatchMessage,
    JobDispatcher,
    JobDispatchError,
)

logger = logging.getLogger(__name__)


async def dispatch_ingestion_job(dispatcher: JobDispatcher, job_id: UUID) -> None:
    """Send a wake-up hint while keeping durable job state in PostgreSQL."""

    try:
        receipt = await dispatcher.dispatch(DispatchMessage(job_id=job_id))
    except JobDispatchError as exc:
        logger.warning(
            "Ingestion wake-up dispatch failed job_id=%s error_code=%s retryable=%s",
            str(job_id),
            "JOB_DISPATCH_UNAVAILABLE",
            True,
            extra={
                "event_name": "ingestion_dispatch_failed",
                "job_id": str(job_id),
                "error_code": "JOB_DISPATCH_UNAVAILABLE",
                "retryable": True,
            },
        )
        raise ApiError(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            code="JOB_DISPATCH_UNAVAILABLE",
            message="The ingestion job was queued but its wake-up hint could not be delivered.",
            retryable=True,
            suggested_action="Retry the request; the existing durable job will be reused.",
        ) from exc
    logger.info(
        "Ingestion wake-up dispatch accepted job_id=%s dispatcher=%s "
        "external_message_id_present=%s",
        str(job_id),
        receipt.dispatcher,
        receipt.external_message_id is not None,
        extra={
            "event_name": "ingestion_dispatch_accepted",
            "job_id": str(job_id),
            "dispatcher": receipt.dispatcher,
            "external_message_id_present": receipt.external_message_id is not None,
        },
    )
