"""One traced retrieval use case for search and grounded questions."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import cast
from uuid import UUID, uuid4

from galaxy_frog.application.retrieval.temporal import (
    InvalidAnchor,
    RetrieveTemporalEvidence,
    TemporalRetrievalResult,
)
from galaxy_frog.domain.retrieval.errors import (
    EmbeddingProviderError,
    RetrievalIntegrityError,
    TemporalResolutionRequired,
)
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.pipeline import RetrievalMode, TextRetrievalResult
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.retrieval.reranking import (
    BGE_RERANKER_MODEL,
    BGE_RERANKER_REVISION,
    RerankerError,
)
from galaxy_frog.domain.retrieval.traces import RetrievalTrace, RetrievalTraceRepository, TraceValue


@dataclass(frozen=True, slots=True)
class SearchExecution:
    trace: RetrievalTrace
    result: TemporalRetrievalResult


class TracedRetrievalFailure(RuntimeError):
    def __init__(self, trace: RetrievalTrace, cause: Exception, code: str) -> None:
        super().__init__(code)
        self.trace = trace
        self.cause = cause
        self.code = code


def _pass_trace(result: TextRetrievalResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "analysis": asdict(result.analysis),
        "mode": result.mode,
        "config": asdict(result.config),
        "elapsed_ms": result.elapsed_ms,
        "stages": [
            {
                "stage": stage.stage,
                "elapsed_ms": stage.elapsed_ms,
                "failure_code": stage.failure_code,
                "results": [
                    {"unit_id": hit.unit.unit_id, "score": hit.score, "rank": rank}
                    for rank, hit in enumerate(stage.results, 1)
                ],
            }
            for stage in result.stages
        ],
        "rankings": [
            {
                "unit_id": hit.unit.unit_id,
                "start_ms": hit.unit.start_ms,
                "end_ms": hit.unit.end_ms,
                "cue_ids": hit.unit.cue_ids,
                "stages": [asdict(stage) for stage in hit.stages],
                "fusion_rank": hit.fusion_rank,
                "fusion_score": hit.fusion_score,
            }
            for hit in result.rankings
        ],
        "candidates": [
            {
                "unit_id": hit.unit.unit_id,
                "rerank_rank": hit.rerank_rank,
                "rerank_score": hit.rerank_score,
            }
            for hit in result.candidates
        ],
        "reranking": asdict(result.reranking) if result.reranking is not None else None,
        "warnings": [asdict(warning) for warning in result.warnings],
        "degraded": result.degraded,
    }


class SearchTranscript:
    def __init__(
        self,
        *,
        retrieval: RetrieveTemporalEvidence,
        traces: RetrievalTraceRepository,
        embedding_spec: EmbeddingCollectionSpec,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._retrieval = retrieval
        self._traces = traces
        self._embedding_spec = embedding_spec
        self._clock = clock

    async def execute(
        self,
        video_id: UUID,
        query: str,
        *,
        mode: RetrievalMode = RetrievalMode.RERANKED,
        limit: int = 8,
        selected_anchor: str | None = None,
        allow_fallback: bool = True,
    ) -> SearchExecution:
        started = self._clock()
        payload: dict[str, object] = {
            "query": query,
            "mode": mode,
            "limit": limit,
            "allow_fallback": allow_fallback,
            "requested_anchor": selected_anchor,
            "analysis": asdict(analyze_query(query)),
            "configuration": self._retrieval.configuration,
            "providers": {
                "lexical": {"provider": "postgresql", "dictionary": "simple"},
                "dense": asdict(self._embedding_spec),
                "reranker": {
                    "provider": "transformers",
                    "model": BGE_RERANKER_MODEL,
                    "revision": BGE_RERANKER_REVISION,
                },
            },
        }
        try:
            result = await self._retrieval.execute(
                video_id,
                query,
                mode=mode,
                limit=limit,
                selected_anchor=selected_anchor,
                allow_fallback=allow_fallback,
            )
        except (
            EmbeddingProviderError,
            RerankerError,
            TemporalResolutionRequired,
            InvalidAnchor,
            RetrievalIntegrityError,
        ) as exc:
            code = (
                "EMBEDDING_UNAVAILABLE"
                if isinstance(exc, EmbeddingProviderError)
                else exc.code.value
                if isinstance(exc, RerankerError)
                else "TEMPORAL_QUERY_UNSUPPORTED"
                if isinstance(exc, TemporalResolutionRequired)
                else "INVALID_ANCHOR"
                if isinstance(exc, InvalidAnchor)
                else "RETRIEVAL_INTEGRITY_FAILED"
            )
            payload.update(
                status="failed", failure_code=code, elapsed_ms=(self._clock() - started) * 1000
            )
            trace = await self._persist(video_id, payload)
            raise TracedRetrievalFailure(trace, exc, code) from exc
        payload.update(
            status=result.status,
            analysis=asdict(result.analysis),
            intent=asdict(result.intent),
            window=asdict(result.window) if result.window is not None else None,
            anchors=[asdict(anchor) for anchor in result.anchors],
            selected_anchor=result.selected_anchor,
            anchor_retrieval=_pass_trace(result.anchor_retrieval),
            retrieval=_pass_trace(result.retrieval),
            groups=[
                {
                    "start_ms": group.start_ms,
                    "end_ms": group.end_ms,
                    "seed_ids": [hit.unit.unit_id for hit in group.hits],
                    "units": [
                        {
                            "unit_id": unit.unit_id,
                            "start_ms": unit.start_ms,
                            "end_ms": unit.end_ms,
                            "cue_ids": unit.cue_ids,
                        }
                        for unit in group.units
                    ],
                    "cues": [
                        {key: value for key, value in asdict(cue).items() if key != "text"}
                        for cue in group.cues
                    ],
                }
                for group in result.groups
            ],
            expansion=asdict(result.expansion),
            context_chars=result.context_chars,
            warnings=[asdict(warning) for warning in result.warnings],
            degraded=result.degraded,
            elapsed_ms=(self._clock() - started) * 1000,
        )
        return SearchExecution(await self._persist(video_id, payload), result)

    async def _persist(self, video_id: UUID, payload: dict[str, object]) -> RetrievalTrace:
        # Convert UUIDs/enums/tuples to a portable JSON value tree. Transcript text
        # is excluded from stage/group copies; IDs and original lineage are kept.
        value = cast(
            dict[str, TraceValue], json.loads(json.dumps(payload, default=str, allow_nan=False))
        )
        trace = RetrievalTrace(uuid4(), video_id, datetime.now(UTC), value)
        await self._traces.save(trace)
        return trace
