"""Durable ingestion values independent of FastAPI, SQLAlchemy, and dispatch providers."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from galaxy_frog.domain.videos.models import SourceReference


class IngestionJobStatus(StrEnum):
    """Persisted lifecycle state for one logical ingestion request."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionStage(StrEnum):
    """Phase 2 stages in execution order, excluding later multimodal capabilities."""

    SOURCE_RESOLUTION = "source_resolution"
    METADATA = "metadata"
    CAPTION_RETRIEVAL = "caption_retrieval"
    AUDIO_ACQUISITION = "audio_acquisition"
    TRANSCRIPTION = "transcription"
    CHUNKING = "chunking"
    PERSISTENCE = "persistence"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    CLEANUP = "cleanup"
    COMPLETED = "completed"


class IngestionEventType(StrEnum):
    """Append-only facts emitted while a durable job advances."""

    CREATED = "created"
    CLAIMED = "claimed"
    HEARTBEAT = "heartbeat"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    RETRY_REQUESTED = "retry_requested"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"


TERMINAL_JOB_STATUSES: Final[frozenset[IngestionJobStatus]] = frozenset(
    {
        IngestionJobStatus.SUCCEEDED,
        IngestionJobStatus.FAILED,
        IngestionJobStatus.CANCELLED,
    }
)
INGESTION_STAGE_ORDER: Final[tuple[IngestionStage, ...]] = tuple(IngestionStage)
_STAGE_INDEX: Final[dict[IngestionStage, int]] = {
    stage: index for index, stage in enumerate(INGESTION_STAGE_ORDER)
}


def _require_aware(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        msg = f"{field_name} must be timezone-aware"
        raise ValueError(msg)


def can_advance_stage(current: IngestionStage, target: IngestionStage) -> bool:
    """Return whether a checkpoint can stay put or move forward without regression."""

    return _STAGE_INDEX[target] >= _STAGE_INDEX[current]


@dataclass(frozen=True, slots=True)
class IngestionJob:
    """Current durable projection of one canonical ingestion request."""

    job_id: UUID
    source: SourceReference
    input_fingerprint: str
    status: IngestionJobStatus
    stage: IngestionStage
    attempt: int
    created_at: datetime
    updated_at: datetime
    video_id: UUID | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_error_retryable: bool | None = None

    def __post_init__(self) -> None:
        if len(self.input_fingerprint) != 64:
            msg = "input_fingerprint must be a SHA-256 hexadecimal digest"
            raise ValueError(msg)
        try:
            bytes.fromhex(self.input_fingerprint)
        except ValueError as exc:
            msg = "input_fingerprint must be a SHA-256 hexadecimal digest"
            raise ValueError(msg) from exc
        if self.attempt < 0:
            msg = "attempt must be non-negative"
            raise ValueError(msg)
        for field_name in (
            "created_at",
            "updated_at",
            "lease_expires_at",
            "heartbeat_at",
            "cancel_requested_at",
            "started_at",
            "completed_at",
        ):
            _require_aware(getattr(self, field_name), field_name)
        if self.updated_at < self.created_at:
            msg = "updated_at must not precede created_at"
            raise ValueError(msg)
        lease_values = (self.lease_owner, self.lease_expires_at, self.heartbeat_at)
        if self.status is IngestionJobStatus.RUNNING:
            if any(value is None for value in lease_values):
                msg = "running jobs require a complete active lease"
                raise ValueError(msg)
        elif any(value is not None for value in lease_values):
            msg = "only running jobs may retain an active lease"
            raise ValueError(msg)
        if self.status in TERMINAL_JOB_STATUSES and self.completed_at is None:
            msg = "terminal jobs require completed_at"
            raise ValueError(msg)
        if self.status not in TERMINAL_JOB_STATUSES and self.completed_at is not None:
            msg = "non-terminal jobs must not have completed_at"
            raise ValueError(msg)
        if (
            self.status is IngestionJobStatus.SUCCEEDED
            and self.stage is not IngestionStage.COMPLETED
        ):
            msg = "succeeded jobs must be at the completed stage"
            raise ValueError(msg)
        if self.status is IngestionJobStatus.SUCCEEDED and self.video_id is None:
            msg = "succeeded jobs require a persisted video_id"
            raise ValueError(msg)
        error_values = (
            self.last_error_code,
            self.last_error_message,
            self.last_error_retryable,
        )
        if self.status is IngestionJobStatus.FAILED:
            if any(value is None for value in error_values):
                msg = "failed jobs require complete safe error metadata"
                raise ValueError(msg)
        elif any(value is not None for value in error_values):
            msg = "only failed jobs may retain last-error metadata"
            raise ValueError(msg)

    @property
    def is_terminal(self) -> bool:
        """Return whether no worker may advance this projection without an explicit retry."""

        return self.status in TERMINAL_JOB_STATUSES

    def is_claimable_at(self, now: datetime) -> bool:
        """Return whether a worker may atomically claim or reclaim this job."""

        _require_aware(now, "now")
        return self.status is IngestionJobStatus.QUEUED or (
            self.status is IngestionJobStatus.RUNNING
            and self.lease_expires_at is not None
            and self.lease_expires_at <= now
        )


@dataclass(frozen=True, slots=True)
class IngestionEvent:
    """One immutable, ordered, user-safe job event."""

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
    details: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            msg = "event sequence must be positive"
            raise ValueError(msg)
        if self.attempt < 0:
            msg = "event attempt must be non-negative"
            raise ValueError(msg)
        _require_aware(self.occurred_at, "occurred_at")
        if self.message is not None and not self.message.strip():
            msg = "event message must not be blank"
            raise ValueError(msg)
        if self.error_code is not None and not self.error_code.strip():
            msg = "event error_code must not be blank"
            raise ValueError(msg)
        if self.event_type is IngestionEventType.FAILED:
            if self.error_code is None or self.retryable is None:
                msg = "failed events require error code and retryability"
                raise ValueError(msg)
        elif self.error_code is not None or self.retryable is not None:
            msg = "only failed events may contain error metadata"
            raise ValueError(msg)
