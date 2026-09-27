"""Shared bounded lexical/dense retrieval with observable embedding fallback."""

from collections.abc import Callable
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


class RetrieveTranscript:
    """Use the existing dense adapter's collection-safe indexing lifecycle.

    Stages are sequential because both adapters may share one database session.
    No generator is accepted by this service. Temporal resolution is added in P3.4;
    until then temporal requests fail explicitly before providers are invoked.
    """

    def __init__(
        self,
        *,
        lexical: TranscriptRetriever,
        dense: TranscriptRetriever,
        config: RetrievalConfig | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._lexical = lexical
        self._dense = dense
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
    ) -> TextRetrievalResult:
        if not 1 <= limit <= 8:
            raise ValueError("result limit must be between 1 and 8")
        mode = RetrievalMode(mode)
        analysis = analyze_query(query)
        if analysis.kind == QueryKind.TEMPORAL:
            raise TemporalResolutionRequired(
                "Resolve the temporal constraint before text retrieval."
            )
        started = self._clock()
        stages: list[RetrievalStage] = []
        warnings: list[RetrievalWarning] = []
        if mode != RetrievalMode.DENSE:
            stages.append(
                await self._run(
                    self._lexical, RetrievalMode.LEXICAL, video_id, analysis.lexical_query
                )
            )
        if mode != RetrievalMode.LEXICAL:
            dense_started = self._clock()
            try:
                stages.append(
                    await self._run(self._dense, RetrievalMode.DENSE, video_id, analysis.normalized)
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
        )

    async def _run(
        self, retriever: TranscriptRetriever, stage: RetrievalMode, video_id: UUID, query: str
    ) -> RetrievalStage:
        started = self._clock()
        results = await retriever.search(video_id, query, limit=self._config.candidate_limit)
        if len(results) > self._config.candidate_limit:
            raise RetrievalIntegrityError("Retriever exceeded its candidate limit.")
        return RetrievalStage(stage, (self._clock() - started) * 1000, results)
