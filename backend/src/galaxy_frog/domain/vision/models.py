"""Provider-independent vision values with temporal evidence and provenance."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from uuid import UUID


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        msg = f"{field_name} must not be blank"
        raise ValueError(msg)


def _require_interval(start_ms: int, end_ms: int) -> None:
    if start_ms < 0:
        msg = "start_ms must be non-negative"
        raise ValueError(msg)
    if end_ms <= start_ms:
        msg = "end_ms must be greater than start_ms"
        raise ValueError(msg)


def _require_optional_score(
    value: float | None,
    method: str | None,
    *,
    field_name: str,
) -> None:
    if value is None:
        if method is not None:
            msg = f"{field_name}_method requires {field_name}"
            raise ValueError(msg)
        return
    if not isfinite(value) or not 0 <= value <= 1:
        msg = f"{field_name} must be finite and between zero and one"
        raise ValueError(msg)
    if method is None or not method.strip():
        msg = f"{field_name}_method is required when {field_name} is present"
        raise ValueError(msg)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must be timezone-aware"
        raise ValueError(msg)


class FrameSource(StrEnum):
    """How a frame was selected from the video stream."""

    SCENE_BOUNDARY = "scene_boundary"
    PERIODIC = "periodic"


@dataclass(frozen=True, slots=True)
class OCRProviderSpec:
    """Identity and revision of one OCR provider instance.

    Follows the same pattern as TranscriptionProviderSpec so persistence and
    reporting layers can treat provider provenance uniformly.
    """

    provider: str
    provider_revision: str

    def __post_init__(self) -> None:
        _require_text(self.provider, "provider")
        _require_text(self.provider_revision, "provider_revision")


@dataclass(frozen=True, slots=True)
class FrameSample:
    """One extracted video frame with a half-open millisecond interval.

    start_ms and end_ms bound the visual content this frame represents, not
    the frame's exact capture timestamp.  start_ms == 0 is valid for the first
    frame of a video.
    """

    video_id: UUID
    sequence: int
    start_ms: int
    end_ms: int
    path: Path
    source: FrameSource

    def __post_init__(self) -> None:
        if self.sequence < 0:
            msg = "sequence must be non-negative"
            raise ValueError(msg)
        _require_interval(self.start_ms, self.end_ms)
        if not self.path.is_absolute():
            msg = "path must be absolute"
            raise ValueError(msg)
        if not isinstance(self.source, FrameSource):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "source must be a FrameSource member"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PerceptualHash:
    """Perceptual hash of one frame image for near-duplicate detection.

    hex_digest is the raw hex string produced by the hashing algorithm (e.g.
    dhash).  bits is the number of meaningful bits (e.g. 64 for an 8x8 dhash).
    """

    hex_digest: str
    algorithm: str
    bits: int

    def __post_init__(self) -> None:
        _require_text(self.hex_digest, "hex_digest")
        _require_text(self.algorithm, "algorithm")
        if self.bits <= 0:
            msg = "bits must be positive"
            raise ValueError(msg)
        expected_hex_chars = (self.bits + 3) // 4
        if len(self.hex_digest) != expected_hex_chars:
            msg = (
                f"hex_digest length {len(self.hex_digest)} does not match "
                f"{self.bits} bits ({expected_hex_chars} hex chars expected)"
            )
            raise ValueError(msg)
        try:
            int(self.hex_digest, 16)
        except ValueError as exc:
            msg = "hex_digest must be a valid hexadecimal string"
            raise ValueError(msg) from exc


@dataclass(frozen=True, slots=True)
class SceneBoundary:
    """One detected visual scene change with a half-open millisecond interval.

    score represents the detector's confidence that a scene boundary exists,
    where 1.0 is maximum confidence.
    """

    start_ms: int
    end_ms: int
    score: float

    def __post_init__(self) -> None:
        _require_interval(self.start_ms, self.end_ms)
        if not isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            msg = "score must be a finite float between 0.0 and 1.0"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SceneDetectionResult:
    """All detected scene boundaries for one video with detector provenance."""

    video_id: UUID
    duration_ms: int
    boundaries: tuple[SceneBoundary, ...]
    detector: str
    detector_revision: str

    def __post_init__(self) -> None:
        if self.duration_ms <= 0:
            msg = "duration_ms must be positive"
            raise ValueError(msg)
        _require_text(self.detector, "detector")
        _require_text(self.detector_revision, "detector_revision")


@dataclass(frozen=True, slots=True)
class DeduplicatedFrameSet:
    """Outcome of perceptual-hash deduplication across a set of sampled frames.

    kept contains the unique frames selected for OCR.  dropped_count is the
    number of frames discarded as near-duplicates so callers can log credit
    savings without retaining the dropped paths.
    """

    kept: tuple[FrameSample, ...]
    dropped_count: int
    hash_algorithm: str
    hamming_threshold: int

    def __post_init__(self) -> None:
        if self.dropped_count < 0:
            msg = "dropped_count must be non-negative"
            raise ValueError(msg)
        _require_text(self.hash_algorithm, "hash_algorithm")
        if self.hamming_threshold < 0:
            msg = "hamming_threshold must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class OCRBlock:
    """One text region extracted from a video frame.

    start_ms/end_ms bound the temporal scene the source frame was drawn from.
    confidence and confidence_method are paired: both present or both absent.
    This mirrors the transcript-cue confidence convention so retrieval and
    evidence layers can apply a uniform filtering policy.
    """

    text: str
    start_ms: int
    end_ms: int
    source_frame_sequence: int
    provider_spec: OCRProviderSpec
    confidence: float | None = None
    confidence_method: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.text, "text")
        _require_interval(self.start_ms, self.end_ms)
        if self.source_frame_sequence < 0:
            msg = "source_frame_sequence must be non-negative"
            raise ValueError(msg)
        if not isinstance(self.provider_spec, OCRProviderSpec):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "provider_spec must be an OCRProviderSpec"
            raise ValueError(msg)
        _require_optional_score(self.confidence, self.confidence_method, field_name="confidence")


@dataclass(frozen=True, slots=True)
class OCRExtractionRequest:
    """An authorised request to OCR-extract one deduplicated video frame."""

    job_id: UUID
    video_id: UUID
    frame: FrameSample

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrameSample):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "frame must be a FrameSample"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class OCRExtractionResult:
    """All OCR blocks extracted from one frame with provider provenance."""

    request: OCRExtractionRequest
    blocks: tuple[OCRBlock, ...]
    provider_spec: OCRProviderSpec
    extracted_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.extracted_at, "extracted_at")
        if not isinstance(self.request, OCRExtractionRequest):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "request must be an OCRExtractionRequest"
            raise ValueError(msg)
        if not isinstance(self.provider_spec, OCRProviderSpec):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "provider_spec must be an OCRProviderSpec"
            raise ValueError(msg)
