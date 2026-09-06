"""Application-facing persistence contract for durable ingestion."""

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from galaxy_frog.domain.ingestion.models import IngestionEvent, IngestionJob, IngestionStage
from galaxy_frog.domain.videos.models import SourceReference


class IngestionRepository(Protocol):
    """Persist and transition ingestion state without exposing database rows."""

    async def create_or_get(
        self,
        source: SourceReference,
        input_fingerprint: str,
        *,
        now: datetime | None = None,
    ) -> tuple[IngestionJob, bool]: ...

    async def get(self, job_id: UUID) -> IngestionJob | None: ...

    async def list_events(self, job_id: UUID) -> tuple[IngestionEvent, ...]: ...

    async def claim_next(
        self,
        worker_id: str,
        lease_duration: timedelta,
        *,
        now: datetime | None = None,
    ) -> IngestionJob | None: ...

    async def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        lease_duration: timedelta,
        *,
        now: datetime | None = None,
    ) -> IngestionJob: ...

    async def start_stage(
        self,
        job_id: UUID,
        worker_id: str,
        stage: IngestionStage,
        *,
        now: datetime | None = None,
    ) -> IngestionJob: ...

    async def complete_stage(
        self,
        job_id: UUID,
        worker_id: str,
        stage: IngestionStage,
        next_stage: IngestionStage,
        *,
        details: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> IngestionJob: ...

    async def request_cancellation(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> IngestionJob: ...

    async def fail(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        error_code: str,
        message: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> IngestionJob: ...

    async def retry(self, job_id: UUID, *, now: datetime | None = None) -> IngestionJob: ...

    async def cancel(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> IngestionJob: ...

    async def succeed(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        video_id: UUID,
        details: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> IngestionJob: ...
