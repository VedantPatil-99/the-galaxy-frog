"""Deterministic stage execution for one lease-owned ingestion job."""

import asyncio
import logging
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, TypeVar
from uuid import UUID

from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionStage

_ResultT = TypeVar("_ResultT")
logger = logging.getLogger(__name__)

_OBSERVABLE_DECISION_KEYS = frozenset(
    {
        "audio_asset_id",
        "audio_end_ms",
        "audio_start_ms",
        "caption_kind",
        "compute_type",
        "cue_count",
        "device",
        "downloader",
        "downloader_revision",
        "embedding_collection_id",
        "end_ms",
        "fallback_reason",
        "language_code",
        "language_confidence",
        "language_confidence_method",
        "model",
        "model_revision",
        "normalizer",
        "normalizer_revision",
        "processing_seconds",
        "provider",
        "provider_revision",
        "reused",
        "size_bytes",
        "start_ms",
        "temporary_media_present",
        "track_id",
        "transcript_origin",
        "transcription_run_id",
        "video_id",
    }
)


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

    async def run_with_heartbeats(self, operation: Awaitable[_ResultT]) -> _ResultT:
        """Keep the durable lease alive while awaiting one long provider operation."""

        task = asyncio.ensure_future(operation)
        interval = self.lease_duration.total_seconds() / 3
        try:
            while not task.done():
                done, _pending = await asyncio.wait((task,), timeout=interval)
                if task in done:
                    break
                await self.heartbeat()
            return await task
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


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
                    succeeded = await self._repository.succeed(
                        current.job_id,
                        self._worker_id,
                        video_id=result.video_id,
                        details=result.details,
                    )
                    self._log_stage_completed(current, result)
                    return succeeded
                completed_stage = current
                current = await self._repository.complete_stage(
                    current.job_id,
                    self._worker_id,
                    current.stage,
                    result.next_stage,
                    details=result.details,
                )
                self._log_stage_completed(completed_stage, result)
                current = await self._repository.heartbeat(
                    current.job_id,
                    self._worker_id,
                    self._lease_duration,
                )
            except IngestionStageError as exc:
                failed = await self._repository.fail(
                    current.job_id,
                    self._worker_id,
                    error_code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                )
                self._log_stage_failed(current, exc.code, exc.retryable)
                return failed
            except Exception:
                failed = await self._repository.fail(
                    current.job_id,
                    self._worker_id,
                    error_code="PROCESSING_FAILED",
                    message="The ingestion stage failed unexpectedly.",
                    retryable=True,
                )
                self._log_stage_failed(current, "PROCESSING_FAILED", True)
                return failed

    @staticmethod
    def _log_stage_completed(job: IngestionJob, result: StageResult) -> None:
        decisions = {
            key: value
            for key, value in (result.details or {}).items()
            if key in _OBSERVABLE_DECISION_KEYS
        }
        logger.info(
            "Ingestion stage completed job_id=%s attempt=%s stage=%s next_stage=%s decisions=%s",
            str(job.job_id),
            job.attempt,
            job.stage.value,
            result.next_stage.value,
            decisions,
            extra={
                "event_name": "ingestion_stage_completed",
                "job_id": str(job.job_id),
                "attempt": job.attempt,
                "stage": job.stage.value,
                "next_stage": result.next_stage.value,
                "decisions": decisions,
            },
        )

    @staticmethod
    def _log_stage_failed(job: IngestionJob, error_code: str, retryable: bool) -> None:
        logger.warning(
            "Ingestion stage failed job_id=%s attempt=%s stage=%s error_code=%s retryable=%s",
            str(job.job_id),
            job.attempt,
            job.stage.value,
            error_code,
            retryable,
            extra={
                "event_name": "ingestion_stage_failed",
                "job_id": str(job.job_id),
                "attempt": job.attempt,
                "stage": job.stage.value,
                "error_code": error_code,
                "retryable": retryable,
            },
        )
