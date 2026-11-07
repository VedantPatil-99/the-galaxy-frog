"""Playhead and evidence-window boundary checks."""

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from galaxy_frog.application.retrieval.moment import (
    InvalidMomentTimestamp,
    MomentEvidenceReader,
    MomentFrames,
    RetrieveMomentEvidence,
)
from galaxy_frog.application.retrieval.temporal import RetrievalVideoNotFound
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.videos.models import SafeVideoMetadata, SourceReference, VideoSourceKind
from galaxy_frog.domain.videos.records import VideoRecord


@pytest.fixture
def video() -> VideoRecord:
    source = SourceReference(
        VideoSourceKind.YOUTUBE, "test", "https://www.youtube.com/watch?v=test"
    )
    return VideoRecord(uuid4(), SafeVideoMetadata(source, "Moment fixture", 60_000))


@pytest.fixture
def reader(video: VideoRecord) -> AsyncMock:
    reader = AsyncMock(spec=MomentEvidenceReader)
    reader.get_video.return_value = video
    reader.get_cues.return_value = ()
    reader.get_ocr.return_value = ()
    reader.get_frames.return_value = MomentFrames()
    return reader


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timestamp", "start", "end"),
    [(0, 0, 15_000), (20_000, 5_000, 35_000), (59_999, 44_999, 60_000)],
)
async def test_moment_window(
    video: VideoRecord, reader: AsyncMock, timestamp: int, start: int, end: int
) -> None:
    result = await RetrieveMomentEvidence(cast(MomentEvidenceReader, reader)).execute(
        video.video_id, timestamp
    )

    assert (result.video_id, result.timestamp_ms) == (video.video_id, timestamp)
    assert result.window == TimeWindow(start, end)
    assert (result.cues, result.ocr, result.frames) == ((), (), MomentFrames())
    reader.get_cues.assert_awaited_once_with(video, start, end)
    reader.get_ocr.assert_awaited_once_with(video.video_id, start, end)
    reader.get_frames.assert_awaited_once_with(video.video_id, timestamp, start, end)


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [-1, 60_000, True, 1.5, "1"])
async def test_invalid_timestamp_stops_before_evidence_reads(
    video: VideoRecord, reader: AsyncMock, timestamp: object
) -> None:
    with pytest.raises(InvalidMomentTimestamp):
        await RetrieveMomentEvidence(cast(MomentEvidenceReader, reader)).execute(
            video.video_id, cast(int, timestamp)
        )
    reader.get_cues.assert_not_awaited()
    reader.get_ocr.assert_not_awaited()
    reader.get_frames.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_video_is_distinct_from_missing_evidence(reader: AsyncMock) -> None:
    reader.get_video.return_value = None
    with pytest.raises(RetrievalVideoNotFound):
        await RetrieveMomentEvidence(cast(MomentEvidenceReader, reader)).execute(uuid4(), 0)
    reader.get_cues.assert_not_awaited()
    reader.get_ocr.assert_not_awaited()
    reader.get_frames.assert_not_awaited()
