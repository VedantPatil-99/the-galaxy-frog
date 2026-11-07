"""Read study inputs without invoking retrieval, embedding, or vision providers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.study_generation import StudyTimeline
from galaxy_frog.db.models import (
    EmbeddingCollectionRow,
    RetrievalUnitRow,
    SceneRow,
    TextEmbeddingRow,
)
from galaxy_frog.db.moment_evidence import SqlAlchemyMomentEvidenceReader
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.study import MAX_VIDEO_MS, StudyCitation, StudyError, StudySource
from galaxy_frog.domain.study_chapters import TimedVector


class StudyEvidenceReader:
    def __init__(self, session: AsyncSession, spec: EmbeddingCollectionSpec) -> None:
        self._session = session
        self._spec = spec
        self._reader = SqlAlchemyMomentEvidenceReader(session)

    async def read(self, video_id: UUID, *, chaptering: bool) -> StudyTimeline:
        video = await self._reader.get_video(video_id)
        if video is None:
            raise StudyError("VIDEO_NOT_FOUND", "The requested video does not exist.", status=404)
        metadata = video.metadata
        duration = metadata.duration_ms
        if duration > MAX_VIDEO_MS:
            raise StudyError(
                "STUDY_VIDEO_TOO_LONG", "Study generation supports videos up to two hours."
            )
        source = StudySource(
            video_id=video_id,
            kind=metadata.reference.kind,
            external_id=metadata.reference.external_id,
            canonical_url=metadata.reference.canonical_url,
            title=metadata.title,
            duration_ms=duration,
        )
        cues = await self._reader.get_cues(video, 0, duration)
        blocks = await self._reader.get_ocr(video_id, 0, duration)
        evidence = [
            StudyCitation(
                source_id=f"cue:{cue.cue_id}",
                modality="transcript",
                quote=cue.text,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                seek_ms=cue.start_ms,
                timestamp_url=None,
                cue_id=cue.cue_id,
                unit_ids=item.unit_ids,
                origin=cue.origin,
                language_code=cue.language_code,
                caption_kind=cue.caption_kind,
                track_id=cue.track_id,
                transcription_run_id=cue.transcription_run_id,
                confidence=cue.confidence,
                confidence_method=cue.confidence_method,
            )
            for item in cues
            for cue in (item.cue,)
        ]
        evidence.extend(
            StudyCitation(
                source_id=f"ocr:{item.block_id}",
                modality="ocr",
                quote=item.block.text,
                start_ms=item.block.start_ms,
                end_ms=item.block.end_ms,
                seek_ms=item.capture_ms,
                timestamp_url=None,
                ocr_block_id=item.block_id,
                frame_id=item.frame_id,
                capture_ms=item.capture_ms,
                confidence=item.block.confidence,
                confidence_method=item.block.confidence_method,
                ocr_provider=item.block.provider_spec.provider,
                ocr_revision=item.block.provider_spec.provider_revision,
            )
            for item in blocks
            if item.block.text.strip()
        )
        vectors, scenes = (), ()
        if chaptering:
            vectors = await self._vectors(video_id, duration)
            rows = await self._session.execute(
                select(SceneRow.start_ms, SceneRow.end_ms)
                .where(SceneRow.video_id == video_id, SceneRow.start_ms < duration)
                .order_by(SceneRow.start_ms, SceneRow.scene_order)
            )
            scenes = tuple((start, min(end, duration)) for start, end in rows.tuples())
        return StudyTimeline(
            source,
            tuple(sorted(evidence, key=lambda item: (item.seek_ms, item.source_id))),
            vectors,
            scenes,
        )

    async def _vectors(self, video_id: UUID, duration: int) -> tuple[TimedVector, ...]:
        spec = self._spec
        rows = await self._session.execute(
            select(RetrievalUnitRow.start_ms, TextEmbeddingRow.embedding)
            .join(
                TextEmbeddingRow,
                TextEmbeddingRow.retrieval_unit_id == RetrievalUnitRow.id,
            )
            .join(
                EmbeddingCollectionRow, EmbeddingCollectionRow.id == TextEmbeddingRow.collection_id
            )
            .where(
                RetrievalUnitRow.video_id == video_id,
                RetrievalUnitRow.start_ms < duration,
                EmbeddingCollectionRow.provider == spec.provider,
                EmbeddingCollectionRow.model == spec.model,
                EmbeddingCollectionRow.revision == spec.revision,
                EmbeddingCollectionRow.dimension == spec.dimension,
                EmbeddingCollectionRow.normalization == spec.normalization,
            )
            .order_by(RetrievalUnitRow.start_ms, RetrievalUnitRow.id)
        )
        return tuple(TimedVector(start, tuple(vector)) for start, vector in rows.tuples())
