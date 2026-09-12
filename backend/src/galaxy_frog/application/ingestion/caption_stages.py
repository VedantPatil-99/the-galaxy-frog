"""Resumable Phase 2 stage handlers for the existing caption-first path."""

from collections.abc import Sequence
from typing import Never

from galaxy_frog.application.ingestion.runner import (
    IngestionStageError,
    IngestionStageHandler,
    StageContext,
    StageResult,
)
from galaxy_frog.application.videos.import_video import ImportVideo, select_caption_track
from galaxy_frog.application.videos.ports import VideoRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionStage
from galaxy_frog.domain.retrieval.ports import TranscriptSearch
from galaxy_frog.domain.videos.source import VideoSource, VideoSourceError


class _CaptionStage:
    def __init__(
        self,
        *,
        sources: Sequence[VideoSource],
        videos: VideoRepository,
        transcript_search: TranscriptSearch,
        preferred_languages: Sequence[str],
    ) -> None:
        self._sources = tuple(sources)
        self._videos = videos
        self._transcript_search = transcript_search
        self._preferred_languages = tuple(preferred_languages)

    def _source(self, job: IngestionJob) -> VideoSource:
        source = next(
            (candidate for candidate in self._sources if candidate.source_kind is job.source.kind),
            None,
        )
        if source is None:
            raise IngestionStageError(
                "INGESTION_SOURCE_UNAVAILABLE",
                "The configured worker does not support this video source.",
                retryable=False,
            )
        return source

    @staticmethod
    def _raise_source_error(error: VideoSourceError) -> Never:
        raise IngestionStageError(
            error.code,
            error.message,
            retryable=error.retryable,
        ) from error


class SourceResolutionStage(_CaptionStage):
    """Revalidate the persisted canonical source identity without external I/O."""

    stage = IngestionStage.SOURCE_RESOLUTION

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        del context
        try:
            resolved = self._source(job).canonicalize(job.source.canonical_url)
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        if resolved != job.source:
            raise IngestionStageError(
                "SOURCE_IDENTITY_CHANGED",
                "The canonical video source identity changed during ingestion.",
                retryable=False,
            )
        return StageResult(
            IngestionStage.METADATA,
            {"source_kind": job.source.kind, "external_id": job.source.external_id},
        )


class MetadataStage(_CaptionStage):
    """Verify safe metadata before any transcript or media work."""

    stage = IngestionStage.METADATA

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        existing = await self._videos.find_by_source(job.source)
        if existing is not None:
            return StageResult(IngestionStage.EMBEDDING, {"reused": True})
        try:
            await context.heartbeat()
            metadata = await self._source(job).fetch_metadata(job.source)
            await context.heartbeat()
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        return StageResult(
            IngestionStage.CAPTION_RETRIEVAL,
            {"title": metadata.title, "duration_ms": metadata.duration_ms},
        )


class CaptionRetrievalStage(_CaptionStage):
    """Select a caption track before the idempotent persistence stage."""

    stage = IngestionStage.CAPTION_RETRIEVAL

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        existing = await self._videos.find_by_source(job.source)
        if existing is not None:
            return StageResult(IngestionStage.EMBEDDING, {"reused": True})
        try:
            await context.heartbeat()
            tracks = await self._source(job).list_caption_tracks(job.source)
            track = select_caption_track(tracks, self._preferred_languages)
            await context.heartbeat()
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        return StageResult(
            IngestionStage.PERSISTENCE,
            {
                "track_id": track.track_id,
                "language_code": track.language_code,
                "caption_kind": track.kind,
            },
        )


class CaptionPersistenceStage(_CaptionStage):
    """Normalize, chunk, and persist captions through the idempotent Phase 1 service."""

    stage = IngestionStage.PERSISTENCE

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        try:
            await context.heartbeat()
            result = await ImportVideo(
                sources=self._sources,
                repository=self._videos,
                preferred_languages=self._preferred_languages,
            ).execute(job.source.canonical_url)
            await context.heartbeat()
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        return StageResult(
            IngestionStage.EMBEDDING,
            {"video_id": str(result.video.video_id), "reused": result.reused},
        )


class EmbeddingStage(_CaptionStage):
    """Idempotently index every persisted transcript unit in the configured collection."""

    stage = IngestionStage.EMBEDDING

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        video = await self._videos.find_by_source(job.source)
        if video is None:
            raise IngestionStageError(
                "PERSISTED_VIDEO_NOT_FOUND",
                "The persisted video could not be loaded for indexing.",
                retryable=True,
            )
        await context.heartbeat()
        collection_id = await self._transcript_search.ensure_indexed(video.video_id)
        await context.heartbeat()
        return StageResult(
            IngestionStage.CLEANUP,
            {"video_id": str(video.video_id), "embedding_collection_id": str(collection_id)},
        )


class CleanupStage(_CaptionStage):
    """Complete caption ingestion; no temporary media exists in this path."""

    stage = IngestionStage.CLEANUP

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        del context
        video = await self._videos.find_by_source(job.source)
        if video is None:
            raise IngestionStageError(
                "PERSISTED_VIDEO_NOT_FOUND",
                "The persisted video could not be loaded during cleanup.",
                retryable=True,
            )
        return StageResult(
            IngestionStage.COMPLETED,
            {"video_id": str(video.video_id), "temporary_media_present": False},
            video_id=video.video_id,
        )


def caption_ingestion_handlers(
    *,
    sources: Sequence[VideoSource],
    videos: VideoRepository,
    transcript_search: TranscriptSearch,
    preferred_languages: Sequence[str] = ("en",),
) -> tuple[IngestionStageHandler, ...]:
    """Build the ordered caption-first handler registry for a worker session."""

    return (
        SourceResolutionStage(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
        ),
        MetadataStage(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
        ),
        CaptionRetrievalStage(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
        ),
        CaptionPersistenceStage(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
        ),
        EmbeddingStage(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
        ),
        CleanupStage(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
        ),
    )
