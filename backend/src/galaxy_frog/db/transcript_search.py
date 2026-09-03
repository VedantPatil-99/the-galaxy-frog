"""Version-safe pgvector indexing and video-scoped dense retrieval."""

from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import (
    EmbeddingCollectionRow,
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    TextEmbeddingRow,
)
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.transcripts.models import RetrievalUnit


class TranscriptIndexError(RuntimeError):
    """Raised when indexing or search would violate collection compatibility."""


class PgVectorTranscriptSearch:
    """Index and retrieve transcript units within one declared embedding collection."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        videos: SqlAlchemyVideoRepository,
        provider: TextEmbeddingProvider,
    ) -> None:
        self._session = session
        self._videos = videos
        self._provider = provider

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        transcript = await self._videos.get_transcript(video_id)
        if transcript is None:
            raise TranscriptIndexError("The requested video does not exist.")
        collection = await self._get_or_create_collection(self._provider.spec)
        existing = set(
            (
                await self._session.scalars(
                    select(TextEmbeddingRow.retrieval_unit_id).where(
                        TextEmbeddingRow.collection_id == collection.id,
                        TextEmbeddingRow.retrieval_unit_id.in_(
                            tuple(unit.unit_id for unit in transcript.units)
                        ),
                    )
                )
            ).all()
        )
        missing = tuple(unit for unit in transcript.units if unit.unit_id not in existing)
        if missing:
            vectors = await self._provider.embed(tuple(unit.text for unit in missing))
            if len(vectors) != len(missing):
                raise TranscriptIndexError("The embedding provider returned an incomplete batch.")
            self._session.add_all(
                TextEmbeddingRow(
                    retrieval_unit_id=unit.unit_id,
                    collection_id=collection.id,
                    input_fingerprint=sha256(unit.text.encode()).hexdigest(),
                    embedding=list(vector),
                )
                for unit, vector in zip(missing, vectors, strict=True)
            )
            await self._session.commit()
        return collection.id

    async def is_indexed(self, video_id: UUID) -> bool:
        """Report readiness only for the provider collection used by this search adapter."""

        transcript = await self._videos.get_transcript(video_id)
        if transcript is None or not transcript.units:
            return False
        collection = await self._find_collection(self._provider.spec)
        if collection is None:
            return False
        embedded_count = await self._session.scalar(
            select(func.count())
            .select_from(TextEmbeddingRow)
            .join(
                RetrievalUnitRow,
                RetrievalUnitRow.id == TextEmbeddingRow.retrieval_unit_id,
            )
            .where(
                RetrievalUnitRow.video_id == video_id,
                TextEmbeddingRow.collection_id == collection.id,
            )
        )
        return embedded_count == len(transcript.units)

    async def search(
        self,
        video_id: UUID,
        query: str,
        *,
        limit: int = 8,
    ) -> tuple[RetrievedEvidence, ...]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 30:
            raise ValueError("limit must be between 1 and 30")
        collection_id = await self.ensure_indexed(video_id)
        vectors = await self._provider.embed((query,))
        query_vector = list(vectors[0])
        distance = TextEmbeddingRow.embedding.cosine_distance(query_vector).label("distance")
        statement = (
            select(RetrievalUnitRow, distance)
            .join(
                TextEmbeddingRow,
                TextEmbeddingRow.retrieval_unit_id == RetrievalUnitRow.id,
            )
            .where(
                RetrievalUnitRow.video_id == video_id,
                TextEmbeddingRow.collection_id == collection_id,
            )
            .order_by(distance, RetrievalUnitRow.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        unit_ids = tuple(row[0].id for row in rows)
        cue_ids: defaultdict[str, list[str]] = defaultdict(list)
        if unit_ids:
            links = (
                await self._session.scalars(
                    select(RetrievalUnitCueRow)
                    .where(RetrievalUnitCueRow.retrieval_unit_id.in_(unit_ids))
                    .order_by(
                        RetrievalUnitCueRow.retrieval_unit_id,
                        RetrievalUnitCueRow.cue_order,
                    )
                )
            ).all()
            for link in links:
                cue_ids[link.retrieval_unit_id].append(link.cue_id)
        return tuple(
            RetrievedEvidence(
                video_id=video_id,
                unit=RetrievalUnit(
                    unit_id=row.id,
                    start_ms=row.start_ms,
                    end_ms=row.end_ms,
                    text=row.text,
                    cue_ids=tuple(cue_ids[row.id]),
                ),
                score=max(0.0, min(1.0, 1.0 - float(distance_value))),
            )
            for row, distance_value in rows
        )

    async def _get_or_create_collection(
        self, spec: EmbeddingCollectionSpec
    ) -> EmbeddingCollectionRow:
        row = await self._find_collection(spec)
        if row is not None:
            return row
        row = EmbeddingCollectionRow(
            id=uuid4(),
            provider=spec.provider,
            model=spec.model,
            revision=spec.revision,
            dimension=spec.dimension,
            normalization=spec.normalization,
            created_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _find_collection(
        self, spec: EmbeddingCollectionSpec
    ) -> EmbeddingCollectionRow | None:
        return await self._session.scalar(
            select(EmbeddingCollectionRow).where(
                EmbeddingCollectionRow.provider == spec.provider,
                EmbeddingCollectionRow.model == spec.model,
                EmbeddingCollectionRow.revision == spec.revision,
                EmbeddingCollectionRow.dimension == spec.dimension,
                EmbeddingCollectionRow.normalization == spec.normalization,
            )
        )
