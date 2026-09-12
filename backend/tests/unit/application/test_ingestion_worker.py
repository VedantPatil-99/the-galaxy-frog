"""Behavior coverage for the durable ingestion polling loop."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest

from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.application.ingestion.worker import ClaimedJobRunner, IngestionWorker
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionJobStatus, IngestionStage
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
LEASE = timedelta(minutes=2)
POLL = timedelta(milliseconds=1)


def claimed_job() -> IngestionJob:
    return IngestionJob(
        job_id=uuid4(),
        source=SourceReference(
            VideoSourceKind.YOUTUBE,
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        input_fingerprint="a" * 64,
        status=IngestionJobStatus.RUNNING,
        stage=IngestionStage.SOURCE_RESOLUTION,
        attempt=1,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1",
        lease_expires_at=NOW + LEASE,
        heartbeat_at=NOW,
        started_at=NOW,
    )


class QueueRepository:
    def __init__(self, jobs: list[IngestionJob | None]) -> None:
        self.jobs = jobs
        self.claims: list[tuple[str, timedelta]] = []

    async def claim_next(
        self,
        worker_id: str,
        lease_duration: timedelta,
        **_kwargs: object,
    ) -> IngestionJob | None:
        self.claims.append((worker_id, lease_duration))
        return self.jobs.pop(0) if self.jobs else None


class RecordingRunner:
    def __init__(self, stop_event: asyncio.Event | None = None) -> None:
        self.jobs: list[IngestionJob] = []
        self.stop_event = stop_event

    async def run(self, job: IngestionJob) -> IngestionJob:
        self.jobs.append(job)
        if self.stop_event is not None:
            self.stop_event.set()
        return job


def worker(
    repository: QueueRepository,
    runner: RecordingRunner,
    *,
    poll_interval: timedelta = POLL,
) -> IngestionWorker:
    return IngestionWorker(
        repository=cast(IngestionRepository, repository),
        runner=cast(ClaimedJobRunner, runner),
        worker_id="worker-1",
        lease_duration=LEASE,
        poll_interval=poll_interval,
    )


def test_worker_configuration_rejects_invalid_values() -> None:
    repository = QueueRepository([])
    runner = RecordingRunner()
    with pytest.raises(ValueError, match="worker_id"):
        IngestionWorker(
            repository=cast(IngestionRepository, repository),
            runner=runner,
            worker_id=" ",
            lease_duration=LEASE,
            poll_interval=POLL,
        )
    with pytest.raises(ValueError, match="lease_duration"):
        IngestionWorker(
            repository=cast(IngestionRepository, repository),
            runner=runner,
            worker_id="worker-1",
            lease_duration=timedelta(0),
            poll_interval=POLL,
        )
    with pytest.raises(ValueError, match="poll_interval"):
        worker(repository, runner, poll_interval=timedelta(0))


@pytest.mark.asyncio
async def test_run_once_returns_idle_or_runs_the_claimed_job() -> None:
    job = claimed_job()
    repository = QueueRepository([None, job])
    runner = RecordingRunner()
    service = worker(repository, runner)

    assert await service.run_once() is None
    assert await service.run_once() is job
    assert repository.claims == [("worker-1", LEASE), ("worker-1", LEASE)]
    assert runner.jobs == [job]


@pytest.mark.asyncio
async def test_run_until_stopped_waits_when_idle_and_counts_claimed_jobs() -> None:
    stop_event = asyncio.Event()
    job = claimed_job()
    repository = QueueRepository([None, job])
    runner = RecordingRunner(stop_event)

    processed = await worker(repository, runner).run_until_stopped(stop_event)

    assert processed == 1
    assert runner.jobs == [job]


@pytest.mark.asyncio
async def test_run_until_stopped_returns_without_claiming_when_already_stopped() -> None:
    stop_event = asyncio.Event()
    stop_event.set()
    repository = QueueRepository([])

    assert await worker(repository, RecordingRunner()).run_until_stopped(stop_event) == 0
    assert repository.claims == []
