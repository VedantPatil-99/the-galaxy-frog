"""Unit checks for mapping persisted moment evidence and assigning frame slots."""

from pathlib import Path
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.unit.db.test_visual_search import row, session

from galaxy_frog.application.retrieval.moment import MomentFrames
from galaxy_frog.db.models import OCRBlockRow, TranscriptCueRow, VideoRow
from galaxy_frog.db.moment_evidence import SqlAlchemyMomentEvidenceReader
from galaxy_frog.domain.transcripts.models import TranscriptOrigin


@pytest.mark.asyncio
async def test_reader_keeps_asr_lineage_and_ocr_provenance() -> None:
    db = session()
    video_id, run_id, frame_id = uuid4(), uuid4(), uuid4()
    db.get.return_value = VideoRow(
        id=video_id,
        source_kind="youtube",
        external_id="test",
        canonical_url="https://www.youtube.com/watch?v=test",
        title="Moment fixture",
        duration_ms=15_000,
    )
    cue = TranscriptCueRow(
        id="a" * 64,
        video_id=video_id,
        source_order=0,
        start_ms=10,
        end_ms=50,
        origin="asr",
        language_code="en",
        transcription_run_id=run_id,
        confidence=0.9,
        confidence_method="segment_probability",
        text="Original ASR cue",
    )
    ocr = OCRBlockRow(
        id=uuid4(),
        video_id=video_id,
        frame_id=frame_id,
        start_ms=0,
        end_ms=80,
        provider="fixture",
        provider_revision="v1",
        text="Slide text",
    )
    db.execute.side_effect = [
        Mock(tuples=lambda: Mock(all=lambda: [(cue, "unit-a"), (cue, "unit-b"), (cue, None)])),
        Mock(tuples=lambda: Mock(all=lambda: [(ocr, 2, 40)])),
    ]
    reader = SqlAlchemyMomentEvidenceReader(cast(AsyncSession, db))

    video = await reader.get_video(video_id)
    assert video is not None
    (selected_cue,) = await reader.get_cues(video, 20, 60)
    (selected_ocr,) = await reader.get_ocr(video_id, 20, 60)

    assert selected_cue.unit_ids == ("unit-a", "unit-b")
    assert selected_cue.cue.cue_id == cue.id and selected_cue.cue.origin is TranscriptOrigin.ASR
    assert selected_cue.cue.transcription_run_id == run_id
    assert (selected_cue.cue.start_ms, selected_cue.cue.end_ms) == (10, 50)
    assert selected_cue.cue.confidence_method == cue.confidence_method
    assert (selected_ocr.block_id, selected_ocr.frame_id, selected_ocr.capture_ms) == (
        ocr.id,
        frame_id,
        40,
    )
    assert selected_ocr.block.provider_spec.provider_revision == "v1"
    assert (selected_ocr.block.start_ms, selected_ocr.block.end_ms) == (0, 80)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ((2, 1), (3,), (1, 2, 3)),
        ((2,), (), (None, 2, None)),
        ((), (3, 4), (None, 3, 4)),
        ((), (3,), (None, 3, None)),
        ((), (), (None, None, None)),
    ],
)
async def test_frame_slots(
    tmp_path: Path,
    before: tuple[int, ...],
    after: tuple[int, ...],
    expected: tuple[int | None, int | None, int | None],
) -> None:
    db = session()
    video_id = uuid4()
    db.scalars.side_effect = [
        Mock(all=[row(tmp_path, video_id, sequence) for sequence in before].copy),
        Mock(all=[row(tmp_path, video_id, sequence) for sequence in after].copy),
    ]
    frames = await SqlAlchemyMomentEvidenceReader(cast(AsyncSession, db)).get_frames(
        video_id, 2000, 0, 15_000
    )
    assert (
        tuple(
            frame.sequence if frame else None
            for frame in (frames.previous, frames.current, frames.next)
        )
        == expected
    )
    if frames.current is None:
        assert frames == MomentFrames()
    else:
        assert frames.current.content_sha256 is None
