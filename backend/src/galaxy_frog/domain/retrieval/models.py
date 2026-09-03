"""Immutable embedding and evidence values."""

from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from galaxy_frog.domain.transcripts.models import RetrievalUnit


@dataclass(frozen=True, slots=True)
class EmbeddingCollectionSpec:
    """Identity of one non-interchangeable embedding space."""

    provider: str
    model: str
    revision: str
    dimension: int
    normalization: str

    def __post_init__(self) -> None:
        values = (self.provider, self.model, self.revision, self.normalization)
        if any(not value.strip() for value in values):
            msg = "embedding collection metadata must not be empty"
            raise ValueError(msg)
        if self.dimension <= 0:
            msg = "embedding dimension must be positive"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    """A ranked transcript unit returned from one compatible collection."""

    video_id: UUID
    unit: RetrievalUnit
    score: float

    def __post_init__(self) -> None:
        if not isfinite(self.score) or not 0 <= self.score <= 1:
            msg = "retrieval score must be finite and between zero and one"
            raise ValueError(msg)
