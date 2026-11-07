"""Multimodal retrieval models, query routing, and reciprocal rank fusion."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from uuid import UUID

from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.vision.models import RetrievedOCREvidence

_VISUAL_CUES = (
    "slide",
    "slides",
    "screen",
    "screens",
    "diagram",
    "diagrams",
    "code",
    "table",
    "tables",
    "chart",
    "charts",
    "written",
    "board",
    "whiteboard",
    "visual",
    "ui",
)

_SPOKEN_CUES = (
    "say",
    "said",
    "speak",
    "spoke",
    "mention",
    "mentioned",
    "talk",
    "talked",
    "discuss",
    "discussed",
    "hear",
    "heard",
    "voice",
    "audio",
)


class EvidenceModality(StrEnum):
    """Origin of a retrieved evidence item."""

    TRANSCRIPT = "transcript"
    OCR = "ocr"


class QueryModality(StrEnum):
    """Target retrieval modality indicated by query intent or configuration."""

    SPOKEN = "spoken"
    VISUAL = "visual"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class QueryRoutingAnalysis:
    """Classified modality intent and matched routing cues."""

    original: str
    normalized: str
    modality: QueryModality
    visual_terms: tuple[str, ...]
    spoken_terms: tuple[str, ...]


def analyze_multimodal_query(
    query: str,
    forced_modality: QueryModality | None = None,
) -> QueryRoutingAnalysis:
    """Analyze query intent to select speech, visual OCR, or hybrid search."""

    normalized = " ".join(query.strip().split())
    if not normalized:
        msg = "query must not be empty"
        raise ValueError(msg)

    words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", normalized.lower()))
    found_visual = tuple(sorted(cue for cue in _VISUAL_CUES if cue in words))
    found_spoken = tuple(sorted(cue for cue in _SPOKEN_CUES if cue in words))

    if forced_modality is not None:
        selected_modality = forced_modality
    elif found_visual and not found_spoken:
        selected_modality = QueryModality.VISUAL
    elif found_spoken and not found_visual:
        selected_modality = QueryModality.SPOKEN
    else:
        selected_modality = QueryModality.HYBRID

    return QueryRoutingAnalysis(
        original=query,
        normalized=normalized,
        modality=selected_modality,
        visual_terms=found_visual,
        spoken_terms=found_spoken,
    )


@dataclass(frozen=True, slots=True)
class MultimodalEvidence:
    """Ranked evidence from either spoken audio transcripts or visual OCR."""

    modality: EvidenceModality
    video_id: UUID
    start_ms: int
    end_ms: int
    text: str
    score: float
    fusion_rank: int
    temporal_bonus: float = 0.0
    transcript_evidence: RetrievedEvidence | None = None
    ocr_evidence: RetrievedOCREvidence | None = None

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            msg = "start_ms must be non-negative"
            raise ValueError(msg)
        if self.end_ms <= self.start_ms:
            msg = "end_ms must be greater than start_ms"
            raise ValueError(msg)
        if not isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            msg = "score must be finite and between 0.0 and 1.0"
            raise ValueError(msg)
        if self.fusion_rank < 1:
            msg = "fusion_rank must be positive"
            raise ValueError(msg)
        if self.temporal_bonus < 0.0:
            msg = "temporal_bonus must be non-negative"
            raise ValueError(msg)

        if not isinstance(self.modality, EvidenceModality):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "modality must be an EvidenceModality member"
            raise ValueError(msg)

        if self.modality == EvidenceModality.TRANSCRIPT:
            if self.transcript_evidence is None or self.ocr_evidence is not None:
                msg = "transcript modality requires transcript_evidence and no ocr_evidence"
                raise ValueError(msg)
            if self.transcript_evidence.video_id != self.video_id:
                msg = "transcript_evidence video_id does not match"
                raise ValueError(msg)
            if (
                self.transcript_evidence.unit.start_ms != self.start_ms
                or self.transcript_evidence.unit.end_ms != self.end_ms
            ):
                msg = "transcript_evidence intervals do not match"
                raise ValueError(msg)
            if self.transcript_evidence.unit.text != self.text:
                msg = "transcript_evidence text does not match"
                raise ValueError(msg)
        else:
            if self.ocr_evidence is None or self.transcript_evidence is not None:
                msg = "ocr modality requires ocr_evidence and no transcript_evidence"
                raise ValueError(msg)
            if self.ocr_evidence.video_id != self.video_id:
                msg = "ocr_evidence video_id does not match"
                raise ValueError(msg)

            if (
                self.ocr_evidence.block.start_ms != self.start_ms
                or self.ocr_evidence.block.end_ms != self.end_ms
            ):
                msg = "ocr_evidence intervals do not match"
                raise ValueError(msg)
            if self.ocr_evidence.block.text != self.text:
                msg = "ocr_evidence text does not match"
                raise ValueError(msg)


def fuse_multimodal_rankings(
    video_id: UUID,
    transcript_candidates: Sequence[RetrievedEvidence],
    ocr_candidates: Sequence[RetrievedOCREvidence],
    *,
    constant: int = 60,
    temporal_overlap_bonus: float = 0.05,
    limit: int = 30,
) -> tuple[MultimodalEvidence, ...]:
    """Fuse transcript and OCR rankings using RRF and temporal co-occurrence bonuses."""

    if constant < 1:
        msg = "RRF constant must be positive"
        raise ValueError(msg)
    if limit < 1:
        msg = "limit must be positive"
        raise ValueError(msg)
    if temporal_overlap_bonus < 0.0:
        msg = "temporal_overlap_bonus must be non-negative"
        raise ValueError(msg)

    for item in transcript_candidates:
        if item.video_id != video_id:
            msg = "Retrieved transcript evidence belongs to another video."
            raise RetrievalIntegrityError(msg)
    for item in ocr_candidates:
        if item.video_id != video_id:
            msg = "Retrieved OCR evidence belongs to another video."
            raise RetrievalIntegrityError(msg)

    # 1. Base RRF scores by intra-modality rank
    t_scores: list[float] = [
        1.0 / (constant + rank) for rank in range(1, len(transcript_candidates) + 1)
    ]
    o_scores: list[float] = [1.0 / (constant + rank) for rank in range(1, len(ocr_candidates) + 1)]

    # 2. Check pairwise temporal overlaps between modalities for co-occurrence bonus
    t_bonuses = [0.0] * len(transcript_candidates)
    o_bonuses = [0.0] * len(ocr_candidates)

    for i, t_hit in enumerate(transcript_candidates):
        for j, o_hit in enumerate(ocr_candidates):
            overlaps = max(t_hit.unit.start_ms, o_hit.block.start_ms) < min(
                t_hit.unit.end_ms, o_hit.block.end_ms
            )
            if overlaps:
                t_bonuses[i] = temporal_overlap_bonus
                o_bonuses[j] = temporal_overlap_bonus

    # 3. Combine scored items
    combined_items: list[
        tuple[float, float, int, EvidenceModality, RetrievedEvidence | RetrievedOCREvidence]
    ] = []

    for i, t_hit in enumerate(transcript_candidates):
        raw_score = t_scores[i] + t_bonuses[i]
        combined_items.append(
            (raw_score, t_bonuses[i], t_hit.unit.start_ms, EvidenceModality.TRANSCRIPT, t_hit)
        )

    for j, o_hit in enumerate(ocr_candidates):
        raw_score = o_scores[j] + o_bonuses[j]
        combined_items.append(
            (raw_score, o_bonuses[j], o_hit.block.start_ms, EvidenceModality.OCR, o_hit)
        )

    if not combined_items:
        return ()

    # 4. Sort by descending score, then ascending start_ms
    combined_items.sort(key=lambda x: (-x[0], x[2], x[3]))

    # Normalization: map highest combined score to 1.0 (relative rank calibration)
    max_raw_score = combined_items[0][0]

    results: list[MultimodalEvidence] = []
    for rank, (raw_score, bonus, _, modality, item) in enumerate(combined_items[:limit], start=1):
        norm_score = max(0.0, min(1.0, raw_score / max_raw_score)) if max_raw_score > 0 else 0.0
        if modality == EvidenceModality.TRANSCRIPT:
            t_item = item  # pyright: ignore[reportAssignmentType]
            assert isinstance(t_item, RetrievedEvidence)
            results.append(
                MultimodalEvidence(
                    modality=EvidenceModality.TRANSCRIPT,
                    video_id=video_id,
                    start_ms=t_item.unit.start_ms,
                    end_ms=t_item.unit.end_ms,
                    text=t_item.unit.text,
                    score=norm_score,
                    fusion_rank=rank,
                    temporal_bonus=bonus,
                    transcript_evidence=t_item,
                )
            )
        else:
            o_item = item  # pyright: ignore[reportAssignmentType]
            assert isinstance(o_item, RetrievedOCREvidence)
            results.append(
                MultimodalEvidence(
                    modality=EvidenceModality.OCR,
                    video_id=video_id,
                    start_ms=o_item.block.start_ms,
                    end_ms=o_item.block.end_ms,
                    text=o_item.block.text,
                    score=norm_score,
                    fusion_rank=rank,
                    temporal_bonus=bonus,
                    ocr_evidence=o_item,
                )
            )

    return tuple(results)
