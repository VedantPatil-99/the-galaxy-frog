"""Async SQLAlchemy repository for videos and transcript provenance."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.ingestion_artifacts import PostgresTranscriptionCheckpointRepository
from galaxy_frog.db.models import (
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    TranscriptCueRow,
    TranscriptionRunRow,
    VideoRow,
)
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue, TranscriptOrigin
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
        *,
        transcription_run_id: UUID | None = None,
    ) -> VideoRecord:
        existing = await self.find_by_source(metadata.reference)
        if existing is not None:
            return existing
        cue_run_ids = {cue.transcription_run_id for cue in cues if cue.transcription_run_id}
        if cue_run_ids != ({transcription_run_id} if transcription_run_id else set()):
            raise ValueError("transcript cues and transcription run must have matching provenance")
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
                origin=cue.origin,
                track_id=cue.track_id,
                language_code=cue.language_code,
                caption_kind=cue.caption_kind,
                transcription_run_id=cue.transcription_run_id,
                confidence=cue.confidence,
                confidence_method=cue.confidence_method,
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
                language_code=row.language_code,
                source_order=row.source_order,
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                text=row.text,
                origin=TranscriptOrigin(row.origin),
                track_id=row.track_id,
                caption_kind=(CaptionKind(row.caption_kind) if row.caption_kind else None),
                transcription_run_id=row.transcription_run_id,
                confidence=row.confidence,
                confidence_method=row.confidence_method,
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
        transcription_run_ids = {
            cue.transcription_run_id for cue in cues if cue.transcription_run_id is not None
        }
        if len(transcription_run_ids) > 1:
            raise ValueError("one persisted transcript cannot mix transcription runs")
        transcription = None
        if transcription_run_ids:
            run_id = next(iter(transcription_run_ids))
            run_row = await self._session.get(TranscriptionRunRow, run_id)
            if run_row is None:
                raise ValueError("the transcript references a missing transcription run")
            transcription = await PostgresTranscriptionCheckpointRepository(self._session).get(
                run_row.job_id
            )
            if transcription is None or transcription.run_id != run_id:
                raise ValueError("the transcript transcription run could not be restored")
        return TranscriptRecord(
            video=video,
            cues=cues,
            units=units,
            transcription=transcription,
        )

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
