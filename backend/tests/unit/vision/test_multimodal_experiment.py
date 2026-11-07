"""Experiment isolation, original lineage, stable ordering, privacy, and strict failures."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from tests.unit.application.test_adaptive_visual import ocr
from tests.unit.vision.test_visual_contracts import SPEC, frame, identity

from galaxy_frog.adapters.vision.experimental_reranking import ReasoningMultimodalReranker
from galaxy_frog.domain.vision.reranking import (
    MultimodalReranker,
    MultimodalRerankRequest,
    MultimodalRerankResult,
)
from galaxy_frog.domain.vision.visual import (
    ExecutionLocation,
    VisualHit,
    VisualProviderError,
    VisualReasoningResult,
)


def request_value() -> MultimodalRerankRequest:
    original = frame()
    return MultimodalRerankRequest(
        "find turtle",
        (
            VisualHit(original, SPEC, 0.7, 1),
            VisualHit(replace(original, frame_id=uuid4()), SPEC, 0.6, 2),
        ),
        ocr=(ocr(original.video_id),),
    )


@pytest.mark.asyncio
async def test_experiment_keeps_original_modalities_and_ranks() -> None:
    assert MultimodalReranker
    request = request_value()
    provider = AsyncMock()
    provider.identity = identity()
    provider.reason.side_effect = [
        VisualReasoningResult(str(grade), (hit.frame.frame_id,), identity(), 1)
        for grade, hit in zip((1, 3), request.frames, strict=True)
    ]
    result = await ReasoningMultimodalReranker(provider).rerank(request)
    assert result.original is request
    assert result.original.ocr[0] is request.ocr[0]
    assert result.ranked == request.frames[::-1]
    assert result.ranked[0].rank == 2
    assert replace(result, grades=(2, 2)).ranked == request.frames


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["privacy", "grade", "citation", "provider", "insufficient", "timeout"]
)
async def test_experiment_fails_strictly(kind: str) -> None:
    request = request_value()
    provider = AsyncMock()
    provider.identity = (
        replace(identity(), location=ExecutionLocation.EXTERNAL)
        if kind == "privacy"
        else identity()
    )
    provider.reason.return_value = VisualReasoningResult(
        "yes" if kind == "grade" else "3",
        (uuid4() if kind == "citation" else request.frames[0].frame.frame_id,),
        replace(identity(), revision="other") if kind == "provider" else identity(),
        1,
        kind == "insufficient",
    )
    if kind == "timeout":
        provider.reason.side_effect = TimeoutError
    with pytest.raises(VisualProviderError):
        await ReasoningMultimodalReranker(provider).rerank(request)


def test_experiment_validation_bounds() -> None:
    request = request_value()
    for changes in (
        {"question": ""},
        {"frames": ()},
        {"frames": request.frames * 3},
        {"ocr": (ocr(uuid4()),)},
        {"ocr": request.ocr * 9},
    ):
        with pytest.raises(ValueError):
            replace(request, **changes)
    for grades, elapsed in (((4, 0), 0), ((1,), 0), ((1, 2), -1)):
        with pytest.raises(ValueError):
            MultimodalRerankResult(request, grades, identity(), elapsed)
