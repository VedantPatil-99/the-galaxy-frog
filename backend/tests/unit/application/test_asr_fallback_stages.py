"""Behavior coverage for durable caption-to-ASR fallback stages."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.ingestion import (
    AudioAssetRepository,
    IngestionRepository,
    TranscriptionCheckpointRepository,
    caption_ingestion_handlers,
)
from galaxy_frog.application.ingestion.runner import (
    IngestionStageError,
    IngestionStageHandler,
    StageContext,
)
from galaxy_frog.application.videos.ports import VideoRepository
from galaxy_frog.domain.ingestion import (
    IngestionEvent,
    IngestionEventType,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.media import (
    AcquiredAudio,
    AudioAcquirer,
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionRequest,
    AudioAsset,
    AudioFallbackReason,
)
from galaxy_frog.domain.retrieval import TranscriptSearch
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.transcription import (
    TranscriptionCheckpoint,
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionProvider,
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)
from galaxy_frog.domain.transcripts import RetrievalUnit, TranscriptCue, TranscriptOrigin
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord
from galaxy_frog.domain.videos.source import VideoSource, VideoSourceError, VideoSourceErrorCode

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
LEASE = timedelta(minutes=2)
REFERENCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)


def running_job(stage: IngestionStage, *, attempt: int = 1) -> IngestionJob:
    return IngestionJob(
        job_id=uuid4(),
        source=REFERENCE,
        input_fingerprint="a" * 64,
        status=IngestionJobStatus.RUNNING,
        stage=stage,
        attempt=attempt,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1",
        lease_expires_at=NOW + LEASE,
        heartbeat_at=NOW,
        started_at=NOW,
    )


class FixtureSource:
    source_kind = VideoSourceKind.YOUTUBE

    def __init__(self, *, captions: bool = False, usable: bool = True) -> None:
        self.tracks: tuple[CaptionTrack, ...] = (
            (CaptionTrack("manual:hi", "hi", CaptionKind.MANUAL),) if captions else ()
        )
        self.usable = usable
        self.failure: VideoSourceError | None = None

    def canonicalize(self, locator: str) -> SourceReference:
        assert locator == REFERENCE.canonical_url
        return REFERENCE

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        if self.failure is not None:
            raise self.failure
        assert reference == REFERENCE
        return SafeVideoMetadata(reference, "Multilingual source", 4000)

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        if self.failure is not None:
            raise self.failure
        assert reference == REFERENCE
        return self.tracks

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        if self.failure is not None:
            raise self.failure
        assert reference == REFERENCE
        assert track == self.tracks[0]
        if not self.usable:
            return ()
        return (SourceCaptionCue(0, 0, 4000, "नमस्ते Galaxy Frog."),)


class MemoryVideos:
    def __init__(self) -> None:
        self.video: VideoRecord | None = None
        self.transcript: TranscriptRecord | None = None
        self.transcription_run_id: UUID | None = None

    async def find_by_source(self, reference: SourceReference) -> VideoRecord | None:
        return self.video if self.video and self.video.metadata.reference == reference else None

    async def save_import(
        self,
        metadata: SafeVideoMetadata,
        cues: tuple[TranscriptCue, ...],
        units: tuple[RetrievalUnit, ...],
        *,
        transcription_run_id: UUID | None = None,
    ) -> VideoRecord:
        self.video = VideoRecord(uuid4(), metadata)
        self.transcript = TranscriptRecord(self.video, cues, units)
        self.transcription_run_id = transcription_run_id
        return self.video

    async def get_video(self, video_id: UUID) -> VideoRecord | None:
        return self.video if self.video and self.video.video_id == video_id else None

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None:
        return self.transcript if self.video and self.video.video_id == video_id else None


class MemoryAudioAssets:
    def __init__(self) -> None:
        self.asset: AudioAsset | None = None
        self.saves = 0
        self.deletions = 0

    async def save(self, audio: AcquiredAudio) -> AudioAsset:
        self.saves += 1
        if self.asset is None or not self.asset.is_available:
            self.asset = AudioAsset(uuid4(), audio)
        return self.asset

    async def get_latest_available(self, job_id: UUID) -> AudioAsset | None:
        if self.asset is None or self.asset.audio.job_id != job_id or not self.asset.is_available:
            return None
        return self.asset

    async def mark_deleted(
        self,
        asset_id: UUID,
        *,
        deleted_at: datetime | None = None,
    ) -> AudioAsset:
        assert self.asset is not None and self.asset.asset_id == asset_id
        self.deletions += 1
        self.asset = replace(self.asset, deleted_at=deleted_at or NOW + timedelta(minutes=1))
        return self.asset


class MemoryTranscriptions:
    def __init__(self) -> None:
        self.checkpoint: TranscriptionCheckpoint | None = None
        self.saves = 0

    async def save(
        self,
        audio_asset_id: UUID,
        result: TranscriptionResult,
    ) -> TranscriptionCheckpoint:
        self.saves += 1
        self.checkpoint = self.checkpoint or TranscriptionCheckpoint(
            uuid4(), audio_asset_id, result
        )
        return self.checkpoint

    async def get(self, job_id: UUID) -> TranscriptionCheckpoint | None:
        if self.checkpoint is None or self.checkpoint.result.job_id != job_id:
            return None
        return self.checkpoint


class FixtureAudioAcquirer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[AudioAcquisitionRequest] = []
        self.cleanup_calls = 0
        self.retain = False
        self.failure: AudioAcquisitionError | None = None
        self.cleanup_failure: AudioAcquisitionError | None = None

    async def acquire(self, request: AudioAcquisitionRequest) -> AcquiredAudio:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"audio")
        return AcquiredAudio(
            job_id=request.job_id,
            attempt=request.attempt,
            source=request.source,
            fallback_reason=request.fallback_reason,
            path=self.path.resolve(),
            start_ms=0,
            end_ms=request.expected_duration_ms,
            size_bytes=5,
            media_type="audio/wav",
            codec="pcm_s16le",
            sample_rate_hz=16_000,
            channels=1,
            downloader="yt-dlp",
            downloader_revision="2026.08.19",
            normalizer="ffmpeg",
            normalizer_revision="9.0.1",
            acquired_at=NOW,
        )

    async def cleanup(self, artifact: AcquiredAudio) -> bool:
        self.cleanup_calls += 1
        if self.cleanup_failure is not None:
            raise self.cleanup_failure
        if self.retain:
            return False
        artifact.path.unlink(missing_ok=True)
        return True


class FixtureTranscriptionProvider:
    spec = TranscriptionProviderSpec(
        "faster-whisper",
        "1.2.1",
        "small",
        "model-revision",
        TranscriptionDevice.CUDA,
        TranscriptionComputeType.INT8_FLOAT16,
    )

    def __init__(self) -> None:
        self.requests: list[TranscriptionRequest] = []
        self.failure: TranscriptionError | None = None

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return TranscriptionResult(
            job_id=request.audio_job_id,
            attempt=request.attempt,
            source=request.source,
            fallback_reason=request.fallback_reason,
            audio_start_ms=request.audio_start_ms,
            audio_end_ms=request.audio_end_ms,
            spec=self.spec,
            language_code="hi",
            language_confidence=0.88,
            language_confidence_method="provider_language_probability",
            cues=(
                TranscriptionCue(0, 0, 1800, "नमस्ते।", 0.91, "mean_word_probability"),
                TranscriptionCue(1, 1800, 4000, "Welcome.", 0.89, "mean_word_probability"),
            ),
            processing_seconds=1.25,
            transcribed_at=NOW,
        )


class FixtureSearch:
    def __init__(self) -> None:
        self.collection_id = uuid4()
        self.indexed: list[UUID] = []

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        self.indexed.append(video_id)
        return self.collection_id

    async def search(
        self,
        video_id: UUID,
        query: str,
        *,
        limit: int,
    ) -> tuple[RetrievedEvidence, ...]:
        del video_id, query, limit
        return ()


class ContextRepository:
    def __init__(self, job: IngestionJob) -> None:
        self.job = job
        self.events: list[IngestionEvent] = []
        self.heartbeats = 0

    async def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        lease_duration: timedelta,
        **_kwargs: object,
    ) -> IngestionJob:
        assert (job_id, worker_id, lease_duration) == (self.job.job_id, "worker-1", LEASE)
        self.heartbeats += 1
        return self.job

    async def list_events(self, job_id: UUID) -> tuple[IngestionEvent, ...]:
        assert job_id == self.job.job_id
        return tuple(self.events)


def stage_context(job: IngestionJob) -> tuple[StageContext, ContextRepository]:
    repository = ContextRepository(job)
    return (
        StageContext(cast(IngestionRepository, repository), job.job_id, "worker-1", LEASE),
        repository,
    )


def fallback_event(job: IngestionJob, reason: AudioFallbackReason) -> IngestionEvent:
    return IngestionEvent(
        event_id=uuid4(),
        job_id=job.job_id,
        sequence=1,
        event_type=IngestionEventType.STAGE_COMPLETED,
        stage=IngestionStage.CAPTION_RETRIEVAL,
        attempt=job.attempt,
        occurred_at=NOW,
        details={"fallback_reason": reason.value},
    )


def handler_map(
    *,
    source: FixtureSource,
    videos: MemoryVideos,
    audio_acquirer: FixtureAudioAcquirer,
    audio_assets: MemoryAudioAssets,
    transcriptions: MemoryTranscriptions,
    provider: FixtureTranscriptionProvider,
    search: FixtureSearch | None = None,
) -> dict[IngestionStage, IngestionStageHandler]:
    return {
        handler.stage: handler
        for handler in caption_ingestion_handlers(
            sources=(cast(VideoSource, source),),
            videos=cast(VideoRepository, videos),
            transcript_search=cast(TranscriptSearch, search or FixtureSearch()),
            preferred_languages=("hi", "en"),
            audio_acquirer=cast(AudioAcquirer, audio_acquirer),
            audio_assets=cast(AudioAssetRepository, audio_assets),
            transcription_checkpoints=cast(TranscriptionCheckpointRepository, transcriptions),
            transcription_provider=cast(TranscriptionProvider, provider),
        )
    }


def test_fallback_services_must_be_configured_as_one_complete_set(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="configured together"):
        caption_ingestion_handlers(
            sources=(cast(VideoSource, FixtureSource()),),
            videos=cast(VideoRepository, MemoryVideos()),
            transcript_search=cast(TranscriptSearch, FixtureSearch()),
            audio_acquirer=cast(AudioAcquirer, FixtureAudioAcquirer(tmp_path / "audio.wav")),
        )


@pytest.mark.asyncio
async def test_captionless_source_runs_durable_asr_and_cleans_temporary_audio(
    tmp_path: Path,
) -> None:
    job = running_job(IngestionStage.CAPTION_RETRIEVAL)
    source = FixtureSource()
    videos = MemoryVideos()
    audio_acquirer = FixtureAudioAcquirer(tmp_path / "attempt-1" / "audio.wav")
    audio_assets = MemoryAudioAssets()
    transcriptions = MemoryTranscriptions()
    provider = FixtureTranscriptionProvider()
    search = FixtureSearch()
    stages = handler_map(
        source=source,
        videos=videos,
        audio_acquirer=audio_acquirer,
        audio_assets=audio_assets,
        transcriptions=transcriptions,
        provider=provider,
        search=search,
    )
    assert set(stages) == {
        IngestionStage.SOURCE_RESOLUTION,
        IngestionStage.METADATA,
        IngestionStage.CAPTION_RETRIEVAL,
        IngestionStage.AUDIO_ACQUISITION,
        IngestionStage.TRANSCRIPTION,
        IngestionStage.PERSISTENCE,
        IngestionStage.EMBEDDING,
        IngestionStage.CLEANUP,
    }
    context, repository = stage_context(job)

    caption_result = await stages[IngestionStage.CAPTION_RETRIEVAL].execute(job, context)  # type: ignore[attr-defined]
    assert caption_result.next_stage is IngestionStage.AUDIO_ACQUISITION
    repository.events.append(fallback_event(job, AudioFallbackReason.CAPTIONS_UNAVAILABLE))
    repository.events.append(
        IngestionEvent(
            uuid4(),
            job.job_id,
            2,
            IngestionEventType.HEARTBEAT,
            IngestionStage.AUDIO_ACQUISITION,
            job.attempt,
            NOW,
        )
    )

    job = replace(job, stage=caption_result.next_stage)
    audio_result = await stages[IngestionStage.AUDIO_ACQUISITION].execute(job, context)  # type: ignore[attr-defined]
    assert audio_result.next_stage is IngestionStage.TRANSCRIPTION
    assert audio_assets.saves == 1
    assert audio_acquirer.requests[0].fallback_reason is AudioFallbackReason.CAPTIONS_UNAVAILABLE

    job = replace(job, stage=audio_result.next_stage)
    transcription_result = await stages[IngestionStage.TRANSCRIPTION].execute(job, context)  # type: ignore[attr-defined]
    assert transcription_result.next_stage is IngestionStage.PERSISTENCE
    assert transcriptions.saves == 1
    assert transcription_result.details is not None
    assert transcription_result.details["model"] == "small"
    assert transcription_result.details["audio_start_ms"] == 0
    assert transcription_result.details["audio_end_ms"] == 4000

    job = replace(job, stage=transcription_result.next_stage)
    persistence = await stages[IngestionStage.PERSISTENCE].execute(job, context)  # type: ignore[attr-defined]
    assert persistence.next_stage is IngestionStage.EMBEDDING
    assert videos.transcription_run_id == transcriptions.checkpoint.run_id  # type: ignore[union-attr]
    assert videos.transcript is not None
    assert [cue.origin for cue in videos.transcript.cues] == [
        TranscriptOrigin.ASR,
        TranscriptOrigin.ASR,
    ]
    assert [(cue.start_ms, cue.end_ms) for cue in videos.transcript.cues] == [
        (0, 1800),
        (1800, 4000),
    ]

    job = replace(job, stage=persistence.next_stage)
    embedded = await stages[IngestionStage.EMBEDDING].execute(job, context)  # type: ignore[attr-defined]
    assert embedded.next_stage is IngestionStage.CLEANUP
    assert search.indexed == [videos.video.video_id]  # type: ignore[union-attr]

    job = replace(job, stage=embedded.next_stage)
    cleanup = await stages[IngestionStage.CLEANUP].execute(job, context)  # type: ignore[attr-defined]
    assert cleanup.next_stage is IngestionStage.COMPLETED
    assert cleanup.details is not None
    assert cleanup.details["temporary_media_present"] is False
    assert audio_acquirer.cleanup_calls == 1
    assert audio_assets.deletions == 1


@pytest.mark.asyncio
async def test_unusable_captions_select_the_distinct_fallback_reason(tmp_path: Path) -> None:
    job = running_job(IngestionStage.CAPTION_RETRIEVAL)
    stages = handler_map(
        source=FixtureSource(captions=True, usable=False),
        videos=MemoryVideos(),
        audio_acquirer=FixtureAudioAcquirer(tmp_path / "audio.wav"),
        audio_assets=MemoryAudioAssets(),
        transcriptions=MemoryTranscriptions(),
        provider=FixtureTranscriptionProvider(),
    )
    context, _repository = stage_context(job)

    result = await stages[IngestionStage.CAPTION_RETRIEVAL].execute(job, context)  # type: ignore[attr-defined]

    assert result.next_stage is IngestionStage.AUDIO_ACQUISITION
    assert result.details == {
        "transcript_origin": "asr",
        "fallback_reason": "captions_unusable",
    }


@pytest.mark.asyncio
async def test_audio_checkpoint_is_reused_after_restart_without_redownload(tmp_path: Path) -> None:
    job = running_job(IngestionStage.AUDIO_ACQUISITION, attempt=2)
    audio_acquirer = FixtureAudioAcquirer(tmp_path / "attempt-1" / "audio.wav")
    audio = await audio_acquirer.acquire(
        AudioAcquisitionRequest(
            job.job_id,
            1,
            job.source,
            4000,
            AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        )
    )
    audio_acquirer.requests.clear()
    audio_assets = MemoryAudioAssets()
    audio_assets.asset = AudioAsset(uuid4(), audio)
    stages = handler_map(
        source=FixtureSource(),
        videos=MemoryVideos(),
        audio_acquirer=audio_acquirer,
        audio_assets=audio_assets,
        transcriptions=MemoryTranscriptions(),
        provider=FixtureTranscriptionProvider(),
    )
    context, _repository = stage_context(job)

    result = await stages[IngestionStage.AUDIO_ACQUISITION].execute(job, context)  # type: ignore[attr-defined]

    assert result.next_stage is IngestionStage.TRANSCRIPTION
    assert result.details is not None and result.details["reused"] is True
    assert audio_acquirer.requests == []
    assert audio_assets.saves == 0


@pytest.mark.asyncio
async def test_missing_audio_file_is_retired_before_reacquisition(tmp_path: Path) -> None:
    job = running_job(IngestionStage.AUDIO_ACQUISITION, attempt=2)
    old_audio = AcquiredAudio(
        job_id=job.job_id,
        attempt=1,
        source=job.source,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        path=(tmp_path / "missing.wav").resolve(),
        start_ms=0,
        end_ms=4000,
        size_bytes=5,
        media_type="audio/wav",
        codec="pcm_s16le",
        sample_rate_hz=16_000,
        channels=1,
        downloader="yt-dlp",
        downloader_revision="2026.08.19",
        normalizer="ffmpeg",
        normalizer_revision="9.0.1",
        acquired_at=NOW,
    )
    assets = MemoryAudioAssets()
    assets.asset = AudioAsset(uuid4(), old_audio)
    acquirer = FixtureAudioAcquirer(tmp_path / "attempt-2" / "audio.wav")
    stages = handler_map(
        source=FixtureSource(),
        videos=MemoryVideos(),
        audio_acquirer=acquirer,
        audio_assets=assets,
        transcriptions=MemoryTranscriptions(),
        provider=FixtureTranscriptionProvider(),
    )
    context, repository = stage_context(job)
    repository.events.append(fallback_event(job, AudioFallbackReason.CAPTIONS_UNAVAILABLE))

    result = await stages[IngestionStage.AUDIO_ACQUISITION].execute(job, context)

    assert result.details is not None and result.details["reused"] is False
    assert assets.deletions == 1
    assert assets.saves == 1
    assert assets.asset is not None and assets.asset.audio.attempt == 2


@pytest.mark.asyncio
async def test_failed_transcription_retries_from_audio_and_reuses_completed_output(
    tmp_path: Path,
) -> None:
    job = running_job(IngestionStage.TRANSCRIPTION)
    audio_acquirer = FixtureAudioAcquirer(tmp_path / "attempt-1" / "audio.wav")
    acquired = await audio_acquirer.acquire(
        AudioAcquisitionRequest(
            job.job_id,
            1,
            job.source,
            4000,
            AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        )
    )
    audio_assets = MemoryAudioAssets()
    audio_assets.asset = AudioAsset(uuid4(), acquired)
    transcriptions = MemoryTranscriptions()
    provider = FixtureTranscriptionProvider()
    provider.failure = TranscriptionError(
        TranscriptionErrorCode.EXECUTION_FAILED,
        "The transcription provider could not process the audio.",
        retryable=True,
    )
    stages = handler_map(
        source=FixtureSource(),
        videos=MemoryVideos(),
        audio_acquirer=audio_acquirer,
        audio_assets=audio_assets,
        transcriptions=transcriptions,
        provider=provider,
    )
    context, _repository = stage_context(job)

    with pytest.raises(IngestionStageError) as failed:
        await stages[IngestionStage.TRANSCRIPTION].execute(job, context)  # type: ignore[attr-defined]
    assert failed.value.code == TranscriptionErrorCode.EXECUTION_FAILED
    assert failed.value.retryable is True
    assert audio_assets.asset.is_available is True

    provider.failure = None
    retried = replace(job, attempt=2)
    result = await stages[IngestionStage.TRANSCRIPTION].execute(retried, context)  # type: ignore[attr-defined]
    assert result.next_stage is IngestionStage.PERSISTENCE
    assert len(provider.requests) == 2
    assert transcriptions.saves == 1

    resumed = await stages[IngestionStage.TRANSCRIPTION].execute(retried, context)
    assert resumed.details is not None and resumed.details["reused"] is True
    assert len(provider.requests) == 2
    assert transcriptions.saves == 1


@pytest.mark.asyncio
async def test_existing_video_short_circuits_every_expensive_fallback_stage(
    tmp_path: Path,
) -> None:
    job = running_job(IngestionStage.AUDIO_ACQUISITION)
    videos = MemoryVideos()
    videos.video = VideoRecord(uuid4(), SafeVideoMetadata(job.source, "Existing", 4000))
    acquirer = FixtureAudioAcquirer(tmp_path / "audio.wav")
    provider = FixtureTranscriptionProvider()
    transcriptions = MemoryTranscriptions()
    request = AudioAcquisitionRequest(
        job.job_id,
        1,
        job.source,
        4000,
        AudioFallbackReason.CAPTIONS_UNAVAILABLE,
    )
    audio = await acquirer.acquire(request)
    provider_result = await provider.transcribe(TranscriptionRequest.from_acquired_audio(audio))
    transcriptions.checkpoint = TranscriptionCheckpoint(uuid4(), uuid4(), provider_result)
    acquirer.requests.clear()
    provider.requests.clear()
    stages = handler_map(
        source=FixtureSource(),
        videos=videos,
        audio_acquirer=acquirer,
        audio_assets=MemoryAudioAssets(),
        transcriptions=transcriptions,
        provider=provider,
    )
    context, _repository = stage_context(job)

    for stage in (
        IngestionStage.AUDIO_ACQUISITION,
        IngestionStage.TRANSCRIPTION,
        IngestionStage.PERSISTENCE,
    ):
        result = await stages[stage].execute(replace(job, stage=stage), context)
        assert result.next_stage is IngestionStage.EMBEDDING
        assert result.details is not None and result.details["reused"] is True
    assert acquirer.requests == []
    assert provider.requests == []

    cleanup = await stages[IngestionStage.CLEANUP].execute(
        replace(job, stage=IngestionStage.CLEANUP), context
    )
    assert cleanup.next_stage is IngestionStage.COMPLETED
    assert cleanup.details is not None
    assert cleanup.details["audio_asset_id"] is None
    assert cleanup.details["temporary_media_present"] is False


@pytest.mark.asyncio
async def test_cleanup_reports_retention_and_maps_cleanup_failure(tmp_path: Path) -> None:
    job = running_job(IngestionStage.CLEANUP)
    videos = MemoryVideos()
    videos.video = VideoRecord(uuid4(), SafeVideoMetadata(job.source, "Existing", 4000))
    acquirer = FixtureAudioAcquirer(tmp_path / "audio.wav")
    acquired = await acquirer.acquire(
        AudioAcquisitionRequest(
            job.job_id,
            1,
            job.source,
            4000,
            AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        )
    )
    assets = MemoryAudioAssets()
    assets.asset = AudioAsset(uuid4(), acquired)
    stages = handler_map(
        source=FixtureSource(),
        videos=videos,
        audio_acquirer=acquirer,
        audio_assets=assets,
        transcriptions=MemoryTranscriptions(),
        provider=FixtureTranscriptionProvider(),
    )
    context, _repository = stage_context(job)
    acquirer.retain = True
    retained = await stages[IngestionStage.CLEANUP].execute(job, context)
    assert retained.details is not None
    assert retained.details["temporary_media_present"] is True
    assert assets.deletions == 0

    acquirer.cleanup_failure = AudioAcquisitionError(
        AudioAcquisitionErrorCode.WORKSPACE_ERROR,
        "The isolated media workspace could not be cleaned.",
        retryable=True,
    )
    with pytest.raises(IngestionStageError) as failed:
        await stages[IngestionStage.CLEANUP].execute(job, context)
    assert failed.value.code == AudioAcquisitionErrorCode.WORKSPACE_ERROR
    assert failed.value.retryable is True


@pytest.mark.asyncio
async def test_source_failures_and_invalid_fallback_events_are_stage_specific(
    tmp_path: Path,
) -> None:
    job = running_job(IngestionStage.CAPTION_RETRIEVAL)
    source = FixtureSource(captions=True)
    source.failure = VideoSourceError(
        VideoSourceErrorCode.SOURCE_UNAVAILABLE,
        "The source is temporarily unavailable.",
        retryable=True,
    )
    stages = handler_map(
        source=source,
        videos=MemoryVideos(),
        audio_acquirer=FixtureAudioAcquirer(tmp_path / "audio.wav"),
        audio_assets=MemoryAudioAssets(),
        transcriptions=MemoryTranscriptions(),
        provider=FixtureTranscriptionProvider(),
    )
    context, _repository = stage_context(job)
    with pytest.raises(IngestionStageError) as caption_failure:
        await stages[IngestionStage.CAPTION_RETRIEVAL].execute(job, context)
    assert caption_failure.value.code == VideoSourceErrorCode.SOURCE_UNAVAILABLE

    audio_job = replace(job, stage=IngestionStage.AUDIO_ACQUISITION)
    audio_context, repository = stage_context(audio_job)
    repository.events.extend(
        (
            IngestionEvent(
                uuid4(),
                audio_job.job_id,
                1,
                IngestionEventType.HEARTBEAT,
                IngestionStage.METADATA,
                1,
                NOW,
            ),
            IngestionEvent(
                uuid4(),
                audio_job.job_id,
                2,
                IngestionEventType.STAGE_COMPLETED,
                IngestionStage.CAPTION_RETRIEVAL,
                1,
                NOW,
                details={"fallback_reason": "invalid"},
            ),
        )
    )
    with pytest.raises(IngestionStageError, match="caption decision"):
        await stages[IngestionStage.AUDIO_ACQUISITION].execute(audio_job, audio_context)

    repository.events[-1] = fallback_event(audio_job, AudioFallbackReason.CAPTIONS_UNAVAILABLE)
    with pytest.raises(IngestionStageError) as metadata_failure:
        await stages[IngestionStage.AUDIO_ACQUISITION].execute(audio_job, audio_context)
    assert metadata_failure.value.code == VideoSourceErrorCode.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_fallback_stage_errors_remain_safe_and_specific(tmp_path: Path) -> None:
    job = running_job(IngestionStage.AUDIO_ACQUISITION)
    audio_acquirer = FixtureAudioAcquirer(tmp_path / "audio.wav")
    audio_acquirer.failure = AudioAcquisitionError(
        AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
        "The source audio could not be downloaded.",
        retryable=True,
    )
    stages = handler_map(
        source=FixtureSource(),
        videos=MemoryVideos(),
        audio_acquirer=audio_acquirer,
        audio_assets=MemoryAudioAssets(),
        transcriptions=MemoryTranscriptions(),
        provider=FixtureTranscriptionProvider(),
    )
    context, repository = stage_context(job)
    with pytest.raises(IngestionStageError, match="caption decision") as missing_reason:
        await stages[IngestionStage.AUDIO_ACQUISITION].execute(job, context)  # type: ignore[attr-defined]
    assert missing_reason.value.retryable is False

    repository.events.append(fallback_event(job, AudioFallbackReason.CAPTIONS_UNAVAILABLE))
    with pytest.raises(IngestionStageError) as acquisition_failure:
        await stages[IngestionStage.AUDIO_ACQUISITION].execute(job, context)  # type: ignore[attr-defined]
    assert acquisition_failure.value.code == AudioAcquisitionErrorCode.DOWNLOAD_FAILED
    assert acquisition_failure.value.retryable is True

    transcription_job = replace(job, stage=IngestionStage.TRANSCRIPTION)
    transcription_context, _repository = stage_context(transcription_job)
    with pytest.raises(IngestionStageError, match="audio checkpoint") as missing_audio:
        await stages[IngestionStage.TRANSCRIPTION].execute(  # type: ignore[attr-defined]
            transcription_job, transcription_context
        )
    assert missing_audio.value.retryable is False
