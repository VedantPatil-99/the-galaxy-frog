"""Async SQLAlchemy repository for videos and transcript provenance."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import RetrievalUnitCueRow, RetrievalUnitRow, TranscriptCueRow, VideoRow
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord


class SqlAlchemyVideoRepository:
    """Persist imports atomically through a request-scoped async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expose the shared infrastructure session to collaborating database adapters."""

        return self._session

    async def find_by_source(self, reference: SourceReference) -> VideoRecord | None:
        row = await self._session.scalar(
            select(VideoRow).where(
                VideoRow.source_kind == reference.kind,
                VideoRow.external_id == reference.external_id,
            )
        )
        return self._video_record(row) if row is not None else None

    async def save_import(
        self,
        metadata: SafeVideoMetadata,
        cues: tuple[TranscriptCue, ...],
        units: tuple[RetrievalUnit, ...],
    ) -> VideoRecord:
        existing = await self.find_by_source(metadata.reference)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        row = VideoRow(
            id=uuid4(),
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
        self._session.add(row)
        await self._session.flush()
        self._session.add_all(
            TranscriptCueRow(
                id=cue.cue_id,
                video_id=row.id,
                track_id=cue.track_id,
                language_code=cue.language_code,
                caption_kind=cue.caption_kind,
                source_order=cue.source_order,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
            )
            for cue in cues
        )
        self._session.add_all(
            RetrievalUnitRow(
                id=unit.unit_id,
                video_id=row.id,
                start_ms=unit.start_ms,
                end_ms=unit.end_ms,
                text=unit.text,
            )
            for unit in units
        )
        await self._session.flush()
        self._session.add_all(
            RetrievalUnitCueRow(
                retrieval_unit_id=unit.unit_id,
                cue_id=cue_id,
                cue_order=order,
            )
            for unit in units
            for order, cue_id in enumerate(unit.cue_ids)
        )
        await self._session.commit()
        return self._video_record(row)

    async def get_video(self, video_id: UUID) -> VideoRecord | None:
        row = await self._session.get(VideoRow, video_id)
        return self._video_record(row) if row is not None else None

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None:
        video = await self.get_video(video_id)
        if video is None:
            return None
        cue_rows = tuple(
            (
                await self._session.scalars(
                    select(TranscriptCueRow)
                    .where(TranscriptCueRow.video_id == video_id)
                    .order_by(TranscriptCueRow.source_order)
                )
            ).all()
        )
        unit_rows = tuple(
            (
                await self._session.scalars(
                    select(RetrievalUnitRow)
                    .where(RetrievalUnitRow.video_id == video_id)
                    .order_by(RetrievalUnitRow.start_ms, RetrievalUnitRow.id)
                )
            ).all()
        )
        links = tuple(
            (
                await self._session.scalars(
                    select(RetrievalUnitCueRow)
                    .join(RetrievalUnitRow)
                    .where(RetrievalUnitRow.video_id == video_id)
                    .order_by(
                        RetrievalUnitCueRow.retrieval_unit_id,
                        RetrievalUnitCueRow.cue_order,
                    )
                )
            ).all()
        )
        cue_ids_by_unit: defaultdict[str, list[str]] = defaultdict(list)
        for link in links:
            cue_ids_by_unit[link.retrieval_unit_id].append(link.cue_id)
        cues = tuple(
            TranscriptCue(
                cue_id=row.id,
                source=video.metadata.reference,
                track_id=row.track_id,
                language_code=row.language_code,
                caption_kind=CaptionKind(row.caption_kind),
                source_order=row.source_order,
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                text=row.text,
            )
            for row in cue_rows
        )
        units = tuple(
            RetrievalUnit(
                unit_id=row.id,
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                text=row.text,
                cue_ids=tuple(cue_ids_by_unit[row.id]),
            )
            for row in unit_rows
        )
        return TranscriptRecord(video=video, cues=cues, units=units)

    @staticmethod
    def _video_record(row: VideoRow) -> VideoRecord:
        reference = SourceReference(
            kind=VideoSourceKind(row.source_kind),
            external_id=row.external_id,
            canonical_url=row.canonical_url,
        )
        return VideoRecord(
            video_id=row.id,
            metadata=SafeVideoMetadata(
                reference=reference,
                title=row.title,
                duration_ms=row.duration_ms,
                channel_name=row.channel_name,
                thumbnail_url=row.thumbnail_url,
            ),
        )
