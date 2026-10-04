"""Durable trace bounds, immutable identities, and video-scoped reads."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import RetrievalTraceRow
from galaxy_frog.db.retrieval_traces import PostgresRetrievalTraces
from galaxy_frog.domain.retrieval.traces import MAX_TRACE_BYTES, RetrievalTrace, TraceLimitExceeded


def test_trace_rejects_oversize_unknown_version_naive_time_and_nonfinite_scores() -> None:
    for payload in ({"data": "x" * MAX_TRACE_BYTES}, {"unicode": "猫" * MAX_TRACE_BYTES}):
        with pytest.raises(TraceLimitExceeded):
            RetrievalTrace(
                uuid4(), uuid4(), datetime.now(UTC), {key: value for key, value in payload.items()}
            )
    with pytest.raises(ValueError, match="version"):
        RetrievalTrace(uuid4(), uuid4(), datetime.now(UTC), {}, 2)
    with pytest.raises(ValueError, match="timezone"):
        RetrievalTrace(uuid4(), uuid4(), datetime(2026, 9, 27), {})
    with pytest.raises(ValueError):
        RetrievalTrace(uuid4(), uuid4(), datetime.now(UTC), {"score": float("nan")})


@pytest.mark.asyncio
async def test_save_and_read_preserve_payload_and_scope() -> None:
    trace = RetrievalTrace(uuid4(), uuid4(), datetime.now(UTC), {"query": "नाम", "ranks": [1, 2]})
    session = AsyncMock(spec=AsyncSession)
    store = PostgresRetrievalTraces(session)
    await store.save(trace)
    row = cast(RetrievalTraceRow, session.add.call_args.args[0])
    assert row.id == trace.trace_id and row.video_id == trace.video_id
    session.commit.assert_awaited_once()
    session.scalar.return_value = row
    assert await store.get(trace.video_id, trace.trace_id) == trace
    statement = session.scalar.call_args.args[0]
    compiled = statement.compile()
    assert trace.video_id in compiled.params.values() and trace.trace_id in compiled.params.values()
    session.scalar.return_value = None
    assert await store.get(uuid4(), trace.trace_id) is None
