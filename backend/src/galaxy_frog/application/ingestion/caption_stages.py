"""Resumable caption-first ingestion with explicit local ASR fallback."""

import asyncio
from collections.abc import Sequence
from typing import Never
from uuid import UUID

from galaxy_frog.application.ingestion.artifact_ports import (
    AudioAssetRepository,
    TranscriptionCheckpointRepository,
)
from galaxy_frog.application.ingestion.runner import (
    IngestionStageError,
    IngestionStageHandler,
    StageContext,
    StageResult,
)
from galaxy_frog.application.videos.import_video import ImportVideo, select_caption_track
from galaxy_frog.application.videos.ports import VideoRepository
from galaxy_frog.domain.ingestion.models import (
    IngestionEventType,
    IngestionJob,
    IngestionStage,
)
from galaxy_frog.domain.media import (
    AcquiredAudio,
    AudioAcquirer,
    AudioAcquisitionError,
    AudioAcquisitionRequest,
    AudioFallbackReason,
)
from galaxy_frog.domain.retrieval.ports import TranscriptSearch
from galaxy_frog.domain.transcription import (
    TranscriptionCheckpoint,
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionRequest,
)
from galaxy_frog.domain.transcripts import TranscriptCue
from galaxy_frog.domain.videos.source import VideoSource, VideoSourceError
from galaxy_frog.pipelines.transcription import TemporalChunker


class _TranscriptStage:
    def __init__(
        self,
        *,
        sources: Sequence[VideoSource],
        videos: VideoRepository,
        transcript_search: TranscriptSearch,
        preferred_languages: Sequence[str],
        audio_acquirer: AudioAcquirer | None,
        audio_assets: AudioAssetRepository | None,
        transcription_checkpoints: TranscriptionCheckpointRepository | None,
        transcription_provider: TranscriptionProvider | None,
    ) -> None:
        self._sources = tuple(sources)
        self._videos = videos
        self._transcript_search = transcript_search
        self._preferred_languages = tuple(preferred_languages)
        self._audio_acquirer = audio_acquirer
        self._audio_assets = audio_assets
        self._transcription_checkpoints = transcription_checkpoints
        self._transcription_provider = transcription_provider
        self._chunker = TemporalChunker()

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

    def _audio_services(self) -> tuple[AudioAcquirer, AudioAssetRepository]:
        if self._audio_acquirer is None or self._audio_assets is None:  # pragma: no cover
            raise IngestionStageError(
                "AUDIO_FALLBACK_UNAVAILABLE",
                "The configured worker cannot acquire fallback audio.",
                retryable=False,
            )
        return self._audio_acquirer, self._audio_assets

    def _transcription_services(
        self,
    ) -> tuple[AudioAssetRepository, TranscriptionCheckpointRepository, TranscriptionProvider]:
        if (  # pragma: no cover
            self._audio_assets is None
            or self._transcription_checkpoints is None
            or self._transcription_provider is None
        ):
            raise IngestionStageError(
                "TRANSCRIPTION_UNAVAILABLE",
                "The configured worker cannot run fallback transcription.",
                retryable=False,
            )
        return self._audio_assets, self._transcription_checkpoints, self._transcription_provider

    @staticmethod
    def _raise_source_error(error: VideoSourceError) -> Never:
        raise IngestionStageError(
            error.code,
            error.message,
            retryable=error.retryable,
        ) from error

    @staticmethod
    def _raise_audio_error(error: AudioAcquisitionError) -> Never:
        raise IngestionStageError(
            error.code,
            error.message,
            retryable=error.retryable,
        ) from error

    @staticmethod
    def _raise_transcription_error(error: TranscriptionError) -> Never:
        raise IngestionStageError(
            error.code,
            error.message,
            retryable=error.retryable,
        ) from error


class SourceResolutionStage(_TranscriptStage):
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


class MetadataStage(_TranscriptStage):
    """Verify safe metadata before any transcript or media work."""

    stage = IngestionStage.METADATA

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        existing = await self._videos.find_by_source(job.source)
        if existing is not None:
            return StageResult(IngestionStage.EMBEDDING, {"reused": True})
        try:
            metadata = await context.run_with_heartbeats(
                self._source(job).fetch_metadata(job.source)
            )
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        return StageResult(
            IngestionStage.CAPTION_RETRIEVAL,
            {"title": metadata.title, "duration_ms": metadata.duration_ms},
        )


class CaptionRetrievalStage(_TranscriptStage):
    """Use a viable caption track or record why local audio is required."""

    stage = IngestionStage.CAPTION_RETRIEVAL

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        existing = await self._videos.find_by_source(job.source)
        if existing is not None:
            return StageResult(IngestionStage.EMBEDDING, {"reused": True})
        source = self._source(job)
        try:
            tracks = await context.run_with_heartbeats(source.list_caption_tracks(job.source))
            if not tracks:
                return self._fallback(AudioFallbackReason.CAPTIONS_UNAVAILABLE)
            track = select_caption_track(tracks, self._preferred_languages)
            source_cues = await context.run_with_heartbeats(
                source.fetch_caption_cues(job.source, track)
            )
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        cues = tuple(
            TranscriptCue.from_source(
                source=job.source,
                track_id=track.track_id,
                language_code=track.language_code,
                caption_kind=track.kind,
                cue=cue,
            )
            for cue in source_cues
        )
        if not cues or not self._chunker.chunk(cues):
            return self._fallback(AudioFallbackReason.CAPTIONS_UNUSABLE)
        return StageResult(
            IngestionStage.PERSISTENCE,
            {
                "transcript_origin": "caption",
                "track_id": track.track_id,
                "language_code": track.language_code,
                "caption_kind": track.kind,
                "cue_count": len(cues),
            },
        )

    @staticmethod
    def _fallback(reason: AudioFallbackReason) -> StageResult:
        return StageResult(
            IngestionStage.AUDIO_ACQUISITION,
            {"transcript_origin": "asr", "fallback_reason": reason.value},
        )


class AudioAcquisitionStage(_TranscriptStage):
    """Acquire and durably checkpoint bounded audio only after caption insufficiency."""

    stage = IngestionStage.AUDIO_ACQUISITION

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        existing_video = await self._videos.find_by_source(job.source)
        if existing_video is not None:
            return StageResult(IngestionStage.EMBEDDING, {"reused": True})
        acquirer, assets = self._audio_services()
        existing = await assets.get_latest_available(job.job_id)
        if existing is not None and await asyncio.to_thread(existing.audio.path.is_file):
            return self._result(existing.asset_id, existing.audio, reused=True)
        if existing is not None:
            await assets.mark_deleted(existing.asset_id)

        fallback_reason = await self._fallback_reason(job, context)
        try:
            metadata = await context.run_with_heartbeats(
                self._source(job).fetch_metadata(job.source)
            )
            audio = await context.run_with_heartbeats(
                acquirer.acquire(
                    AudioAcquisitionRequest(
                        job_id=job.job_id,
                        attempt=job.attempt,
                        source=job.source,
                        expected_duration_ms=metadata.duration_ms,
                        fallback_reason=fallback_reason,
                    )
                )
            )
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        except AudioAcquisitionError as exc:
            self._raise_audio_error(exc)
        checkpoint = await assets.save(audio)
        return self._result(checkpoint.asset_id, checkpoint.audio, reused=False)

    @staticmethod
    def _result(asset_id: UUID, audio: AcquiredAudio, *, reused: bool) -> StageResult:
        return StageResult(
            IngestionStage.TRANSCRIPTION,
            {
                "audio_asset_id": str(asset_id),
                "fallback_reason": audio.fallback_reason.value,
                "start_ms": audio.start_ms,
                "end_ms": audio.end_ms,
                "size_bytes": audio.size_bytes,
                "downloader": audio.downloader,
                "downloader_revision": audio.downloader_revision,
                "normalizer": audio.normalizer,
                "normalizer_revision": audio.normalizer_revision,
                "reused": reused,
            },
        )

    @staticmethod
    async def _fallback_reason(
        job: IngestionJob,
        context: StageContext,
    ) -> AudioFallbackReason:
        events = await context.repository.list_events(job.job_id)
        for event in reversed(events):
            if (
                event.event_type is IngestionEventType.STAGE_COMPLETED
                and event.stage is IngestionStage.CAPTION_RETRIEVAL
                and event.details is not None
            ):
                value = event.details.get("fallback_reason")
                try:
                    return AudioFallbackReason(str(value))
                except ValueError:
                    break
        raise IngestionStageError(
            "CAPTION_FALLBACK_REASON_MISSING",
            "The caption decision required for audio fallback is missing.",
            retryable=False,
        )


class TranscriptionStage(_TranscriptStage):
    """Run local multilingual ASR once and persist its complete provider output."""

    stage = IngestionStage.TRANSCRIPTION

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        existing_video = await self._videos.find_by_source(job.source)
        if existing_video is not None:
            return StageResult(IngestionStage.EMBEDDING, {"reused": True})
        assets, checkpoints, provider = self._transcription_services()
        existing = await checkpoints.get(job.job_id)
        if existing is not None:
            return self._result(existing, reused=True)
        asset = await assets.get_latest_available(job.job_id)
        if asset is None:
            raise IngestionStageError(
                "AUDIO_CHECKPOINT_NOT_FOUND",
                "The durable audio checkpoint required for transcription is missing.",
                retryable=False,
            )
        try:
            result = await context.run_with_heartbeats(
                provider.transcribe(TranscriptionRequest.from_acquired_audio(asset.audio))
            )
        except TranscriptionError as exc:
            self._raise_transcription_error(exc)
        checkpoint = await checkpoints.save(asset.asset_id, result)
        return self._result(checkpoint, reused=False)

    @staticmethod
    def _result(checkpoint: TranscriptionCheckpoint, *, reused: bool) -> StageResult:
        result = checkpoint.result
        return StageResult(
            IngestionStage.PERSISTENCE,
            {
                "transcription_run_id": str(checkpoint.run_id),
                "provider": result.spec.provider,
                "provider_revision": result.spec.provider_revision,
                "model": result.spec.model,
                "model_revision": result.spec.model_revision,
                "device": result.spec.device.value,
                "compute_type": result.spec.compute_type.value,
                "language_code": result.language_code,
                "language_confidence": result.language_confidence,
                "language_confidence_method": result.language_confidence_method,
                "cue_count": len(result.cues),
                "processing_seconds": result.processing_seconds,
                "fallback_reason": result.fallback_reason.value,
                "audio_start_ms": result.audio_start_ms,
                "audio_end_ms": result.audio_end_ms,
                "reused": reused,
            },
        )


class TranscriptPersistenceStage(_TranscriptStage):
    """Normalize, chunk, and persist either captions or a durable ASR checkpoint."""

    stage = IngestionStage.PERSISTENCE

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        checkpoint = (
            await self._transcription_checkpoints.get(job.job_id)
            if self._transcription_checkpoints is not None
            else None
        )
        try:
            if checkpoint is None:
                result = await context.run_with_heartbeats(
                    ImportVideo(
                        sources=self._sources,
                        repository=self._videos,
                        preferred_languages=self._preferred_languages,
                    ).execute(job.source.canonical_url)
                )
                video = result.video
                reused = result.reused
                origin = "caption"
            else:
                existing = await self._videos.find_by_source(job.source)
                if existing is not None:
                    video = existing
                    reused = True
                else:
                    metadata = await context.run_with_heartbeats(
                        self._source(job).fetch_metadata(job.source)
                    )
                    cues = tuple(
                        TranscriptCue.from_transcription(
                            run_id=checkpoint.run_id,
                            result=checkpoint.result,
                            cue=cue,
                        )
                        for cue in checkpoint.result.cues
                    )
                    units = self._chunker.chunk(cues)
                    video = await self._videos.save_import(
                        metadata,
                        cues,
                        units,
                        transcription_run_id=checkpoint.run_id,
                    )
                    reused = False
                origin = "asr"
        except VideoSourceError as exc:
            self._raise_source_error(exc)
        return StageResult(
            IngestionStage.EMBEDDING,
            {
                "video_id": str(video.video_id),
                "reused": reused,
                "transcript_origin": origin,
            },
        )


class EmbeddingStage(_TranscriptStage):
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
        collection_id = await context.run_with_heartbeats(
            self._transcript_search.ensure_indexed(video.video_id)
        )
        return StageResult(
            IngestionStage.CLEANUP,
            {"video_id": str(video.video_id), "embedding_collection_id": str(collection_id)},
        )


class CleanupStage(_TranscriptStage):
    """Delete successful temporary audio when configured, while retaining its DB lineage."""

    stage = IngestionStage.CLEANUP

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        video = await self._videos.find_by_source(job.source)
        if video is None:
            raise IngestionStageError(
                "PERSISTED_VIDEO_NOT_FOUND",
                "The persisted video could not be loaded during cleanup.",
                retryable=True,
            )
        temporary_media_present = False
        audio_asset_id: str | None = None
        if self._audio_assets is not None and self._audio_acquirer is not None:
            asset = await self._audio_assets.get_latest_available(job.job_id)
            if asset is not None:
                audio_asset_id = str(asset.asset_id)
                try:
                    removed = await context.run_with_heartbeats(
                        self._audio_acquirer.cleanup(asset.audio)
                    )
                except AudioAcquisitionError as exc:
                    self._raise_audio_error(exc)
                if removed:
                    await self._audio_assets.mark_deleted(asset.asset_id)
                temporary_media_present = not removed
        return StageResult(
            IngestionStage.COMPLETED,
            {
                "video_id": str(video.video_id),
                "audio_asset_id": audio_asset_id,
                "temporary_media_present": temporary_media_present,
            },
            video_id=video.video_id,
        )


def caption_ingestion_handlers(
    *,
    sources: Sequence[VideoSource],
    videos: VideoRepository,
    transcript_search: TranscriptSearch,
    preferred_languages: Sequence[str] = ("en",),
    audio_acquirer: AudioAcquirer | None = None,
    audio_assets: AudioAssetRepository | None = None,
    transcription_checkpoints: TranscriptionCheckpointRepository | None = None,
    transcription_provider: TranscriptionProvider | None = None,
) -> tuple[IngestionStageHandler, ...]:
    """Build caption-first handlers and activate ASR only when every service is configured."""

    configured_services = (
        audio_acquirer,
        audio_assets,
        transcription_checkpoints,
        transcription_provider,
    )
    if any(service is not None for service in configured_services) and not all(
        service is not None for service in configured_services
    ):
        raise ValueError("audio fallback services must be configured together")

    handler_types: tuple[type[_TranscriptStage], ...] = (
        SourceResolutionStage,
        MetadataStage,
        CaptionRetrievalStage,
        TranscriptPersistenceStage,
        EmbeddingStage,
        CleanupStage,
    )
    if all(service is not None for service in configured_services):
        handler_types = (
            SourceResolutionStage,
            MetadataStage,
            CaptionRetrievalStage,
            AudioAcquisitionStage,
            TranscriptionStage,
            TranscriptPersistenceStage,
            EmbeddingStage,
            CleanupStage,
        )
    return tuple(
        handler_type(
            sources=sources,
            videos=videos,
            transcript_search=transcript_search,
            preferred_languages=preferred_languages,
            audio_acquirer=audio_acquirer,
            audio_assets=audio_assets,
            transcription_checkpoints=transcription_checkpoints,
            transcription_provider=transcription_provider,
        )
        for handler_type in handler_types
    )
