"""Behavior coverage for resumable caption-first ingestion stages."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.ingestion.caption_stages import caption_ingestion_handlers
from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.application.ingestion.runner import (
    IngestionStageError,
    IngestionStageHandler,
    StageContext,
)
from galaxy_frog.application.videos.ports import VideoRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionJobStatus, IngestionStage
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.ports import TranscriptSearch
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
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

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
LEASE = timedelta(minutes=2)
REFERENCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)


def running_job(stage: IngestionStage = IngestionStage.SOURCE_RESOLUTION) -> IngestionJob:
    return IngestionJob(
        job_id=uuid4(),
        source=REFERENCE,
        input_fingerprint="a" * 64,
        status=IngestionJobStatus.RUNNING,
        stage=stage,
        attempt=1,
        created_at=NOW,
        updated_at=NOW,
        lease_owner="worker-1",
        lease_expires_at=NOW + LEASE,
        heartbeat_at=NOW,
        started_at=NOW,
    )


class FixtureSource:
    source_kind = VideoSourceKind.YOUTUBE

    def __init__(self) -> None:
        self.tracks: tuple[CaptionTrack, ...] = (
            CaptionTrack("automatic:en", "en", CaptionKind.AUTOMATIC),
            CaptionTrack("manual:en", "en", CaptionKind.MANUAL),
        )
        self.failure: VideoSourceError | None = None

    def canonicalize(self, locator: str) -> SourceReference:
        if self.failure is not None:
            raise self.failure
        assert locator == REFERENCE.canonical_url
        return REFERENCE

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        if self.failure is not None:
            raise self.failure
        return SafeVideoMetadata(reference, "Durable captions", 3000)

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        del reference
        if self.failure is not None:
            raise self.failure
        return self.tracks

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        del reference
        if self.failure is not None:
            raise self.failure
        assert track.track_id == "manual:en"
        return (
            SourceCaptionCue(0, 0, 1000, "First durable cue."),
            SourceCaptionCue(1, 1000, 2000, "Second durable cue."),
            SourceCaptionCue(2, 2000, 3000, "Third durable cue."),
        )


class MemoryVideoRepository:
    def __init__(self) -> None:
        self.video: VideoRecord | None = None
        self.transcript: TranscriptRecord | None = None

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
        del transcription_run_id
        self.video = VideoRecord(uuid4(), metadata)
        self.transcript = TranscriptRecord(self.video, cues, units)
        return self.video

    async def get_video(self, video_id: UUID) -> VideoRecord | None:
        return self.video if self.video and self.video.video_id == video_id else None

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None:
        return self.transcript if self.video and self.video.video_id == video_id else None


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


class HeartbeatRepository:
    def __init__(self, job: IngestionJob) -> None:
        self.job = job
        self.heartbeats = 0

    async def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        lease_duration: timedelta,
        **_kwargs: object,
    ) -> IngestionJob:
        assert job_id == self.job.job_id
        assert worker_id == "worker-1"
        assert lease_duration == LEASE
        self.heartbeats += 1
        return self.job


def context(job: IngestionJob) -> tuple[StageContext, HeartbeatRepository]:
    repository = HeartbeatRepository(job)
    return (
        StageContext(cast(IngestionRepository, repository), job.job_id, "worker-1", LEASE),
        repository,
    )


def handlers(
    source: FixtureSource,
    videos: MemoryVideoRepository,
    search: FixtureSearch,
) -> dict[IngestionStage, IngestionStageHandler]:
    return {
        handler.stage: handler
        for handler in caption_ingestion_handlers(
            sources=(cast(VideoSource, source),),
            videos=cast(VideoRepository, videos),
            transcript_search=cast(TranscriptSearch, search),
        )
    }


@pytest.mark.asyncio
async def test_caption_stages_checkpoint_and_complete_with_provenance() -> None:
    source = FixtureSource()
    videos = MemoryVideoRepository()
    search = FixtureSearch()
    stages = handlers(source, videos, search)
    expected = {
        IngestionStage.SOURCE_RESOLUTION,
        IngestionStage.METADATA,
        IngestionStage.CAPTION_RETRIEVAL,
        IngestionStage.PERSISTENCE,
        IngestionStage.EMBEDDING,
        IngestionStage.CLEANUP,
    }
    assert set(stages) == expected

    job = running_job()
    stage_context, heartbeat_repository = context(job)
    resolved = await stages[job.stage].execute(job, stage_context)
    assert resolved.next_stage is IngestionStage.METADATA
    assert resolved.details == {"source_kind": "youtube", "external_id": REFERENCE.external_id}

    job = replace(job, stage=resolved.next_stage)
    metadata = await stages[job.stage].execute(job, stage_context)
    assert metadata.next_stage is IngestionStage.CAPTION_RETRIEVAL
    assert metadata.details == {"title": "Durable captions", "duration_ms": 3000}

    job = replace(job, stage=metadata.next_stage)
    captions = await stages[job.stage].execute(job, stage_context)
    assert captions.next_stage is IngestionStage.PERSISTENCE
    assert captions.details == {
        "transcript_origin": "caption",
        "track_id": "manual:en",
        "language_code": "en",
        "caption_kind": "manual",
        "cue_count": 3,
    }

    job = replace(job, stage=captions.next_stage)
    persisted = await stages[job.stage].execute(job, stage_context)
    assert persisted.next_stage is IngestionStage.EMBEDDING
    assert persisted.details is not None
    assert persisted.details["reused"] is False
    assert videos.transcript is not None
    assert videos.transcript.cues[0].start_ms == 0
    assert videos.transcript.cues[-1].end_ms == 3000
    assert videos.transcript.units[0].cue_ids

    job = replace(job, stage=persisted.next_stage)
    embedded = await stages[job.stage].execute(job, stage_context)
    assert embedded.next_stage is IngestionStage.CLEANUP
    assert videos.video is not None
    assert search.indexed == [videos.video.video_id]

    job = replace(job, stage=embedded.next_stage)
    completed = await stages[job.stage].execute(job, stage_context)
    assert completed.next_stage is IngestionStage.COMPLETED
    assert completed.video_id == videos.video.video_id
    assert completed.details is not None
    assert completed.details["temporary_media_present"] is False
    assert heartbeat_repository.heartbeats == 0


@pytest.mark.asyncio
async def test_existing_import_skips_provider_work_and_resumes_at_embedding() -> None:
    source = FixtureSource()
    videos = MemoryVideoRepository()
    videos.video = VideoRecord(uuid4(), SafeVideoMetadata(REFERENCE, "Existing", 3000))
    stages = handlers(source, videos, FixtureSearch())

    for stage in (
        IngestionStage.METADATA,
        IngestionStage.CAPTION_RETRIEVAL,
    ):
        job = running_job(stage)
        stage_context, repository = context(job)
        result = await stages[stage].execute(job, stage_context)
        assert result.next_stage is IngestionStage.EMBEDDING
        assert result.details == {"reused": True}
        assert repository.heartbeats == 0

    persistence_job = running_job(IngestionStage.PERSISTENCE)
    stage_context, repository = context(persistence_job)
    result = await stages[IngestionStage.PERSISTENCE].execute(
        persistence_job,
        stage_context,
    )
    assert result.details is not None
    assert result.details["reused"] is True
    assert repository.heartbeats == 0


@pytest.mark.asyncio
async def test_stage_failures_are_safe_and_provider_independent() -> None:
    videos = MemoryVideoRepository()
    search = FixtureSearch()
    job = running_job()
    stage_context, _repository = context(job)
    unsupported = caption_ingestion_handlers(
        sources=(),
        videos=cast(VideoRepository, videos),
        transcript_search=cast(TranscriptSearch, search),
    )[0]
    with pytest.raises(IngestionStageError, match="does not support"):
        await unsupported.execute(job, stage_context)

    class ChangedIdentitySource(FixtureSource):
        def canonicalize(self, locator: str) -> SourceReference:
            del locator
            return SourceReference(
                VideoSourceKind.YOUTUBE,
                "changed-id1",
                "https://www.youtube.com/watch?v=changed-id1",
            )

    changed = handlers(ChangedIdentitySource(), videos, search)[IngestionStage.SOURCE_RESOLUTION]
    with pytest.raises(IngestionStageError, match="identity changed"):
        await changed.execute(job, stage_context)

    source = FixtureSource()
    source.failure = VideoSourceError(
        VideoSourceErrorCode.SOURCE_UNAVAILABLE,
        "The source is temporarily unavailable.",
        retryable=True,
    )
    with pytest.raises(IngestionStageError) as resolution_failure:
        await handlers(source, videos, search)[IngestionStage.SOURCE_RESOLUTION].execute(
            job,
            stage_context,
        )
    assert resolution_failure.value.code == VideoSourceErrorCode.SOURCE_UNAVAILABLE
    assert resolution_failure.value.retryable is True

    metadata_job = running_job(IngestionStage.METADATA)
    metadata_context, _repository = context(metadata_job)
    with pytest.raises(IngestionStageError) as failure:
        await handlers(source, videos, search)[IngestionStage.METADATA].execute(
            metadata_job,
            metadata_context,
        )
    assert failure.value.code == VideoSourceErrorCode.SOURCE_UNAVAILABLE
    assert failure.value.retryable is True


@pytest.mark.asyncio
async def test_caption_and_persisted_video_failures_remain_stage_specific() -> None:
    source = FixtureSource()
    source.tracks = ()
    videos = MemoryVideoRepository()
    search = FixtureSearch()
    stages = handlers(source, videos, search)
    caption_job = running_job(IngestionStage.CAPTION_RETRIEVAL)
    caption_context, _repository = context(caption_job)
    no_captions = await stages[IngestionStage.CAPTION_RETRIEVAL].execute(
        caption_job,
        caption_context,
    )
    assert no_captions.next_stage is IngestionStage.AUDIO_ACQUISITION
    assert no_captions.details == {
        "transcript_origin": "asr",
        "fallback_reason": "captions_unavailable",
    }

    source.tracks = (CaptionTrack("manual:en", "en", CaptionKind.MANUAL),)
    source.failure = VideoSourceError(
        VideoSourceErrorCode.SOURCE_UNAVAILABLE,
        "Caption retrieval failed.",
        retryable=True,
    )
    persistence_job = running_job(IngestionStage.PERSISTENCE)
    persistence_context, _repository = context(persistence_job)
    with pytest.raises(IngestionStageError, match="Caption retrieval failed"):
        await stages[IngestionStage.PERSISTENCE].execute(
            persistence_job,
            persistence_context,
        )

    source.failure = None
    for stage in (IngestionStage.EMBEDDING, IngestionStage.CLEANUP):
        missing_job = running_job(stage)
        missing_context, _repository = context(missing_job)
        with pytest.raises(IngestionStageError, match="persisted video") as missing:
            await stages[stage].execute(missing_job, missing_context)
        assert missing.value.retryable is True
