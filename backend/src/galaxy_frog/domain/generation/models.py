"""Immutable generation drafts and validated grounded answers."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class AnswerConfidence(StrEnum):
    """Small public confidence vocabulary for transcript-only answers."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class GenerationCitation:
    """Provider-proposed citation that must be validated before exposure."""

    retrieval_unit_id: str
    quote: str


@dataclass(frozen=True, slots=True)
class GenerationDraft:
    """Untrusted structured provider output."""

    answer: str
    confidence: AnswerConfidence
    citations: tuple[GenerationCitation, ...]


@dataclass(frozen=True, slots=True)
class ValidatedCitation:
    """Evidence citation resolved to one known transcript interval."""

    video_id: UUID
    retrieval_unit_id: str
    cue_ids: tuple[str, ...]
    quote: str
    start_ms: int
    end_ms: int
    modality: str = "transcript"


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    """Public grounded answer after deterministic citation validation."""

    answer: str
    confidence: AnswerConfidence
    evidence: tuple[ValidatedCitation, ...]
    warnings: tuple[str, ...] = ()
    degraded_mode: bool = False
