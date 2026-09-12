"""Unit coverage for SQLAlchemy transcript persistence projections."""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import (
    IngestionJobRow,
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    TranscriptCueRow,
    TranscriptionRunCueRow,
    TranscriptionRunRow,
    VideoRow,
)
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.ingestion import IngestionJobStatus, IngestionStage
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionProviderSpec,
    TranscriptionResult,
)
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue, TranscriptOrigin
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)


class AllResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeSession:
    def __init__(self) -> None:
        self.scalar_values: deque[object | None] = deque()
        self.get_values: deque[object | None] = deque()
        self.scalars_values: deque[list[object]] = deque()
        self.added: list[object] = []
        self.added_groups: list[tuple[object, ...]] = []
        self.flushes = 0
        self.commits = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.popleft()

    async def get(self, _model: object, _identity: UUID) -> object | None:
        return self.get_values.popleft()

    async def scalars(self, _statement: object) -> AllResult:
        return AllResult(self.scalars_values.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: Iterable[object]) -> None:
        self.added_groups.append(tuple(values))

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


def values() -> tuple[SafeVideoMetadata, TranscriptCue, RetrievalUnit]:
    reference = SourceReference(
        VideoSourceKind.YOUTUBE,
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    metadata = SafeVideoMetadata(reference, "Video", 1000, "Channel")
    cue = TranscriptCue.from_source(
        source=reference,
        track_id="manual:en",
        language_code="en",
        caption_kind=CaptionKind.MANUAL,
        cue=SourceCaptionCue(0, 0, 1000, "Evidence."),
    )
    unit = RetrievalUnit("a" * 64, 0, 1000, "Evidence.", (cue.cue_id,))
    return metadata, cue, unit


def video_row(metadata: SafeVideoMetadata, video_id: UUID | None = None) -> VideoRow:
    now = datetime.now(UTC)
    return VideoRow(
        id=video_id or uuid4(),
        source_kind=metadata.reference.kind,
        external_id=metadata.reference.external_id,
        canonical_url=metadata.reference.canonical_url,
        title=metadata.title,
        duration_ms=metadata.duration_ms,
        channel_name=metadata.channel_name,
        thumbnail_url=metadata.thumbnail_url,
        created_at=now,
        updated_at=now,
    )


def asr_values() -> tuple[SafeVideoMetadata, TranscriptionResult, TranscriptCue, RetrievalUnit]:
    metadata, _caption, _unit = values()
    result = TranscriptionResult(
        job_id=uuid4(),
        attempt=2,
        source=metadata.reference,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_start_ms=0,
        audio_end_ms=1000,
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
        cues=(TranscriptionCue(0, 0, 1000, "नमस्ते।", 0.91, "mean_word_probability"),),
        processing_seconds=1.25,
        transcribed_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
    )
    run_id = uuid4()
    cue = TranscriptCue.from_transcription(
        run_id=run_id,
        result=result,
        cue=result.cues[0],
    )
    unit = RetrievalUnit("c" * 64, 0, 1000, cue.text, (cue.cue_id,))
    return metadata, result, cue, unit


def job_row(result: TranscriptionResult) -> IngestionJobRow:
    return IngestionJobRow(
        id=result.job_id,
        source_kind=result.source.kind,
        external_id=result.source.external_id,
        canonical_url=result.source.canonical_url,
        input_fingerprint="d" * 64,
        status=IngestionJobStatus.SUCCEEDED,
        stage=IngestionStage.COMPLETED,
        attempt=2,
        video_id=uuid4(),
        completed_at=result.transcribed_at,
        created_at=result.transcribed_at,
        updated_at=result.transcribed_at,
    )


def run_row(
    result: TranscriptionResult,
    run_id: UUID,
    audio_asset_id: UUID | None = None,
) -> TranscriptionRunRow:
    return TranscriptionRunRow(
        id=run_id,
        job_id=result.job_id,
        audio_asset_id=audio_asset_id or uuid4(),
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


@pytest.mark.asyncio
async def test_find_get_and_existing_save_paths() -> None:
    metadata, cue, unit = values()
    row = video_row(metadata)
    session = FakeSession()
    session.scalar_values.extend((None, row, row))
    session.get_values.extend((None, row))
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    assert repository.session is cast(object, session)
    assert await repository.find_by_source(metadata.reference) is None
    found = await repository.find_by_source(metadata.reference)
    assert found is not None and found.video_id == row.id
    existing = await repository.save_import(metadata, (cue,), (unit,))
    assert existing.video_id == row.id
    assert await repository.get_video(uuid4()) is None
    assert (await repository.get_video(row.id)) == found


@pytest.mark.asyncio
async def test_save_builds_all_provenance_rows_and_commits() -> None:
    metadata, cue, unit = values()
    session = FakeSession()
    session.scalar_values.append(None)
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    saved = await repository.save_import(metadata, (cue,), (unit,))

    assert saved.metadata == metadata
    assert len(session.added) == 1
    assert isinstance(session.added[0], VideoRow)
    assert [type(group[0]) for group in session.added_groups] == [
        TranscriptCueRow,
        RetrievalUnitRow,
        RetrievalUnitCueRow,
    ]
    assert session.commits == 1
    assert session.flushes == 2


@pytest.mark.asyncio
async def test_save_links_asr_cues_to_the_matching_durable_run() -> None:
    metadata, _result, cue, unit = asr_values()
    assert cue.transcription_run_id is not None
    session = FakeSession()
    session.scalar_values.append(None)
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    await repository.save_import(
        metadata,
        (cue,),
        (unit,),
        transcription_run_id=cue.transcription_run_id,
    )

    cue_row = cast(TranscriptCueRow, session.added_groups[0][0])
    assert cue_row.origin == TranscriptOrigin.ASR
    assert cue_row.track_id is None
    assert cue_row.caption_kind is None
    assert cue_row.transcription_run_id == cue.transcription_run_id
    assert cue_row.confidence == 0.91


@pytest.mark.asyncio
async def test_save_rejects_mismatched_transcription_run_provenance() -> None:
    metadata, _result, cue, unit = asr_values()
    session = FakeSession()
    session.scalar_values.append(None)

    with pytest.raises(ValueError, match="matching provenance"):
        await SqlAlchemyVideoRepository(cast(AsyncSession, session)).save_import(
            metadata,
            (cue,),
            (unit,),
            transcription_run_id=uuid4(),
        )
    assert session.added == []


@pytest.mark.asyncio
async def test_get_transcript_reconstructs_ordered_provenance() -> None:
    metadata, cue, unit = values()
    row = video_row(metadata)
    cue_row = TranscriptCueRow(
        id=cue.cue_id,
        video_id=row.id,
        origin=TranscriptOrigin.CAPTION,
        track_id=cue.track_id,
        language_code=cue.language_code,
        caption_kind=cue.caption_kind,
        transcription_run_id=None,
        confidence=None,
        confidence_method=None,
        source_order=cue.source_order,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        text=cue.text,
    )
    unit_row = RetrievalUnitRow(
        id=unit.unit_id,
        video_id=row.id,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        text=unit.text,
    )
    link = RetrievalUnitCueRow(
        retrieval_unit_id=unit.unit_id,
        cue_id=cue.cue_id,
        cue_order=0,
    )
    session = FakeSession()
    session.get_values.extend((None, row))
    session.scalars_values.extend(([cue_row], [unit_row], [link]))
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    assert await repository.get_transcript(uuid4()) is None
    transcript = await repository.get_transcript(row.id)

    assert transcript is not None
    assert transcript.video.video_id == row.id
    assert transcript.cues == (cue,)
    assert transcript.units == (unit,)
    assert transcript.transcription is None


def asr_database_rows(
    metadata: SafeVideoMetadata,
    result: TranscriptionResult,
    cue: TranscriptCue,
    unit: RetrievalUnit,
) -> tuple[
    VideoRow,
    TranscriptCueRow,
    RetrievalUnitRow,
    RetrievalUnitCueRow,
    TranscriptionRunRow,
    TranscriptionRunCueRow,
]:
    assert cue.transcription_run_id is not None
    video = video_row(metadata)
    stored_cue = TranscriptCueRow(
        id=cue.cue_id,
        video_id=video.id,
        origin=cue.origin,
        track_id=None,
        language_code=cue.language_code,
        caption_kind=None,
        transcription_run_id=cue.transcription_run_id,
        confidence=cue.confidence,
        confidence_method=cue.confidence_method,
        source_order=cue.source_order,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        text=cue.text,
    )
    stored_unit = RetrievalUnitRow(
        id=unit.unit_id,
        video_id=video.id,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        text=unit.text,
    )
    link = RetrievalUnitCueRow(
        retrieval_unit_id=unit.unit_id,
        cue_id=cue.cue_id,
        cue_order=0,
    )
    run = run_row(result, cue.transcription_run_id)
    run_cue = TranscriptionRunCueRow(
        run_id=run.id,
        source_order=result.cues[0].source_order,
        start_ms=result.cues[0].start_ms,
        end_ms=result.cues[0].end_ms,
        text=result.cues[0].text,
        confidence=result.cues[0].confidence,
        confidence_method=result.cues[0].confidence_method,
    )
    return video, stored_cue, stored_unit, link, run, run_cue


@pytest.mark.asyncio
async def test_get_transcript_restores_asr_run_model_language_and_confidence() -> None:
    metadata, result, cue, unit = asr_values()
    video, stored_cue, stored_unit, link, run, run_cue = asr_database_rows(
        metadata, result, cue, unit
    )
    session = FakeSession()
    session.get_values.extend((video, run, job_row(result)))
    session.scalar_values.append(run)
    session.scalars_values.extend(([stored_cue], [stored_unit], [link], [run_cue]))

    transcript = await SqlAlchemyVideoRepository(cast(AsyncSession, session)).get_transcript(
        video.id
    )

    assert transcript is not None
    assert transcript.cues == (cue,)
    assert transcript.transcription is not None
    assert transcript.transcription.run_id == run.id
    assert transcript.transcription.result == result


@pytest.mark.asyncio
async def test_get_transcript_rejects_broken_asr_run_links() -> None:
    metadata, result, cue, unit = asr_values()
    video, stored_cue, stored_unit, link, run, run_cue = asr_database_rows(
        metadata, result, cue, unit
    )

    missing_run_session = FakeSession()
    missing_run_session.get_values.extend((video, None))
    missing_run_session.scalars_values.extend(([stored_cue], [stored_unit], [link]))
    with pytest.raises(ValueError, match="missing transcription run"):
        await SqlAlchemyVideoRepository(cast(AsyncSession, missing_run_session)).get_transcript(
            video.id
        )

    unloadable_session = FakeSession()
    unloadable_session.get_values.extend((video, run))
    unloadable_session.scalar_values.append(None)
    unloadable_session.scalars_values.extend(([stored_cue], [stored_unit], [link]))
    with pytest.raises(ValueError, match="could not be restored"):
        await SqlAlchemyVideoRepository(cast(AsyncSession, unloadable_session)).get_transcript(
            video.id
        )

    wrong_run = run_row(result, uuid4())
    wrong_run_cue = TranscriptionRunCueRow(
        run_id=wrong_run.id,
        source_order=run_cue.source_order,
        start_ms=run_cue.start_ms,
        end_ms=run_cue.end_ms,
        text=run_cue.text,
        confidence=run_cue.confidence,
        confidence_method=run_cue.confidence_method,
    )
    mismatched_session = FakeSession()
    mismatched_session.get_values.extend((video, run, job_row(result)))
    mismatched_session.scalar_values.append(wrong_run)
    mismatched_session.scalars_values.extend(([stored_cue], [stored_unit], [link], [wrong_run_cue]))
    with pytest.raises(ValueError, match="could not be restored"):
        await SqlAlchemyVideoRepository(cast(AsyncSession, mismatched_session)).get_transcript(
            video.id
        )


@pytest.mark.asyncio
async def test_get_transcript_rejects_mixed_transcription_runs() -> None:
    metadata, result, cue, unit = asr_values()
    video, stored_cue, stored_unit, link, _run, _run_cue = asr_database_rows(
        metadata, result, cue, unit
    )
    second_cue = TranscriptCueRow(
        id="e" * 64,
        video_id=video.id,
        origin=TranscriptOrigin.ASR,
        track_id=None,
        language_code="hi",
        caption_kind=None,
        transcription_run_id=uuid4(),
        confidence=None,
        confidence_method=None,
        source_order=1,
        start_ms=1000,
        end_ms=2000,
        text="Second run.",
    )
    session = FakeSession()
    session.get_values.append(video)
    session.scalars_values.extend(([stored_cue, second_cue], [stored_unit], [link]))

    with pytest.raises(ValueError, match="cannot mix"):
        await SqlAlchemyVideoRepository(cast(AsyncSession, session)).get_transcript(video.id)
