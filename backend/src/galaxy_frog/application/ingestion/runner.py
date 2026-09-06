"""Deterministic stage execution for one lease-owned ingestion job."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionStage


class IngestionStageError(RuntimeError):
    """A safe stage failure that can be persisted and exposed to callers."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        if not code.strip() or not message.strip():
            raise ValueError("stage failure metadata must not be blank")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class StageResult:
    """A completed stage checkpoint and the next deterministic stage."""

    next_stage: IngestionStage
    details: dict[str, object] | None = None
    video_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.next_stage is IngestionStage.COMPLETED and self.video_id is None:
            raise ValueError("a completed pipeline requires a persisted video_id")
        if self.next_stage is not IngestionStage.COMPLETED and self.video_id is not None:
            raise ValueError("video_id is only accepted for a completed pipeline")


@dataclass(frozen=True, slots=True)
class StageContext:
    """Lease and cancellation controls available to long-running handlers."""

    repository: IngestionRepository
    job_id: UUID
    worker_id: str
    lease_duration: timedelta

    async def heartbeat(self) -> IngestionJob:
        """Extend the worker lease during a long-running stage."""

        return await self.repository.heartbeat(
            self.job_id,
            self.worker_id,
            self.lease_duration,
        )

    async def cancellation_requested(self) -> bool:
        """Read the durable cancellation flag without relying on process memory."""

        job = await self.repository.get(self.job_id)
        if job is None:
            raise IngestionStageError(
                "INGESTION_JOB_NOT_FOUND",
                "The ingestion job no longer exists.",
                retryable=False,
            )
        return job.cancel_requested_at is not None


class IngestionStageHandler(Protocol):
    """Execute one idempotent stage behind the durable runner."""

    stage: IngestionStage

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult: ...


class IngestionJobRunner:
    """Advance a claimed job until success, cancellation, or a persisted failure."""

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        handlers: tuple[IngestionStageHandler, ...],
        worker_id: str,
        lease_duration: timedelta,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        by_stage = {handler.stage: handler for handler in handlers}
        if len(by_stage) != len(handlers):
            raise ValueError("ingestion stage handlers must be unique")
        if IngestionStage.COMPLETED in by_stage:
            raise ValueError("the completed stage cannot have a handler")
        self._repository = repository
        self._handlers = by_stage
        self._worker_id = worker_id
        self._lease_duration = lease_duration

    async def run(self, job: IngestionJob) -> IngestionJob:
        """Run deterministic checkpoints while retaining the caller's lease."""

        current = job
        context = StageContext(
            repository=self._repository,
            job_id=job.job_id,
            worker_id=self._worker_id,
            lease_duration=self._lease_duration,
        )
        while True:
            try:
                if current.cancel_requested_at is not None:
                    return await self._repository.cancel(current.job_id, self._worker_id)
                handler = self._handlers.get(current.stage)
                if handler is None:
                    raise IngestionStageError(
                        "INGESTION_STAGE_UNAVAILABLE",
                        "The active ingestion stage is not configured.",
                        retryable=False,
                    )
                current = await self._repository.start_stage(
                    current.job_id,
                    self._worker_id,
                    current.stage,
                )
                result = await handler.execute(current, context)
                refreshed = await self._repository.get(current.job_id)
                if refreshed is None:
                    raise IngestionStageError(
                        "INGESTION_JOB_NOT_FOUND",
                        "The ingestion job no longer exists.",
                        retryable=False,
                    )
                if refreshed.cancel_requested_at is not None:
                    return await self._repository.cancel(current.job_id, self._worker_id)
                if result.next_stage is IngestionStage.COMPLETED:
                    assert result.video_id is not None
                    return await self._repository.succeed(
                        current.job_id,
                        self._worker_id,
                        video_id=result.video_id,
                        details=result.details,
                    )
                current = await self._repository.complete_stage(
                    current.job_id,
                    self._worker_id,
                    current.stage,
                    result.next_stage,
                    details=result.details,
                )
                current = await self._repository.heartbeat(
                    current.job_id,
                    self._worker_id,
                    self._lease_duration,
                )
            except IngestionStageError as exc:
                return await self._repository.fail(
                    current.job_id,
                    self._worker_id,
                    error_code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                )
            except Exception:
                return await self._repository.fail(
                    current.job_id,
                    self._worker_id,
                    error_code="PROCESSING_FAILED",
                    message="The ingestion stage failed unexpectedly.",
                    retryable=True,
                )
