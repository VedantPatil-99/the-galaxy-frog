"""Video-scoped cache storage, typed result validation, and serialized byte budgets."""

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession
from tests.unit.vision.test_visual_contracts import identity

from galaxy_frog.db.reasoning_cache import PostgresVisualReasoningCache, VisualReasoningCacheRow
from galaxy_frog.domain.vision.visual import VisualProviderError, VisualReasoningResult


@pytest.mark.asyncio
async def test_cache_stores_and_reads_typed_results_scoped_by_video() -> None:
    db = AsyncMock(spec=AsyncSession)
    cache = PostgresVisualReasoningCache(cast(AsyncSession, db))
    video = uuid4()
    answer = VisualReasoningResult("answer", (uuid4(),), identity(), 1)
    await cache.put(video, "a" * 64, answer)
    db.commit.assert_awaited_once()
    db.scalar.return_value = VisualReasoningCacheRow(
        payload=TypeAdapter(VisualReasoningResult).dump_python(answer, mode="json")
    )
    assert await cache.get(video, "a" * 64) == answer
    statement = str(db.scalar.call_args.args[0])
    assert "visual_reasoning_cache.video_id =" in statement
    db.scalar.return_value = None
    assert await cache.get(video, "b" * 64) is None
    db.scalar.return_value = VisualReasoningCacheRow(payload={"invalid": True})
    with pytest.raises(VisualProviderError):
        await cache.get(video, "a" * 64)
    with pytest.raises(VisualProviderError):
        await cache.put(
            video, "a" * 64, VisualReasoningResult("😀" * 8000, (uuid4(),), identity(), 1)
        )
