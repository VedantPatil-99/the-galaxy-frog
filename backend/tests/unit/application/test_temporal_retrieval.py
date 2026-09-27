"""Temporal intent, anchor confirmation, expansion bounds, and original lineage."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.retrieval.temporal import (
    InvalidAnchor,
    RetrievalStatus,
    RetrievalVideoNotFound,
    RetrieveTemporalEvidence,
)
from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.domain.retrieval.errors import (
    EmbeddingProviderError,
    RetrievalIntegrityError,
    TemporalResolutionRequired,
)
from galaxy_frog.domain.retrieval.evidence import ExpansionConfig, expand_evidence
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.pipeline import RankedEvidence, RetrievalMode
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.retrieval.temporal import (
    TemporalRelation,
    TimeWindow,
    parse_temporal,
)
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue, TranscriptOrigin
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord


def transcript(*passages: tuple[int, int, str]) -> TranscriptRecord:
    source = SourceReference(
        VideoSourceKind.YOUTUBE, "abcdefghijk", "https://youtube.com/watch?v=abcdefghijk"
    )
    cues = tuple(
        TranscriptCue(
            f"{index:064x}",
            source,
            "en",
            index,
            start,
            end,
            text,
            track_id="en",
            caption_kind=CaptionKind.MANUAL,
        )
        for index, (start, end, text) in enumerate(passages, 1)
    )
    units = tuple(
        RetrievalUnit(f"{index + 100:064x}", cue.start_ms, cue.end_ms, cue.text, (cue.cue_id,))
        for index, cue in enumerate(cues)
    )
    return TranscriptRecord(
        VideoRecord(
            uuid4(),
            SafeVideoMetadata(source, "Fixture", max((cue.end_ms for cue in cues), default=1)),
        ),
        cues,
        units,
    )


def hits(record: TranscriptRecord, *indexes: int) -> tuple[RankedEvidence, ...]:
    return tuple(
        RankedEvidence(record.video.video_id, record.units[index], (), rank, 1 / (60 + rank))
        for rank, index in enumerate(indexes, 1)
    )


class MemoryTranscript:
    def __init__(self, record: TranscriptRecord | None) -> None:
        self.record = record

    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None:
        return self.record


class FilteredRetriever:
    def __init__(self, record: TranscriptRecord, *, fail: bool = False) -> None:
        self.record = record
        self.fail = fail
        self.calls: list[tuple[str, TimeWindow | None]] = []

    async def search(
        self, video_id: UUID, query: str, *, limit: int, window: TimeWindow | None = None
    ) -> tuple[RetrievedEvidence, ...]:
        self.calls.append((query, window))
        if self.fail:
            raise EmbeddingProviderError("unavailable")
        return tuple(
            RetrievedEvidence(video_id, unit, 0.9)
            for unit in self.record.units
            if window is None or window.overlaps(unit.start_ms, unit.end_ms)
        )[:limit]


def service(record: TranscriptRecord) -> tuple[RetrieveTemporalEvidence, FilteredRetriever]:
    retriever = FilteredRetriever(record)
    return RetrieveTemporalEvidence(
        videos=MemoryTranscript(record),
        retrieval=RetrieveTranscript(lexical=retriever, dense=retriever),
    ), retriever


@pytest.mark.parametrize(
    ("query", "text", "window"),
    [
        ("Explain provenance", "Explain provenance", None),
        ('Find "before 01:20"', 'Find "before 01:20"', None),
        ("What happened at 01:20?", "", TimeWindow(65000, 95000)),
        ("0:10", "", TimeWindow(0, 25000)),
        ("during 0:10", "", TimeWindow(10000, 10001)),
        ("name before 1:02:03", "name", TimeWindow(0, 3723000)),
        ("after 1.5 minutes", "", TimeWindow(90000)),
        ("before 2 hours", "", TimeWindow(0, 7200000)),
        ("around 3 secs", "", TimeWindow(0, 18000)),
        ("explain between 00:10 and 00:20", "explain", TimeWindow(10000, 20000)),
        ("from 2 seconds to 1 minute", "", TimeWindow(2000, 60000)),
        ("00:10-00:20", "", TimeWindow(10000, 20000)),
        ("before 0 seconds", "", TimeWindow(0, 0)),
    ],
)
def test_timestamp_grammar_and_literal_quotes(
    query: str, text: str, window: TimeWindow | None
) -> None:
    intent = parse_temporal(analyze_query(query))
    assert intent.text_query == text
    assert intent.window == window
    assert intent.event is None


@pytest.mark.parametrize(
    "query",
    [
        "after 00:99",
        "between 00:20 and 00:10",
        "00:10 to 00:10",
        "before 00:10 after 00:20",
        "between two events",
        "before the",
        "before " + "x" * 201,
        "before demo after intro",
        "at 01:20 explain it",
        'before ""',
        "before",
    ],
)
def test_uncertain_grammar_never_loses_constraint(query: str) -> None:
    with pytest.raises(TemporalResolutionRequired):
        parse_temporal(analyze_query(query))


@pytest.mark.parametrize("relation", list(TemporalRelation))
def test_named_events_preserve_label_and_question(relation: TemporalRelation) -> None:
    intent = parse_temporal(analyze_query(f'explain {relation.value} the "demo"?'))
    assert intent.event == "demo"
    assert intent.relation == relation
    assert intent.text_query == "explain"
    assert intent.window is None


def test_half_open_windows_validate_boundaries_and_empty_interval() -> None:
    assert parse_temporal(analyze_query("after 3 seconds.")).window == TimeWindow(3000)
    with pytest.raises(TemporalResolutionRequired, match="supported"):
        parse_temporal(analyze_query("after 999:59:59"))
    with pytest.raises(TemporalResolutionRequired, match="supported"):
        parse_temporal(analyze_query("after 9999999999999999999999999 hours"))
    for start, end in ((-1, None), (20, 10)):
        with pytest.raises(ValueError):
            TimeWindow(start, end)
    window = TimeWindow(10, 20)
    assert window.overlaps(9, 11) and window.overlaps(19, 21)
    assert not window.overlaps(0, 10) and not window.overlaps(20, 30)
    assert not TimeWindow(0, 0).overlaps(0, 10)
    assert TimeWindow(20).overlaps(20, 30)


@pytest.mark.parametrize(
    "values",
    [
        {"padding_ms": -1},
        {"padding_ms": 60001},
        {"merge_gap_ms": -1},
        {"merge_gap_ms": 10001},
        {"max_context_chars": 0},
        {"max_context_chars": 30001},
    ],
)
def test_expansion_configuration_is_bounded(values: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ExpansionConfig(**values)


def test_expansion_merges_overlaps_small_gaps_and_preserves_asr_lineage() -> None:
    record = transcript(
        (0, 1000, "neighbor"),
        (20000, 21000, "match"),
        (35000, 36000, "second"),
        (70000, 71000, "third"),
        (106000, 107000, "fourth"),
    )
    asr = replace(
        record.cues[1],
        origin=TranscriptOrigin.ASR,
        track_id=None,
        caption_kind=None,
        transcription_run_id=uuid4(),
        confidence=0.9,
        confidence_method="fixture",
    )
    record = replace(record, cues=(record.cues[0], asr, *record.cues[2:]))
    result = expand_evidence(record, hits(record, 1, 2, 3, 4))
    assert [(group.start_ms, group.end_ms) for group in result.groups] == [
        (5000, 86000),
        (91000, 107000),
    ]
    assert result.groups[0].cues[0] is asr
    assert [hit.unit for hit in result.groups[0].hits] == list(record.units[1:4])
    assert result.groups[0].units[0].start_ms == 20000
    assert result.context_chars == sum(len(unit.text) for unit in record.units[1:])


def test_diversity_uses_all_candidates_and_context_prioritizes_matches() -> None:
    record = transcript(
        (0, 1000, "neighbor"),
        (2000, 3000, "seed"),
        (50000, 51000, "next"),
        (100000, 101000, "last"),
    )
    result = expand_evidence(
        record, hits(record, 1, 2, 3), limit=2, config=ExpansionConfig(max_context_chars=8)
    )
    assert len(result.groups) == 2
    assert [group.units[0].text for group in result.groups] == ["seed", "next"]
    assert result.context_chars == 8
    assert result.warnings[0].code == "context_budget_exhausted"
    empty = expand_evidence(record, hits(record, 0), config=ExpansionConfig(max_context_chars=1))
    assert not empty.groups and empty.warnings
    with pytest.raises(ValueError):
        expand_evidence(record, (), limit=0)


def test_duplicate_cues_and_units_remain_unique_and_scope_is_visible() -> None:
    record = transcript((0, 10000, "original"), (20000, 30000, "outside"))
    duplicate = replace(record.units[0], unit_id="f" * 64)
    record = replace(record, units=(*record.units, duplicate))
    result = expand_evidence(record, hits(record, 0, 2, 0, 1), window=TimeWindow(5000, 8000))
    assert result.groups[0].start_ms == 5000 and result.groups[0].end_ms == 8000
    assert len(result.groups[0].units) == 2 and len(result.groups[0].cues) == 1
    assert result.groups[0].cues[0] == record.cues[0]
    assert result.warnings[0].code == "temporal_boundary_overlap"
    assert not expand_evidence(record, hits(record, 0), window=TimeWindow(0, 0)).groups
    assert not expand_evidence(record, hits(record, 0), window=TimeWindow(30000)).groups
    assert not expand_evidence(
        replace(
            record,
            video=replace(record.video, metadata=replace(record.video.metadata, duration_ms=1000)),
        ),
        hits(record, 1),
    ).groups


def test_expansion_rejects_foreign_mutated_or_missing_lineage() -> None:
    record = transcript((0, 1000, "safe"))
    for hit in (
        replace(hits(record, 0)[0], video_id=uuid4()),
        replace(hits(record, 0)[0], unit=replace(record.units[0], text="changed")),
    ):
        with pytest.raises(RetrievalIntegrityError):
            expand_evidence(record, (hit,))
    with pytest.raises(RetrievalIntegrityError, match="missing cue"):
        expand_evidence(replace(record, cues=()), ())
    foreign = replace(record.cues[0], source=replace(record.cues[0].source, external_id="other"))
    with pytest.raises(RetrievalIntegrityError, match="another source"):
        expand_evidence(replace(record, cues=(foreign,)), ())


@pytest.mark.asyncio
async def test_time_only_browsing_never_calls_a_model_and_bounds_candidates() -> None:
    record = transcript(*((index * 1000, (index + 1) * 1000, str(index)) for index in range(31)))
    app, provider = service(record)
    result = await app.execute(record.video.video_id, "after 0 seconds")
    assert not provider.calls
    assert result.status == RetrievalStatus.RESOLVED and result.retrieval is None
    assert result.warnings[0].code == "temporal_candidate_limit"
    assert len(result.groups[0].hits) == 30
    assert (await app.execute(record.video.video_id, "before 0 seconds")).groups == ()


@pytest.mark.asyncio
async def test_scope_reaches_both_retrievers_before_ranking() -> None:
    record = transcript((0, 10000, "outside"), (10000, 20000, "inside"), (20000, 30000, "outside"))
    app, provider = service(record)
    result = await app.execute(record.video.video_id, "explain between 00:10 and 00:20")
    assert provider.calls == [("explain", TimeWindow(10000, 20000))] * 2
    assert result.groups[0].units == (record.units[1],)
    assert not result.degraded
    assert result.analysis.original == "explain between 00:10 and 00:20"
    plain = await app.execute(record.video.video_id, "explain")
    assert plain.window is None and plain.groups


@pytest.mark.asyncio
async def test_semantic_anchor_requires_confirmation_even_with_one_candidate() -> None:
    record = transcript((50000, 51000, "Welcome everyone"))
    app, provider = service(record)
    result = await app.execute(record.video.video_id, "What happened before the introduction?")
    assert result.status == RetrievalStatus.ANCHOR_SELECTION_REQUIRED
    assert len(result.anchors) == 1 and result.anchors[0].match_kind == "semantic"
    assert result.retrieval is None and not result.groups
    assert len(provider.calls) == 2
    chosen = await app.execute(
        record.video.video_id,
        "What happened before the introduction?",
        selected_anchor=result.anchors[0].anchor_id,
    )
    assert chosen.status == RetrievalStatus.RESOLVED
    assert chosen.window == TimeWindow(0, 50000)
    assert chosen.selected_anchor == result.anchors[0].anchor_id


@pytest.mark.asyncio
async def test_literal_unique_anchor_resolves_and_ambiguous_moments_are_capped() -> None:
    record = transcript(
        (0, 1000, "before"), (50000, 51000, "The demo begins"), (52000, 53000, "demo continued")
    )
    app, _ = service(record)
    result = await app.execute(record.video.video_id, "explain before the demo")
    assert result.status == RetrievalStatus.RESOLVED and result.selected_anchor
    assert result.window == TimeWindow(0, 50000)
    assert result.anchors[0].match_kind == "literal"
    many = transcript(*((index * 50000, index * 50000 + 1000, "demo") for index in range(7)))
    app, _ = service(many)
    result = await app.execute(many.video.video_id, "after the demo")
    assert result.status == RetrievalStatus.ANCHOR_SELECTION_REQUIRED
    assert len(result.anchors) == 5
    assert result.anchors == (await app.execute(many.video.video_id, "after the demo")).anchors


@pytest.mark.asyncio
async def test_missing_stale_foreign_and_non_event_selections_never_search_unrestricted() -> None:
    record = transcript()
    app, provider = service(record)
    result = await app.execute(
        record.video.video_id, "before the demo", mode=RetrievalMode.RERANKED
    )
    assert result.status == RetrievalStatus.ANCHOR_UNRESOLVED
    assert not result.groups and result.window is None
    assert len(provider.calls) == 2
    with pytest.raises(InvalidAnchor):
        await app.execute(record.video.video_id, "before the demo", selected_anchor="stale")
    with pytest.raises(InvalidAnchor):
        await app.execute(record.video.video_id, "at 00:01", selected_anchor="stale")
    with pytest.raises(ValueError):
        await app.execute(record.video.video_id, "query", limit=9)
    missing = RetrieveTemporalEvidence(
        videos=MemoryTranscript(None),
        retrieval=RetrieveTranscript(lexical=provider, dense=provider),
    )
    with pytest.raises(RetrievalVideoNotFound):
        await missing.execute(uuid4(), "query")


@pytest.mark.asyncio
async def test_anchor_and_main_pass_preserve_explicit_fallback_and_strict_mode() -> None:
    record = transcript((0, 1000, "context"), (50000, 51000, "demo"))
    lexical, dense = FilteredRetriever(record), FilteredRetriever(record, fail=True)
    app = RetrieveTemporalEvidence(
        videos=MemoryTranscript(record), retrieval=RetrieveTranscript(lexical=lexical, dense=dense)
    )
    result = await app.execute(record.video.video_id, "explain before the demo")
    assert result.degraded and len(result.warnings) == 2
    assert result.anchor_retrieval and result.anchor_retrieval.degraded
    assert result.retrieval and result.retrieval.degraded
    with pytest.raises(EmbeddingProviderError):
        await app.execute(record.video.video_id, "before the demo", allow_fallback=False)


@pytest.mark.asyncio
async def test_anchor_uses_literal_cue_interval_and_rejects_foreign_video() -> None:
    record = transcript(
        (0, 10000, "context"), (10000, 20000, "demo"), (20000, 30000, "more context")
    )
    combined = replace(
        record.units[0],
        text="context demo more context",
        end_ms=30000,
        cue_ids=tuple(cue.cue_id for cue in record.cues),
    )
    record = replace(record, units=(combined,))
    app, _ = service(record)
    result = await app.execute(record.video.video_id, "after the demo")
    assert (result.anchors[0].start_ms, result.anchors[0].end_ms) == (10000, 20000)
    assert result.window == TimeWindow(20000)
    with pytest.raises(RetrievalIntegrityError, match="another video"):
        await app.execute(uuid4(), "query")
    phrase_record = replace(
        record,
        units=(replace(combined, text="control panel"),),
        cues=(
            replace(record.cues[0], text="control"),
            replace(record.cues[1], text="panel"),
            record.cues[2],
        ),
    )
    app, _ = service(phrase_record)
    phrase = await app.execute(record.video.video_id, "during the control panel")
    assert phrase.window == TimeWindow(0, 30000)


@pytest.mark.asyncio
async def test_adjacent_semantic_units_do_not_become_one_video_long_anchor() -> None:
    record = transcript(
        *((index * 10000, (index + 1) * 10000, "Welcome everyone") for index in range(12))
    )
    app, _ = service(record)
    result = await app.execute(record.video.video_id, "before the introduction")
    assert len(result.anchors) == 5
    assert all(choice.end_ms - choice.start_ms == 10000 for choice in result.anchors)


@pytest.mark.asyncio
async def test_literal_mentions_outside_top_k_still_prevent_auto_selection() -> None:
    record = transcript(
        *(
            (index * 50000, index * 50000 + 1000, "demo" if index in (0, 30) else "other")
            for index in range(31)
        )
    )
    app, _ = service(record)
    result = await app.execute(record.video.video_id, "before the demo")
    assert result.status == RetrievalStatus.ANCHOR_SELECTION_REQUIRED
    assert len(result.anchors) == 2
