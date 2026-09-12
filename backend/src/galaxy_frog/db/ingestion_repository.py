"""PostgreSQL repository for durable ingestion state and lease-safe claims."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import IngestionJobRow, JobEventRow
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


class IngestionRepositoryError(RuntimeError):
    """Raised when a requested durable transition is not currently valid."""


class PostgresIngestionRepository:
    """Keep the current job projection and event log in one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_or_get(
        self,
        source: SourceReference,
        input_fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> tuple[IngestionJob, bool]:
        timestamp = now or datetime.now(UTC)
        job_id = uuid4()
        statement = (
            insert(IngestionJobRow)
            .values(
                id=job_id,
                source_kind=source.kind,
                external_id=source.external_id,
                canonical_url=source.canonical_url,
                input_fingerprint=input_fingerprint,
                status=IngestionJobStatus.QUEUED,
                stage=IngestionStage.SOURCE_RESOLUTION,
                attempt=0,
                created_at=timestamp,
                updated_at=timestamp,
            )
            .on_conflict_do_nothing(index_elements=[IngestionJobRow.input_fingerprint])
            .returning(IngestionJobRow.id)
        )
        inserted_id = await self._session.scalar(statement)
        created = inserted_id is not None
        if created:
            row = await self._locked_job(job_id)
            await self._append_event(row, IngestionEventType.CREATED, timestamp)
            await self._session.commit()
            return self._job(row), True
        row = await self._session.scalar(
            select(IngestionJobRow).where(IngestionJobRow.input_fingerprint == input_fingerprint)
        )
        if row is None:
            await self._session.commit()
            raise IngestionRepositoryError("The idempotent ingestion job could not be loaded.")
        await self._session.commit()
        return self._job(row), False

    async def get(self, job_id: UUID) -> IngestionJob | None:
        row = await self._session.get(IngestionJobRow, job_id)
        return self._job(row) if row is not None else None

    async def list_events(self, job_id: UUID) -> tuple[IngestionEvent, ...]:
        rows = (
            await self._session.scalars(
                select(JobEventRow)
                .where(JobEventRow.job_id == job_id)
                .order_by(JobEventRow.sequence)
            )
        ).all()
        return tuple(self._event(row) for row in rows)

    async def claim_next(
        self,
        worker_id: str,
        lease_duration: timedelta,
        *,
        now: datetime | None = None,
    ) -> IngestionJob | None:
        self._validate_worker_and_lease(worker_id, lease_duration)
        timestamp = now or datetime.now(UTC)
        row = await self._session.scalar(
            select(IngestionJobRow)
            .where(
                or_(
                    IngestionJobRow.status == IngestionJobStatus.QUEUED,
                    and_(
                        IngestionJobRow.status == IngestionJobStatus.RUNNING,
                        IngestionJobRow.lease_expires_at <= timestamp,
                    ),
                )
            )
            .order_by(IngestionJobRow.created_at, IngestionJobRow.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            await self._session.commit()
            return None
        row.status = IngestionJobStatus.RUNNING
        row.attempt += 1
        row.lease_owner = worker_id
        row.heartbeat_at = timestamp
        row.lease_expires_at = timestamp + lease_duration
        row.started_at = row.started_at or timestamp
        row.updated_at = timestamp
        await self._append_event(row, IngestionEventType.CLAIMED, timestamp)
        await self._session.commit()
        return self._job(row)

    async def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        lease_duration: timedelta,
        *,
        now: datetime | None = None,
    ) -> IngestionJob:
        self._validate_worker_and_lease(worker_id, lease_duration)
        timestamp = now or datetime.now(UTC)
        row = await self._owned_running_job(job_id, worker_id, timestamp)
        row.heartbeat_at = timestamp
        row.lease_expires_at = timestamp + lease_duration
        row.updated_at = timestamp
        await self._append_event(row, IngestionEventType.HEARTBEAT, timestamp)
        await self._session.commit()
        return self._job(row)

    async def start_stage(
        self,
        job_id: UUID,
        worker_id: str,
        stage: IngestionStage,
        *,
        now: datetime | None = None,
    ) -> IngestionJob:
        timestamp = now or datetime.now(UTC)
        row = await self._owned_running_job(job_id, worker_id, timestamp)
        current = IngestionStage(row.stage)
        if not can_advance_stage(current, stage) or stage is IngestionStage.COMPLETED:
            raise IngestionRepositoryError("The requested ingestion stage transition is invalid.")
        row.stage = stage
        row.updated_at = timestamp
        await self._append_event(row, IngestionEventType.STAGE_STARTED, timestamp)
        await self._session.commit()
        return self._job(row)

    async def complete_stage(
        self,
        job_id: UUID,
        worker_id: str,
        stage: IngestionStage,
        next_stage: IngestionStage,
        *,
        details: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> IngestionJob:
        timestamp = now or datetime.now(UTC)
        row = await self._owned_running_job(job_id, worker_id, timestamp)
        if (
            IngestionStage(row.stage) is not stage
            or stage is IngestionStage.COMPLETED
            or next_stage is stage
            or next_stage is IngestionStage.COMPLETED
            or not can_advance_stage(stage, next_stage)
        ):
            raise IngestionRepositoryError("Only the active ingestion stage can be completed.")
        row.updated_at = timestamp
        await self._append_event(
            row,
            IngestionEventType.STAGE_COMPLETED,
            timestamp,
            stage=stage,
            details=details,
        )
        row.stage = next_stage
        await self._session.commit()
        return self._job(row)

    async def request_cancellation(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IngestionJob:
        timestamp = now or datetime.now(UTC)
        row = await self._locked_job(job_id)
        if IngestionJobStatus(row.status) in TERMINAL_JOB_STATUSES:
            await self._session.commit()
            return self._job(row)
        if row.cancel_requested_at is None:
            row.cancel_requested_at = timestamp
            row.updated_at = timestamp
            await self._append_event(row, IngestionEventType.CANCEL_REQUESTED, timestamp)
        if IngestionJobStatus(row.status) is IngestionJobStatus.QUEUED:
            self._finish(row, IngestionJobStatus.CANCELLED, timestamp)
            await self._append_event(row, IngestionEventType.CANCELLED, timestamp)
        await self._session.commit()
        return self._job(row)

    async def fail(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> IngestionJob:
        if not error_code.strip() or not message.strip():
            raise ValueError("failure metadata must not be blank")
        timestamp = now or datetime.now(UTC)
        row = await self._owned_running_job(job_id, worker_id, timestamp)
        self._finish(row, IngestionJobStatus.FAILED, timestamp)
        row.last_error_code = error_code
        row.last_error_message = message
        row.last_error_retryable = retryable
        await self._append_event(
            row,
            IngestionEventType.FAILED,
            timestamp,
            message=message,
            error_code=error_code,
            retryable=retryable,
        )
        await self._session.commit()
        return self._job(row)

    async def retry(self, job_id: UUID, *, now: datetime | None = None) -> IngestionJob:
        timestamp = now or datetime.now(UTC)
        row = await self._locked_job(job_id)
        if (
            IngestionJobStatus(row.status) is not IngestionJobStatus.FAILED
            or row.last_error_retryable is not True
        ):
            raise IngestionRepositoryError("Only retryable failed jobs can be queued again.")
        row.status = IngestionJobStatus.QUEUED
        row.completed_at = None
        row.last_error_code = None
        row.last_error_message = None
        row.last_error_retryable = None
        row.cancel_requested_at = None
        row.updated_at = timestamp
        await self._append_event(row, IngestionEventType.RETRY_REQUESTED, timestamp)
        await self._session.commit()
        return self._job(row)

    async def cancel(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> IngestionJob:
        timestamp = now or datetime.now(UTC)
        row = await self._owned_running_job(job_id, worker_id, timestamp)
        self._finish(row, IngestionJobStatus.CANCELLED, timestamp)
        row.cancel_requested_at = row.cancel_requested_at or timestamp
        await self._append_event(row, IngestionEventType.CANCELLED, timestamp)
        await self._session.commit()
        return self._job(row)

    async def succeed(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        video_id: UUID,
        details: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> IngestionJob:
        timestamp = now or datetime.now(UTC)
        row = await self._owned_running_job(job_id, worker_id, timestamp)
        active_stage = IngestionStage(row.stage)
        if active_stage is IngestionStage.COMPLETED:
            raise IngestionRepositoryError("The active ingestion stage is already complete.")
        await self._append_event(
            row,
            IngestionEventType.STAGE_COMPLETED,
            timestamp,
            stage=active_stage,
            details=details,
        )
        row.stage = IngestionStage.COMPLETED
        row.video_id = video_id
        self._finish(row, IngestionJobStatus.SUCCEEDED, timestamp)
        await self._append_event(row, IngestionEventType.COMPLETED, timestamp)
        await self._session.commit()
        return self._job(row)

    async def _locked_job(self, job_id: UUID) -> IngestionJobRow:
        row = await self._session.scalar(
            select(IngestionJobRow).where(IngestionJobRow.id == job_id).with_for_update()
        )
        if row is None:
            raise IngestionRepositoryError("The ingestion job does not exist.")
        return row

    async def _owned_running_job(
        self,
        job_id: UUID,
        worker_id: str,
        timestamp: datetime,
    ) -> IngestionJobRow:
        row = await self._locked_job(job_id)
        if (
            IngestionJobStatus(row.status) is not IngestionJobStatus.RUNNING
            or row.lease_owner != worker_id
            or row.lease_expires_at is None
            or row.lease_expires_at <= timestamp
        ):
            raise IngestionRepositoryError("The worker does not own the active ingestion lease.")
        return row

    async def _append_event(
        self,
        row: IngestionJobRow,
        event_type: IngestionEventType,
        timestamp: datetime,
        *,
        message: str | None = None,
        error_code: str | None = None,
        retryable: bool | None = None,
        details: dict[str, object] | None = None,
        stage: IngestionStage | None = None,
    ) -> None:
        sequence = await self._session.scalar(
            select(func.coalesce(func.max(JobEventRow.sequence), 0) + 1).where(
                JobEventRow.job_id == row.id
            )
        )
        self._session.add(
            JobEventRow(
                id=uuid4(),
                job_id=row.id,
                sequence=int(sequence or 1),
                event_type=event_type,
                stage=stage or row.stage,
                attempt=row.attempt,
                occurred_at=timestamp,
                message=message,
                error_code=error_code,
                retryable=retryable,
                details=details,
            )
        )

    @staticmethod
    def _finish(
        row: IngestionJobRow,
        status: IngestionJobStatus,
        timestamp: datetime,
    ) -> None:
        row.status = status
        row.lease_owner = None
        row.lease_expires_at = None
        row.heartbeat_at = None
        row.completed_at = timestamp
        row.updated_at = timestamp

    @staticmethod
    def _validate_worker_and_lease(worker_id: str, lease_duration: timedelta) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")

    @staticmethod
    def _job(row: IngestionJobRow) -> IngestionJob:
        return IngestionJob(
            job_id=row.id,
            source=SourceReference(
                kind=VideoSourceKind(row.source_kind),
                external_id=row.external_id,
                canonical_url=row.canonical_url,
            ),
            input_fingerprint=row.input_fingerprint,
            status=IngestionJobStatus(row.status),
            stage=IngestionStage(row.stage),
            attempt=row.attempt,
            video_id=row.video_id,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            heartbeat_at=row.heartbeat_at,
            cancel_requested_at=row.cancel_requested_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            last_error_retryable=row.last_error_retryable,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _event(row: JobEventRow) -> IngestionEvent:
        return IngestionEvent(
            event_id=row.id,
            job_id=row.job_id,
            sequence=row.sequence,
            event_type=IngestionEventType(row.event_type),
            stage=IngestionStage(row.stage),
            attempt=row.attempt,
            occurred_at=row.occurred_at,
            message=row.message,
            error_code=row.error_code,
            retryable=row.retryable,
            details=dict(row.details) if row.details is not None else None,
        )
