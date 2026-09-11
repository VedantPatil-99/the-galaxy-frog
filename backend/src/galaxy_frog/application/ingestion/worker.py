"""Framework-independent polling loop for durable ingestion workers."""

import asyncio
import logging
from datetime import timedelta
from typing import Protocol

from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob

logger = logging.getLogger(__name__)


class ClaimedJobRunner(Protocol):
    """Execute one job that is already owned by the configured worker."""

    async def run(self, job: IngestionJob) -> IngestionJob: ...


class IngestionWorker:
    """Claim one durable job at a time and wait efficiently while the queue is idle."""

    def __init__(
        self,
        *,
        repository: IngestionRepository,
        runner: ClaimedJobRunner,
        worker_id: str,
        lease_duration: timedelta,
        poll_interval: timedelta,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if poll_interval <= timedelta(0):
            raise ValueError("poll_interval must be positive")
        self._repository = repository
        self._runner = runner
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._poll_interval_seconds = poll_interval.total_seconds()

    async def run_once(self) -> IngestionJob | None:
        """Claim and execute at most one job, returning immediately when idle."""

        job = await self._repository.claim_next(self._worker_id, self._lease_duration)
        if job is None:
            return None
        logger.info(
            "Ingestion job claimed",
            extra={
                "event_name": "ingestion_job_claimed",
                "job_id": str(job.job_id),
                "attempt": job.attempt,
                "stage": job.stage.value,
                "worker_id": self._worker_id,
            },
        )
        result = await self._runner.run(job)
        logger.info(
            "Ingestion job processing stopped",
            extra={
                "event_name": "ingestion_job_stopped",
                "job_id": str(result.job_id),
                "attempt": result.attempt,
                "stage": result.stage.value,
                "status": result.status.value,
                "error_code": result.last_error_code,
                "retryable": result.last_error_retryable,
                "worker_id": self._worker_id,
            },
        )
        return result

    async def run_until_stopped(self, stop_event: asyncio.Event) -> int:
        """Process jobs until stopped and return the number claimed by this process."""

        processed = 0
        while not stop_event.is_set():
            result = await self.run_once()
            if result is not None:
                processed += 1
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue
        return processed
