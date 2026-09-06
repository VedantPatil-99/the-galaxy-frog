"""Unit coverage for durable ingestion persistence transitions."""

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.ingestion import IngestionRepository
from galaxy_frog.db.ingestion_repository import (
    IngestionRepositoryError,
    PostgresIngestionRepository,
)
from galaxy_frog.db.models import IngestionJobRow, JobEventRow
from galaxy_frog.domain.ingestion.models import (
    IngestionEventType,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
LEASE = timedelta(minutes=1)
SOURCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)
FINGERPRINT = "a" * 64


def test_application_port_exposes_the_durable_repository_contract() -> None:
    assert IngestionRepository.__name__ == "IngestionRepository"


class AllResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values


class FakeSession:
    def __init__(self, *scalar_values: object | None) -> None:
        self.scalar_values = deque(scalar_values)
        self.get_values: deque[object | None] = deque()
        self.scalars_values: deque[list[object]] = deque()
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.popleft()

    async def get(self, _model: object, _identity: UUID) -> object | None:
        return self.get_values.popleft()

    async def scalars(self, _statement: object) -> AllResult:
        return AllResult(self.scalars_values.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


def repository(session: FakeSession) -> PostgresIngestionRepository:
    return PostgresIngestionRepository(cast(AsyncSession, session))


def queued_row() -> IngestionJobRow:
    return IngestionJobRow(
        id=uuid4(),
        source_kind=SOURCE.kind,
        external_id=SOURCE.external_id,
        canonical_url=SOURCE.canonical_url,
        input_fingerprint=FINGERPRINT,
        status=IngestionJobStatus.QUEUED,
        stage=IngestionStage.SOURCE_RESOLUTION,
        attempt=0,
        video_id=None,
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        cancel_requested_at=None,
        started_at=None,
        completed_at=None,
        last_error_code=None,
        last_error_message=None,
        last_error_retryable=None,
        created_at=NOW,
        updated_at=NOW,
    )


def running_row(*, expired: bool = False) -> IngestionJobRow:
    row = queued_row()
    row.status = IngestionJobStatus.RUNNING
    row.attempt = 1
    row.lease_owner = "worker-1"
    row.heartbeat_at = NOW - timedelta(minutes=2) if expired else NOW
    row.lease_expires_at = NOW - timedelta(minutes=1) if expired else NOW + LEASE
    row.started_at = NOW - timedelta(minutes=2)
    return row


def failed_row(*, retryable: bool = True) -> IngestionJobRow:
    row = queued_row()
    row.status = IngestionJobStatus.FAILED
    row.stage = IngestionStage.TRANSCRIPTION
    row.attempt = 1
    row.completed_at = NOW
    row.last_error_code = "TRANSCRIPTION_FAILED"
    row.last_error_message = "Transcription failed safely."
    row.last_error_retryable = retryable
    return row


@pytest.mark.asyncio
async def test_create_or_get_inserts_created_event_or_reuses_existing() -> None:
    created_row = queued_row()
    created_session = FakeSession(created_row.id, created_row, 0)
    created, was_created = await repository(created_session).create_or_get(
        SOURCE, FINGERPRINT, now=NOW
    )

    assert was_created is True
    assert created.job_id == created_row.id
    assert created_session.commits == 1
    assert isinstance(created_session.added[0], JobEventRow)
    assert created_session.added[0].event_type == IngestionEventType.CREATED

    existing_row = queued_row()
    existing_session = FakeSession(None, existing_row)
    existing, was_created = await repository(existing_session).create_or_get(
        SOURCE, FINGERPRINT, now=NOW
    )
    assert was_created is False
    assert existing.job_id == existing_row.id
    assert existing_session.commits == 1


@pytest.mark.asyncio
async def test_create_or_get_rejects_an_unloadable_conflict() -> None:
    session = FakeSession(None, None)
    with pytest.raises(IngestionRepositoryError, match="could not be loaded"):
        await repository(session).create_or_get(SOURCE, FINGERPRINT, now=NOW)
    assert session.commits == 1


@pytest.mark.asyncio
async def test_get_and_list_events_map_rows() -> None:
    row = queued_row()
    session = FakeSession()
    session.get_values.extend((None, row))
    details = {"output_ids": ["cue-1"]}
    session.scalars_values.append(
        [
            JobEventRow(
                id=uuid4(),
                job_id=row.id,
                sequence=1,
                event_type=IngestionEventType.CREATED,
                stage=IngestionStage.SOURCE_RESOLUTION,
                attempt=0,
                occurred_at=NOW,
                message=None,
                error_code=None,
                retryable=None,
                details=None,
            ),
            JobEventRow(
                id=uuid4(),
                job_id=row.id,
                sequence=2,
                event_type=IngestionEventType.STAGE_COMPLETED,
                stage=IngestionStage.SOURCE_RESOLUTION,
                attempt=1,
                occurred_at=NOW,
                message="Resolved.",
                error_code=None,
                retryable=None,
                details=details,
            ),
        ]
    )
    repo = repository(session)

    assert await repo.get(uuid4()) is None
    assert (await repo.get(row.id)).job_id == row.id  # type: ignore[union-attr]
    events = await repo.list_events(row.id)
    assert [item.sequence for item in events] == [1, 2]
    assert events[0].details is None
    assert events[1].details == details


@pytest.mark.asyncio
async def test_claim_validates_inputs_and_returns_none_when_idle() -> None:
    repo = repository(FakeSession())
    with pytest.raises(ValueError, match="worker_id"):
        await repo.claim_next(" ", LEASE, now=NOW)
    with pytest.raises(ValueError, match="lease_duration"):
        await repo.claim_next("worker", timedelta(0), now=NOW)
    idle_session = FakeSession(None)
    assert await repository(idle_session).claim_next("worker", LEASE, now=NOW) is None
    assert idle_session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [queued_row(), running_row(expired=True)])
async def test_claim_assigns_or_reassigns_a_bounded_lease(row: IngestionJobRow) -> None:
    original_attempt = row.attempt
    original_started = row.started_at
    session = FakeSession(row, 0)
    claimed = await repository(session).claim_next("worker-2", LEASE, now=NOW)

    assert claimed is not None
    assert claimed.status is IngestionJobStatus.RUNNING
    assert claimed.attempt == original_attempt + 1
    assert claimed.lease_owner == "worker-2"
    assert claimed.lease_expires_at == NOW + LEASE
    assert claimed.started_at == (original_started or NOW)
    assert session.commits == 1


@pytest.mark.asyncio
async def test_heartbeat_refreshes_only_a_live_owned_lease() -> None:
    row = running_row()
    session = FakeSession(row, 4)
    heartbeat = await repository(session).heartbeat(row.id, "worker-1", LEASE, now=NOW)
    assert heartbeat.heartbeat_at == NOW
    assert heartbeat.lease_expires_at == NOW + LEASE

    with pytest.raises(ValueError, match="worker_id"):
        await repository(FakeSession()).heartbeat(row.id, "", LEASE, now=NOW)
    with pytest.raises(IngestionRepositoryError, match="does not exist"):
        await repository(FakeSession(None)).heartbeat(row.id, "worker-1", LEASE, now=NOW)
    with pytest.raises(IngestionRepositoryError, match="does not own"):
        await repository(FakeSession(running_row())).heartbeat(
            row.id, "other-worker", LEASE, now=NOW
        )
    with pytest.raises(IngestionRepositoryError, match="does not own"):
        await repository(FakeSession(running_row(expired=True))).heartbeat(
            row.id, "worker-1", LEASE, now=NOW
        )


@pytest.mark.asyncio
async def test_stage_start_and_completion_are_monotonic_and_checkpoint_details() -> None:
    row = running_row()
    start_session = FakeSession(row, 1)
    started = await repository(start_session).start_stage(
        row.id, "worker-1", IngestionStage.METADATA, now=NOW
    )
    assert started.stage is IngestionStage.METADATA

    complete_session = FakeSession(row, 2)
    completed = await repository(complete_session).complete_stage(
        row.id,
        "worker-1",
        IngestionStage.METADATA,
        IngestionStage.CAPTION_RETRIEVAL,
        details={"metadata_ready": True},
        now=NOW,
    )
    assert completed.stage is IngestionStage.CAPTION_RETRIEVAL
    assert cast(JobEventRow, complete_session.added[0]).details == {"metadata_ready": True}
    assert cast(JobEventRow, complete_session.added[0]).stage == IngestionStage.METADATA

    row.stage = IngestionStage.INDEXING
    with pytest.raises(IngestionRepositoryError, match="transition"):
        await repository(FakeSession(row)).start_stage(
            row.id, "worker-1", IngestionStage.PERSISTENCE, now=NOW
        )
    with pytest.raises(IngestionRepositoryError, match="transition"):
        await repository(FakeSession(row)).start_stage(
            row.id, "worker-1", IngestionStage.COMPLETED, now=NOW
        )
    with pytest.raises(IngestionRepositoryError, match="active"):
        await repository(FakeSession(row)).complete_stage(
            row.id,
            "worker-1",
            IngestionStage.PERSISTENCE,
            IngestionStage.EMBEDDING,
            now=NOW,
        )
    row.stage = IngestionStage.COMPLETED
    with pytest.raises(IngestionRepositoryError, match="active"):
        await repository(FakeSession(row)).complete_stage(
            row.id,
            "worker-1",
            IngestionStage.COMPLETED,
            IngestionStage.COMPLETED,
            now=NOW,
        )
    row.stage = IngestionStage.METADATA
    with pytest.raises(IngestionRepositoryError, match="active"):
        await repository(FakeSession(row)).complete_stage(
            row.id,
            "worker-1",
            IngestionStage.METADATA,
            IngestionStage.METADATA,
            now=NOW,
        )
    with pytest.raises(IngestionRepositoryError, match="active"):
        await repository(FakeSession(row)).complete_stage(
            row.id,
            "worker-1",
            IngestionStage.METADATA,
            IngestionStage.SOURCE_RESOLUTION,
            now=NOW,
        )
    with pytest.raises(IngestionRepositoryError, match="active"):
        await repository(FakeSession(row)).complete_stage(
            row.id,
            "worker-1",
            IngestionStage.METADATA,
            IngestionStage.COMPLETED,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_cancellation_is_idempotent_and_immediate_for_queued_jobs() -> None:
    terminal = queued_row()
    terminal.status = IngestionJobStatus.CANCELLED
    terminal.completed_at = NOW
    terminal_session = FakeSession(terminal)
    assert (
        await repository(terminal_session).request_cancellation(terminal.id, now=NOW)
    ).status is IngestionJobStatus.CANCELLED
    assert terminal_session.commits == 1

    queued = queued_row()
    queued_session = FakeSession(queued, 0, 1)
    cancelled = await repository(queued_session).request_cancellation(queued.id, now=NOW)
    assert cancelled.status is IngestionJobStatus.CANCELLED
    assert cancelled.completed_at == NOW
    assert len(queued_session.added) == 2

    active = running_row()
    active_session = FakeSession(active, 0)
    requested = await repository(active_session).request_cancellation(active.id, now=NOW)
    assert requested.status is IngestionJobStatus.RUNNING
    assert requested.cancel_requested_at == NOW

    repeated_session = FakeSession(active)
    repeated = await repository(repeated_session).request_cancellation(active.id, now=NOW)
    assert repeated.cancel_requested_at == NOW
    assert repeated_session.added == []


@pytest.mark.asyncio
async def test_failure_retry_cancel_and_success_transitions() -> None:
    row = running_row()
    repo = repository(FakeSession())
    with pytest.raises(ValueError, match="failure metadata"):
        await repo.fail(row.id, "worker-1", error_code=" ", message="safe", retryable=True)

    fail_session = FakeSession(row, 0)
    failed = await repository(fail_session).fail(
        row.id,
        "worker-1",
        error_code="TRANSCRIPTION_FAILED",
        message="Transcription failed safely.",
        retryable=True,
        now=NOW,
    )
    assert failed.status is IngestionJobStatus.FAILED
    assert failed.lease_owner is None
    assert failed.last_error_retryable is True

    row.cancel_requested_at = NOW
    retry_session = FakeSession(row, 1)
    retried = await repository(retry_session).retry(row.id, now=NOW)
    assert retried.status is IngestionJobStatus.QUEUED
    assert retried.completed_at is None
    assert retried.last_error_code is None
    assert retried.cancel_requested_at is None

    not_failed = queued_row()
    with pytest.raises(IngestionRepositoryError, match="retryable failed"):
        await repository(FakeSession(not_failed)).retry(not_failed.id, now=NOW)
    not_retryable = failed_row(retryable=False)
    with pytest.raises(IngestionRepositoryError, match="retryable failed"):
        await repository(FakeSession(not_retryable)).retry(not_retryable.id, now=NOW)

    active_cancel = running_row()
    cancel_session = FakeSession(active_cancel, 0)
    cancelled = await repository(cancel_session).cancel(active_cancel.id, "worker-1", now=NOW)
    assert cancelled.status is IngestionJobStatus.CANCELLED
    assert cancelled.cancel_requested_at == NOW

    active_success = running_row()
    success_session = FakeSession(active_success, 0, 1)
    video_id = uuid4()
    succeeded = await repository(success_session).succeed(
        active_success.id,
        "worker-1",
        video_id=video_id,
        details={"indexed": True},
        now=NOW,
    )
    assert succeeded.status is IngestionJobStatus.SUCCEEDED
    assert succeeded.stage is IngestionStage.COMPLETED
    assert succeeded.video_id == video_id
    stage_event, completed_event = cast(
        tuple[JobEventRow, JobEventRow], tuple(success_session.added)
    )
    assert stage_event.event_type == IngestionEventType.STAGE_COMPLETED
    assert stage_event.stage == IngestionStage.SOURCE_RESOLUTION
    assert stage_event.details == {"indexed": True}
    assert completed_event.event_type == IngestionEventType.COMPLETED

    already_complete = running_row()
    already_complete.stage = IngestionStage.COMPLETED
    with pytest.raises(IngestionRepositoryError, match="already complete"):
        await repository(FakeSession(already_complete)).succeed(
            already_complete.id,
            "worker-1",
            video_id=video_id,
            now=NOW,
        )
