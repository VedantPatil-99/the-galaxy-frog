"""Deterministic, conservative routing; rank scores are not confidence probabilities."""

import re
from dataclasses import dataclass
from enum import StrEnum

from galaxy_frog.domain.vision.visual import ExecutionLocation

_VISUAL = frozenset(
    [
        "diagram",
        "chart",
        "graph",
        "color",
        "colour",
        "shape",
        "image",
        "picture",
        "object",
        "scene",
        "person",
        "wearing",
        "shown",
        "screen",
        "slide",
        "table",
        "code",
        "visual",
        "logo",
        "arrow",
        "red",
        "blue",
        "green",
        "frame",
        "visible",
        "appear",
        "appears",
        "animated",
        "animation",
        "animal",
        "turtle",
        "panda",
        "shirt",
    ]
)
_REASON = frozenset(
    [
        "why",
        "explain",
        "compare",
        "relationship",
        "direction",
        "difference",
        "count",
        "many",
        "color",
        "colour",
        "wearing",
        "left",
        "right",
        "above",
        "below",
        "arrow",
    ]
)
_OCR = frozenset(
    [
        "read",
        "quote",
        "caption",
        "captions",
        "text",
        "written",
        "label",
        "labels",
        "title",
        "says",
        "code",
        "table",
    ]
)


class VisualDecision(StrEnum):
    TEXT_SUFFICIENT = "transcript_ocr_sufficient"
    RETRIEVE = "visual_retrieval_required"
    REASON = "vlm_reasoning_required"
    UNAVAILABLE = "visual_provider_unavailable"
    PRIVACY_DENIED = "external_processing_not_permitted"
    INSUFFICIENT = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class QuerySignals:
    visual_terms: tuple[str, ...]
    reasoning_terms: tuple[str, ...]
    ocr_terms: tuple[str, ...]


def visual_signals(query: str) -> QuerySignals:
    if not query.strip() or len(query) > 2000:
        raise ValueError("query must contain one to 2000 characters")
    words = set(re.findall(r"\b\w+\b", query.casefold()))
    reasoning = words & _REASON
    if words & _VISUAL and not words & _OCR:
        reasoning |= words & {"what", "which", "who", "is", "are"}
    return QuerySignals(
        tuple(sorted(words & _VISUAL)), tuple(sorted(reasoning)), tuple(sorted(words & _OCR))
    )


@dataclass(frozen=True, slots=True)
class EvidenceSufficiency:
    """Coverage is lexical support, never a calibrated answer confidence."""

    transcript_coverage: float
    ocr_coverage: float
    visual_hits: int = 0

    def __post_init__(self) -> None:
        if (
            not 0 <= self.transcript_coverage <= 1
            or not 0 <= self.ocr_coverage <= 1
            or self.visual_hits < 0
        ):
            raise ValueError("invalid evidence sufficiency")


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    decision: VisualDecision
    reason: str
    degraded: bool = False


def decide_visual(
    signals: QuerySignals, evidence: EvidenceSufficiency, *, after_retrieval: bool = False
) -> EscalationDecision:
    if not signals.visual_terms:
        return EscalationDecision(VisualDecision.TEXT_SUFFICIENT, "no_visual_information_requested")
    if signals.ocr_terms and not signals.reasoning_terms and evidence.ocr_coverage >= 0.75:
        return EscalationDecision(VisualDecision.TEXT_SUFFICIENT, "ocr_covers_requested_text")
    if not after_retrieval:
        return EscalationDecision(VisualDecision.RETRIEVE, "visual_content_not_established_by_text")
    if evidence.visual_hits == 0:
        return EscalationDecision(VisualDecision.INSUFFICIENT, "no_relevant_visual_frames", True)
    if signals.reasoning_terms or (signals.ocr_terms and evidence.ocr_coverage < 0.75):
        return EscalationDecision(
            VisualDecision.REASON, "retrieval_cannot_resolve_visual_attributes_or_relations"
        )
    return EscalationDecision(
        VisualDecision.TEXT_SUFFICIENT, "timestamped_visual_evidence_retrieved"
    )


def external_allowed(location: ExecutionLocation, *, configured: bool, permitted: bool) -> bool:
    """A credential alone cannot authorize external frame submission."""
    return location == ExecutionLocation.LOCAL or (configured and permitted)
