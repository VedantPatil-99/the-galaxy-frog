"""Application service for multimodal (transcript + visual OCR) video retrieval."""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from galaxy_frog.domain.retrieval.multimodal import (
    MultimodalEvidence,
    QueryModality,
    QueryRoutingAnalysis,
    analyze_multimodal_query,
    fuse_multimodal_rankings,
)
from galaxy_frog.domain.retrieval.ports import OCRRetriever, TranscriptRetriever
from galaxy_frog.domain.retrieval.temporal import TimeWindow


@dataclass(frozen=True, slots=True)
class MultimodalSearchResult:
    """Outcome of a multimodal search query across audio and visual evidence."""

    video_id: UUID
    query: str
    routing: QueryRoutingAnalysis
    evidence: tuple[MultimodalEvidence, ...]
    warnings: tuple[str, ...]
    degraded: bool
    elapsed_ms: float


class MultimodalSearchService:
    """Orchestrate query routing and multi-modal evidence fusion with observable fallback."""

    def __init__(
        self,
        *,
        transcript_retriever: TranscriptRetriever,
        ocr_retriever: OCRRetriever,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._transcript_retriever = transcript_retriever
        self._ocr_retriever = ocr_retriever
        self._clock = clock

    async def search(
        self,
        video_id: UUID,
        query: str,
        *,
        limit: int = 8,
        window: TimeWindow | None = None,
        forced_modality: QueryModality | None = None,
    ) -> MultimodalSearchResult:
        """Route query and search spoken transcripts and visual OCR blocks."""

        if not 1 <= limit <= 30:
            msg = "result limit must be between 1 and 30"
            raise ValueError(msg)

        routing = analyze_multimodal_query(query, forced_modality=forced_modality)
        started = self._clock()

        warnings: list[str] = []
        transcript_hits = ()
        ocr_hits = ()

        if routing.modality in (QueryModality.SPOKEN, QueryModality.HYBRID):
            try:
                transcript_hits = await self._transcript_retriever.search(
                    video_id, query, limit=limit, window=window
                )
            except Exception as exc:
                if routing.modality == QueryModality.SPOKEN:
                    raise
                warnings.append(f"transcript_retrieval_failed: {exc}")

        if routing.modality in (QueryModality.VISUAL, QueryModality.HYBRID):
            try:
                ocr_hits = await self._ocr_retriever.search(
                    video_id, query, limit=limit, window=window
                )
            except Exception as exc:
                if routing.modality == QueryModality.VISUAL:
                    raise
                warnings.append(f"ocr_retrieval_failed: {exc}")

        evidence = fuse_multimodal_rankings(
            video_id,
            transcript_hits,
            ocr_hits,
            limit=limit,
        )

        return MultimodalSearchResult(
            video_id=video_id,
            query=query,
            routing=routing,
            evidence=evidence,
            warnings=tuple(warnings),
            degraded=bool(warnings),
            elapsed_ms=(self._clock() - started) * 1000,
        )
