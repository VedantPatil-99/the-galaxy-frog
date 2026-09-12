"""FastAPI-owned schemas for durable ingestion state and ordered event history."""

from datetime import datetime
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue

from galaxy_frog.domain.ingestion.models import (
    IngestionEvent,
    IngestionEventType,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.videos.models import VideoSourceKind


class IngestionJobResponse(BaseModel):
    """Current durable job projection with source identity and lifecycle timestamps."""

    job_id: UUID
    source_kind: VideoSourceKind
    external_id: str
    canonical_url: str
    input_fingerprint: str
    status: IngestionJobStatus
    stage: IngestionStage
    attempt: int
    video_id: UUID | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_error_retryable: bool | None = None
    created_at: datetime
    updated_at: datetime


class IngestionEventResponse(BaseModel):
    """One append-only event with its durable order, stage, attempt, and safe details."""

    event_id: UUID
    job_id: UUID
    sequence: int
    event_type: IngestionEventType
    stage: IngestionStage
    attempt: int
    occurred_at: datetime
    message: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    details: dict[str, JsonValue] | None = None


class IngestionEventsResponse(BaseModel):
    """Ordered event history scoped to one durable ingestion job."""

    job_id: UUID
    events: list[IngestionEventResponse]


def ingestion_job_response(job: IngestionJob) -> IngestionJobResponse:
    """Map the framework-independent job projection into its public HTTP schema."""

    return IngestionJobResponse(
        job_id=job.job_id,
        source_kind=job.source.kind,
        external_id=job.source.external_id,
        canonical_url=job.source.canonical_url,
        input_fingerprint=job.input_fingerprint,
        status=job.status,
        stage=job.stage,
        attempt=job.attempt,
        video_id=job.video_id,
        cancel_requested_at=job.cancel_requested_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        last_error_code=job.last_error_code,
        last_error_message=job.last_error_message,
        last_error_retryable=job.last_error_retryable,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def ingestion_event_response(event: IngestionEvent) -> IngestionEventResponse:
    """Map one persisted event without dropping its sequence or stage provenance."""

    return IngestionEventResponse(
        event_id=event.event_id,
        job_id=event.job_id,
        sequence=event.sequence,
        event_type=event.event_type,
        stage=event.stage,
        attempt=event.attempt,
        occurred_at=event.occurred_at,
        message=event.message,
        error_code=event.error_code,
        retryable=event.retryable,
        details=cast(dict[str, JsonValue] | None, event.details),
    )
