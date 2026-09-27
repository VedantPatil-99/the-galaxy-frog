"""Lexical retrieval preserves ranking, safe binding, and ordered cue lineage."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from galaxy_frog.db.models import RetrievalUnitCueRow, RetrievalUnitRow
from galaxy_frog.db.transcript_lexical_search import PostgresTranscriptLexicalSearch
from galaxy_frog.domain.retrieval.ports import TranscriptRetriever
from galaxy_frog.domain.retrieval.temporal import TimeWindow


@pytest.mark.parametrize(("query", "limit"), [(" ", 8), ("word", 0), ("word", 31)])
@pytest.mark.asyncio
async def test_invalid_request_does_not_query_database(query: str, limit: int) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError):
        await PostgresTranscriptLexicalSearch(session).search(uuid4(), query, limit=limit)
    session.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [None, TimeWindow(0), TimeWindow(100, 900)])
async def test_no_match_never_loads_cues(window: TimeWindow | None) -> None:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.all.return_value = []
    session.execute.return_value = result
    assert (
        await PostgresTranscriptLexicalSearch(session).search(uuid4(), "missing", window=window)
        == ()
    )
    session.scalars.assert_not_called()
    sql = str(session.execute.call_args.args[0])
    if window is not None:
        assert "retrieval_units.end_ms >" in sql
        assert sql.index("retrieval_units.end_ms >") < sql.index("LIMIT")
        assert ("retrieval_units.start_ms <" in sql) == (window.end_ms is not None)


@pytest.mark.asyncio
async def test_empty_window_does_not_query() -> None:
    session = AsyncMock(spec=AsyncSession)
    assert (
        await PostgresTranscriptLexicalSearch(session).search(
            uuid4(), "query", window=TimeWindow(0, 0)
        )
        == ()
    )
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_ranked_units_keep_intervals_and_ordered_cues() -> None:
    video_id = uuid4()
    first = RetrievalUnitRow(
        id="a" * 64, video_id=video_id, start_ms=1200, end_ms=4300, text="Exact phrase."
    )
    second = RetrievalUnitRow(
        id="b" * 64, video_id=video_id, start_ms=6000, end_ms=7300, text="Other mention."
    )
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = MagicMock(all=lambda: [(first, 0.2), (second, 0.1)])
    links = [
        RetrievalUnitCueRow(retrieval_unit_id=first.id, cue_id="c" * 64, cue_order=0),
        RetrievalUnitCueRow(retrieval_unit_id=first.id, cue_id="d" * 64, cue_order=1),
        RetrievalUnitCueRow(retrieval_unit_id=second.id, cue_id="e" * 64, cue_order=0),
    ]
    session.scalars.return_value = MagicMock(all=lambda: links)
    adapter: TranscriptRetriever = PostgresTranscriptLexicalSearch(session)
    query = '"exact phrase" OR O\'Reilly; DROP TABLE videos;'
    results = await adapter.search(video_id, query, limit=2)

    assert [result.video_id for result in results] == [video_id, video_id]
    assert [result.score for result in results] == [0.2, 0.1]
    assert results[0].unit.cue_ids == ("c" * 64, "d" * 64)
    assert results[1].unit.cue_ids == ("e" * 64,)
    assert (results[0].unit.start_ms, results[0].unit.end_ms) == (1200, 4300)
    assert results[0].unit.text == first.text
    statement = cast(Select[tuple[RetrievalUnitRow, float]], session.execute.call_args.args[0])
    compiled = statement.compile(dialect=postgresql.dialect())
    assert query not in str(compiled)
    assert compiled.params is not None
    assert query in compiled.params.values()
    assert video_id in compiled.params.values()
    assert "ORDER BY rank DESC, retrieval_units.id" in str(compiled)
    assert "@@" in str(compiled)
    cue_statement = str(session.scalars.call_args.args[0])
    assert (
        "ORDER BY retrieval_unit_cues.retrieval_unit_id, retrieval_unit_cues.cue_order"
        in cue_statement
    )
    session.commit.assert_not_called()
