"""Behavior coverage for durable audio and transcription stage outputs."""

from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.ingestion import (
    AudioAssetRepository,
    TranscriptionCheckpointRepository,
)
from galaxy_frog.db.ingestion_artifacts import (
    IngestionArtifactRepositoryError,
    PostgresAudioAssetRepository,
    PostgresTranscriptionCheckpointRepository,
)
from galaxy_frog.db.models import (
    IngestionJobRow,
    MediaAssetRow,
    TranscriptionRunCueRow,
    TranscriptionRunRow,
)
from galaxy_frog.domain.ingestion import IngestionJobStatus, IngestionStage
from galaxy_frog.domain.media import AcquiredAudio, AudioFallbackReason
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionProviderSpec,
    TranscriptionResult,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
SOURCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)


class AllResult:
    def __init__(self, values: Sequence[object]) -> None:
        self._values = values

    def all(self) -> Sequence[object]:
        return self._values


class FakeSession:
    def __init__(self, *scalar_values: object | None) -> None:
        self.scalar_values = deque(scalar_values)
        self.get_values: deque[object | None] = deque()
        self.scalars_values: deque[Sequence[object]] = deque()
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.popleft()

    async def get(self, _model: object, _identity: object) -> object | None:
        return self.get_values.popleft()

    async def scalars(self, _statement: object) -> AllResult:
        return AllResult(self.scalars_values.popleft())

    def add_all(self, values: object) -> None:
        self.added.extend(values)  # type: ignore[arg-type]

    async def commit(self) -> None:
        self.commits += 1


def job_row(job_id: UUID) -> IngestionJobRow:
    return IngestionJobRow(
        id=job_id,
        source_kind=SOURCE.kind,
        external_id=SOURCE.external_id,
        canonical_url=SOURCE.canonical_url,
        input_fingerprint="a" * 64,
        status=IngestionJobStatus.RUNNING,
        stage=IngestionStage.TRANSCRIPTION,
        attempt=2,
        lease_owner="worker",
        lease_expires_at=NOW + timedelta(minutes=2),
        heartbeat_at=NOW,
        started_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def audio(job_id: UUID | None = None, attempt: int = 1) -> AcquiredAudio:
    return AcquiredAudio(
        job_id=job_id or uuid4(),
        attempt=attempt,
        source=SOURCE,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        path=Path("C:/tmp/audio.wav"),
        start_ms=0,
        end_ms=4000,
        size_bytes=128_078,
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


def media_row(acquired: AcquiredAudio, asset_id: UUID | None = None) -> MediaAssetRow:
    return MediaAssetRow(
        id=asset_id or uuid4(),
        job_id=acquired.job_id,
        attempt=acquired.attempt,
        asset_kind="audio",
        fallback_reason=acquired.fallback_reason,
        storage_path=str(acquired.path),
        start_ms=acquired.start_ms,
        end_ms=acquired.end_ms,
        size_bytes=acquired.size_bytes,
        media_type=acquired.media_type,
        codec=acquired.codec,
        sample_rate_hz=acquired.sample_rate_hz,
        channels=acquired.channels,
        downloader=acquired.downloader,
        downloader_revision=acquired.downloader_revision,
        normalizer=acquired.normalizer,
        normalizer_revision=acquired.normalizer_revision,
        acquired_at=acquired.acquired_at,
        deleted_at=None,
    )


def transcription(job_id: UUID) -> TranscriptionResult:
    return TranscriptionResult(
        job_id=job_id,
        attempt=1,
        source=SOURCE,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_start_ms=0,
        audio_end_ms=4000,
        spec=TranscriptionProviderSpec(
            "faster-whisper",
            "1.2.1",
            "small",
            "model-revision",
            TranscriptionDevice.CUDA,
            TranscriptionComputeType.INT8_FLOAT16,
        ),
        language_code="hi",
        language_confidence=0.88,
        language_confidence_method="provider_language_probability",
        cues=(
            TranscriptionCue(0, 0, 2000, "Namaste.", 0.91, "mean_word_probability"),
            TranscriptionCue(1, 2000, 4000, "Welcome.", None, None),
        ),
        processing_seconds=1.25,
        transcribed_at=NOW,
    )


def run_row(
    result: TranscriptionResult,
    asset_id: UUID,
    run_id: UUID | None = None,
) -> TranscriptionRunRow:
    return TranscriptionRunRow(
        id=run_id or uuid4(),
        job_id=result.job_id,
        audio_asset_id=asset_id,
        audio_attempt=result.attempt,
        fallback_reason=result.fallback_reason,
        audio_start_ms=result.audio_start_ms,
        audio_end_ms=result.audio_end_ms,
        provider=result.spec.provider,
        provider_revision=result.spec.provider_revision,
        model=result.spec.model,
        model_revision=result.spec.model_revision,
        device=result.spec.device,
        compute_type=result.spec.compute_type,
        language_code=result.language_code,
        language_confidence=result.language_confidence,
        language_confidence_method=result.language_confidence_method,
        processing_seconds=result.processing_seconds,
        transcribed_at=result.transcribed_at,
    )


def cue_rows(run_id: UUID, result: TranscriptionResult) -> list[TranscriptionRunCueRow]:
    return [
        TranscriptionRunCueRow(
            run_id=run_id,
            source_order=cue.source_order,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=cue.text,
            confidence=cue.confidence,
            confidence_method=cue.confidence_method,
        )
        for cue in result.cues
    ]


def audio_repository(session: FakeSession) -> PostgresAudioAssetRepository:
    return PostgresAudioAssetRepository(cast(AsyncSession, session))


def transcription_repository(
    session: FakeSession,
) -> PostgresTranscriptionCheckpointRepository:
    return PostgresTranscriptionCheckpointRepository(cast(AsyncSession, session))


def test_artifact_ports_are_provider_independent() -> None:
    assert AudioAssetRepository.__name__ == "AudioAssetRepository"
    assert TranscriptionCheckpointRepository.__name__ == "TranscriptionCheckpointRepository"


@pytest.mark.asyncio
async def test_audio_save_inserts_or_reuses_one_attempt_checkpoint() -> None:
    acquired = audio()
    asset_id = uuid4()
    inserted_session = FakeSession(asset_id)

    inserted = await audio_repository(inserted_session).save(acquired)

    assert inserted.asset_id == asset_id
    assert inserted.audio == acquired
    assert inserted_session.commits == 1

    row = media_row(acquired, asset_id)
    reused_session = FakeSession(None, row)
    reused = await audio_repository(reused_session).save(acquired)
    assert reused == inserted
    assert reused_session.commits == 1


@pytest.mark.asyncio
async def test_audio_save_rejects_an_unloadable_conflict() -> None:
    with pytest.raises(IngestionArtifactRepositoryError, match="could not be loaded"):
        await audio_repository(FakeSession(None, None)).save(audio())


@pytest.mark.asyncio
async def test_latest_audio_restores_source_and_only_returns_available_assets() -> None:
    job_id = uuid4()
    acquired = audio(job_id)
    row = media_row(acquired)
    session = FakeSession(row)
    session.get_values.append(job_row(job_id))

    restored = await audio_repository(session).get_latest_available(job_id)

    assert restored is not None
    assert restored.audio == acquired
    assert await audio_repository(FakeSession(None)).get_latest_available(job_id) is None


@pytest.mark.asyncio
async def test_audio_deletion_is_idempotent_and_requires_its_job() -> None:
    job_id = uuid4()
    row = media_row(audio(job_id))
    session = FakeSession()
    session.get_values.extend((row, job_row(job_id)))
    deleted_at = NOW + timedelta(minutes=1)

    deleted = await audio_repository(session).mark_deleted(row.id, deleted_at=deleted_at)

    assert deleted.deleted_at == deleted_at
    assert deleted.is_available is False
    assert session.commits == 1

    repeated_session = FakeSession()
    repeated_session.get_values.extend((row, job_row(job_id)))
    repeated = await audio_repository(repeated_session).mark_deleted(
        row.id, deleted_at=NOW + timedelta(minutes=2)
    )
    assert repeated.deleted_at == deleted_at

    missing_session = FakeSession()
    missing_session.get_values.append(None)
    with pytest.raises(IngestionArtifactRepositoryError, match="does not exist"):
        await audio_repository(missing_session).mark_deleted(uuid4())


@pytest.mark.asyncio
async def test_artifact_source_requires_the_owning_job() -> None:
    job_id = uuid4()
    row = media_row(audio(job_id))
    session = FakeSession(row)
    session.get_values.append(None)

    with pytest.raises(IngestionArtifactRepositoryError, match="ingestion job"):
        await audio_repository(session).get_latest_available(job_id)


@pytest.mark.asyncio
async def test_transcription_save_inserts_cues_and_reuses_a_completed_run() -> None:
    job_id = uuid4()
    result = transcription(job_id)
    asset_id = uuid4()
    run_id = uuid4()
    inserted_session = FakeSession(run_id)

    inserted = await transcription_repository(inserted_session).save(asset_id, result)

    assert inserted.run_id == run_id
    assert inserted.result == result
    assert len(inserted_session.added) == 2
    assert inserted_session.commits == 1

    row = run_row(result, asset_id, run_id)
    reused_session = FakeSession(None, row)
    reused_session.scalars_values.append(cue_rows(run_id, result))
    reused_session.get_values.append(job_row(job_id))
    reused = await transcription_repository(reused_session).save(asset_id, result)
    assert reused == inserted
    assert reused_session.commits == 1


@pytest.mark.asyncio
async def test_transcription_save_rejects_an_unloadable_conflict() -> None:
    result = transcription(uuid4())
    session = FakeSession(None, None)

    with pytest.raises(IngestionArtifactRepositoryError, match="could not be loaded"):
        await transcription_repository(session).save(uuid4(), result)


@pytest.mark.asyncio
async def test_transcription_get_restores_complete_result_or_none() -> None:
    job_id = uuid4()
    result = transcription(job_id)
    asset_id = uuid4()
    row = run_row(result, asset_id)
    session = FakeSession(row)
    session.scalars_values.append(cue_rows(row.id, result))
    session.get_values.append(job_row(job_id))

    checkpoint = await transcription_repository(session).get(job_id)

    assert checkpoint is not None
    assert checkpoint.audio_asset_id == asset_id
    assert checkpoint.result == result
    assert await transcription_repository(FakeSession(None)).get(job_id) is None


@pytest.mark.asyncio
async def test_transcription_source_requires_the_owning_job() -> None:
    job_id = uuid4()
    result = transcription(job_id)
    row = run_row(result, uuid4())
    session = FakeSession(row)
    session.scalars_values.append(cue_rows(row.id, result))
    session.get_values.append(None)

    with pytest.raises(IngestionArtifactRepositoryError, match="ingestion job"):
        await transcription_repository(session).get(job_id)
