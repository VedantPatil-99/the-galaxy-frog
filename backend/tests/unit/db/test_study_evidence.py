"""Check how study inputs project persisted transcript, OCR, and scene lineage."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.retrieval.moment import MomentCue, MomentOCR
from galaxy_frog.db import study_evidence
from galaxy_frog.db.moment_evidence import SqlAlchemyMomentEvidenceReader
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.study import StudyError
from galaxy_frog.domain.transcripts.models import TranscriptCue, TranscriptOrigin
from galaxy_frog.domain.videos.models import SafeVideoMetadata, SourceReference, VideoSourceKind
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.vision.models import OCRBlock, OCRProviderSpec


@pytest.mark.asyncio
async def test_reader_preserves_lineage_and_reuses_existing_index_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_id, run_id, block_id, frame_id = (uuid4() for _ in range(4))
    ref = SourceReference(
        VideoSourceKind.YOUTUBE, "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"
    )
    video = MagicMock(spec=VideoRecord)
    video.metadata = SafeVideoMetadata(ref, "Fixture", 90_000)
    reader = AsyncMock(spec=SqlAlchemyMomentEvidenceReader)
    reader.get_video.return_value = video
    reader.get_cues.return_value = (
        MomentCue(
            TranscriptCue(
                cue_id="a" * 64,
                source=ref,
                language_code="en",
                source_order=0,
                start_ms=2_000,
                end_ms=3_000,
                text="ASR fixture",
                origin=TranscriptOrigin.ASR,
                transcription_run_id=run_id,
            ),
            ("b" * 64,),
        ),
    )
    reader.get_ocr.return_value = (
        MomentOCR(
            block_id,
            frame_id,
            25_000,
            OCRBlock(
                text="On-screen",
                start_ms=0,
                end_ms=90_000,
                source_frame_sequence=1,
                provider_spec=OCRProviderSpec("fixture", "v1"),
                confidence=None,
                confidence_method=None,
            ),
        ),
    )

    def get_reader(_session: AsyncSession) -> AsyncMock:
        return reader

    monkeypatch.setattr(study_evidence, "SqlAlchemyMomentEvidenceReader", get_reader)
    session = AsyncMock(spec=AsyncSession)
    vectors, scenes = MagicMock(), MagicMock()
    vectors.tuples.return_value = [(2_000, [1.0, 0.0])]
    scenes.tuples.return_value = [(0, 100_000)]
    session.execute.side_effect = [vectors, scenes]
    spec = EmbeddingCollectionSpec("ollama", "bge-m3", "ollama", 1024, "l2")
    service = study_evidence.StudyEvidenceReader(cast(AsyncSession, session), spec)
    result = await service.read(video_id, chaptering=True)
    transcript, ocr = result.evidence
    assert transcript.transcription_run_id == run_id and transcript.unit_ids == ("b" * 64,)
    assert ocr.ocr_block_id == block_id and ocr.frame_id == frame_id and ocr.capture_ms == 25_000
    assert result.scenes == ((0, 90_000),) and result.vectors[0].vector == (1.0, 0.0)
    assert (await service.read(video_id, chaptering=False)).vectors == ()
    assert session.execute.await_count == 2
    video.metadata = SafeVideoMetadata(ref, "Fixture", 7_200_001)
    with pytest.raises(StudyError, match="two hours"):
        await service.read(video_id, chaptering=False)
    reader.get_video.return_value = None
    with pytest.raises(StudyError, match="does not exist"):
        await service.read(video_id, chaptering=False)
