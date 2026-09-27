"""Bounded immutable records for inspectable text retrieval stages."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.reranking import RerankingRun
from galaxy_frog.domain.transcripts.models import RetrievalUnit


class RetrievalMode(StrEnum):
    LEXICAL = "lexical"
    DENSE = "dense"
    HYBRID = "hybrid"
    RERANKED = "reranked"


class QueryKind(StrEnum):
    SPOKEN = "spoken"
    EXACT = "exact"
    TEMPORAL = "temporal"


@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    original: str
    normalized: str
    lexical_query: str
    exact_phrases: tuple[str, ...]
    kind: QueryKind
    version: str = "1"


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    candidate_limit: int = 30
    rrf_constant: int = 60
    fusion_limit: int = 30

    def __post_init__(self) -> None:
        if not 1 <= self.candidate_limit <= 30:
            raise ValueError("candidate_limit must be between 1 and 30")
        if self.rrf_constant < 1:
            raise ValueError("rrf_constant must be positive")
        if not 1 <= self.fusion_limit <= 30:
            raise ValueError("fusion_limit must be between 1 and 30")


@dataclass(frozen=True, slots=True)
class StageRank:
    stage: RetrievalMode
    rank: int
    score: float


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    video_id: UUID
    unit: RetrievalUnit
    stages: tuple[StageRank, ...]
    fusion_rank: int
    fusion_score: float
    rerank_rank: int | None = None
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class RetrievalStage:
    stage: RetrievalMode
    elapsed_ms: float
    results: tuple[RetrievedEvidence, ...]
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class TextRetrievalResult:
    analysis: QueryAnalysis
    mode: RetrievalMode
    config: RetrievalConfig
    stages: tuple[RetrievalStage, ...]
    rankings: tuple[RankedEvidence, ...]
    candidates: tuple[RankedEvidence, ...]
    evidence: tuple[RankedEvidence, ...]
    warnings: tuple[RetrievalWarning, ...]
    degraded: bool
    elapsed_ms: float
    reranking: RerankingRun | None = None
