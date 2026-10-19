"""Async SQLAlchemy repository for visual scene boundaries, frames, and OCR blocks."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import OCRBlockRow, SceneRow, VideoFrameRow
from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    OCRExtractionResult,
    SceneDetectionResult,
)


class SqlAlchemyVisionRepository:
    """Persist and query vision scenes, sampled frames, and extracted OCR blocks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expose session to collaborating adapters."""

        return self._session

    async def save_scenes(
        self,
        video_id: UUID,
        result: SceneDetectionResult,
    ) -> tuple[SceneRow, ...]:
        """Persist detected scene boundaries idempotently."""

        existing = await self.get_scenes(video_id)
        if existing:
            return existing

        now = datetime.now(UTC)
        rows = tuple(
            SceneRow(
                id=uuid4(),
                video_id=video_id,
                scene_order=order,
                start_ms=boundary.start_ms,
                end_ms=boundary.end_ms,
                score=boundary.score,
                detector=result.detector,
                detector_revision=result.detector_revision,
                created_at=now,
            )
            for order, boundary in enumerate(result.boundaries)
        )
        self._session.add_all(rows)
        await self._session.commit()
        return rows

    async def get_scenes(self, video_id: UUID) -> tuple[SceneRow, ...]:
        """Fetch all scenes for a video ordered by scene index."""

        return tuple(
            (
                await self._session.scalars(
                    select(SceneRow)
                    .where(SceneRow.video_id == video_id)
                    .order_by(SceneRow.scene_order)
                )
            ).all()
        )

    async def save_frames(
        self,
        video_id: UUID,
        frames: Sequence[FrameSample],
        *,
        deduplication: DeduplicatedFrameSet | None = None,
        dhashes: Mapping[int, str] | None = None,
    ) -> tuple[VideoFrameRow, ...]:
        """Persist sampled video frames with optional scene linkage and duplicate flags."""

        existing = await self.get_frames(video_id)
        if existing:
            return existing

        scenes = await self.get_scenes(video_id)
        kept_sequences = (
            {f.sequence for f in deduplication.kept} if deduplication is not None else None
        )
        now = datetime.now(UTC)
        rows: list[VideoFrameRow] = []
        for frame in frames:
            scene = next(
                (s for s in scenes if s.start_ms <= frame.start_ms < s.end_ms),
                None,
            )
            is_dup = (frame.sequence not in kept_sequences) if kept_sequences is not None else False
            frame_dhash = dhashes.get(frame.sequence) if dhashes is not None else None
            rows.append(
                VideoFrameRow(
                    id=uuid4(),
                    video_id=video_id,
                    scene_id=scene.id if scene is not None else None,
                    sequence=frame.sequence,
                    start_ms=frame.start_ms,
                    end_ms=frame.end_ms,
                    source=frame.source.value,
                    dhash=frame_dhash,
                    is_duplicate=is_dup,
                    image_path=str(frame.path),
                    created_at=now,
                )
            )
        self._session.add_all(rows)
        await self._session.commit()
        return tuple(rows)

    async def get_frames(
        self,
        video_id: UUID,
        *,
        only_kept: bool = False,
    ) -> tuple[VideoFrameRow, ...]:
        """Fetch sampled frames for a video, optionally excluding near-duplicates."""

        stmt = (
            select(VideoFrameRow)
            .where(VideoFrameRow.video_id == video_id)
            .order_by(VideoFrameRow.sequence)
        )
        if only_kept:
            stmt = stmt.where(VideoFrameRow.is_duplicate.is_(False))
        return tuple((await self._session.scalars(stmt)).all())

    async def save_ocr_results(
        self,
        video_id: UUID,
        results: Sequence[OCRExtractionResult],
    ) -> tuple[OCRBlockRow, ...]:
        """Persist OCR blocks linked to sampled video frames."""

        existing = await self.get_ocr_blocks(video_id)
        if existing:
            return existing

        frames = await self.get_frames(video_id)
        frames_by_seq = {frame.sequence: frame for frame in frames}
        rows: list[OCRBlockRow] = []
        for res in results:
            seq = res.request.frame.sequence
            frame = frames_by_seq.get(seq)
            if frame is None:
                msg = f"Frame with sequence {seq} not found for video {video_id}"
                raise ValueError(msg)
            for block in res.blocks:
                rows.append(
                    OCRBlockRow(
                        id=uuid4(),
                        video_id=video_id,
                        frame_id=frame.id,
                        text=block.text,
                        start_ms=block.start_ms,
                        end_ms=block.end_ms,
                        confidence=block.confidence,
                        confidence_method=block.confidence_method,
                        provider=block.provider_spec.provider,
                        provider_revision=block.provider_spec.provider_revision,
                        created_at=res.extracted_at,
                    )
                )
        self._session.add_all(rows)
        await self._session.commit()
        return tuple(rows)

    async def get_ocr_blocks(self, video_id: UUID) -> tuple[OCRBlockRow, ...]:
        """Fetch all OCR blocks for a video ordered by temporal start."""

        return tuple(
            (
                await self._session.scalars(
                    select(OCRBlockRow)
                    .where(OCRBlockRow.video_id == video_id)
                    .order_by(OCRBlockRow.start_ms, OCRBlockRow.id)
                )
            ).all()
        )
