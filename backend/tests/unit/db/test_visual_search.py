"""Collection, provenance, restart, and SQL bounds for the visual index."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from tests.unit.vision.test_visual_contracts import identity

from galaxy_frog.application.retrieval.visual_ports import VisualIndexer, VisualSearch
from galaxy_frog.db.models import VideoFrameRow
from galaxy_frog.db.visual_models import VisualEmbeddingRow
from galaxy_frog.db.visual_search import PgVectorVisualSearch
from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.vision.visual import (
    VisualEmbedding,
    VisualFrame,
    VisualProviderError,
)


class Provider:
    identity = identity()
    collection = EmbeddingCollectionSpec("fake-visual", "model", "revision", 768, "l2")

    def __init__(self) -> None:
        self.calls = 0
        self.invalid = False

    async def embed_query(self, query: str) -> tuple[float, ...]:
        return (1.0, *([0.0] * 767))

    async def embed_frames(self, frames: tuple[VisualFrame, ...]) -> tuple[VisualEmbedding, ...]:
        self.calls += 1
        vector = await self.embed_query("")
        return (
            tuple(VisualEmbedding(f, self.collection, vector) for f in frames)
            if not self.invalid
            else ()
        )


def row(tmp_path: Path, video: UUID | None = None, sequence: int = 0) -> VideoFrameRow:
    path = tmp_path / f"{sequence}.jpg"
    path.write_bytes(str(sequence).encode())
    return VideoFrameRow(
        id=uuid4(),
        video_id=video or uuid4(),
        scene_id=uuid4(),
        sequence=sequence,
        start_ms=sequence * 1000,
        end_ms=(sequence + 1) * 1000,
        capture_ms=sequence * 1000,
        source="periodic",
        image_path=str(path),
        dhash="0" * 16,
        content_sha256=None,
        is_duplicate=False,
        created_at=datetime.now(UTC),
    )


def session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.mark.asyncio
async def test_incremental_index_reuses_saved_vectors(tmp_path: Path) -> None:
    assert VisualIndexer and VisualSearch
    db, provider = session(), Provider()
    original = row(tmp_path)
    db.scalar.return_value = uuid4()
    db.scalars.side_effect = [Mock(all=lambda: [original]), Mock(all=Mock(return_value=[]))]
    db.get.return_value = None
    adapter = PgVectorVisualSearch(cast(AsyncSession, db), provider)
    collection = await adapter.ensure_indexed(original.video_id)
    assert provider.calls == 1 and original.content_sha256
    saved = VisualEmbeddingRow(content_sha256=original.content_sha256)
    db.get.return_value = saved
    db.scalars.side_effect = [Mock(all=lambda: [original]), Mock(all=Mock(return_value=[]))]
    assert await adapter.ensure_indexed(original.video_id) == collection
    assert provider.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["budget", "changed", "embedding", "output", "hash"])
async def test_index_integrity_failures(tmp_path: Path, kind: str) -> None:
    db, provider = session(), Provider()
    original = row(tmp_path, sequence=10000 if kind == "budget" else 0)
    db.scalar.return_value = uuid4()
    db.scalars.return_value = Mock(all=lambda: [original])
    db.get.return_value = None
    if kind == "changed":
        original.content_sha256 = "b" * 64
    if kind == "embedding":
        db.get.return_value = VisualEmbeddingRow(content_sha256="b" * 64)
    if kind == "output":
        provider.invalid = True
    if kind == "hash":
        original.dhash = None
    adapter = PgVectorVisualSearch(cast(AsyncSession, db), provider)
    if kind == "hash":
        with patch(
            "galaxy_frog.db.visual_search.compute_dhash_sync",
            return_value=Mock(hex_digest="0" * 16),
        ):
            db.scalars.side_effect = [Mock(all=lambda: [original]), Mock(all=Mock(return_value=[]))]
            await adapter.ensure_indexed(original.video_id)
        return
    with pytest.raises(VisualProviderError if kind == "budget" else RetrievalIntegrityError):
        await adapter.ensure_indexed(original.video_id)


@pytest.mark.asyncio
async def test_index_rejects_oversized_image(tmp_path: Path) -> None:
    db = session()
    original = row(tmp_path)
    await asyncio.to_thread(Path(original.image_path).write_bytes, b"x" * 4_194_305)
    db.scalar.return_value = uuid4()
    db.scalars.return_value = Mock(all=lambda: [original])
    with pytest.raises(VisualProviderError):
        await PgVectorVisualSearch(cast(AsyncSession, db), Provider()).ensure_indexed(
            original.video_id
        )


@pytest.mark.asyncio
async def test_search_scope_and_nearby_preserve_ids(tmp_path: Path) -> None:
    db, provider = session(), Provider()
    original = row(tmp_path)
    original.content_sha256 = "a" * 64
    db.scalar.return_value = uuid4()
    db.execute.return_value = Mock(all=lambda: [(original, 0.25)])
    adapter = PgVectorVisualSearch(cast(AsyncSession, db), provider)
    hits = await adapter.search(original.video_id, "diagram", window=TimeWindow(0, 1000))
    assert hits[0].score == 0.75 and hits[0].frame.frame_id == original.id
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "video_frames.video_id =" in sql and "visual_embeddings.collection_id =" in sql
    assert "video_frames.capture_ms <" in sql and "video_frames.capture_ms >=" in sql
    db.scalars.return_value = Mock(all=lambda: [original])
    assert (await adapter.nearby(original.video_id, hits, window=TimeWindow(0)))[
        0
    ].frame_id == original.id
    assert await adapter.nearby(original.video_id, ()) == ()
    assert (
        await adapter.nearby(
            original.video_id, (replace(hits[0], frame=replace(hits[0].frame, capture_ms=None)),)
        )
        == ()
    )
    with pytest.raises(RetrievalIntegrityError):
        await adapter.nearby(uuid4(), hits)
    original.content_sha256 = None
    with pytest.raises(RetrievalIntegrityError):
        await adapter.search(original.video_id, "diagram")


@pytest.mark.asyncio
async def test_search_missing_collection_and_invalid_arguments() -> None:
    db, provider = session(), Provider()
    db.scalar.return_value = None
    adapter = PgVectorVisualSearch(cast(AsyncSession, db), provider)
    with pytest.raises(VisualProviderError):
        await adapter.search(uuid4(), "diagram")
    for query, limit in (("", 1), ("diagram", 31)):
        with pytest.raises(ValueError):
            await adapter.search(uuid4(), query, limit=limit)
    assert await adapter.search(uuid4(), "diagram", window=TimeWindow(0, 0)) == ()
    provider.collection = replace(provider.collection, dimension=2)
    with pytest.raises(ValueError):
        PgVectorVisualSearch(cast(AsyncSession, db), provider)
