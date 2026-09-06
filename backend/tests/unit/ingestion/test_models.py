"""Unit coverage for durable ingestion domain invariants."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from galaxy_frog.domain.ingestion.models import (
    TERMINAL_JOB_STATUSES,
    IngestionEvent,
    IngestionEventType,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
    can_advance_stage,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
SOURCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)
FINGERPRINT = "a" * 64


def queued_job() -> IngestionJob:
    return IngestionJob(
        job_id=uuid4(),
        source=SOURCE,
        input_fingerprint=FINGERPRINT,
        status=IngestionJobStatus.QUEUED,
        stage=IngestionStage.SOURCE_RESOLUTION,
        attempt=0,
        created_at=NOW,
        updated_at=NOW,
    )


def running_job() -> IngestionJob:
    return replace(
        queued_job(),
        status=IngestionJobStatus.RUNNING,
        attempt=1,
        lease_owner="worker-1",
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=1),
        started_at=NOW,
    )


def test_stage_order_allows_forward_progress_and_skips_but_not_regression() -> None:
    assert can_advance_stage(IngestionStage.METADATA, IngestionStage.METADATA)
    assert can_advance_stage(IngestionStage.CAPTION_RETRIEVAL, IngestionStage.CHUNKING)
    assert not can_advance_stage(IngestionStage.INDEXING, IngestionStage.PERSISTENCE)
    assert {
        IngestionJobStatus.SUCCEEDED,
        IngestionJobStatus.FAILED,
        IngestionJobStatus.CANCELLED,
    } == TERMINAL_JOB_STATUSES


def test_job_reports_terminal_and_claimable_states() -> None:
    queued = queued_job()
    active = running_job()
    expired = replace(
        active,
        heartbeat_at=NOW - timedelta(minutes=2),
        lease_expires_at=NOW - timedelta(minutes=1),
    )
    cancelled = replace(
        queued,
        status=IngestionJobStatus.CANCELLED,
        completed_at=NOW,
    )

    assert queued.is_claimable_at(NOW)
    assert not active.is_claimable_at(NOW)
    assert expired.is_claimable_at(NOW)
    assert not cancelled.is_claimable_at(NOW)
    assert not queued.is_terminal
    assert cancelled.is_terminal


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"input_fingerprint": "bad"}, "input_fingerprint"),
        ({"input_fingerprint": "z" * 64}, "input_fingerprint"),
        ({"attempt": -1}, "attempt"),
        ({"created_at": NOW.replace(tzinfo=None)}, "created_at"),
        ({"updated_at": NOW - timedelta(seconds=1)}, "updated_at"),
        (
            {"status": IngestionJobStatus.RUNNING, "attempt": 1},
            "complete active lease",
        ),
        ({"lease_owner": "orphan"}, "only running jobs"),
        (
            {"status": IngestionJobStatus.CANCELLED},
            "terminal jobs require completed_at",
        ),
        ({"completed_at": NOW}, "non-terminal jobs"),
        (
            {
                "status": IngestionJobStatus.SUCCEEDED,
                "completed_at": NOW,
            },
            "completed stage",
        ),
        (
            {
                "status": IngestionJobStatus.SUCCEEDED,
                "stage": IngestionStage.COMPLETED,
                "completed_at": NOW,
            },
            "persisted video_id",
        ),
        (
            {"status": IngestionJobStatus.FAILED, "completed_at": NOW},
            "complete safe error",
        ),
        ({"last_error_code": "ERROR"}, "only failed jobs"),
    ],
)
def test_job_rejects_invalid_persisted_projections(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(queued_job(), **changes)


def test_valid_success_and_failure_projections() -> None:
    succeeded = replace(
        queued_job(),
        status=IngestionJobStatus.SUCCEEDED,
        stage=IngestionStage.COMPLETED,
        completed_at=NOW,
        video_id=uuid4(),
    )
    failed = replace(
        queued_job(),
        status=IngestionJobStatus.FAILED,
        completed_at=NOW,
        last_error_code="TRANSCRIPTION_FAILED",
        last_error_message="Transcription failed safely.",
        last_error_retryable=True,
    )

    assert succeeded.is_terminal
    assert failed.last_error_retryable is True


def test_claimable_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        queued_job().is_claimable_at(NOW.replace(tzinfo=None))


def event(event_type: IngestionEventType = IngestionEventType.CREATED) -> IngestionEvent:
    return IngestionEvent(
        event_id=uuid4(),
        job_id=uuid4(),
        sequence=1,
        event_type=event_type,
        stage=IngestionStage.SOURCE_RESOLUTION,
        attempt=0,
        occurred_at=NOW,
    )


def test_valid_failure_event_retains_safe_error_metadata() -> None:
    failed = IngestionEvent(
        event_id=uuid4(),
        job_id=uuid4(),
        sequence=1,
        event_type=IngestionEventType.FAILED,
        stage=IngestionStage.TRANSCRIPTION,
        attempt=1,
        occurred_at=NOW,
        message="The provider timed out.",
        error_code="PROVIDER_TIMEOUT",
        retryable=True,
        details={"provider": "fixture"},
    )
    assert failed.retryable is True
    assert failed.details == {"provider": "fixture"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"sequence": 0}, "sequence"),
        ({"attempt": -1}, "attempt"),
        ({"occurred_at": NOW.replace(tzinfo=None)}, "occurred_at"),
        ({"message": " "}, "message"),
        ({"error_code": " "}, "error_code"),
        (
            {"event_type": IngestionEventType.FAILED},
            "failed events require",
        ),
        ({"retryable": True}, "only failed events"),
    ],
)
def test_event_rejects_invalid_values(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        replace(event(), **changes)
