"""PostgreSQL lexical and dense vector search over video OCR blocks."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import (
    EmbeddingCollectionRow,
    OCRBlockRow,
    OCREmbeddingRow,
    VideoFrameRow,
)
from galaxy_frog.domain.retrieval.errors import OCRIndexError
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.vision.models import (
    OCRBlock,
    OCRProviderSpec,
    RetrievedOCREvidence,
)


class PostgresOCRLexicalSearch:
    """Retrieve OCR blocks via PostgreSQL tsvector full-text search."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        video_id: UUID,
        query: str,
        *,
        limit: int = 30,
        window: TimeWindow | None = None,
    ) -> tuple[RetrievedOCREvidence, ...]:
        """Search video OCR text using websearch tsquery with rank normalization 32."""

        if not query.strip():
            msg = "query must not be empty"
            raise ValueError(msg)
        if not 1 <= limit <= 30:
            msg = "limit must be between 1 and 30"
            raise ValueError(msg)
        if window is not None and window.start_ms == window.end_ms:
            return ()

        terms = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank_cd(OCRBlockRow.search_vector, terms, 32).label("rank")
        statement = (
            select(OCRBlockRow, VideoFrameRow, rank)
            .join(VideoFrameRow, VideoFrameRow.id == OCRBlockRow.frame_id)
            .where(
                OCRBlockRow.video_id == video_id,
                OCRBlockRow.search_vector.bool_op("@@")(terms),
            )
            .order_by(rank.desc(), OCRBlockRow.id)
            .limit(limit)
        )
        if window is not None:
            statement = statement.where(OCRBlockRow.end_ms > window.start_ms)
            if window.end_ms is not None:
                statement = statement.where(OCRBlockRow.start_ms < window.end_ms)

        rows = (await self._session.execute(statement)).all()
        return tuple(
            RetrievedOCREvidence(
                video_id=video_id,
                block_id=block_row.id,
                frame_id=frame_row.id,
                block=OCRBlock(
                    text=block_row.text,
                    start_ms=block_row.start_ms,
                    end_ms=block_row.end_ms,
                    source_frame_sequence=frame_row.sequence,
                    provider_spec=OCRProviderSpec(
                        provider=block_row.provider,
                        provider_revision=block_row.provider_revision,
                    ),
                    confidence=block_row.confidence,
                    confidence_method=block_row.confidence_method,
                ),
                score=float(score_val),
                image_path=Path(frame_row.image_path),
            )
            for block_row, frame_row, score_val in rows
        )


class PgVectorOCRSearch:
    """Index and retrieve OCR text blocks using pgvector cosine similarity."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        provider: TextEmbeddingProvider,
    ) -> None:
        self._session = session
        self._provider = provider

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        """Ensure all OCR blocks for a video have embeddings in the current collection."""

        collection = await self._get_or_create_collection(self._provider.spec)
        blocks = tuple(
            (
                await self._session.scalars(
                    select(OCRBlockRow)
                    .where(OCRBlockRow.video_id == video_id)
                    .order_by(OCRBlockRow.start_ms, OCRBlockRow.id)
                )
            ).all()
        )
        if not blocks:
            return collection.id

        existing_ids = set(
            (
                await self._session.scalars(
                    select(OCREmbeddingRow.block_id).where(
                        OCREmbeddingRow.collection_id == collection.id,
                        OCREmbeddingRow.block_id.in_(tuple(b.id for b in blocks)),
                    )
                )
            ).all()
        )
        missing = tuple(b for b in blocks if b.id not in existing_ids)
        if missing:
            vectors = await self._provider.embed(tuple(b.text for b in missing))
            if len(vectors) != len(missing):
                msg = "The embedding provider returned an incomplete batch."
                raise OCRIndexError(msg)
            self._session.add_all(
                OCREmbeddingRow(
                    block_id=b.id,
                    collection_id=collection.id,
                    input_fingerprint=sha256(b.text.encode()).hexdigest(),
                    embedding=list(vector),
                )
                for b, vector in zip(missing, vectors, strict=True)
            )
            await self._session.commit()
        return collection.id

    async def is_indexed(self, video_id: UUID) -> bool:
        """Check whether all OCR blocks for a video have been embedded."""

        collection = await self._find_collection(self._provider.spec)
        if collection is None:
            return False
        total_blocks = await self._session.scalar(
            select(func.count()).select_from(OCRBlockRow).where(OCRBlockRow.video_id == video_id)
        )
        if not total_blocks:
            return False
        embedded_count = await self._session.scalar(
            select(func.count())
            .select_from(OCREmbeddingRow)
            .join(OCRBlockRow, OCRBlockRow.id == OCREmbeddingRow.block_id)
            .where(
                OCRBlockRow.video_id == video_id,
                OCREmbeddingRow.collection_id == collection.id,
            )
        )
        return embedded_count == total_blocks

    async def search(
        self,
        video_id: UUID,
        query: str,
        *,
        limit: int = 8,
        window: TimeWindow | None = None,
    ) -> tuple[RetrievedOCREvidence, ...]:
        """Search video OCR blocks by embedding cosine similarity."""

        if not query.strip():
            msg = "query must not be empty"
            raise ValueError(msg)
        if not 1 <= limit <= 30:
            msg = "limit must be between 1 and 30"
            raise ValueError(msg)
        if window is not None and window.start_ms == window.end_ms:
            return ()

        collection_id = await self.ensure_indexed(video_id)
        vectors = await self._provider.embed((query,))
        query_vector = list(vectors[0])
        distance = OCREmbeddingRow.embedding.cosine_distance(query_vector).label("distance")
        statement = (
            select(OCRBlockRow, VideoFrameRow, distance)
            .join(OCREmbeddingRow, OCREmbeddingRow.block_id == OCRBlockRow.id)
            .join(VideoFrameRow, VideoFrameRow.id == OCRBlockRow.frame_id)
            .where(
                OCRBlockRow.video_id == video_id,
                OCREmbeddingRow.collection_id == collection_id,
            )
            .order_by(distance, OCRBlockRow.id)
            .limit(limit)
        )
        if window is not None:
            statement = statement.where(OCRBlockRow.end_ms > window.start_ms)
            if window.end_ms is not None:
                statement = statement.where(OCRBlockRow.start_ms < window.end_ms)

        rows = (await self._session.execute(statement)).all()
        return tuple(
            RetrievedOCREvidence(
                video_id=video_id,
                block_id=block_row.id,
                frame_id=frame_row.id,
                block=OCRBlock(
                    text=block_row.text,
                    start_ms=block_row.start_ms,
                    end_ms=block_row.end_ms,
                    source_frame_sequence=frame_row.sequence,
                    provider_spec=OCRProviderSpec(
                        provider=block_row.provider,
                        provider_revision=block_row.provider_revision,
                    ),
                    confidence=block_row.confidence,
                    confidence_method=block_row.confidence_method,
                ),
                score=max(0.0, min(1.0, 1.0 - float(distance_val))),
                image_path=Path(frame_row.image_path),
            )
            for block_row, frame_row, distance_val in rows
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
