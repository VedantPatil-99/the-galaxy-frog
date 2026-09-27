"""Shared bounded lexical/dense retrieval with observable embedding fallback."""

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter
from uuid import UUID

from galaxy_frog.domain.retrieval.errors import (
    EmbeddingProviderError,
    RetrievalIntegrityError,
    TemporalResolutionRequired,
)
from galaxy_frog.domain.retrieval.fusion import fuse_rankings
from galaxy_frog.domain.retrieval.pipeline import (
    QueryKind,
    RetrievalConfig,
    RetrievalMode,
    RetrievalStage,
    RetrievalWarning,
    TextRetrievalResult,
)
from galaxy_frog.domain.retrieval.ports import TranscriptRetriever
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.retrieval.reranking import (
    RerankCandidate,
    RerankerError,
    RerankerErrorCode,
    RerankingRun,
    TextReranker,
)
from galaxy_frog.domain.retrieval.temporal import TimeWindow


class RetrieveTranscript:
    """Use the existing dense adapter's collection-safe indexing lifecycle.

    Stages are sequential because both adapters may share one database session.
    No generator is accepted. The temporal orchestrator supplies a resolved
    window; unresolved temporal requests fail before providers are invoked.
    """

    def __init__(
        self,
        *,
        lexical: TranscriptRetriever,
        dense: TranscriptRetriever,
        reranker: TextReranker | None = None,
        config: RetrievalConfig | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._lexical = lexical
        self._dense = dense
        self._reranker = reranker
        self._config = config or RetrievalConfig()
        self._clock = clock

    async def execute(
        self,
        video_id: UUID,
        query: str,
        *,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        limit: int = 8,
        allow_fallback: bool = True,
        window: TimeWindow | None = None,
    ) -> TextRetrievalResult:
        if not 1 <= limit <= 8:
            raise ValueError("result limit must be between 1 and 8")
        mode = RetrievalMode(mode)
        analysis = analyze_query(query)
        if analysis.kind == QueryKind.TEMPORAL and window is None:
            raise TemporalResolutionRequired(
                "Resolve the temporal constraint before text retrieval."
            )
        started = self._clock()
        stages: list[RetrievalStage] = []
        warnings: list[RetrievalWarning] = []
        if mode != RetrievalMode.DENSE:
            stages.append(
                await self._run(
                    self._lexical, RetrievalMode.LEXICAL, video_id, analysis.lexical_query, window
                )
            )
        if mode != RetrievalMode.LEXICAL:
            dense_started = self._clock()
            try:
                stages.append(
                    await self._run(
                        self._dense, RetrievalMode.DENSE, video_id, analysis.normalized, window
                    )
                )
            except EmbeddingProviderError:
                if not allow_fallback or mode == RetrievalMode.DENSE:
                    raise
                stages.append(
                    RetrievalStage(
                        RetrievalMode.DENSE,
                        (self._clock() - dense_started) * 1000,
                        (),
                        "embedding_provider_unavailable",
                    )
                )
                warnings.append(
                    RetrievalWarning(
                        "embedding_provider_unavailable",
                        "Dense retrieval failed; results use lexical retrieval only.",
                    )
                )
        rankings = fuse_rankings(video_id, tuple(stages), constant=self._config.rrf_constant)
        candidates = rankings[: self._config.fusion_limit]
        reranking = None
        if mode == RetrievalMode.RERANKED and candidates:
            try:
                if self._reranker is None:
                    raise RerankerError(RerankerErrorCode.NOT_CONFIGURED)
                reranked = await self._reranker.rerank(
                    analysis.normalized,
                    tuple(
                        RerankCandidate(item.unit.unit_id, item.unit.text) for item in candidates
                    ),
                )
                if sorted(item.unit_id for item in reranked.scores) != sorted(
                    item.unit.unit_id for item in candidates
                ):
                    raise RetrievalIntegrityError("Reranker changed candidate identities.")
                by_id = {item.unit_id: item for item in reranked.scores}
                ordered = sorted(
                    candidates, key=lambda item: (-by_id[item.unit.unit_id].score, item.fusion_rank)
                )
                candidates = tuple(
                    replace(item, rerank_rank=rank, rerank_score=by_id[item.unit.unit_id].score)
                    for rank, item in enumerate(ordered, start=1)
                )
                reranking = RerankingRun(reranked)
            except RerankerError as exc:
                if not allow_fallback:
                    raise
                reranking = RerankingRun(None, exc.code)
                warnings.append(
                    RetrievalWarning(
                        exc.code.value, "Reranking failed; results retain fused ordering."
                    )
                )
        return TextRetrievalResult(
            analysis=analysis,
            mode=mode,
            config=self._config,
            stages=tuple(stages),
            rankings=rankings,
            candidates=candidates,
            evidence=candidates[:limit],
            warnings=tuple(warnings),
            degraded=bool(warnings),
            elapsed_ms=(self._clock() - started) * 1000,
            reranking=reranking,
        )

    async def _run(
        self,
        retriever: TranscriptRetriever,
        stage: RetrievalMode,
        video_id: UUID,
        query: str,
        window: TimeWindow | None,
    ) -> RetrievalStage:
        started = self._clock()
        results = await retriever.search(
            video_id, query, limit=self._config.candidate_limit, window=window
        )
        if len(results) > self._config.candidate_limit:
            raise RetrievalIntegrityError("Retriever exceeded its candidate limit.")
        if window is not None and any(
            not window.overlaps(row.unit.start_ms, row.unit.end_ms) for row in results
        ):
            raise RetrievalIntegrityError("Retriever returned evidence outside the time window.")
        return RetrievalStage(stage, (self._clock() - started) * 1000, results)
