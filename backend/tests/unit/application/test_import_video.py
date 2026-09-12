"""Tests for synchronous caption import and idempotent indexing."""

# pyright: reportPrivateUsage=false

from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.videos.import_video import ImportVideo
from galaxy_frog.domain.transcripts import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord
from galaxy_frog.domain.videos.source import VideoSourceError, VideoSourceErrorCode
from galaxy_frog.pipelines.transcription import TemporalChunker, TemporalChunkingPolicy


class FakeSource:
    source_kind = VideoSourceKind.YOUTUBE

    def __init__(self) -> None:
        self.metadata_calls = 0

    def canonicalize(self, locator: str) -> SourceReference:
        if locator != "valid":
            raise VideoSourceError(VideoSourceErrorCode.INVALID_SOURCE, "invalid")
        return SourceReference(
            self.source_kind,
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        self.metadata_calls += 1
        return SafeVideoMetadata(reference, "Example", 3000)

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        del reference
        return (
            CaptionTrack("automatic:en", "en", CaptionKind.AUTOMATIC),
            CaptionTrack("manual:en", "en", CaptionKind.MANUAL),
        )

    async def fetch_caption_cues(
        self, reference: SourceReference, track: CaptionTrack
    ) -> tuple[SourceCaptionCue, ...]:
        del reference
        assert track.track_id == "manual:en"
        return (
            SourceCaptionCue(0, 0, 1000, "First sentence."),
            SourceCaptionCue(1, 1000, 2000, "Second sentence."),
            SourceCaptionCue(2, 2000, 3000, "Third sentence."),
        )


class MemoryRepository:
    record: VideoRecord | None = None
    transcript: TranscriptRecord | None = None

    async def find_by_source(self, reference: SourceReference) -> VideoRecord | None:
        if self.record is not None and self.record.metadata.reference == reference:
            return self.record
        return None

    async def save_import(
        self,
        metadata: SafeVideoMetadata,
        cues: tuple[TranscriptCue, ...],
        units: tuple[RetrievalUnit, ...],
        *,
        transcription_run_id: UUID | None = None,
    ) -> VideoRecord:
        del transcription_run_id
        self.record = VideoRecord(uuid4(), metadata)
        self.transcript = TranscriptRecord(self.record, cues, units)
        return self.record

    async def get_video(self, video_id: UUID) -> VideoRecord | None:
        return self.record if self.record and self.record.video_id == video_id else None

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None:
        return self.transcript if self.record and self.record.video_id == video_id else None


class FakeSearch:
    indexed: list[UUID]

    def __init__(self) -> None:
        self.indexed = []

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        self.indexed.append(video_id)
        return uuid4()

    async def search(self, video_id: UUID, query: str, *, limit: int):
        del video_id, query, limit
        return ()


@pytest.mark.asyncio
async def test_imports_once_and_retries_idempotent_indexing() -> None:
    source = FakeSource()
    repository = MemoryRepository()
    search = FakeSearch()
    service = ImportVideo(
        sources=(source,),
        repository=repository,
        transcript_search=search,
        chunker=TemporalChunker(
            TemporalChunkingPolicy(
                min_tokens=2,
                max_tokens=8,
                min_duration_ms=1000,
                max_duration_ms=3000,
                overlap_ms=0,
            )
        ),
    )

    first = await service.execute("valid")
    second = await service.execute("valid")

    assert first.reused is False
    assert second.reused is True
    assert first.video == second.video
    assert source.metadata_calls == 1
    assert search.indexed == [first.video.video_id, first.video.video_id]
    assert repository.transcript is not None
    assert repository.transcript.units[0].cue_ids


@pytest.mark.asyncio
async def test_rejects_unsupported_sources_and_missing_tracks() -> None:
    service = ImportVideo(sources=(FakeSource(),), repository=MemoryRepository())

    with pytest.raises(VideoSourceError):
        await service.execute("invalid")

    source = FakeSource()

    async def no_tracks(reference: SourceReference) -> tuple[CaptionTrack, ...]:
        del reference
        return ()

    source.list_caption_tracks = no_tracks  # type: ignore[method-assign]
    with pytest.raises(VideoSourceError):
        await ImportVideo(sources=(source,), repository=MemoryRepository()).execute("valid")


@pytest.mark.asyncio
async def test_import_without_search_persists_and_reuses() -> None:
    source = FakeSource()
    repository = MemoryRepository()
    service = ImportVideo(sources=(source,), repository=repository)

    first = await service.execute("valid")
    second = await service.execute("valid")

    assert first.reused is False
    assert second.reused is True


@pytest.mark.asyncio
async def test_rejects_a_transcript_that_produces_no_units() -> None:
    class EmptyChunker(TemporalChunker):
        def chunk(self, cues: tuple[TranscriptCue, ...]) -> tuple[RetrievalUnit, ...]:
            assert cues
            return ()

    with pytest.raises(VideoSourceError, match="no usable transcript"):
        await ImportVideo(
            sources=(FakeSource(),),
            repository=MemoryRepository(),
            chunker=EmptyChunker(),
        ).execute("valid")


def test_source_resolution_propagates_specific_failures() -> None:
    class UnsupportedSource(FakeSource):
        def canonicalize(self, locator: str) -> SourceReference:
            del locator
            from galaxy_frog.domain.videos.source import VideoSourceErrorCode

            raise VideoSourceError(VideoSourceErrorCode.UNSUPPORTED_VIDEO, "playlist")

    service = ImportVideo(sources=(UnsupportedSource(),), repository=MemoryRepository())

    with pytest.raises(VideoSourceError, match="playlist"):
        service._resolve_source("valid")


def test_caption_selection_prefers_requested_automatic_over_other_manual() -> None:
    service = ImportVideo(
        sources=(FakeSource(),),
        repository=MemoryRepository(),
        preferred_languages=("en",),
    )
    selected = service._select_track(
        (
            CaptionTrack("manual:fr", "fr", CaptionKind.MANUAL),
            CaptionTrack("automatic:en", "en", CaptionKind.AUTOMATIC),
        )
    )

    assert selected.track_id == "automatic:en"
