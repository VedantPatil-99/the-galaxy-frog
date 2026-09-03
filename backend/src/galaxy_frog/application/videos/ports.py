"""Persistence port for transcript-first video application services."""

from typing import Protocol
from uuid import UUID

from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import SafeVideoMetadata, SourceReference
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord


class VideoRepository(Protocol):
    """Repository operations required by the Phase 1 use cases."""

    async def find_by_source(self, reference: SourceReference) -> VideoRecord | None: ...

    async def save_import(
        self,
        metadata: SafeVideoMetadata,
        cues: tuple[TranscriptCue, ...],
        units: tuple[RetrievalUnit, ...],
    ) -> VideoRecord: ...

    async def get_video(self, video_id: UUID) -> VideoRecord | None: ...

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None: ...
