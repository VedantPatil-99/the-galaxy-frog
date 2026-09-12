"""Synchronous caption-only import orchestration."""

from collections.abc import Sequence
from dataclasses import dataclass

from galaxy_frog.application.videos.ports import VideoRepository
from galaxy_frog.domain.retrieval.ports import TranscriptSearch
from galaxy_frog.domain.transcripts.models import TranscriptCue
from galaxy_frog.domain.videos.models import CaptionKind, CaptionTrack, SourceReference
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.videos.source import VideoSource, VideoSourceError, VideoSourceErrorCode
from galaxy_frog.pipelines.transcription.chunking import TemporalChunker


@dataclass(frozen=True, slots=True)
class ImportVideoResult:
    """Import outcome including whether canonical data already existed."""

    video: VideoRecord
    reused: bool


def select_caption_track(
    tracks: Sequence[CaptionTrack],
    preferred_languages: Sequence[str],
) -> CaptionTrack:
    """Select one caption track deterministically without depending on a provider SDK."""

    if not tracks:
        raise VideoSourceError(
            VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE,
            "This video has no available captions.",
        )
    language_rank = {
        language.casefold(): index for index, language in enumerate(preferred_languages)
    }

    def rank(track: CaptionTrack) -> tuple[int, int, str]:
        language = language_rank.get(track.language_code.casefold())
        if language is not None:
            group = 0 if track.kind is CaptionKind.MANUAL else 1
            return (group, language, track.language_code)
        return (
            2 if track.kind is CaptionKind.MANUAL else 3,
            0,
            track.language_code,
        )

    return min(tracks, key=rank)


class ImportVideo:
    """Resolve one source, normalize captions, chunk, and persist atomically."""

    def __init__(
        self,
        *,
        sources: Sequence[VideoSource],
        repository: VideoRepository,
        chunker: TemporalChunker | None = None,
        transcript_search: TranscriptSearch | None = None,
        preferred_languages: Sequence[str] = ("en",),
    ) -> None:
        self._sources = tuple(sources)
        self._repository = repository
        self._chunker = chunker or TemporalChunker()
        self._transcript_search = transcript_search
        self._preferred_languages = tuple(preferred_languages)

    async def execute(self, locator: str) -> ImportVideoResult:
        source, reference = self._resolve_source(locator)
        existing = await self._repository.find_by_source(reference)
        if existing is not None:
            if self._transcript_search is not None:
                await self._transcript_search.ensure_indexed(existing.video_id)
            return ImportVideoResult(existing, reused=True)

        metadata = await source.fetch_metadata(reference)
        tracks = await source.list_caption_tracks(reference)
        track = select_caption_track(tracks, self._preferred_languages)
        source_cues = await source.fetch_caption_cues(reference, track)
        cues = tuple(
            TranscriptCue.from_source(
                source=reference,
                track_id=track.track_id,
                language_code=track.language_code,
                caption_kind=track.kind,
                cue=cue,
            )
            for cue in source_cues
        )
        units = self._chunker.chunk(cues)
        if not cues or not units:
            raise VideoSourceError(
                VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE,
                "The selected caption track contains no usable transcript evidence.",
            )
        saved = await self._repository.save_import(metadata, cues, units)
        if self._transcript_search is not None:
            await self._transcript_search.ensure_indexed(saved.video_id)
        return ImportVideoResult(saved, reused=False)

    def _resolve_source(self, locator: str) -> tuple[VideoSource, SourceReference]:
        for source in self._sources:
            try:
                return source, source.canonicalize(locator)
            except VideoSourceError as exc:
                if exc.code is not VideoSourceErrorCode.INVALID_SOURCE:
                    raise
        raise VideoSourceError(
            VideoSourceErrorCode.INVALID_SOURCE,
            "The video source is not supported.",
        )

    def _select_track(self, tracks: Sequence[CaptionTrack]) -> CaptionTrack:
        return select_caption_track(tracks, self._preferred_languages)
