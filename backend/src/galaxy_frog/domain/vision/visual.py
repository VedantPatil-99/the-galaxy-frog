"""Visual retrieval and reasoning contracts. No runtime, storage, or SDK dependencies."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from pathlib import Path
from uuid import UUID

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.vision.models import FrameSource, PerceptualHash


class ExecutionLocation(StrEnum):
    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class VisualProviderIdentity:
    provider: str
    model: str
    revision: str
    location: ExecutionLocation

    def __post_init__(self) -> None:
        if any(
            not value.strip() or len(value) > 256
            for value in (self.provider, self.model, self.revision)
        ):
            raise ValueError("provider identity must contain bounded nonempty values")
        if not isinstance(self.location, ExecutionLocation):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("execution location must be explicit")


@dataclass(frozen=True, slots=True)
class VisualFrame:
    """Original frame identity; the local path is internal and never serialized to HTTP/traces."""

    video_id: UUID
    frame_id: UUID
    scene_id: UUID | None
    sequence: int
    start_ms: int
    end_ms: int
    perceptual_hash: PerceptualHash
    content_sha256: str
    source: FrameSource
    path: Path

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("frame requires a sequence and half-open interval")
        if len(self.content_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.content_sha256
        ):
            raise ValueError("frame content hash must be lowercase SHA-256")
        if not self.path.is_absolute():
            raise ValueError("frame path must be absolute")


@dataclass(frozen=True, slots=True)
class VisualEmbedding:
    frame: VisualFrame
    collection: EmbeddingCollectionSpec
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        validate_vector(self.values, self.collection)


def validate_vector(values: tuple[float, ...], collection: EmbeddingCollectionSpec) -> None:
    if len(values) != collection.dimension or not all(isfinite(value) for value in values):
        raise ValueError("visual vector does not match the collection")
    norm = sum(value * value for value in values)
    if not isfinite(norm) or norm <= 0:
        raise ValueError("visual vector must have a finite nonzero norm")
    if collection.normalization != "l2" or abs(norm - 1) > 0.001:
        raise ValueError("visual vectors require explicit unit L2 normalization")


@dataclass(frozen=True, slots=True)
class VisualHit:
    frame: VisualFrame
    collection: EmbeddingCollectionSpec
    score: float
    rank: int
    expanded: bool = False

    def __post_init__(self) -> None:
        if not isfinite(self.score) or not -1 <= self.score <= 1 or self.rank < 1:
            raise ValueError("visual hit requires a finite cosine score and positive rank")


@dataclass(frozen=True, slots=True)
class VisualReasoningRequest:
    video_id: UUID
    question: str
    frames: tuple[VisualFrame, ...]
    evidence: str
    prompt_version: str = "visual-grounding-v1"
    schema_version: str = "visual-answer-v1"
    external_permitted: bool = False

    def __post_init__(self) -> None:
        if not self.question.strip() or len(self.question) > 2000 or len(self.evidence) > 12000:
            raise ValueError("reasoning input exceeds the query/evidence budget")
        if not 1 <= len(self.frames) <= 4 or len({f.frame_id for f in self.frames}) != len(
            self.frames
        ):
            raise ValueError("reasoning requires one to four distinct frames")
        if any(frame.video_id != self.video_id for frame in self.frames):
            raise ValueError("reasoning frames must belong to the requested video")
        if not self.prompt_version.strip() or not self.schema_version.strip():
            raise ValueError("reasoning versions must be explicit")


@dataclass(frozen=True, slots=True)
class VisualReasoningResult:
    answer: str
    cited_frame_ids: tuple[UUID, ...]
    provider: VisualProviderIdentity
    elapsed_ms: float
    insufficient_evidence: bool = False

    def __post_init__(self) -> None:
        if not self.answer.strip() or len(self.answer) > 8000:
            raise ValueError("reasoning answer must be bounded and nonempty")
        if not isfinite(self.elapsed_ms) or self.elapsed_ms < 0:
            raise ValueError("reasoning timing must be finite and nonnegative")
        if len(self.cited_frame_ids) > 4 or len(set(self.cited_frame_ids)) != len(
            self.cited_frame_ids
        ):
            raise ValueError("reasoning citations must be distinct and bounded")
        if not self.insufficient_evidence and not self.cited_frame_ids:
            raise ValueError("supported visual reasoning requires citations")


class VisualFailureCode(StrEnum):
    UNAVAILABLE = "visual_provider_unavailable"
    INVALID_OUTPUT = "visual_invalid_output"
    PRIVACY_DENIED = "external_processing_not_permitted"
    TIMEOUT = "visual_timeout"
    BUSY = "visual_busy"


class VisualProviderError(RuntimeError):
    """Only a stable safe code crosses the provider boundary."""

    def __init__(self, code: VisualFailureCode) -> None:
        super().__init__(code.value)
        self.code = code
