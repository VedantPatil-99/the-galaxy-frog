"""Behavior coverage for the deterministic durable ingestion runner."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from galaxy_frog.application.ingestion.runner import (
    IngestionJobRunner,
    IngestionStageError,
    StageContext,
    StageResult,
)
from galaxy_frog.domain.ingestion.models import (
    IngestionEvent,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
LEASE = timedelta(minutes=1)
VIDEO_ID = uuid4()


def running_job() -> IngestionJob:
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


class FakeRepository:
    def __init__(self, job: IngestionJob) -> None:
        self.job: IngestionJob | None = job
        self.calls: list[tuple[str, object | None]] = []

    async def create_or_get(self, *args: object, **kwargs: object) -> tuple[IngestionJob, bool]:
        raise AssertionError((args, kwargs))

    async def get(self, job_id: object) -> IngestionJob | None:
        self.calls.append(("get", job_id))
        return self.job

    async def list_events(self, job_id: object) -> tuple[IngestionEvent, ...]:
        raise AssertionError(job_id)

    async def claim_next(self, *args: object, **kwargs: object) -> IngestionJob | None:
        raise AssertionError((args, kwargs))

    async def heartbeat(
        self, job_id: object, worker_id: str, lease_duration: timedelta, **kwargs: object
    ) -> IngestionJob:
        del worker_id, lease_duration, kwargs
        self.calls.append(("heartbeat", job_id))
        assert self.job is not None
        return self.job

    async def start_stage(
        self, job_id: object, worker_id: str, stage: IngestionStage, **kwargs: object
    ) -> IngestionJob:
        del worker_id, kwargs
        self.calls.append(("start", stage))
        assert self.job is not None
        self.job = replace(self.job, stage=stage)
        return self.job

    async def complete_stage(
        self,
        job_id: object,
        worker_id: str,
        stage: IngestionStage,
        next_stage: IngestionStage,
        **kwargs: object,
    ) -> IngestionJob:
        del job_id, worker_id, kwargs
        self.calls.append(("complete", stage))
        assert self.job is not None
        self.job = replace(self.job, stage=next_stage)
        return self.job

    async def request_cancellation(self, *args: object, **kwargs: object) -> IngestionJob:
        raise AssertionError((args, kwargs))

    async def fail(
        self,
        job_id: object,
        worker_id: str,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        **kwargs: object,
    ) -> IngestionJob:
        del job_id, worker_id, kwargs
        self.calls.append(("fail", error_code))
        assert self.job is not None
        self.job = replace(
            self.job,
            status=IngestionJobStatus.FAILED,
            lease_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            completed_at=NOW,
            last_error_code=error_code,
            last_error_message=message,
            last_error_retryable=retryable,
        )
        return self.job

    async def retry(self, *args: object, **kwargs: object) -> IngestionJob:
        raise AssertionError((args, kwargs))

    async def cancel(self, job_id: object, worker_id: str, **kwargs: object) -> IngestionJob:
        del worker_id, kwargs
        self.calls.append(("cancel", job_id))
        assert self.job is not None
        self.job = replace(
            self.job,
            status=IngestionJobStatus.CANCELLED,
            lease_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            cancel_requested_at=self.job.cancel_requested_at or NOW,
            completed_at=NOW,
        )
        return self.job

    async def succeed(
        self,
        job_id: object,
        worker_id: str,
        *,
        video_id: object,
        details: dict[str, object] | None = None,
        **kwargs: object,
    ) -> IngestionJob:
        del job_id, worker_id, details, kwargs
        self.calls.append(("succeed", video_id))
        assert self.job is not None
        self.job = replace(
            self.job,
            status=IngestionJobStatus.SUCCEEDED,
            stage=IngestionStage.COMPLETED,
            video_id=VIDEO_ID,
            lease_owner=None,
            lease_expires_at=None,
            heartbeat_at=None,
            completed_at=NOW,
        )
        return self.job


class Handler:
    def __init__(
        self,
        stage: IngestionStage,
        result: StageResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.stage = stage
        self.result = result
        self.error = error
        self.calls = 0

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        del job, context
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def runner(repo: FakeRepository, *handlers: Handler) -> IngestionJobRunner:
    return IngestionJobRunner(
        repository=repo,
        handlers=handlers,
        worker_id="worker-1",
        lease_duration=LEASE,
    )


def test_stage_error_and_result_validate_public_failure_metadata() -> None:
    error = IngestionStageError("PROVIDER_TIMEOUT", "The provider timed out.", retryable=True)
    assert error.code == "PROVIDER_TIMEOUT"
    assert error.message == "The provider timed out."
    assert error.retryable is True
    with pytest.raises(ValueError, match="metadata"):
        IngestionStageError(" ", "safe", retryable=False)
    with pytest.raises(ValueError, match="video_id"):
        StageResult(IngestionStage.COMPLETED)
    with pytest.raises(ValueError, match="only accepted"):
        StageResult(IngestionStage.METADATA, video_id=VIDEO_ID)


def test_runner_configuration_rejects_invalid_workers_leases_and_handlers() -> None:
    repo = FakeRepository(running_job())
    handler = Handler(IngestionStage.SOURCE_RESOLUTION, StageResult(IngestionStage.METADATA))
    with pytest.raises(ValueError, match="worker_id"):
        IngestionJobRunner(repository=repo, handlers=(), worker_id=" ", lease_duration=LEASE)
    with pytest.raises(ValueError, match="lease_duration"):
        IngestionJobRunner(
            repository=repo,
            handlers=(),
            worker_id="worker-1",
            lease_duration=timedelta(0),
        )
    with pytest.raises(ValueError, match="unique"):
        runner(repo, handler, handler)
    completed = Handler(
        IngestionStage.COMPLETED, StageResult(IngestionStage.COMPLETED, video_id=VIDEO_ID)
    )
    with pytest.raises(ValueError, match="completed stage"):
        runner(repo, completed)


@pytest.mark.asyncio
async def test_stage_context_heartbeats_and_reads_cancellation() -> None:
    job = running_job()
    repo = FakeRepository(job)
    context = StageContext(repo, job.job_id, "worker-1", LEASE)
    assert await context.heartbeat() == job
    assert await context.cancellation_requested() is False
    repo.job = replace(job, cancel_requested_at=NOW)
    assert await context.cancellation_requested() is True
    repo.job = None
    with pytest.raises(IngestionStageError, match="no longer exists"):
        await context.cancellation_requested()


@pytest.mark.asyncio
async def test_runner_advances_multiple_stages_and_succeeds() -> None:
    job = running_job()
    repo = FakeRepository(job)
    source = Handler(
        IngestionStage.SOURCE_RESOLUTION,
        StageResult(IngestionStage.METADATA, {"source": "youtube"}),
    )
    metadata = Handler(
        IngestionStage.METADATA,
        StageResult(IngestionStage.COMPLETED, {"video_id": str(VIDEO_ID)}, VIDEO_ID),
    )

    result = await runner(repo, source, metadata).run(job)

    assert result.status is IngestionJobStatus.SUCCEEDED
    assert source.calls == metadata.calls == 1
    assert [name for name, _value in repo.calls].count("heartbeat") == 1
    assert ("complete", IngestionStage.METADATA) not in repo.calls
    assert repo.calls[-1] == ("succeed", VIDEO_ID)


@pytest.mark.asyncio
async def test_runner_honors_cancellation_before_and_after_a_stage() -> None:
    requested = replace(running_job(), cancel_requested_at=NOW)
    before_repo = FakeRepository(requested)
    before = await runner(before_repo).run(requested)
    assert before.status is IngestionJobStatus.CANCELLED

    job = running_job()
    after_repo = FakeRepository(job)

    class CancellationHandler(Handler):
        async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
            assert after_repo.job is not None
            after_repo.job = replace(after_repo.job, cancel_requested_at=NOW)
            return await super().execute(job, context)

    handler = CancellationHandler(
        IngestionStage.SOURCE_RESOLUTION,
        StageResult(IngestionStage.METADATA),
    )
    after = await runner(after_repo, handler).run(job)
    assert after.status is IngestionJobStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected_code", "retryable"),
    [
        (None, "INGESTION_STAGE_UNAVAILABLE", False),
        (
            Handler(
                IngestionStage.SOURCE_RESOLUTION,
                error=IngestionStageError(
                    "PROVIDER_TIMEOUT",
                    "The provider timed out.",
                    retryable=True,
                ),
            ),
            "PROVIDER_TIMEOUT",
            True,
        ),
        (
            Handler(IngestionStage.SOURCE_RESOLUTION, error=RuntimeError("private detail")),
            "PROCESSING_FAILED",
            True,
        ),
    ],
)
async def test_runner_persists_safe_failures(
    handler: Handler | None, expected_code: str, retryable: bool
) -> None:
    job = running_job()
    repo = FakeRepository(job)
    handlers = () if handler is None else (handler,)
    failed = await runner(repo, *handlers).run(job)
    assert failed.status is IngestionJobStatus.FAILED
    assert failed.last_error_code == expected_code
    assert failed.last_error_retryable is retryable
    if expected_code == "PROCESSING_FAILED":
        assert failed.last_error_message == "The ingestion stage failed unexpectedly."


@pytest.mark.asyncio
async def test_runner_fails_safely_when_job_disappears_after_stage_execution() -> None:
    job = running_job()
    repo = FakeRepository(job)

    class MissingJobHandler(Handler):
        async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
            result = await super().execute(job, context)
            repo.job = None
            return result

    handler = MissingJobHandler(
        IngestionStage.SOURCE_RESOLUTION,
        StageResult(IngestionStage.METADATA),
    )
    with pytest.raises(AssertionError):
        await runner(repo, handler).run(job)
