"""Read a bounded moment directly from persisted transcript and vision artifacts."""

from collections.abc import Sequence
from itertools import groupby
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.retrieval.moment import MomentCue, MomentFrame, MomentFrames, MomentOCR
from galaxy_frog.db.models import (
    OCRBlockRow,
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    TranscriptCueRow,
    VideoFrameRow,
)
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository, transcript_cue_from_row
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.vision.models import FrameSource, OCRBlock, OCRProviderSpec


def _frame(row: VideoFrameRow) -> MomentFrame:
    assert row.capture_ms is not None  # The temporal predicates require a known capture time.
    return MomentFrame(
        frame_id=row.id,
        scene_id=row.scene_id,
        sequence=row.sequence,
        start_ms=row.start_ms,
        end_ms=row.end_ms,
        capture_ms=row.capture_ms,
        source=FrameSource(row.source),
        path=Path(row.image_path),
        dhash=row.dhash,
        content_sha256=row.content_sha256,
    )


class SqlAlchemyMomentEvidenceReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._videos = SqlAlchemyVideoRepository(session)

    async def get_video(self, video_id: UUID) -> VideoRecord | None:
        return await self._videos.get_video(video_id)

    async def get_cues(
        self, video: VideoRecord, start_ms: int, end_ms: int
    ) -> tuple[MomentCue, ...]:
        statement = (
            select(TranscriptCueRow, RetrievalUnitRow.id)
            .outerjoin(RetrievalUnitCueRow, RetrievalUnitCueRow.cue_id == TranscriptCueRow.id)
            .outerjoin(
                RetrievalUnitRow,
                and_(
                    RetrievalUnitRow.id == RetrievalUnitCueRow.retrieval_unit_id,
                    RetrievalUnitRow.video_id == video.video_id,
                ),
            )
            .where(
                TranscriptCueRow.video_id == video.video_id,
                TranscriptCueRow.end_ms > start_ms,
                TranscriptCueRow.start_ms < end_ms,
            )
            .order_by(
                TranscriptCueRow.start_ms,
                TranscriptCueRow.source_order,
                TranscriptCueRow.id,
                RetrievalUnitRow.start_ms,
                RetrievalUnitRow.id,
            )
        )
        # SQLAlchemy's column type does not include NULL introduced by the outer join.
        rows = cast(
            Sequence[tuple[TranscriptCueRow, str | None]],
            (await self._session.execute(statement)).tuples().all(),
        )
        return tuple(
            MomentCue(
                transcript_cue_from_row(cue, video.metadata.reference),
                tuple(unit_id for _, unit_id in links if unit_id is not None),
            )
            for cue, links in groupby(rows, key=lambda row: row[0])
        )

    async def get_ocr(self, video_id: UUID, start_ms: int, end_ms: int) -> tuple[MomentOCR, ...]:
        statement = (
            select(OCRBlockRow, VideoFrameRow.sequence, VideoFrameRow.capture_ms)
            .join(VideoFrameRow, VideoFrameRow.id == OCRBlockRow.frame_id)
            .where(
                OCRBlockRow.video_id == video_id,
                VideoFrameRow.video_id == video_id,
                VideoFrameRow.capture_ms >= start_ms,
                VideoFrameRow.capture_ms < end_ms,
            )
            .order_by(VideoFrameRow.capture_ms, VideoFrameRow.sequence, OCRBlockRow.id)
        )
        rows = (await self._session.execute(statement)).tuples().all()
        evidence: list[MomentOCR] = []
        for row, sequence, capture_ms in rows:
            assert capture_ms is not None  # The temporal predicates exclude nulls.
            evidence.append(
                MomentOCR(
                    block_id=row.id,
                    frame_id=row.frame_id,
                    capture_ms=capture_ms,
                    block=OCRBlock(
                        text=row.text,
                        start_ms=row.start_ms,
                        end_ms=row.end_ms,
                        source_frame_sequence=sequence,
                        provider_spec=OCRProviderSpec(row.provider, row.provider_revision),
                        confidence=row.confidence,
                        confidence_method=row.confidence_method,
                    ),
                )
            )
        return tuple(evidence)

    async def get_frames(
        self, video_id: UUID, timestamp_ms: int, start_ms: int, end_ms: int
    ) -> MomentFrames:
        capture = VideoFrameRow.capture_ms
        # PostgreSQL DISTINCT ON keeps the lowest sequence at each capture time.
        candidates = (
            select(VideoFrameRow)
            .where(
                VideoFrameRow.video_id == video_id,
                VideoFrameRow.is_duplicate.is_(False),
                capture >= start_ms,
                capture < end_ms,
            )
            .distinct(capture)
            .limit(2)
        )
        before = (
            await self._session.scalars(
                candidates.where(capture <= timestamp_ms).order_by(
                    capture.desc(), VideoFrameRow.sequence
                )
            )
        ).all()
        after = (
            await self._session.scalars(
                candidates.where(capture > timestamp_ms).order_by(capture, VideoFrameRow.sequence)
            )
        ).all()
        if before:
            return MomentFrames(
                previous=_frame(before[1]) if len(before) > 1 else None,
                current=_frame(before[0]),
                next=_frame(after[0]) if after else None,
            )
        return MomentFrames(
            current=_frame(after[0]) if after else None,
            next=_frame(after[1]) if len(after) > 1 else None,
        )
