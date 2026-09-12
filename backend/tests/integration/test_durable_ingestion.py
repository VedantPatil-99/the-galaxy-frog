"""Opt-in PostgreSQL acceptance coverage for the durable ingestion foundation."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galaxy_frog.application.ingestion import caption_ingestion_handlers
from galaxy_frog.application.ingestion.runner import (
    IngestionJobRunner,
    IngestionStageError,
    StageContext,
    StageResult,
)
from galaxy_frog.application.ingestion.worker import IngestionWorker
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.ingestion_artifacts import (
    PostgresAudioAssetRepository,
    PostgresTranscriptionCheckpointRepository,
)
from galaxy_frog.db.ingestion_repository import (
    IngestionRepositoryError,
    PostgresIngestionRepository,
)
from galaxy_frog.db.models import (
    IngestionJobRow,
    JobEventRow,
    MediaAssetRow,
    RetrievalUnitRow,
    TextEmbeddingRow,
    TranscriptCueRow,
    TranscriptionRunCueRow,
    TranscriptionRunRow,
    VideoRow,
)
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.ingestion.models import (
    IngestionEventType,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.media import (
    AcquiredAudio,
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionRequest,
    AudioFallbackReason,
)
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence
from galaxy_frog.domain.retrieval.ports import TranscriptSearch
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)
from galaxy_frog.domain.videos.models import (
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="set RUN_DATABASE_INTEGRATION=1 with the local pgvector database running",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply the real Alembic chain before exercising PostgreSQL claim semantics."""

    backend_root = Path(__file__).resolve().parents[2]
    command.upgrade(Config(str(backend_root / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_concurrent_claim_recovery_cancellation_and_retry_are_durable() -> None:
    """Prove the P2.1 lifecycle against real row locks and database constraints."""

    engine = create_database_engine(Settings(app_env="test"))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    external_id = uuid4().hex[:11]
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    fingerprint = sha256(f"durable:{external_id}".encode()).hexdigest()
    retry_fingerprint = sha256(f"retry:{external_id}".encode()).hexdigest()
    job_ids: list[UUID] = []

    async def create(value: str) -> tuple[IngestionJob, bool]:
        async with sessions() as session:
            return await PostgresIngestionRepository(session).create_or_get(source, value)

    async def claim(worker_id: str, *, clock_offset_seconds: int = 0) -> IngestionJob | None:
        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            return await repository.claim_next(
                worker_id,
                lease_duration=timedelta(seconds=1),
                now=datetime.now(UTC) + timedelta(seconds=clock_offset_seconds),
            )

    try:
        (first, first_created), (second, second_created) = await asyncio.gather(
            create(fingerprint),
            create(fingerprint),
        )
        assert sorted((first_created, second_created)) == [False, True]
        assert first.job_id == second.job_id
        job_id = first.job_id
        job_ids.append(job_id)

        claims = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        claimed = [job for job in claims if job is not None]
        assert len(claimed) == 1
        assert claimed[0].attempt == 1

        recovered = await claim("worker-recovery", clock_offset_seconds=2)
        assert recovered is not None
        assert recovered.attempt == 2
        assert recovered.lease_owner == "worker-recovery"

        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            requested = await repository.request_cancellation(job_id)
            assert requested.cancel_requested_at is not None
            cancelled = await repository.cancel(job_id, "worker-recovery")
            assert cancelled.status is IngestionJobStatus.CANCELLED
            events = await repository.list_events(job_id)
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            assert [event.event_type for event in events] == [
                IngestionEventType.CREATED,
                IngestionEventType.CLAIMED,
                IngestionEventType.CLAIMED,
                IngestionEventType.CANCEL_REQUESTED,
                IngestionEventType.CANCELLED,
            ]

        retry_job, created = await create(retry_fingerprint)
        assert created is True
        retry_job_id = retry_job.job_id
        job_ids.append(retry_job_id)
        retry_claim = await claim("worker-retry")
        assert retry_claim is not None

        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            failed = await repository.fail(
                retry_job_id,
                "worker-retry",
                error_code="TRANSCRIPTION_UNAVAILABLE",
                message="Transcription is temporarily unavailable.",
                retryable=True,
            )
            assert failed.status is IngestionJobStatus.FAILED
            queued = await repository.retry(retry_job_id)
            assert queued.status is IngestionJobStatus.QUEUED

        reclaimed = await claim("worker-final")
        assert reclaimed is not None
        assert reclaimed.job_id == retry_job_id
        assert reclaimed.attempt == 2

        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            await repository.fail(
                retry_job_id,
                "worker-final",
                error_code="UNSUPPORTED_MEDIA",
                message="The media cannot be processed.",
                retryable=False,
            )
            with pytest.raises(IngestionRepositoryError, match="retryable failed"):
                await repository.retry(retry_job_id)
            events = await repository.list_events(retry_job_id)
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    finally:
        async with sessions() as session:
            await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id.in_(job_ids)))
            await session.commit()
        await engine.dispose()


class FailAfterRestart:
    """Record the resumed stage, then stop without advancing into later Phase 2 work."""

    stage = IngestionStage.METADATA

    def __init__(self) -> None:
        self.jobs: list[IngestionJob] = []

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        del context
        self.jobs.append(job)
        raise IngestionStageError(
            "RESTART_CHECK_COMPLETE",
            "The restart recovery checkpoint was exercised.",
            retryable=True,
        )


@pytest.mark.asyncio
async def test_worker_restart_resumes_the_last_completed_stage() -> None:
    """Prove a new process resumes at metadata without repeating source resolution."""

    engine = create_database_engine(Settings(app_env="test"))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    external_id = uuid4().hex[:11]
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    fingerprint = sha256(f"restart:{external_id}".encode()).hexdigest()
    crash_time = datetime.now(UTC) - timedelta(minutes=1)
    job_id: UUID | None = None

    try:
        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            created, was_created = await repository.create_or_get(
                source,
                fingerprint,
                now=crash_time,
            )
            assert was_created is True
            job_id = created.job_id
            claimed = await repository.claim_next(
                "worker-before-crash",
                timedelta(seconds=1),
                now=crash_time,
            )
            assert claimed is not None
            assert claimed.job_id == job_id
            await repository.start_stage(
                job_id,
                "worker-before-crash",
                IngestionStage.SOURCE_RESOLUTION,
                now=crash_time,
            )
            checkpoint = await repository.complete_stage(
                job_id,
                "worker-before-crash",
                IngestionStage.SOURCE_RESOLUTION,
                IngestionStage.METADATA,
                details={"source_kind": "youtube", "external_id": external_id},
                now=crash_time,
            )
            assert checkpoint.stage is IngestionStage.METADATA

        resumed_stage = FailAfterRestart()
        async with sessions() as session:
            repository = PostgresIngestionRepository(session)
            lease_duration = timedelta(minutes=2)
            runner = IngestionJobRunner(
                repository=repository,
                handlers=(resumed_stage,),
                worker_id="worker-after-restart",
                lease_duration=lease_duration,
            )
            worker = IngestionWorker(
                repository=repository,
                runner=runner,
                worker_id="worker-after-restart",
                lease_duration=lease_duration,
                poll_interval=timedelta(milliseconds=1),
            )

            result = await worker.run_once()

            assert result is not None
            assert result.job_id == job_id
            assert result.status is IngestionJobStatus.FAILED
            assert result.stage is IngestionStage.METADATA
            assert result.attempt == 2
            assert [job.stage for job in resumed_stage.jobs] == [IngestionStage.METADATA]
            events = await repository.list_events(job_id)
            assert [event.event_type for event in events] == [
                IngestionEventType.CREATED,
                IngestionEventType.CLAIMED,
                IngestionEventType.STAGE_STARTED,
                IngestionEventType.STAGE_COMPLETED,
                IngestionEventType.CLAIMED,
                IngestionEventType.STAGE_STARTED,
                IngestionEventType.FAILED,
            ]
            source_completions = [
                event
                for event in events
                if event.event_type is IngestionEventType.STAGE_COMPLETED
                and event.stage is IngestionStage.SOURCE_RESOLUTION
            ]
            assert len(source_completions) == 1
            assert source_completions[0].details == {
                "source_kind": "youtube",
                "external_id": external_id,
            }
    finally:
        if job_id is not None:
            async with sessions() as session:
                await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id == job_id))
                await session.commit()
        await engine.dispose()


class CaptionlessSource:
    """Deterministic source that authorizes ASR by returning no caption tracks."""

    source_kind = VideoSourceKind.YOUTUBE

    def __init__(self, reference: SourceReference) -> None:
        self.reference = reference

    def canonicalize(self, locator: str) -> SourceReference:
        assert locator == self.reference.canonical_url
        return self.reference

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        assert reference == self.reference
        return SafeVideoMetadata(reference, "ASR restart integration", 30_000)

    async def list_caption_tracks(
        self,
        reference: SourceReference,
    ) -> tuple[CaptionTrack, ...]:
        assert reference == self.reference
        return ()

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        del reference, track
        raise AssertionError("caption cues must not be requested without a track")


class CountingAudioAcquirer:
    """Create one bounded local artifact and record whether restart repeats work."""

    def __init__(self, path: Path, *, cleanup_failures: int = 0) -> None:
        self.path = path.resolve()
        self.acquire_calls = 0
        self.cleanup_calls = 0
        self.cleanup_failures = cleanup_failures

    async def acquire(self, request: AudioAcquisitionRequest) -> AcquiredAudio:
        self.acquire_calls += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"integration-audio")
        return AcquiredAudio(
            job_id=request.job_id,
            attempt=request.attempt,
            source=request.source,
            fallback_reason=request.fallback_reason,
            path=self.path,
            start_ms=0,
            end_ms=request.expected_duration_ms,
            size_bytes=self.path.stat().st_size,
            media_type="audio/wav",
            codec="pcm_s16le",
            sample_rate_hz=16_000,
            channels=1,
            downloader="integration-downloader",
            downloader_revision="1.0.0",
            normalizer="integration-normalizer",
            normalizer_revision="1.0.0",
            acquired_at=datetime.now(UTC),
        )

    async def cleanup(self, artifact: AcquiredAudio) -> bool:
        self.cleanup_calls += 1
        if self.cleanup_failures > 0:
            self.cleanup_failures -= 1
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.WORKSPACE_ERROR,
                "The integration cleanup failed safely.",
                retryable=True,
            )
        artifact.path.unlink(missing_ok=True)
        return True


class FailOnceTranscriptionProvider:
    """Fail one inference, then return stable multilingual temporal evidence."""

    spec = TranscriptionProviderSpec(
        provider="integration-asr",
        provider_revision="1.0.0",
        model="small",
        model_revision="integration-revision",
        device=TranscriptionDevice.CUDA,
        compute_type=TranscriptionComputeType.INT8_FLOAT16,
    )

    def __init__(self, *, failures: int = 1) -> None:
        self.requests: list[TranscriptionRequest] = []
        self.failures = failures

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.requests.append(request)
        if self.failures > 0:
            self.failures -= 1
            raise TranscriptionError(
                TranscriptionErrorCode.EXECUTION_FAILED,
                "The integration provider failed before producing output.",
                retryable=True,
            )
        return TranscriptionResult(
            job_id=request.audio_job_id,
            attempt=request.attempt,
            source=request.source,
            fallback_reason=request.fallback_reason,
            audio_start_ms=request.audio_start_ms,
            audio_end_ms=request.audio_end_ms,
            spec=self.spec,
            language_code="hi",
            language_confidence=0.87,
            language_confidence_method="provider_language_probability",
            cues=(
                TranscriptionCue(
                    0,
                    0,
                    12_500,
                    "नमस्ते Galaxy Frog.",
                    0.93,
                    "mean_word_probability",
                ),
                TranscriptionCue(
                    1,
                    12_500,
                    30_000,
                    "Temporal evidence remains exact.",
                    0.89,
                    "mean_word_probability",
                ),
            ),
            processing_seconds=2.5,
            transcribed_at=datetime.now(UTC),
        )


class RecordingTranscriptSearch:
    """Finish the pipeline without requiring an external embedding service."""

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


class FixtureEmbeddings:
    """Persist stable 1,024-dimensional vectors without an external service."""

    spec = EmbeddingCollectionSpec("fixture", "bge-m3", "phase-2-exit", 1024, "l2")

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, *([0.0] * 1023)) for _text in texts)


def build_captionless_worker(
    *,
    session: AsyncSession,
    source: CaptionlessSource,
    acquirer: CountingAudioAcquirer,
    provider: FailOnceTranscriptionProvider,
    search: TranscriptSearch,
    worker_id: str,
) -> IngestionWorker:
    """Compose the real PostgreSQL pipeline around deterministic local test providers."""

    ingestion = PostgresIngestionRepository(session)
    videos = SqlAlchemyVideoRepository(session)
    lease_duration = timedelta(minutes=2)
    runner = IngestionJobRunner(
        repository=ingestion,
        handlers=caption_ingestion_handlers(
            sources=(source,),
            videos=videos,
            transcript_search=search,
            audio_acquirer=acquirer,
            audio_assets=PostgresAudioAssetRepository(session),
            transcription_checkpoints=PostgresTranscriptionCheckpointRepository(session),
            transcription_provider=provider,
        ),
        worker_id=worker_id,
        lease_duration=lease_duration,
    )
    return IngestionWorker(
        repository=ingestion,
        runner=runner,
        worker_id=worker_id,
        lease_duration=lease_duration,
        poll_interval=timedelta(milliseconds=1),
    )


@pytest.mark.asyncio
async def test_asr_failure_restart_reuses_audio_and_persists_one_provenance_chain(
    tmp_path: Path,
) -> None:
    """Prove P2.7 restart and idempotency against the migrated PostgreSQL schema."""

    engine = create_database_engine(Settings(app_env="test"))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    external_id = uuid4().hex[:11]
    source_reference = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    source = CaptionlessSource(source_reference)
    fingerprint = sha256(f"asr-restart:{external_id}".encode()).hexdigest()
    acquirer = CountingAudioAcquirer(tmp_path / external_id / "audio.wav")
    provider = FailOnceTranscriptionProvider()
    worker_id = "asr-restart-integration-worker"
    job_id: UUID | None = None
    video_id: UUID | None = None

    try:
        async with sessions() as session:
            ingestion = PostgresIngestionRepository(session)
            created, was_created = await ingestion.create_or_get(source_reference, fingerprint)
            assert was_created is True
            job_id = created.job_id

            failed = await build_captionless_worker(
                session=session,
                source=source,
                acquirer=acquirer,
                provider=provider,
                search=RecordingTranscriptSearch(),
                worker_id=worker_id,
            ).run_once()

            assert failed is not None
            assert failed.job_id == job_id
            assert failed.status is IngestionJobStatus.FAILED
            assert failed.stage is IngestionStage.TRANSCRIPTION
            assert failed.attempt == 1
            assert failed.last_error_code == TranscriptionErrorCode.EXECUTION_FAILED
            assert failed.last_error_retryable is True
            assert acquirer.acquire_calls == 1
            assert acquirer.path.is_file()
            assert len(provider.requests) == 1
            await ingestion.retry(job_id)

        async with sessions() as restarted_session:
            restarted_ingestion = PostgresIngestionRepository(restarted_session)
            search = PgVectorTranscriptSearch(
                session=restarted_session,
                videos=SqlAlchemyVideoRepository(restarted_session),
                provider=FixtureEmbeddings(),
            )
            worker = build_captionless_worker(
                session=restarted_session,
                source=source,
                acquirer=acquirer,
                provider=provider,
                search=search,
                worker_id=worker_id,
            )
            completed = await worker.run_once()

            assert completed is not None
            assert completed.job_id == job_id
            assert completed.status is IngestionJobStatus.SUCCEEDED
            assert completed.attempt == 2
            assert completed.video_id is not None
            video_id = completed.video_id
            assert acquirer.acquire_calls == 1
            assert acquirer.cleanup_calls == 1
            assert not acquirer.path.exists()
            assert len(provider.requests) == 2
            assert provider.requests[1].attempt == 1

            transcript = await SqlAlchemyVideoRepository(restarted_session).get_transcript(video_id)
            assert transcript is not None
            assert transcript.transcription is not None
            checkpoint = transcript.transcription
            assert checkpoint.result.spec == provider.spec
            assert checkpoint.result.fallback_reason is AudioFallbackReason.CAPTIONS_UNAVAILABLE
            assert checkpoint.result.language_code == "hi"
            assert checkpoint.result.audio_start_ms == 0
            assert checkpoint.result.audio_end_ms == 30_000
            assert [(cue.start_ms, cue.end_ms) for cue in transcript.cues] == [
                (0, 12_500),
                (12_500, 30_000),
            ]
            assert {cue.transcription_run_id for cue in transcript.cues} == {checkpoint.run_id}

            events_before_duplicate = await restarted_ingestion.list_events(job_id)
            duplicate, duplicate_created = await restarted_ingestion.create_or_get(
                source_reference, fingerprint
            )
            assert duplicate_created is False
            assert duplicate.job_id == job_id
            assert await worker.run_once() is None
            events_after_duplicate = await restarted_ingestion.list_events(job_id)
            assert events_after_duplicate == events_before_duplicate

        async with sessions() as verification_session:
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(IngestionJobRow)
                    .where(IngestionJobRow.input_fingerprint == fingerprint)
                )
                == 1
            )
            event_sequences = (
                await verification_session.scalars(
                    select(JobEventRow.sequence)
                    .where(JobEventRow.job_id == job_id)
                    .order_by(JobEventRow.sequence)
                )
            ).all()
            assert event_sequences == list(range(1, len(event_sequences) + 1))
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TranscriptionRunRow)
                    .where(TranscriptionRunRow.job_id == job_id)
                )
                == 1
            )
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TranscriptionRunCueRow)
                    .join(TranscriptionRunRow)
                    .where(TranscriptionRunRow.job_id == job_id)
                )
                == 2
            )
            retrieval_unit_count = await verification_session.scalar(
                select(func.count())
                .select_from(RetrievalUnitRow)
                .where(RetrievalUnitRow.video_id == video_id)
            )
            embedding_count = await verification_session.scalar(
                select(func.count())
                .select_from(TextEmbeddingRow)
                .join(RetrievalUnitRow)
                .where(RetrievalUnitRow.video_id == video_id)
            )
            assert retrieval_unit_count is not None
            assert retrieval_unit_count > 0
            assert embedding_count == retrieval_unit_count
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TranscriptCueRow)
                    .where(TranscriptCueRow.video_id == video_id)
                )
                == 2
            )
            asset = await verification_session.scalar(
                select(MediaAssetRow).where(MediaAssetRow.job_id == job_id)
            )
            assert asset is not None
            assert asset.deleted_at is not None
    finally:
        async with sessions() as session:
            if video_id is not None:
                await session.execute(
                    delete(TranscriptCueRow).where(TranscriptCueRow.video_id == video_id)
                )
            if job_id is not None:
                await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id == job_id))
            if video_id is not None:
                await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_failure_retry_reuses_every_persisted_output(tmp_path: Path) -> None:
    """Prove cleanup resumes alone after transcript persistence and indexing have succeeded."""

    engine = create_database_engine(Settings(app_env="test"))
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    external_id = uuid4().hex[:11]
    source_reference = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    source = CaptionlessSource(source_reference)
    fingerprint = sha256(f"cleanup-retry:{external_id}".encode()).hexdigest()
    acquirer = CountingAudioAcquirer(
        tmp_path / external_id / "audio.wav",
        cleanup_failures=1,
    )
    provider = FailOnceTranscriptionProvider(failures=0)
    search = RecordingTranscriptSearch()
    worker_id = "cleanup-retry-integration-worker"
    job_id: UUID | None = None
    video_id: UUID | None = None

    try:
        async with sessions() as session:
            ingestion = PostgresIngestionRepository(session)
            created, was_created = await ingestion.create_or_get(source_reference, fingerprint)
            assert was_created is True
            job_id = created.job_id

            failed = await build_captionless_worker(
                session=session,
                source=source,
                acquirer=acquirer,
                provider=provider,
                search=search,
                worker_id=worker_id,
            ).run_once()

            assert failed is not None
            assert failed.status is IngestionJobStatus.FAILED
            assert failed.stage is IngestionStage.CLEANUP
            assert failed.attempt == 1
            assert failed.last_error_code == AudioAcquisitionErrorCode.WORKSPACE_ERROR
            assert failed.last_error_retryable is True
            assert acquirer.acquire_calls == 1
            assert acquirer.cleanup_calls == 1
            assert len(provider.requests) == 1
            persisted_video = await SqlAlchemyVideoRepository(session).find_by_source(
                source_reference
            )
            assert persisted_video is not None
            video_id = persisted_video.video_id
            assert search.indexed == [video_id]
            assert acquirer.path.is_file()

            retained_asset = await session.scalar(
                select(MediaAssetRow).where(MediaAssetRow.job_id == job_id)
            )
            assert retained_asset is not None
            assert retained_asset.deleted_at is None
            await ingestion.retry(job_id)

        async with sessions() as restarted_session:
            ingestion = PostgresIngestionRepository(restarted_session)
            completed = await build_captionless_worker(
                session=restarted_session,
                source=source,
                acquirer=acquirer,
                provider=provider,
                search=search,
                worker_id=worker_id,
            ).run_once()

            assert completed is not None
            assert completed.status is IngestionJobStatus.SUCCEEDED
            assert completed.stage is IngestionStage.COMPLETED
            assert completed.attempt == 2
            assert completed.video_id is not None
            assert completed.video_id == video_id
            assert acquirer.acquire_calls == 1
            assert acquirer.cleanup_calls == 2
            assert len(provider.requests) == 1
            assert search.indexed == [video_id]
            assert not acquirer.path.exists()

            events = await ingestion.list_events(job_id)
            failure = next(
                event for event in events if event.event_type is IngestionEventType.FAILED
            )
            assert failure.stage is IngestionStage.CLEANUP
            assert failure.error_code == AudioAcquisitionErrorCode.WORKSPACE_ERROR
            assert failure.retryable is True
            assert (
                sum(event.event_type is IngestionEventType.RETRY_REQUESTED for event in events) == 1
            )
            cleanup_completed = [
                event
                for event in events
                if event.event_type is IngestionEventType.STAGE_COMPLETED
                and event.stage is IngestionStage.CLEANUP
            ]
            assert len(cleanup_completed) == 1
            assert cleanup_completed[0].attempt == 2
            assert cleanup_completed[0].details is not None
            assert cleanup_completed[0].details["temporary_media_present"] is False

        async with sessions() as verification_session:
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TranscriptionRunRow)
                    .where(TranscriptionRunRow.job_id == job_id)
                )
                == 1
            )
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TranscriptionRunCueRow)
                    .join(TranscriptionRunRow)
                    .where(TranscriptionRunRow.job_id == job_id)
                )
                == 2
            )
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TranscriptCueRow)
                    .where(TranscriptCueRow.video_id == video_id)
                )
                == 2
            )
            asset = await verification_session.scalar(
                select(MediaAssetRow).where(MediaAssetRow.job_id == job_id)
            )
            assert asset is not None
            assert asset.deleted_at is not None
    finally:
        async with sessions() as session:
            if video_id is not None:
                await session.execute(
                    delete(TranscriptCueRow).where(TranscriptCueRow.video_id == video_id)
                )
            if job_id is not None:
                await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id == job_id))
            if video_id is not None:
                await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
            await session.commit()
        await engine.dispose()
