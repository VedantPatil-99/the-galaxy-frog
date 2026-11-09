"""Reason only when needed; privacy, model revisions, source identity, and cache isolation."""

from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from tests.unit.application.test_adaptive_visual import temporal
from tests.unit.application.test_temporal_retrieval import transcript
from tests.unit.vision.test_visual_contracts import SPEC, frame, identity

from galaxy_frog.api.app import create_app
from galaxy_frog.application.retrieval.reasoning import AdaptiveVisualReasoning
from galaxy_frog.application.retrieval.search import SearchTranscript
from galaxy_frog.application.retrieval.temporal import RetrievalStatus, RetrieveTemporalEvidence
from galaxy_frog.application.retrieval.visual import VisualRetrievalResult
from galaxy_frog.config import Settings
from galaxy_frog.domain.retrieval.evidence import EvidenceGroup
from galaxy_frog.domain.vision.policy import (
    EscalationDecision,
    EvidenceSufficiency,
    VisualDecision,
    visual_signals,
)
from galaxy_frog.domain.vision.reasoning_cache import reasoning_cache_key
from galaxy_frog.domain.vision.visual import (
    ExecutionLocation,
    VisualFailureCode,
    VisualHit,
    VisualProviderError,
    VisualReasoningRequest,
    VisualReasoningResult,
)


def visual_result() -> VisualRetrievalResult:
    original = frame()
    return VisualRetrievalResult(
        visual_signals("What color is shown?"),
        EvidenceSufficiency(0, 0, 1),
        (EscalationDecision(VisualDecision.REASON, "visual_attributes_require_reasoning"),),
        (),
        (VisualHit(original, SPEC, 0.8, 1, collection_id=uuid4()),),
        (),
        (),
        (),
        (),
        1,
        identity(),
    )


@pytest.mark.asyncio
async def test_reasoning_cache_reuses_only_verified_matching_evidence() -> None:
    visual = visual_result()
    provider, cache, verifier = AsyncMock(), AsyncMock(), AsyncMock()
    provider.identity = identity()
    answer = VisualReasoningResult("blue", (visual.hits[0].frame.frame_id,), identity(), 10)
    provider.reason.return_value = answer
    cache.get.return_value = None
    service = AdaptiveVisualReasoning(provider=provider, cache=cache, verifier=verifier)
    first = await service.execute(
        visual.hits[0].frame.video_id, "What color is shown?", temporal(), visual
    )
    assert first.result is answer and not first.cache_hit and not first.external_permitted
    assert first.cache_key and len(first.cache_key) == 64
    cache.get.return_value = answer
    second = await service.execute(
        visual.hits[0].frame.video_id, "What color is shown?", temporal(), visual
    )
    assert second.cache_hit
    provider.reason.assert_awaited_once()
    assert verifier.verify.await_count == 2
    assert "path" not in str(second.trace_payload())
    assert second.trace_payload()["provider_elapsed_ms"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["disabled", "privacy", "failure", "wrong_provider", "wrong_citation", "tampered"]
)
async def test_failure_preserves_retrieval_and_strict_mode_refuses_fallback(kind: str) -> None:
    visual = visual_result()
    provider, cache, verifier = AsyncMock(), AsyncMock(), AsyncMock()
    provider.identity = (
        replace(identity(), location=ExecutionLocation.EXTERNAL)
        if kind == "privacy"
        else identity()
    )
    cache.get.return_value = None
    answer = VisualReasoningResult(
        "answer", (visual.hits[0].frame.frame_id,), provider.identity, 10
    )
    if kind == "wrong_provider":
        answer = replace(answer, provider=replace(identity(), revision="different"))
    if kind == "wrong_citation":
        answer = replace(answer, cited_frame_ids=(uuid4(),))
    provider.reason.return_value = answer
    if kind == "failure":
        provider.reason.side_effect = VisualProviderError(VisualFailureCode.TIMEOUT)
    if kind == "tampered":
        verifier.verify.side_effect = VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
    service = AdaptiveVisualReasoning(
        provider=None if kind == "disabled" else provider, cache=cache, verifier=verifier
    )
    value = await service.execute(visual.hits[0].frame.video_id, "What color?", temporal(), visual)
    assert value.degraded and value.result is None
    assert value.trace_payload()["cited_frame_ids"] == []
    with pytest.raises(VisualProviderError):
        await service.execute(
            visual.hits[0].frame.video_id, "What color?", temporal(), visual, allow_fallback=False
        )
    if kind == "privacy":
        verifier.verify.assert_not_awaited()
        cache.get.assert_not_awaited()
        provider.reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_unresolved_and_sufficient_text_skip_all_reasoning() -> None:
    visual = visual_result()
    provider, cache, verifier = AsyncMock(), AsyncMock(), AsyncMock()
    provider.identity = identity()
    service = AdaptiveVisualReasoning(provider=provider, cache=cache, verifier=verifier)
    result = await service.execute(
        visual.hits[0].frame.video_id,
        "What color?",
        replace(temporal(), status=RetrievalStatus.ANCHOR_UNRESOLVED),
        visual,
    )
    assert result.reason == "reasoning_not_required"
    result = await service.execute(
        visual.hits[0].frame.video_id,
        "What color?",
        temporal(),
        replace(visual, decisions=(EscalationDecision(VisualDecision.TEXT_SUFFICIENT, "covered"),)),
    )
    assert result.result is None
    provider.reason.assert_not_awaited()
    cache.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_budget_insufficiency_and_question_only_trace() -> None:
    visual = visual_result()
    original = visual.hits[0].frame
    provider, cache, verifier = AsyncMock(), AsyncMock(), AsyncMock()
    provider.identity = identity()
    provider.reason.return_value = VisualReasoningResult(
        "Not enough visual evidence", (), identity(), 2, True
    )
    cache.get.return_value = None
    record = transcript((0, 1000, "x" * 4000))
    text = replace(temporal(), groups=(EvidenceGroup(0, 1000, (), record.units, record.cues),))
    service = AdaptiveVisualReasoning(provider=provider, cache=cache, verifier=verifier)
    result = await service.execute(original.video_id, "What color?", text, visual)
    assert (
        result.decision == VisualDecision.INSUFFICIENT
        and result.warnings[0].code == "visual_context_limit"
    )
    assert len(provider.reason.call_args.args[0].evidence) == 1200
    retrieval, visual_service, traces = (
        AsyncMock(spec=RetrieveTemporalEvidence),
        AsyncMock(),
        AsyncMock(),
    )
    retrieval.configuration = {}
    retrieval.execute.return_value = text
    visual_service.configuration = {}
    visual_service.execute.return_value = visual
    search = SearchTranscript(
        retrieval=cast(RetrieveTemporalEvidence, retrieval),
        traces=traces,
        embedding_spec=SPEC,
        visual=visual_service,
        reasoning=service,
    )
    execution = await search.execute(original.video_id, "What color?", reason=True)
    assert "visual_reasoning" in execution.trace.payload and execution.reasoning is not None
    provider.reason.reset_mock()
    await search.execute(original.video_id, "What color?", reason=False)
    provider.reason.assert_not_awaited()


def test_cache_keys_separate_models_queries_sources_frames_and_versions() -> None:
    original = frame()
    neighbor = replace(original, frame_id=uuid4(), sequence=1)
    request = VisualReasoningRequest(
        original.video_id,
        "question",
        (original, neighbor),
        "evidence",
        evidence_fingerprint="a" * 64,
    )
    key = reasoning_cache_key(request, identity())
    for changed in (
        replace(request, question="different"),
        replace(request, evidence="different"),
        replace(request, prompt_version="v2"),
        replace(request, schema_version="v2"),
        replace(request, frames=(neighbor, original)),
        replace(request, evidence_fingerprint="b" * 64),
        replace(request, frames=(replace(original, content_sha256="b" * 64), neighbor)),
    ):
        assert reasoning_cache_key(changed, identity()) != key
    assert reasoning_cache_key(request, replace(identity(), revision="different")) != key
    for value in ("short", "z" * 64):
        with pytest.raises(ValueError):
            replace(request, evidence_fingerprint=value)


def test_app_composes_only_explicit_reasoning_provider() -> None:
    create_app(Settings(vlm_provider="ollama"))
    create_app(Settings(vlm_provider="gemini"))
    create_app(
        Settings(
            vlm_provider="gemini",
            gemini_api_key=SecretStr("test"),
            gemini_visual_model="gemini-test",
            gemini_visual_revision="pinned-v1",
        )
    )
