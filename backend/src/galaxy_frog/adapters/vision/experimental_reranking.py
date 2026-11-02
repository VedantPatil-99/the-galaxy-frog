"""Opt-in pointwise VLM experiment. Never wired into production retrieval defaults."""

import asyncio
from time import perf_counter

from galaxy_frog.domain.vision.reranking import MultimodalRerankRequest, MultimodalRerankResult
from galaxy_frog.domain.vision.visual import (
    ExecutionLocation,
    VisualFailureCode,
    VisualProviderError,
    VisualReasoningRequest,
)
from galaxy_frog.domain.vision.visual_ports import VisualReasoningProvider


class ReasoningMultimodalReranker:
    def __init__(self, provider: VisualReasoningProvider) -> None:
        self._provider = provider

    async def rerank(self, request: MultimodalRerankRequest) -> MultimodalRerankResult:
        if self._provider.identity.location != ExecutionLocation.LOCAL:
            raise VisualProviderError(VisualFailureCode.PRIVACY_DENIED)
        started = perf_counter()
        evidence = "\n".join(
            [
                *(item.unit.text for item in request.transcript),
                *(item.block.text for item in request.ocr),
            ]
        )
        grades: list[int] = []
        try:
            async with asyncio.timeout(120):
                for hit in request.frames:
                    value = await self._provider.reason(
                        VisualReasoningRequest(
                            hit.frame.video_id,
                            "Rate this frame's relevance to the query. Answer with exactly one digit: "
                            "0 unrelated, 1 weakly related, 2 relevant, 3 directly answers. "
                            "Cite the supplied frame. Query: " + request.question,
                            (hit.frame,),
                            evidence,
                            prompt_version="visual-rerank-experiment-v1",
                        )
                    )
                    if (
                        value.provider != self._provider.identity
                        or value.cited_frame_ids != (hit.frame.frame_id,)
                        or value.answer.strip() not in {"0", "1", "2", "3"}
                        or value.insufficient_evidence
                    ):
                        raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
                    grades.append(int(value.answer.strip()))
        except TimeoutError as exc:
            raise VisualProviderError(VisualFailureCode.TIMEOUT) from exc
        # Strict by design: caller records failure; no silent fusion substitution in measurements.
        return MultimodalRerankResult(
            request, tuple(grades), self._provider.identity, (perf_counter() - started) * 1000
        )
