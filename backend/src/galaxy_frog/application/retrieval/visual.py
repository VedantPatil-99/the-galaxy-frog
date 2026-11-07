"""Query-adaptive frame retrieval after temporal resolution, with inspectable rank fusion."""

import re
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from uuid import UUID

from galaxy_frog.application.retrieval.temporal import RetrievalStatus, TemporalRetrievalResult
from galaxy_frog.application.retrieval.visual_ports import VisualSearch
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError, RetrievalIntegrityError
from galaxy_frog.domain.retrieval.pipeline import RetrievalWarning
from galaxy_frog.domain.retrieval.ports import OCRRetriever
from galaxy_frog.domain.vision.models import RetrievedOCREvidence
from galaxy_frog.domain.vision.policy import (
    EscalationDecision,
    EvidenceSufficiency,
    QuerySignals,
    VisualDecision,
    decide_visual,
    visual_signals,
)
from galaxy_frog.domain.vision.visual import (
    VisualFrame,
    VisualHit,
    VisualProviderError,
    VisualProviderIdentity,
)

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "what",
        "which",
        "who",
        "where",
        "when",
        "how",
        "does",
        "do",
        "did",
        "can",
        "you",
        "me",
        "show",
        "find",
        "read",
        "written",
        "on",
        "in",
        "of",
        "to",
        "this",
        "that",
        "please",
    ]
)


def coverage(query: str, text: str) -> float:
    terms = set(re.findall(r"\b\w+\b", query.casefold())) - _STOP
    words = set(re.findall(r"\b\w+\b", text.casefold()))
    return len(terms & words) / len(terms) if terms else 0.0


@dataclass(frozen=True, slots=True)
class ModalRank:
    modality: str
    identity: str
    stage: str
    rank: int
    score: float


@dataclass(frozen=True, slots=True)
class FusedModalRank:
    modality: str
    identity: str
    rank: int
    rrf_score: float


@dataclass(frozen=True, slots=True)
class ModalityTiming:
    stage: str
    elapsed_ms: float
    failure_code: str | None = None


def fuse_modal_ranks(ranks: tuple[ModalRank, ...], limit: int = 30) -> tuple[FusedModalRank, ...]:
    scores: dict[tuple[str, str], float] = {}
    for item in ranks:
        key = item.modality, item.identity
        scores[key] = scores.get(key, 0) + 1 / (60 + item.rank)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        FusedModalRank(key[0], key[1], rank, score)
        for rank, (key, score) in enumerate(ordered[:limit], 1)
    )


def frame_lineage(frame: VisualFrame) -> dict[str, object]:
    return {
        "video_id": str(frame.video_id),
        "frame_id": str(frame.frame_id),
        "scene_id": str(frame.scene_id) if frame.scene_id else None,
        "sequence": frame.sequence,
        "start_ms": frame.start_ms,
        "end_ms": frame.end_ms,
        "capture_ms": frame.capture_ms,
        "perceptual_hash": asdict(frame.perceptual_hash),
        "content_sha256": frame.content_sha256,
        "source": frame.source,
    }


def distinct_frames(hits: tuple[VisualHit, ...], limit: int) -> tuple[VisualHit, ...]:
    kept: list[VisualHit] = []
    for hit in hits:
        if any(
            hit.frame.frame_id == old.frame.frame_id
            or hit.frame.content_sha256 == old.frame.content_sha256
            or (
                int(hit.frame.perceptual_hash.hex_digest, 16)
                ^ int(old.frame.perceptual_hash.hex_digest, 16)
            ).bit_count()
            <= 4
            for old in kept
        ):
            continue
        kept.append(hit)
        if len(kept) == limit:
            break
    return tuple(kept)


@dataclass(frozen=True, slots=True)
class VisualRetrievalResult:
    signals: QuerySignals
    sufficiency: EvidenceSufficiency
    decisions: tuple[EscalationDecision, ...]
    ocr: tuple[RetrievedOCREvidence, ...]
    hits: tuple[VisualHit, ...]
    nearby: tuple[VisualFrame, ...]
    stages: tuple[ModalRank, ...]
    fusion: tuple[FusedModalRank, ...]
    warnings: tuple[RetrievalWarning, ...]
    elapsed_ms: float
    provider: VisualProviderIdentity | None
    timings: tuple[ModalityTiming, ...] = ()

    @property
    def degraded(self) -> bool:
        return bool(self.warnings) or any(item.degraded for item in self.decisions)

    def trace_payload(self) -> dict[str, object]:
        return {
            "policy_version": "visual-policy-v1",
            "signals": asdict(self.signals),
            "sufficiency": asdict(self.sufficiency),
            "decisions": [asdict(item) for item in self.decisions],
            "provider": asdict(self.provider) if self.provider else None,
            "elapsed_ms": self.elapsed_ms,
            "timings": [asdict(item) for item in self.timings],
            "stages": [asdict(item) for item in self.stages],
            "fusion": [asdict(item) for item in self.fusion],
            "frames": [
                {
                    **frame_lineage(hit.frame),
                    "collection": asdict(hit.collection),
                    "collection_id": str(hit.collection_id) if hit.collection_id else None,
                    "score": hit.score,
                    "rank": hit.rank,
                }
                for hit in self.hits
            ],
            "nearby_frames": [frame_lineage(frame) for frame in self.nearby],
            "ocr": [
                {
                    "block_id": str(hit.block_id),
                    "frame_id": str(hit.frame_id),
                    "video_id": str(hit.video_id),
                    "start_ms": hit.block.start_ms,
                    "end_ms": hit.block.end_ms,
                    "capture_ms": hit.capture_ms,
                    "sequence": hit.block.source_frame_sequence,
                    "provider": asdict(hit.block.provider_spec),
                    "score": hit.score,
                }
                for hit in self.ocr
            ],
            "warnings": [asdict(item) for item in self.warnings],
            "degraded": self.degraded,
        }


class AdaptiveVisualRetrieval:
    """No generator or VLM is reachable from retrieval-only execution."""

    def __init__(
        self,
        *,
        ocr_lexical: OCRRetriever,
        ocr_dense: OCRRetriever | None = None,
        visual: VisualSearch | None = None,
        provider: VisualProviderIdentity | None = None,
    ) -> None:
        self._ocr_lexical, self._ocr_dense, self._visual, self._provider = (
            ocr_lexical,
            ocr_dense,
            visual,
            provider,
        )

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "provider": asdict(self._provider) if self._provider else None,
            "policy_version": "visual-policy-v1",
            "visual_candidate_limit": 12,
            "nearby_frame_limit": 4,
            "rrf_constant": 60,
        }

    async def execute(
        self,
        video_id: UUID,
        query: str,
        text: TemporalRetrievalResult,
        *,
        limit: int = 8,
        allow_fallback: bool = True,
    ) -> VisualRetrievalResult:
        if not 1 <= limit <= 8:
            raise ValueError("visual result limit must be between one and eight")
        started = perf_counter()
        signals = visual_signals(query)
        sufficiency = EvidenceSufficiency(
            coverage(query, " ".join(unit.text for group in text.groups for unit in group.units)), 0
        )
        decisions: list[EscalationDecision] = []
        warnings: list[RetrievalWarning] = []
        timings: list[ModalityTiming] = []
        ranks: list[ModalRank] = []
        ocr_by_id: dict[str, RetrievedOCREvidence] = {}
        hits: tuple[VisualHit, ...] = ()
        nearby: tuple[VisualFrame, ...] = ()
        if text.status != RetrievalStatus.RESOLVED:
            decisions.append(
                EscalationDecision(VisualDecision.INSUFFICIENT, "temporal_selection_required")
            )
        elif not signals.visual_terms:
            decisions.append(decide_visual(signals, sufficiency))
        else:
            if text.retrieval:
                ranks.extend(
                    ModalRank(
                        "transcript", item.unit.unit_id, "transcript", index, item.fusion_score
                    )
                    for index, item in enumerate(text.retrieval.candidates[:30], 1)
                )
            for stage, retriever in (
                ("ocr_lexical", self._ocr_lexical),
                ("ocr_dense", self._ocr_dense),
            ):
                if retriever is None:
                    continue
                stage_started = perf_counter()
                try:
                    candidates = await retriever.search(
                        video_id, text.intent.text_query or query, limit=30, window=text.window
                    )
                except EmbeddingProviderError:
                    if not allow_fallback:
                        raise
                    timings.append(
                        ModalityTiming(
                            stage, (perf_counter() - stage_started) * 1000, "ocr_dense_unavailable"
                        )
                    )
                    warnings.append(
                        RetrievalWarning(
                            "ocr_dense_unavailable",
                            "Dense OCR retrieval is unavailable; other evidence was retained.",
                        )
                    )
                    continue
                timings.append(ModalityTiming(stage, (perf_counter() - stage_started) * 1000))
                if len(candidates) > 30:
                    raise RetrievalIntegrityError("OCR retriever exceeded the candidate budget.")
                for index, item in enumerate(candidates, 1):
                    if item.video_id != video_id or (
                        text.window is not None
                        and (
                            item.capture_ms is None
                            or item.capture_ms < text.window.start_ms
                            or (
                                text.window.end_ms is not None
                                and item.capture_ms >= text.window.end_ms
                            )
                        )
                    ):
                        raise RetrievalIntegrityError(
                            "OCR retrieval violated video or temporal scope."
                        )
                    previous = ocr_by_id.get(str(item.block_id))
                    if previous is not None and (
                        previous.frame_id != item.frame_id or previous.block != item.block
                    ):
                        raise RetrievalIntegrityError(
                            "OCR stages returned conflicting source lineage."
                        )
                    ranks.append(ModalRank("ocr", str(item.block_id), stage, index, item.score))
                    ocr_by_id[str(item.block_id)] = item
            fused_ocr = fuse_modal_ranks(
                tuple(item for item in ranks if item.modality == "ocr"), limit
            )
            selected_ocr: list[RetrievedOCREvidence] = []
            chars = 0
            for rank in fused_ocr:
                item = ocr_by_id[rank.identity]
                if chars + len(item.block.text) > 12000:
                    warnings.append(
                        RetrievalWarning(
                            "ocr_context_limit",
                            "OCR evidence outside the context budget was omitted.",
                        )
                    )
                    continue
                selected_ocr.append(item)
                chars += len(item.block.text)
            ocr_by_id = {str(item.block_id): item for item in selected_ocr}
            sufficiency = replace(
                sufficiency,
                ocr_coverage=coverage(query, " ".join(item.block.text for item in selected_ocr)),
            )
            decisions.append(decide_visual(signals, sufficiency))
            if decisions[-1].decision == VisualDecision.RETRIEVE:
                if self._visual is None:
                    if not allow_fallback:
                        from galaxy_frog.domain.vision.visual import VisualFailureCode

                        raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
                    decisions.append(
                        EscalationDecision(
                            VisualDecision.UNAVAILABLE, "visual_embedding_not_configured", True
                        )
                    )
                    warnings.append(
                        RetrievalWarning(
                            "visual_provider_unavailable",
                            "Visual embedding is disabled; transcript/OCR evidence was retained.",
                        )
                    )
                else:
                    visual_started = perf_counter()
                    try:
                        initial = await self._visual.search(
                            video_id, text.intent.text_query or query, limit=12, window=text.window
                        )
                        timings.append(
                            ModalityTiming("visual_dense", (perf_counter() - visual_started) * 1000)
                        )
                        if len(initial) > 12:
                            raise RetrievalIntegrityError(
                                "Visual retriever exceeded the candidate budget."
                            )
                        if any(
                            item.frame.video_id != video_id
                            or (
                                text.window is not None
                                and (
                                    item.frame.capture_ms is None
                                    or item.frame.capture_ms < text.window.start_ms
                                    or (
                                        text.window.end_ms is not None
                                        and item.frame.capture_ms >= text.window.end_ms
                                    )
                                )
                            )
                            for item in initial
                        ):
                            raise RetrievalIntegrityError(
                                "Visual retrieval violated video or temporal scope."
                            )
                        ranks.extend(
                            ModalRank(
                                "visual",
                                str(item.frame.frame_id),
                                "visual_dense",
                                item.rank,
                                item.score,
                            )
                            for item in initial
                        )
                        hits = distinct_frames(initial, limit)
                        if hits:
                            nearby_started = perf_counter()
                            nearby = await self._visual.nearby(video_id, hits, window=text.window)
                            timings.append(
                                ModalityTiming(
                                    "nearby_frames", (perf_counter() - nearby_started) * 1000
                                )
                            )
                            if len(nearby) > 4:
                                raise RetrievalIntegrityError(
                                    "Visual expansion exceeded the frame budget."
                                )
                            if any(
                                frame.video_id != video_id
                                or (
                                    text.window is not None
                                    and (
                                        frame.capture_ms is None
                                        or frame.capture_ms < text.window.start_ms
                                        or (
                                            text.window.end_ms is not None
                                            and frame.capture_ms >= text.window.end_ms
                                        )
                                    )
                                )
                                for frame in nearby
                            ):
                                raise RetrievalIntegrityError(
                                    "Visual expansion violated video or temporal scope."
                                )
                        sufficiency = replace(sufficiency, visual_hits=len(hits))
                        decisions.append(decide_visual(signals, sufficiency, after_retrieval=True))
                    except VisualProviderError as exc:
                        if not allow_fallback:
                            raise
                        timings.append(
                            ModalityTiming(
                                "visual", (perf_counter() - visual_started) * 1000, exc.code.value
                            )
                        )
                        decisions.append(
                            EscalationDecision(VisualDecision.UNAVAILABLE, exc.code.value, True)
                        )
                        warnings.append(
                            RetrievalWarning(
                                exc.code.value,
                                "Visual retrieval is unavailable; transcript/OCR evidence was retained.",
                            )
                        )
        return VisualRetrievalResult(
            signals,
            sufficiency,
            tuple(decisions),
            tuple(ocr_by_id.values()),
            hits,
            nearby[:4],
            tuple(ranks),
            fuse_modal_ranks(tuple(ranks)),
            tuple(warnings),
            (perf_counter() - started) * 1000,
            self._provider,
            tuple(timings),
        )
