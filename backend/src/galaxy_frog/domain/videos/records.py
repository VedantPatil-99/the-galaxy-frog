"""Persisted video and transcript projections used by application services."""

from dataclasses import dataclass
from uuid import UUID

from galaxy_frog.domain.transcription import TranscriptionCheckpoint
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import SafeVideoMetadata


@dataclass(frozen=True, slots=True)
class VideoRecord:
    """Stable persisted identity and safe metadata for one canonical video."""

    video_id: UUID
    metadata: SafeVideoMetadata


@dataclass(frozen=True, slots=True)
class TranscriptRecord:
    """Complete ordered transcript projection with retrieval provenance."""

    video: VideoRecord
    cues: tuple[TranscriptCue, ...]
    units: tuple[RetrievalUnit, ...]
    transcription: TranscriptionCheckpoint | None = None
