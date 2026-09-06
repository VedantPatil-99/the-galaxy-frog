"""Opt-in PostgreSQL acceptance coverage for the durable ingestion foundation."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galaxy_frog.application.ingestion.runner import (
    IngestionJobRunner,
    IngestionStageError,
    StageContext,
    StageResult,
)
from galaxy_frog.application.ingestion.worker import IngestionWorker
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.ingestion_repository import (
    IngestionRepositoryError,
    PostgresIngestionRepository,
)
from galaxy_frog.db.models import IngestionJobRow
from galaxy_frog.domain.ingestion.models import (
    IngestionEventType,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="set RUN_DATABASE_INTEGRATION=1 with the local pgvector database running",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply the real Alembic chain before exercising PostgreSQL claim semantics."""

    backend_root = Path(__file__).resolve().parents[2]
    command.upgrade(Config(str(backend_root / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_concurrent_claim_recovery_cancellation_and_retry_are_durable() -> None:
    """Prove the P2.1 lifecycle against real row locks and database constraints."""

    engine = create_database_engine(Settings(app_env="test"))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    external_id = uuid4().hex[:11]
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    fingerprint = sha256(f"durable:{external_id}".encode()).hexdigest()
    retry_fingerprint = sha256(f"retry:{external_id}".encode()).hexdigest()
    job_ids: list[UUID] = []

    async def create(value: str) -> tuple[IngestionJob, bool]:
        async with sessions() as session:
            return await PostgresIngestionRepository(session).create_or_get(source, value)

    async def claim(worker_id: str, *, clock_offset_seconds: int = 0) -> IngestionJob | None:
        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            return await repository.claim_next(
                worker_id,
                lease_duration=timedelta(seconds=1),
                now=datetime.now(UTC) + timedelta(seconds=clock_offset_seconds),
            )

    try:
        (first, first_created), (second, second_created) = await asyncio.gather(
            create(fingerprint),
            create(fingerprint),
        )
        assert sorted((first_created, second_created)) == [False, True]
        assert first.job_id == second.job_id
        job_id = first.job_id
        job_ids.append(job_id)

        claims = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        claimed = [job for job in claims if job is not None]
        assert len(claimed) == 1
        assert claimed[0].attempt == 1

        recovered = await claim("worker-recovery", clock_offset_seconds=2)
        assert recovered is not None
        assert recovered.attempt == 2
        assert recovered.lease_owner == "worker-recovery"

        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            requested = await repository.request_cancellation(job_id)
            assert requested.cancel_requested_at is not None
            cancelled = await repository.cancel(job_id, "worker-recovery")
            assert cancelled.status is IngestionJobStatus.CANCELLED
            events = await repository.list_events(job_id)
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            assert [event.event_type for event in events] == [
                IngestionEventType.CREATED,
                IngestionEventType.CLAIMED,
                IngestionEventType.CLAIMED,
                IngestionEventType.CANCEL_REQUESTED,
                IngestionEventType.CANCELLED,
            ]

        retry_job, created = await create(retry_fingerprint)
        assert created is True
        retry_job_id = retry_job.job_id
        job_ids.append(retry_job_id)
        retry_claim = await claim("worker-retry")
        assert retry_claim is not None

        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            failed = await repository.fail(
                retry_job_id,
                "worker-retry",
                error_code="TRANSCRIPTION_UNAVAILABLE",
                message="Transcription is temporarily unavailable.",
                retryable=True,
            )
            assert failed.status is IngestionJobStatus.FAILED
            queued = await repository.retry(retry_job_id)
            assert queued.status is IngestionJobStatus.QUEUED

        reclaimed = await claim("worker-final")
        assert reclaimed is not None
        assert reclaimed.job_id == retry_job_id
        assert reclaimed.attempt == 2

        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            await repository.fail(
                retry_job_id,
                "worker-final",
                error_code="UNSUPPORTED_MEDIA",
                message="The media cannot be processed.",
                retryable=False,
            )
            with pytest.raises(IngestionRepositoryError, match="retryable failed"):
                await repository.retry(retry_job_id)
            events = await repository.list_events(retry_job_id)
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    finally:
        async with sessions() as session:
            await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id.in_(job_ids)))
            await session.commit()
        await engine.dispose()


class FailAfterRestart:
    """Record the resumed stage, then stop without advancing into later Phase 2 work."""

    stage = IngestionStage.METADATA

    def __init__(self) -> None:
        self.jobs: list[IngestionJob] = []

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        del context
        self.jobs.append(job)
        raise IngestionStageError(
            "RESTART_CHECK_COMPLETE",
            "The restart recovery checkpoint was exercised.",
            retryable=True,
        )


@pytest.mark.asyncio
async def test_worker_restart_resumes_the_last_completed_stage() -> None:
    """Prove a new process resumes at metadata without repeating source resolution."""

    engine = create_database_engine(Settings(app_env="test"))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    external_id = uuid4().hex[:11]
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    fingerprint = sha256(f"restart:{external_id}".encode()).hexdigest()
    crash_time = datetime.now(UTC) - timedelta(minutes=1)
    job_id: UUID | None = None

    try:
        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            created, was_created = await repository.create_or_get(
                source,
                fingerprint,
                now=crash_time,
            )
            assert was_created is True
            job_id = created.job_id
            claimed = await repository.claim_next(
                "worker-before-crash",
                timedelta(seconds=1),
                now=crash_time,
            )
            assert claimed is not None
            assert claimed.job_id == job_id
            await repository.start_stage(
                job_id,
                "worker-before-crash",
                IngestionStage.SOURCE_RESOLUTION,
                now=crash_time,
            )
            checkpoint = await repository.complete_stage(
                job_id,
                "worker-before-crash",
                IngestionStage.SOURCE_RESOLUTION,
                IngestionStage.METADATA,
                details={"source_kind": "youtube", "external_id": external_id},
                now=crash_time,
            )
            assert checkpoint.stage is IngestionStage.METADATA

        resumed_stage = FailAfterRestart()
        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            lease_duration = timedelta(minutes=2)
            runner = IngestionJobRunner(
                repository=repository,
                handlers=(resumed_stage,),
                worker_id="worker-after-restart",
                lease_duration=lease_duration,
            )
            worker = IngestionWorker(
                repository=repository,
                runner=runner,
                worker_id="worker-after-restart",
                lease_duration=lease_duration,
                poll_interval=timedelta(milliseconds=1),
            )

            result = await worker.run_once()

            assert result is not None
            assert result.job_id == job_id
            assert result.status is IngestionJobStatus.FAILED
            assert result.stage is IngestionStage.METADATA
            assert result.attempt == 2
            assert [job.stage for job in resumed_stage.jobs] == [IngestionStage.METADATA]
            events = await repository.list_events(job_id)
            assert [event.event_type for event in events] == [
                IngestionEventType.CREATED,
                IngestionEventType.CLAIMED,
                IngestionEventType.STAGE_STARTED,
                IngestionEventType.STAGE_COMPLETED,
                IngestionEventType.CLAIMED,
                IngestionEventType.STAGE_STARTED,
                IngestionEventType.FAILED,
            ]
            source_completions = [
                event
                for event in events
                if event.event_type is IngestionEventType.STAGE_COMPLETED
                and event.stage is IngestionStage.SOURCE_RESOLUTION
            ]
            assert len(source_completions) == 1
            assert source_completions[0].details == {
                "source_kind": "youtube",
                "external_id": external_id,
            }
    finally:
        if job_id is not None:
            async with sessions() as session:
                await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id == job_id))
                await session.commit()
        await engine.dispose()
