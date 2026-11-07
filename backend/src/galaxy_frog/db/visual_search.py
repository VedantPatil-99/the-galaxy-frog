"""Collection-safe pgvector baseline; bounded resumable indexing of persisted kept frames."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from galaxy_frog.adapters.vision.deduplication import compute_dhash_sync
from galaxy_frog.db.models import EmbeddingCollectionRow, VideoFrameRow
from galaxy_frog.db.visual_models import VisualEmbeddingRow
from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.vision.models import FrameSource, PerceptualHash
from galaxy_frog.domain.vision.visual import (
    VisualFailureCode,
    VisualFrame,
    VisualHit,
    VisualProviderError,
    validate_vector,
)
from galaxy_frog.domain.vision.visual_ports import VisualEmbeddingProvider


def _scope[*Ts](statement: Select[tuple[*Ts]], window: TimeWindow | None) -> Select[tuple[*Ts]]:
    if window is not None:
        statement = statement.where(VideoFrameRow.capture_ms >= window.start_ms)
        if window.end_ms is not None:
            statement = statement.where(VideoFrameRow.capture_ms < window.end_ms)
    return statement


def _frame(row: VideoFrameRow) -> VisualFrame:
    if row.dhash is None or row.content_sha256 is None:
        raise RetrievalIntegrityError("Indexed frame hashes are missing.")
    return VisualFrame(
        row.video_id,
        row.id,
        row.scene_id,
        row.sequence,
        row.start_ms,
        row.end_ms,
        PerceptualHash(row.dhash, "dhash", 64),
        row.content_sha256,
        FrameSource(row.source),
        Path(row.image_path),
        capture_ms=row.capture_ms,
    )


def _hash_frame(row: VideoFrameRow) -> tuple[str, str]:
    path = Path(row.image_path)
    if path.stat().st_size > 4_194_304:
        raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
    return sha256(path.read_bytes()).hexdigest(), row.dhash or compute_dhash_sync(path).hex_digest


class PgVectorVisualSearch:
    def __init__(self, session: AsyncSession, provider: VisualEmbeddingProvider) -> None:
        if provider.collection.dimension != 768:
            raise ValueError("baseline visual storage requires 768 dimensions")
        self._session = session
        self._provider = provider

    async def _collection(self, *, create: bool = False) -> UUID | None:
        spec = self._provider.collection
        values = {
            "provider": spec.provider,
            "model": spec.model,
            "revision": spec.revision,
            "dimension": spec.dimension,
            "normalization": spec.normalization,
        }
        if create:
            await self._session.execute(
                insert(EmbeddingCollectionRow)
                .values(id=uuid4(), created_at=datetime.now(UTC), **values)
                .on_conflict_do_nothing(constraint="uq_embedding_collection_identity")
            )
        return await self._session.scalar(select(EmbeddingCollectionRow.id).filter_by(**values))

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        collection = await self._collection(create=True)
        assert collection is not None
        cursor = -1
        while True:
            rows = tuple(
                (
                    await self._session.scalars(
                        select(VideoFrameRow)
                        .where(
                            VideoFrameRow.video_id == video_id,
                            VideoFrameRow.is_duplicate.is_(False),
                            VideoFrameRow.sequence > cursor,
                        )
                        .order_by(VideoFrameRow.sequence)
                        .limit(4)
                    )
                ).all()
            )
            if not rows:
                await self._session.commit()
                return collection
            if rows[-1].sequence >= 10000:
                raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
            pending: list[VisualFrame] = []
            for row in rows:
                digest, dhash = await asyncio.to_thread(_hash_frame, row)
                if row.content_sha256 is not None and row.content_sha256 != digest:
                    raise RetrievalIntegrityError(
                        "Stored frame content changed; explicit re-ingestion is required."
                    )
                row.content_sha256, row.dhash = digest, dhash
                existing = await self._session.get(VisualEmbeddingRow, (row.id, collection))
                if existing is None:
                    pending.append(_frame(row))
                elif existing.content_sha256 != digest:
                    raise RetrievalIntegrityError(
                        "Visual embedding content hash does not match its frame."
                    )
            if pending:
                embeddings = await self._provider.embed_frames(tuple(pending))
                if tuple(item.frame for item in embeddings) != tuple(pending) or any(
                    item.collection != self._provider.collection for item in embeddings
                ):
                    raise RetrievalIntegrityError(
                        "Visual provider changed source identities or collection."
                    )
                for item in embeddings:
                    await self._session.execute(
                        insert(VisualEmbeddingRow)
                        .values(
                            frame_id=item.frame.frame_id,
                            collection_id=collection,
                            content_sha256=item.frame.content_sha256,
                            embedding=list(item.values),
                        )
                        .on_conflict_do_nothing(index_elements=["frame_id", "collection_id"])
                    )
            # Each small committed batch is a restart checkpoint; never recompute saved vectors.
            await self._session.commit()
            cursor = rows[-1].sequence

    async def search(
        self, video_id: UUID, query: str, *, limit: int = 12, window: TimeWindow | None = None
    ) -> tuple[VisualHit, ...]:
        if not query.strip() or not 1 <= limit <= 30:
            raise ValueError("visual search requires bounded query and limit")
        if window is not None and window.start_ms == window.end_ms:
            return ()
        collection = await self._collection()
        if collection is None:
            raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
        vector = await self._provider.embed_query(query)
        validate_vector(vector, self._provider.collection)
        distance = VisualEmbeddingRow.embedding.cosine_distance(list(vector)).label("distance")
        statement = (
            _scope(
                select(VideoFrameRow, distance)
                .join(VisualEmbeddingRow, VisualEmbeddingRow.frame_id == VideoFrameRow.id)
                .where(
                    VideoFrameRow.video_id == video_id,
                    VideoFrameRow.is_duplicate.is_(False),
                    VisualEmbeddingRow.collection_id == collection,
                    VisualEmbeddingRow.content_sha256 == VideoFrameRow.content_sha256,
                ),
                window,
            )
            .order_by(distance, VideoFrameRow.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(
            VisualHit(
                _frame(row),
                self._provider.collection,
                max(-1.0, min(1.0, 1 - float(distance))),
                rank,
                collection_id=collection,
            )
            for rank, (row, distance) in enumerate(rows, 1)
        )

    async def nearby(
        self, video_id: UUID, seeds: tuple[VisualHit, ...], *, window: TimeWindow | None = None
    ) -> tuple[VisualFrame, ...]:
        if not seeds:
            return ()
        if any(hit.frame.video_id != video_id for hit in seeds):
            raise RetrievalIntegrityError("Nearby-frame seeds belong to another video.")
        # Only the best initial seed can open a bounded +/-15s expansion window.
        seed = seeds[0].frame
        if seed.capture_ms is None:
            return ()
        start, end = max(0, seed.capture_ms - 15000), seed.capture_ms + 15000
        statement = _scope(
            select(VideoFrameRow)
            .where(
                VideoFrameRow.video_id == video_id,
                VideoFrameRow.is_duplicate.is_(False),
                VideoFrameRow.capture_ms >= start,
                VideoFrameRow.capture_ms < end,
                VideoFrameRow.content_sha256.is_not(None),
                VideoFrameRow.dhash.is_not(None),
                VideoFrameRow.id.not_in([hit.frame.frame_id for hit in seeds]),
            )
            .order_by(VideoFrameRow.capture_ms, VideoFrameRow.id)
            .limit(4),
            window,
        )
        return tuple(_frame(row) for row in (await self._session.scalars(statement)).all())
