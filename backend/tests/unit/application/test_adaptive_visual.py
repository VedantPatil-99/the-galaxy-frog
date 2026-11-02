"""Policy, temporal isolation, rank provenance, and explicit visual-provider degradation."""

from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from tests.unit.application.test_temporal_retrieval import hits, transcript
from tests.unit.vision.test_visual_contracts import SPEC, frame, identity

from galaxy_frog.api.app import create_app
from galaxy_frog.application.retrieval.search import SearchTranscript, TracedRetrievalFailure
from galaxy_frog.application.retrieval.temporal import (
    RetrievalStatus,
    RetrieveTemporalEvidence,
    TemporalRetrievalResult,
)
from galaxy_frog.application.retrieval.visual import (
    AdaptiveVisualRetrieval,
    coverage,
    distinct_frames,
)
from galaxy_frog.config import Settings
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError, RetrievalIntegrityError
from galaxy_frog.domain.retrieval.evidence import DEFAULT_EXPANSION
from galaxy_frog.domain.retrieval.pipeline import (
    RetrievalConfig,
    RetrievalMode,
    TextRetrievalResult,
)
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.retrieval.temporal import TemporalIntent, TimeWindow
from galaxy_frog.domain.vision.models import OCRBlock, OCRProviderSpec, RetrievedOCREvidence
from galaxy_frog.domain.vision.policy import VisualDecision
from galaxy_frog.domain.vision.visual import VisualFailureCode, VisualHit, VisualProviderError


def temporal(
    query: str = "find diagram", *, window: TimeWindow | None = None
) -> TemporalRetrievalResult:
    return TemporalRetrievalResult(
        analyze_query(query),
        TemporalIntent(query),
        RetrievalStatus.RESOLVED,
        window,
        (),
        None,
        None,
        None,
        (),
        0,
        (),
        False,
        DEFAULT_EXPANSION,
    )


def ocr(video: UUID, text: str = "chart text") -> RetrievedOCREvidence:
    return RetrievedOCREvidence(
        video, uuid4(), uuid4(), OCRBlock(text, 0, 1000, 0, OCRProviderSpec("fake", "v1")), 0.5
    )


def ports() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    lexical, dense, visual = AsyncMock(), AsyncMock(), AsyncMock()
    lexical.search.return_value = dense.search.return_value = ()
    visual.search.return_value = ()
    visual.nearby.return_value = ()
    return lexical, dense, visual


@pytest.mark.asyncio
async def test_spoken_and_unresolved_queries_never_touch_visual_providers() -> None:
    lexical, dense, visual = ports()
    service = AdaptiveVisualRetrieval(ocr_lexical=lexical, ocr_dense=dense, visual=visual)
    result = await service.execute(uuid4(), "What did he say?", temporal())
    assert result.decisions[0].decision == VisualDecision.TEXT_SUFFICIENT
    result = await service.execute(
        uuid4(),
        "Find diagram",
        replace(temporal(), status=RetrievalStatus.ANCHOR_SELECTION_REQUIRED),
    )
    assert result.decisions[0].reason == "temporal_selection_required"
    lexical.search.assert_not_awaited()
    dense.search.assert_not_awaited()
    visual.search.assert_not_awaited()
    assert coverage("what", "") == 0


@pytest.mark.asyncio
async def test_ocr_sufficiency_preserves_both_stage_scores_and_skips_visual_embedding() -> None:
    video = uuid4()
    lexical, dense, visual = ports()
    evidence = ocr(video)
    lexical.search.return_value = (evidence,)
    dense.search.return_value = (replace(evidence, score=0.9),)
    result = await AdaptiveVisualRetrieval(
        ocr_lexical=lexical, ocr_dense=dense, visual=visual
    ).execute(video, "read chart text", temporal())
    assert result.decisions[-1].decision == VisualDecision.TEXT_SUFFICIENT
    assert len(result.stages) == 2 and len(result.fusion) == 1
    assert result.stages[0].score == 0.5 and result.stages[1].score == 0.9
    visual.search.assert_not_awaited()
    assert "image_path" not in str(result.trace_payload())


@pytest.mark.asyncio
async def test_visual_retrieval_deduplication_then_bounded_expansion() -> None:
    lexical, _, visual = ports()
    original = frame()
    duplicate = replace(original, frame_id=uuid4())
    neighbor = replace(
        original, frame_id=uuid4(), sequence=1, start_ms=1000, end_ms=2000, capture_ms=1000
    )
    visual.search.return_value = (
        VisualHit(original, SPEC, 0.8, 1),
        VisualHit(duplicate, SPEC, 0.7, 2),
    )
    visual.nearby.return_value = (neighbor,)
    result = await AdaptiveVisualRetrieval(
        ocr_lexical=lexical, visual=visual, provider=identity()
    ).execute(original.video_id, "What color is shown?", temporal())
    assert len(result.hits) == 1 and result.nearby == (neighbor,)
    assert result.decisions[-1].decision == VisualDecision.REASON
    assert "path" not in result.trace_payload()["frames"][0]  # type: ignore[index]
    assert distinct_frames(visual.search.return_value, 1)[0].frame is original


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["none", "unavailable", "dense", "empty", "budget"])
async def test_degraded_and_empty_outcomes_are_explicit(kind: str) -> None:
    lexical, dense, visual = ports()
    video = uuid4()
    if kind == "unavailable":
        visual.search.side_effect = VisualProviderError(VisualFailureCode.UNAVAILABLE)
    if kind == "dense":
        dense.search.side_effect = EmbeddingProviderError("private")
    if kind == "budget":
        lexical.search.return_value = (ocr(video, "x" * 12001),)
    result = await AdaptiveVisualRetrieval(
        ocr_lexical=lexical, ocr_dense=dense, visual=None if kind == "none" else visual
    ).execute(video, "find diagram", temporal())
    assert result.degraded
    assert result.decisions[-1].decision in {
        VisualDecision.UNAVAILABLE,
        VisualDecision.INSUFFICIENT,
    }
    if kind in {"unavailable", "dense"}:
        with pytest.raises(
            VisualProviderError if kind == "unavailable" else EmbeddingProviderError
        ):
            await AdaptiveVisualRetrieval(
                ocr_lexical=lexical, ocr_dense=dense, visual=visual
            ).execute(video, "find diagram", temporal(), allow_fallback=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "ocr_video",
        "ocr_time",
        "ocr_budget",
        "ocr_conflict",
        "visual_video",
        "visual_time",
        "visual_budget",
        "nearby_video",
        "nearby_time",
        "nearby_budget",
    ],
)
async def test_provider_lineage_scope_and_budgets_are_enforced(kind: str) -> None:
    lexical, dense, visual = ports()
    original = frame()
    video = original.video_id
    item = ocr(video)
    lexical.search.return_value = (item,)
    visual.search.return_value = (VisualHit(original, SPEC, 0.8, 1),)
    window = TimeWindow(0, 1000)
    if kind == "ocr_video":
        lexical.search.return_value = (replace(item, video_id=uuid4()),)
    elif kind == "ocr_time":
        lexical.search.return_value = (
            replace(item, block=replace(item.block, start_ms=1000, end_ms=2000)),
        )
    elif kind == "ocr_budget":
        lexical.search.return_value = (item,) * 31
    elif kind == "ocr_conflict":
        dense.search.return_value = (replace(item, frame_id=uuid4()),)
    elif kind == "visual_video":
        visual.search.return_value = (VisualHit(replace(original, video_id=uuid4()), SPEC, 0.8, 1),)
    elif kind == "visual_time":
        visual.search.return_value = (
            VisualHit(replace(original, start_ms=1000, end_ms=2000, capture_ms=1000), SPEC, 0.8, 1),
        )
    elif kind == "visual_budget":
        visual.search.return_value = visual.search.return_value * 13
    elif kind == "nearby_video":
        visual.nearby.return_value = (replace(original, video_id=uuid4()),)
    elif kind == "nearby_time":
        visual.nearby.return_value = (
            replace(original, start_ms=1000, end_ms=2000, capture_ms=1000),
        )
    else:
        visual.nearby.return_value = (original,) * 5
    with pytest.raises(RetrievalIntegrityError):
        await AdaptiveVisualRetrieval(ocr_lexical=lexical, ocr_dense=dense, visual=visual).execute(
            video, "find diagram", temporal(window=window)
        )


@pytest.mark.asyncio
async def test_transcript_ranks_and_durable_visual_decisions() -> None:
    record = transcript((0, 1000, "A diagram"))
    text = temporal()
    text = replace(
        text,
        retrieval=TextRetrievalResult(
            text.analysis,
            RetrievalMode.LEXICAL,
            RetrievalConfig(),
            (),
            (),
            hits(record, 0),
            (),
            (),
            False,
            0,
        ),
    )
    retrieval, traces = AsyncMock(spec=RetrieveTemporalEvidence), AsyncMock()
    retrieval.configuration = {}
    retrieval.execute.return_value = text
    lexical, _, _ = ports()
    service = SearchTranscript(
        retrieval=cast(RetrieveTemporalEvidence, retrieval),
        traces=traces,
        embedding_spec=SPEC,
        visual=AdaptiveVisualRetrieval(ocr_lexical=lexical),
    )
    result = await service.execute(record.video.video_id, "find diagram")
    assert result.visual and result.visual.stages[0].identity == record.units[0].unit_id
    assert "visual" in result.trace.payload
    traces.save.assert_awaited_once()
    with pytest.raises(ValueError):
        await AdaptiveVisualRetrieval(ocr_lexical=lexical).execute(
            uuid4(), "diagram", temporal(), limit=9
        )
    lexical.search.side_effect = VisualProviderError(VisualFailureCode.UNAVAILABLE)
    with pytest.raises(TracedRetrievalFailure) as error:
        await service.execute(record.video.video_id, "find diagram", allow_fallback=False)
    assert error.value.code == "VISUAL_UNAVAILABLE"
    create_app(Settings(visual_embedding_enabled=True))


@pytest.mark.asyncio
async def test_strict_visual_mode_rejects_disabled_provider() -> None:
    lexical, _, _ = ports()
    with pytest.raises(VisualProviderError):
        await AdaptiveVisualRetrieval(ocr_lexical=lexical).execute(
            uuid4(), "find diagram", temporal(), allow_fallback=False
        )
