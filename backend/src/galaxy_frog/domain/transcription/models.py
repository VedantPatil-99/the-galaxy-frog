"""Provider-independent transcription values with temporal evidence provenance."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from uuid import UUID

from galaxy_frog.domain.media.models import AcquiredAudio, AudioFallbackReason
from galaxy_frog.domain.videos.models import SourceReference


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        msg = f"{field_name} must not be blank"
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


def _require_device(value: object) -> None:
    if not isinstance(value, TranscriptionDevice):
        msg = "device must be a supported transcription device"
        raise ValueError(msg)


def _require_compute_type(value: object) -> None:
    if not isinstance(value, TranscriptionComputeType):
        msg = "compute_type must be a supported transcription compute type"
        raise ValueError(msg)


def _require_fallback_reason(value: object) -> None:
    if not isinstance(value, AudioFallbackReason):
        msg = "fallback_reason must be an approved caption outcome"
        raise ValueError(msg)


def _require_provider_spec(value: object) -> None:
    if not isinstance(value, TranscriptionProviderSpec):
        msg = "spec must identify the transcription provider and model"
        raise ValueError(msg)


class TranscriptionDevice(StrEnum):
    """Explicit local execution devices supported by the Phase 2 provider."""

    CPU = "cpu"
    CUDA = "cuda"


class TranscriptionComputeType(StrEnum):
    """CTranslate2 compute modes that may be selected explicitly."""

    INT8 = "int8"
    INT8_FLOAT16 = "int8_float16"
    FLOAT16 = "float16"
    FLOAT32 = "float32"


class TranscriptionErrorCode(StrEnum):
    """Stable provider-neutral failures safe to persist on an ingestion job."""

    DEPENDENCY_UNAVAILABLE = "ASR_DEPENDENCY_UNAVAILABLE"
    MODEL_UNAVAILABLE = "ASR_MODEL_UNAVAILABLE"
    DEVICE_UNAVAILABLE = "ASR_DEVICE_UNAVAILABLE"
    INVALID_AUDIO = "ASR_INVALID_AUDIO"
    EXECUTION_FAILED = "ASR_EXECUTION_FAILED"
    TIMEOUT = "ASR_TIMEOUT"


class TranscriptionError(RuntimeError):
    """A transcription failure with safe metadata and explicit retryability."""

    def __init__(
        self,
        code: TranscriptionErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class TranscriptionProviderSpec:
    """Identity of the exact provider, model and local execution environment."""

    provider: str
    provider_revision: str
    model: str
    model_revision: str
    device: TranscriptionDevice
    compute_type: TranscriptionComputeType

    def __post_init__(self) -> None:
        for field_name in ("provider", "provider_revision", "model", "model_revision"):
            _require_text(getattr(self, field_name), field_name)
        _require_device(self.device)
        _require_compute_type(self.compute_type)
        if self.device is TranscriptionDevice.CPU and self.compute_type in {
            TranscriptionComputeType.FLOAT16,
            TranscriptionComputeType.INT8_FLOAT16,
        }:
            msg = "CPU transcription does not support float16 compute"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    """One normalized audio artifact authorized for provider transcription."""

    audio_job_id: UUID
    attempt: int
    source: SourceReference
    fallback_reason: AudioFallbackReason
    audio_path: Path
    audio_start_ms: int
    audio_end_ms: int
    language_code: str | None = None

    @classmethod
    def from_acquired_audio(
        cls,
        audio: AcquiredAudio,
        *,
        language_code: str | None = None,
    ) -> TranscriptionRequest:
        """Copy the complete authorized audio lineage into a transcription request."""

        return cls(
            audio_job_id=audio.job_id,
            attempt=audio.attempt,
            source=audio.source,
            fallback_reason=audio.fallback_reason,
            audio_path=audio.path,
            audio_start_ms=audio.start_ms,
            audio_end_ms=audio.end_ms,
            language_code=language_code,
        )

    def __post_init__(self) -> None:
        if self.attempt < 1:
            msg = "attempt must be positive"
            raise ValueError(msg)
        _require_fallback_reason(self.fallback_reason)
        if not self.audio_path.is_absolute():
            msg = "audio_path must be absolute"
            raise ValueError(msg)
        if self.audio_start_ms != 0 or self.audio_end_ms <= self.audio_start_ms:
            msg = "audio interval must be a positive whole-source half-open interval"
            raise ValueError(msg)
        if self.language_code is not None:
            normalized = self.language_code.strip().casefold()
            if not normalized:
                msg = "language_code must not be blank"
                raise ValueError(msg)
            object.__setattr__(self, "language_code", normalized)


@dataclass(frozen=True, slots=True)
class TranscriptionCue:
    """One ordered provider cue with an exact half-open source interval."""

    source_order: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None
    confidence_method: str | None

    def __post_init__(self) -> None:
        if self.source_order < 0:
            msg = "source_order must not be negative"
            raise ValueError(msg)
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            msg = "transcription cue interval must be valid"
            raise ValueError(msg)
        _require_text(self.text, "text")
        _require_optional_score(
            self.confidence,
            self.confidence_method,
            field_name="confidence",
        )


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Complete transcription output with model, language and source provenance."""

    job_id: UUID
    attempt: int
    source: SourceReference
    fallback_reason: AudioFallbackReason
    audio_start_ms: int
    audio_end_ms: int
    spec: TranscriptionProviderSpec
    language_code: str
    language_confidence: float | None
    language_confidence_method: str | None
    cues: tuple[TranscriptionCue, ...]
    processing_seconds: float
    transcribed_at: datetime

    def __post_init__(self) -> None:
        if self.attempt < 1:
            msg = "attempt must be positive"
            raise ValueError(msg)
        _require_fallback_reason(self.fallback_reason)
        if self.audio_start_ms != 0 or self.audio_end_ms <= self.audio_start_ms:
            msg = "audio interval must be a positive whole-source half-open interval"
            raise ValueError(msg)
        _require_provider_spec(self.spec)
        _require_text(self.language_code, "language_code")
        object.__setattr__(self, "language_code", self.language_code.strip().casefold())
        _require_optional_score(
            self.language_confidence,
            self.language_confidence_method,
            field_name="language_confidence",
        )
        if not self.cues:
            msg = "transcription result must contain at least one cue"
            raise ValueError(msg)
        if self.processing_seconds < 0 or not isfinite(self.processing_seconds):
            msg = "processing_seconds must be finite and non-negative"
            raise ValueError(msg)
        if self.transcribed_at.tzinfo is None or self.transcribed_at.utcoffset() is None:
            msg = "transcribed_at must be timezone-aware"
            raise ValueError(msg)

        previous_start_ms = -1
        for expected_order, cue in enumerate(self.cues):
            if cue.source_order != expected_order:
                msg = "transcription cue order must be contiguous"
                raise ValueError(msg)
            if cue.start_ms < previous_start_ms:
                msg = "transcription cues must be ordered by start time"
                raise ValueError(msg)
            if cue.start_ms < self.audio_start_ms or cue.end_ms > self.audio_end_ms:
                msg = "transcription cue must remain within the source audio interval"
                raise ValueError(msg)
            previous_start_ms = cue.start_ms


@dataclass(frozen=True, slots=True)
class TranscriptionCheckpoint:
    """Durable provider output that can be reused without repeating inference."""

    run_id: UUID
    audio_asset_id: UUID
    result: TranscriptionResult
