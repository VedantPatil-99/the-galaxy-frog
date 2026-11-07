"""Visual contracts reject ambiguous identity, incompatible vectors, and unsafe budgets."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.vision.models import FrameSource, PerceptualHash
from galaxy_frog.domain.vision.policy import (
    EvidenceSufficiency,
    VisualDecision,
    decide_visual,
    external_allowed,
    visual_signals,
)
from galaxy_frog.domain.vision.visual import (
    ExecutionLocation,
    VisualEmbedding,
    VisualFailureCode,
    VisualFrame,
    VisualHit,
    VisualProviderError,
    VisualProviderIdentity,
    VisualReasoningRequest,
    VisualReasoningResult,
    validate_vector,
)
from galaxy_frog.domain.vision.visual_ports import VisualEmbeddingProvider, VisualReasoningProvider


def frame() -> VisualFrame:
    return VisualFrame(
        uuid4(),
        uuid4(),
        uuid4(),
        0,
        0,
        1000,
        PerceptualHash("0" * 16, "dhash", 64),
        "a" * 64,
        FrameSource.PERIODIC,
        Path.cwd() / "frame.jpg",
        capture_ms=0,
    )


def identity() -> VisualProviderIdentity:
    return VisualProviderIdentity("fake", "model", "immutable", ExecutionLocation.LOCAL)


SPEC = EmbeddingCollectionSpec("fake", "model", "immutable", 2, "l2")


def test_valid_contracts_preserve_original_frame() -> None:
    assert VisualEmbeddingProvider and VisualReasoningProvider
    original = frame()
    assert VisualEmbedding(original, SPEC, (1.0, 0.0)).frame is original
    assert VisualHit(original, SPEC, -0.5, 1).frame is original
    request = VisualReasoningRequest(original.video_id, "Which arrow?", (original,), "")
    result = VisualReasoningResult("Right", (original.frame_id,), identity(), 1)
    assert request.frames[0].scene_id == original.scene_id
    assert result.cited_frame_ids == (original.frame_id,)
    assert str(VisualProviderError(VisualFailureCode.BUSY)) == "visual_busy"
    assert (
        decide_visual(visual_signals("Read the text"), EvidenceSufficiency(0, 0)).decision
        == VisualDecision.RETRIEVE
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": -1},
        {"start_ms": -1},
        {"end_ms": 0},
        {"capture_ms": -1},
        {"capture_ms": 1000},
        {"content_sha256": "a"},
        {"content_sha256": "z" * 64},
        {"path": Path("relative.jpg")},
    ],
)
def test_frame_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(frame(), **changes)


@pytest.mark.parametrize(
    "provider,model,revision", [("", "m", "r"), ("p", " ", "r"), ("p", "m", "x" * 257)]
)
def test_provider_identity_validation(provider: str, model: str, revision: str) -> None:
    with pytest.raises(ValueError):
        VisualProviderIdentity(provider, model, revision, ExecutionLocation.LOCAL)


def test_location_must_be_typed() -> None:
    with pytest.raises(ValueError):
        VisualProviderIdentity("p", "m", "r", "local")  # type: ignore[arg-type]


@pytest.mark.parametrize("values", [(1.0,), (float("nan"), 0), (0, 0), (1e308, 0), (1, 1)])
def test_incompatible_vectors(values: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        validate_vector(values, SPEC)


def test_requires_normalization() -> None:
    with pytest.raises(ValueError):
        validate_vector((1, 0), replace(SPEC, normalization="none"))


@pytest.mark.parametrize("score,rank", [(float("inf"), 1), (1.1, 1), (0, 0)])
def test_hit_validation(score: float, rank: int) -> None:
    with pytest.raises(ValueError):
        VisualHit(frame(), SPEC, score, rank)


@pytest.mark.parametrize(
    "kind",
    ["empty", "long", "evidence", "zero", "many", "duplicate", "foreign", "prompt", "schema"],
)
def test_reasoning_request_bounds(kind: str) -> None:
    original = frame()
    request = VisualReasoningRequest(original.video_id, "question", (original,), "")
    changes: Mapping[str, object] = {
        "empty": {"question": " "},
        "long": {"question": "x" * 2001},
        "evidence": {"evidence": "x" * 12001},
        "zero": {"frames": ()},
        "many": {"frames": (original,) * 5},
        "duplicate": {"frames": (original, original)},
        "foreign": {"video_id": uuid4()},
        "prompt": {"prompt_version": ""},
        "schema": {"schema_version": ""},
    }[kind]
    with pytest.raises(ValueError):
        replace(request, **changes)


@pytest.mark.parametrize(
    "kind", ["empty", "long", "negative", "nan", "many", "duplicate", "missing"]
)
def test_reasoning_result_bounds(kind: str) -> None:
    fid = uuid4()
    result = VisualReasoningResult("answer", (fid,), identity(), 1)
    changes: Mapping[str, object] = {
        "empty": {"answer": " "},
        "long": {"answer": "x" * 8001},
        "negative": {"elapsed_ms": -1},
        "nan": {"elapsed_ms": float("nan")},
        "many": {"cited_frame_ids": tuple(uuid4() for _ in range(5))},
        "duplicate": {"cited_frame_ids": (fid, fid)},
        "missing": {"cited_frame_ids": ()},
    }[kind]
    with pytest.raises(ValueError):
        replace(result, **changes)
    assert VisualReasoningResult("Insufficient", (), identity(), 0, True).insufficient_evidence


@pytest.mark.parametrize(
    "query,ocr,hits,after,decision",
    [
        ("What did the speaker say?", 0, 0, False, VisualDecision.TEXT_SUFFICIENT),
        ("Read the slide title", 1, 0, False, VisualDecision.TEXT_SUFFICIENT),
        ("Find the red diagram", 1, 0, False, VisualDecision.RETRIEVE),
        ("Explain the diagram", 1, 0, True, VisualDecision.INSUFFICIENT),
        ("Explain the diagram", 1, 1, True, VisualDecision.REASON),
        ("Find the diagram", 0, 1, True, VisualDecision.TEXT_SUFFICIENT),
        ("Find the diagram", 0, 0, False, VisualDecision.RETRIEVE),
    ],
)
def test_routing_is_deterministic(
    query: str, ocr: float, hits: int, after: bool, decision: VisualDecision
) -> None:
    assert (
        decide_visual(
            visual_signals(query), EvidenceSufficiency(1, ocr, hits), after_retrieval=after
        ).decision
        == decision
    )


@pytest.mark.parametrize("query", ["", "x" * 2001])
def test_query_bounds(query: str) -> None:
    with pytest.raises(ValueError):
        visual_signals(query)


@pytest.mark.parametrize("transcript,ocr,hits", [(-1, 0, 0), (0, float("nan"), 0), (0, 0, -1)])
def test_sufficiency_bounds(transcript: float, ocr: float, hits: int) -> None:
    with pytest.raises(ValueError):
        EvidenceSufficiency(transcript, ocr, hits)


@pytest.mark.parametrize(
    "configured,permitted", [(False, False), (False, True), (True, False), (True, True)]
)
def test_privacy_needs_both_configuration_and_permission(configured: bool, permitted: bool) -> None:
    assert external_allowed(ExecutionLocation.LOCAL, configured=configured, permitted=permitted)
    assert external_allowed(
        ExecutionLocation.EXTERNAL, configured=configured, permitted=permitted
    ) == (configured and permitted)
