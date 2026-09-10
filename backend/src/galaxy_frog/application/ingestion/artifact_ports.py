"""Persistence ports for resumable media and transcription stage outputs."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from galaxy_frog.domain.media import AcquiredAudio, AudioAsset
from galaxy_frog.domain.transcription import TranscriptionCheckpoint, TranscriptionResult


class AudioAssetRepository(Protocol):
    """Persist local audio evidence independently from a worker process."""

    async def save(self, audio: AcquiredAudio) -> AudioAsset: ...

    async def get_latest_available(self, job_id: UUID) -> AudioAsset | None: ...

    async def mark_deleted(
        self,
        asset_id: UUID,
        *,
        deleted_at: datetime | None = None,
    ) -> AudioAsset: ...


class TranscriptionCheckpointRepository(Protocol):
    """Persist and restore one provider result without repeating inference."""

    async def save(
        self,
        audio_asset_id: UUID,
        result: TranscriptionResult,
    ) -> TranscriptionCheckpoint: ...

    async def get(self, job_id: UUID) -> TranscriptionCheckpoint | None: ...
