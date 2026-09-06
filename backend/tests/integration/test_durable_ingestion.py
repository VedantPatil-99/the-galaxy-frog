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
