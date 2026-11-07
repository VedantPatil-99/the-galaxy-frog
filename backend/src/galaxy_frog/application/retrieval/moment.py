"""Select persisted evidence around a paused video playhead."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from galaxy_frog.application.retrieval.temporal import RetrievalVideoNotFound
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.transcripts.models import TranscriptCue
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.vision.models import FrameSource, OCRBlock

_MOMENT_RADIUS_MS = 15_000


class InvalidMomentTimestamp(ValueError):
    """The playhead is not an integer millisecond within the video."""


@dataclass(frozen=True, slots=True)
class MomentCue:
    cue: TranscriptCue
    unit_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MomentOCR:
    block_id: UUID
    frame_id: UUID
    capture_ms: int
    block: OCRBlock


@dataclass(frozen=True, slots=True)
class MomentFrame:
    """Persisted frame metadata, including a backend-only path and optional hashes."""

    frame_id: UUID
    scene_id: UUID | None
    sequence: int
    start_ms: int
    end_ms: int
    capture_ms: int
    source: FrameSource
    path: Path
    dhash: str | None
    content_sha256: str | None


@dataclass(frozen=True, slots=True)
class MomentFrames:
    previous: MomentFrame | None = None
    current: MomentFrame | None = None
    next: MomentFrame | None = None


@dataclass(frozen=True, slots=True)
class MomentEvidence:
    video_id: UUID
    timestamp_ms: int
    window: TimeWindow
    cues: tuple[MomentCue, ...]
    ocr: tuple[MomentOCR, ...]
    frames: MomentFrames


class MomentEvidenceReader(Protocol):
    async def get_video(self, video_id: UUID) -> VideoRecord | None: ...

    async def get_cues(
        self, video: VideoRecord, start_ms: int, end_ms: int
    ) -> tuple[MomentCue, ...]: ...

    async def get_ocr(
        self, video_id: UUID, start_ms: int, end_ms: int
    ) -> tuple[MomentOCR, ...]: ...

    async def get_frames(
        self, video_id: UUID, timestamp_ms: int, start_ms: int, end_ms: int
    ) -> MomentFrames: ...


class RetrieveMomentEvidence:
    def __init__(self, reader: MomentEvidenceReader) -> None:
        self._reader = reader

    async def execute(self, video_id: UUID, timestamp_ms: int) -> MomentEvidence:
        video = await self._reader.get_video(video_id)
        if video is None:
            raise RetrievalVideoNotFound("Video not found.")
        duration_ms = video.metadata.duration_ms
        if type(timestamp_ms) is not int or not 0 <= timestamp_ms < duration_ms:
            raise InvalidMomentTimestamp("Timestamp must be an integer within the video duration.")

        start_ms = max(0, timestamp_ms - _MOMENT_RADIUS_MS)
        end_ms = min(duration_ms, timestamp_ms + _MOMENT_RADIUS_MS)
        return MomentEvidence(
            video_id=video_id,
            timestamp_ms=timestamp_ms,
            window=TimeWindow(start_ms, end_ms),
            cues=await self._reader.get_cues(video, start_ms, end_ms),
            ocr=await self._reader.get_ocr(video_id, start_ms, end_ms),
            frames=await self._reader.get_frames(video_id, timestamp_ms, start_ms, end_ms),
        )
