"""Shared retrieval behavior: deterministic fusion, scope, bounds, and explicit fallback."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.db.transcript_search import TranscriptIndexError
from galaxy_frog.domain.retrieval.errors import (
    EmbeddingProviderError,
    RetrievalIntegrityError,
    TemporalResolutionRequired,
)
from galaxy_frog.domain.retrieval.fusion import fuse_rankings
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.pipeline import (
    QueryKind,
    RetrievalConfig,
    RetrievalMode,
    RetrievalStage,
)
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.transcripts.models import RetrievalUnit


class StubRetriever:
    def __init__(
        self, results: tuple[RetrievedEvidence, ...] = (), error: Exception | None = None
    ) -> None:
        self.results = results
        self.error = error
        self.calls: list[tuple[UUID, str, int]] = []

    async def search(
        self, video_id: UUID, query: str, *, limit: int
    ) -> tuple[RetrievedEvidence, ...]:
        self.calls.append((video_id, query, limit))
        if self.error is not None:
            raise self.error
        return self.results


def item(video_id: UUID, number: int, score: float = 0.9) -> RetrievedEvidence:
    return RetrievedEvidence(
        video_id,
        RetrievalUnit(
            f"{number:064x}", number * 1000, (number + 1) * 1000, f"Passage {number}", ("a" * 64,)
        ),
        score,
    )


@pytest.mark.parametrize(
    ("query", "kind", "phrases", "lexical"),
    [
        ("  Explain\n  provenance ", QueryKind.SPOKEN, (), "Explain provenance"),
        ('Find "BGE M3"', QueryKind.EXACT, ("BGE M3",), '"BGE M3"'),
        ('“München” and "München"', QueryKind.EXACT, ("München",), '"München"'),
        ('"a" or "b"', QueryKind.EXACT, ("a", "b"), '"a" OR "b"'),
        ('"before"', QueryKind.EXACT, ("before",), '"before"'),
        ('" "', QueryKind.SPOKEN, (), '" "'),
        ('Find "unfinished', QueryKind.SPOKEN, (), 'Find "unfinished'),
        ("at 01:20", QueryKind.TEMPORAL, (), "at 01:20"),
        ("at 1:02:03", QueryKind.TEMPORAL, (), "at 1:02:03"),
        ("after 20 seconds", QueryKind.TEMPORAL, (), "after 20 seconds"),
        ("before the demo", QueryKind.TEMPORAL, (), "before the demo"),
    ],
)
def test_analysis_preserves_intent_without_provider_calls(
    query: str, kind: QueryKind, phrases: tuple[str, ...], lexical: str
) -> None:
    analysis = analyze_query(query)
    assert analysis == analyze_query(query)
    assert analysis.original == query
    assert analysis.kind == kind
    assert analysis.exact_phrases == phrases
    assert analysis.lexical_query == lexical


@pytest.mark.parametrize("query", [" ", "x" * 2001])
def test_query_is_bounded(query: str) -> None:
    with pytest.raises(ValueError, match="2000"):
        analyze_query(query)


@pytest.mark.parametrize(
    "values",
    [
        {"candidate_limit": 0},
        {"candidate_limit": 31},
        {"rrf_constant": 0},
        {"fusion_limit": 0},
        {"fusion_limit": 31},
    ],
)
def test_configuration_is_bounded(values: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        RetrievalConfig(**values)


def test_fusion_uses_ranks_and_keeps_every_source_score() -> None:
    video_id = uuid4()
    first, second, third = (item(video_id, number) for number in (1, 2, 3))
    lexical = RetrievalStage(RetrievalMode.LEXICAL, 1, (replace(first, score=0.01), second))
    dense = RetrievalStage(RetrievalMode.DENSE, 2, (third, replace(second, score=0.001)))
    ranked = fuse_rankings(video_id, (lexical, dense))
    assert [row.unit for row in ranked] == [second.unit, first.unit, third.unit]
    assert ranked[0].fusion_score == pytest.approx(2 / 62)
    assert [(rank.stage, rank.rank, rank.score) for rank in ranked[0].stages] == [
        (RetrievalMode.LEXICAL, 2, 0.9),
        (RetrievalMode.DENSE, 2, 0.001),
    ]
    assert [row.fusion_rank for row in ranked] == [1, 2, 3]
    assert ranked == fuse_rankings(video_id, (lexical, dense))
    assert [row.unit for row in fuse_rankings(video_id, (dense, lexical))] == [
        second.unit,
        first.unit,
        third.unit,
    ]


def test_duplicate_in_one_stage_does_not_boost_score() -> None:
    video_id = uuid4()
    first, second = item(video_id, 1), item(video_id, 2)
    result = fuse_rankings(
        video_id, (RetrievalStage(RetrievalMode.LEXICAL, 0, (first, first, second)),)
    )
    assert len(result) == 2
    assert result[0].fusion_score == 1 / 61
    assert result[1].stages[0].rank == 3


def test_fusion_rejects_foreign_or_conflicting_evidence_and_bad_constant() -> None:
    video_id = uuid4()
    first = item(video_id, 1)
    for invalid in (
        replace(first, video_id=uuid4()),
        replace(first, unit=replace(first.unit, cue_ids=("b" * 64,))),
    ):
        with pytest.raises(RetrievalIntegrityError):
            fuse_rankings(video_id, (RetrievalStage(RetrievalMode.LEXICAL, 0, (first, invalid)),))
    with pytest.raises(ValueError, match="positive"):
        fuse_rankings(video_id, (), constant=0)


@pytest.mark.asyncio
async def test_hybrid_caps_candidates_and_preserves_all_stage_records() -> None:
    video_id = uuid4()
    lexical = StubRetriever(tuple(item(video_id, number) for number in range(30)))
    dense = StubRetriever(tuple(item(video_id, number) for number in range(30, 60)))
    result = await RetrieveTranscript(lexical=lexical, dense=dense).execute(
        video_id, 'Find "exact term"', limit=4
    )
    assert lexical.calls == [(video_id, '"exact term"', 30)]
    assert dense.calls == [(video_id, 'Find "exact term"', 30)]
    assert len(result.rankings) == 60
    assert len(result.candidates) == 30
    assert result.evidence == result.candidates[:4]
    assert not result.degraded and result.warnings == ()
    assert result.elapsed_ms >= 0
    assert all(stage.elapsed_ms >= 0 for stage in result.stages)
    assert result.stages[0].results == lexical.results
    assert result.stages[1].results == dense.results


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(RetrievalMode))
async def test_modes_call_only_requested_stages(mode: RetrievalMode) -> None:
    lexical, dense = StubRetriever(), StubRetriever()
    result = await RetrieveTranscript(lexical=lexical, dense=dense).execute(
        uuid4(), "query", mode=mode
    )
    assert len(lexical.calls) == (mode != RetrievalMode.DENSE)
    assert len(dense.calls) == (mode != RetrievalMode.LEXICAL)
    assert result.evidence == ()
    assert result.mode == mode
    assert not result.degraded


@pytest.mark.asyncio
async def test_embedding_failure_retains_lexical_and_safe_observable_reason() -> None:
    video_id = uuid4()
    lexical = StubRetriever((item(video_id, 1),))
    dense = StubRetriever(error=EmbeddingProviderError("unsafe provider detail"))
    result = await RetrieveTranscript(lexical=lexical, dense=dense).execute(video_id, "query")
    assert result.degraded
    assert result.evidence[0].unit == lexical.results[0].unit
    assert result.warnings[0].code == "embedding_provider_unavailable"
    assert "unsafe" not in result.warnings[0].message
    assert result.stages[1].failure_code == result.warnings[0].code
    assert result.stages[1].results == ()


@pytest.mark.asyncio
async def test_explicit_config_controls_candidate_budget_and_fusion() -> None:
    video_id = uuid4()
    lexical = StubRetriever((item(video_id, 1), item(video_id, 2)))
    config = RetrievalConfig(candidate_limit=2, rrf_constant=10, fusion_limit=1)
    result = await RetrieveTranscript(
        lexical=lexical, dense=StubRetriever(), config=config
    ).execute(video_id, "query")
    assert lexical.calls[0][2] == 2
    assert len(result.rankings) == 2
    assert len(result.candidates) == len(result.evidence) == 1
    assert result.evidence[0].fusion_score == 1 / 11
    assert result.config == config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "allow_fallback"), [(RetrievalMode.HYBRID, False), (RetrievalMode.DENSE, True)]
)
async def test_strict_or_dense_only_requests_never_substitute_lexical(
    mode: RetrievalMode, allow_fallback: bool
) -> None:
    dense = StubRetriever(error=EmbeddingProviderError("unavailable"))
    with pytest.raises(EmbeddingProviderError):
        await RetrieveTranscript(lexical=StubRetriever(), dense=dense).execute(
            uuid4(), "query", mode=mode, allow_fallback=allow_fallback
        )


@pytest.mark.asyncio
async def test_collection_and_database_errors_are_not_degraded_provider_failures() -> None:
    for error in (TranscriptIndexError("incompatible collection"), RuntimeError("database")):
        dense = StubRetriever(error=error)
        with pytest.raises(type(error)):
            await RetrieveTranscript(lexical=StubRetriever(), dense=dense).execute(uuid4(), "query")
    with pytest.raises(RuntimeError, match="lexical"):
        await RetrieveTranscript(
            lexical=StubRetriever(error=RuntimeError("lexical")), dense=StubRetriever()
        ).execute(uuid4(), "query")


@pytest.mark.asyncio
async def test_excessive_candidates_are_rejected() -> None:
    video_id = uuid4()
    lexical = StubRetriever((item(video_id, 1),) * 31)
    with pytest.raises(RetrievalIntegrityError, match="candidate limit"):
        await RetrieveTranscript(lexical=lexical, dense=StubRetriever()).execute(video_id, "query")


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 9])
async def test_invalid_limits_do_not_invoke_retrievers(limit: int) -> None:
    lexical, dense = StubRetriever(), StubRetriever()
    with pytest.raises(ValueError, match="result limit"):
        await RetrieveTranscript(lexical=lexical, dense=dense).execute(
            uuid4(), "query", limit=limit
        )
    assert lexical.calls == dense.calls == []


@pytest.mark.asyncio
async def test_unresolved_temporal_query_never_becomes_unrestricted_search() -> None:
    lexical, dense = StubRetriever(), StubRetriever()
    with pytest.raises(TemporalResolutionRequired):
        await RetrieveTranscript(lexical=lexical, dense=dense).execute(uuid4(), "before the demo")
    assert lexical.calls == dense.calls == []
