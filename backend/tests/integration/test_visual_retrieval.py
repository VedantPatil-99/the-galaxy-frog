"""Real pgvector visual retrieval; deterministic fake vectors, no model quality claim."""

import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.test_postgres_lexical_search import (
    _seed,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.db.test_visual_search import Provider, row

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import EmbeddingCollectionRow, VideoRow
from galaxy_frog.db.visual_models import VisualEmbeddingRow
from galaxy_frog.db.visual_search import PgVectorVisualSearch
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.vision.visual import (
    VisualEmbedding,
    VisualFailureCode,
    VisualFrame,
    VisualProviderError,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires existing PostgreSQL/pgvector"
)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_visual_index_restart_boundaries_and_video_isolation(tmp_path: Path) -> None:
    engine = create_database_engine(Settings())

    class Interrupted(Provider):
        fail_once = True

        async def embed_frames(
            self, frames: tuple[VisualFrame, ...]
        ) -> tuple[VisualEmbedding, ...]:
            if self.calls == 1 and self.fail_once:
                self.fail_once = False
                raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
            return await super().embed_frames(frames)

    provider = Interrupted()
    provider.collection = type(provider.collection)(
        "integration-" + uuid4().hex, "fixture", "v1", 768, "l2"
    )
    videos: list[UUID] = []
    collection = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            for _ in range(2):
                video, _ = await _seed(db, ("Transcript fixture",))
                videos.append(video)
                frames = [row(tmp_path, video, index) for index in range(6)]
                for frame in frames:
                    frame.scene_id = None
                frames[-1].is_duplicate = True
                db.add_all(frames)
                await db.commit()
                if len(videos) == 1:
                    with pytest.raises(VisualProviderError):
                        await PgVectorVisualSearch(db, provider).ensure_indexed(video)
                    await db.rollback()
                collection = await PgVectorVisualSearch(db, provider).ensure_indexed(video)
            calls = provider.calls
            await PgVectorVisualSearch(db, provider).ensure_indexed(videos[0])
            assert calls == provider.calls
            count = len(
                (
                    await db.scalars(
                        select(VisualEmbeddingRow).where(
                            VisualEmbeddingRow.collection_id == collection
                        )
                    )
                ).all()
            )
            assert count == 10
        async with AsyncSession(engine, expire_on_commit=False) as db:
            search = PgVectorVisualSearch(db, provider)
            hits = await search.search(videos[0], "diagram", window=TimeWindow(1000, 2000))
            assert len(hits) == 1 and hits[0].frame.start_ms == 1000
            assert hits[0].frame.video_id == videos[0]
            assert await search.search(videos[0], "diagram", window=TimeWindow(5000, 6000)) == ()
            assert all(f.video_id == videos[0] for f in await search.nearby(videos[0], hits))
            provider.collection = replace(provider.collection, revision="incompatible-v2")
            with pytest.raises(VisualProviderError):
                await search.search(videos[0], "diagram")
    finally:
        async with AsyncSession(engine) as db:
            await db.execute(delete(VideoRow).where(VideoRow.id.in_(videos)))
            if collection is not None:
                await db.execute(
                    delete(EmbeddingCollectionRow).where(EmbeddingCollectionRow.id == collection)
                )
            await db.commit()
        await engine.dispose()
