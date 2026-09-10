"""Bounded audio acquisition values with source and tool provenance."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from galaxy_frog.domain.videos.models import SourceReference


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        msg = f"{field_name} must not be blank"
        raise ValueError(msg)


def _require_fallback_reason(value: object) -> None:
    if not isinstance(value, AudioFallbackReason):
        msg = "fallback_reason must be an approved caption outcome"
        raise ValueError(msg)


class AudioFallbackReason(StrEnum):
    """Caption outcomes that are allowed to trigger audio acquisition."""

    CAPTIONS_UNAVAILABLE = "captions_unavailable"
    CAPTIONS_UNUSABLE = "captions_unusable"


class AudioAcquisitionErrorCode(StrEnum):
    """Stable provider-neutral failures safe to persist on an ingestion job."""

    UNSUPPORTED_SOURCE = "AUDIO_UNSUPPORTED_SOURCE"
    DURATION_LIMIT_EXCEEDED = "AUDIO_DURATION_LIMIT_EXCEEDED"
    DOWNLOAD_FAILED = "AUDIO_DOWNLOAD_FAILED"
    DOWNLOAD_SIZE_LIMIT_EXCEEDED = "AUDIO_DOWNLOAD_SIZE_LIMIT_EXCEEDED"
    PROBE_FAILED = "AUDIO_PROBE_FAILED"
    NORMALIZATION_FAILED = "AUDIO_NORMALIZATION_FAILED"
    OUTPUT_SIZE_LIMIT_EXCEEDED = "AUDIO_OUTPUT_SIZE_LIMIT_EXCEEDED"
    TIMEOUT = "AUDIO_TIMEOUT"
    WORKSPACE_ERROR = "AUDIO_WORKSPACE_ERROR"


class AudioAcquisitionError(RuntimeError):
    """An audio failure with stable safe metadata and explicit retryability."""

    def __init__(
        self,
        code: AudioAcquisitionErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AudioAcquisitionLimits:
    """Hard resource ceilings applied before and during local media work."""

    max_duration_ms: int
    max_download_bytes: int
    max_output_bytes: int
    timeout_seconds: float
    max_concurrency: int

    def __post_init__(self) -> None:
        for field_name in ("max_duration_ms", "max_download_bytes", "max_output_bytes"):
            if getattr(self, field_name) <= 0:
                msg = f"{field_name} must be positive"
                raise ValueError(msg)
        if self.timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)
        if self.max_concurrency < 1:
            msg = "max_concurrency must be at least one"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AudioAcquisitionRequest:
    """Authorized request for whole-source audio after a known caption outcome."""

    job_id: UUID
    attempt: int
    source: SourceReference
    expected_duration_ms: int
    fallback_reason: AudioFallbackReason

    def __post_init__(self) -> None:
        if self.attempt < 1:
            msg = "attempt must be positive"
            raise ValueError(msg)
        if self.expected_duration_ms <= 0:
            msg = "expected_duration_ms must be positive"
            raise ValueError(msg)
        _require_fallback_reason(self.fallback_reason)


@dataclass(frozen=True, slots=True)
class AcquiredAudio:
    """Normalized local audio with an exact source interval and evidence lineage."""

    job_id: UUID
    attempt: int
    source: SourceReference
    fallback_reason: AudioFallbackReason
    path: Path
    start_ms: int
    end_ms: int
    size_bytes: int
    media_type: str
    codec: str
    sample_rate_hz: int
    channels: int
    downloader: str
    downloader_revision: str
    normalizer: str
    normalizer_revision: str
    acquired_at: datetime

    def __post_init__(self) -> None:
        if self.attempt < 1:
            msg = "attempt must be positive"
            raise ValueError(msg)
        _require_fallback_reason(self.fallback_reason)
        if not self.path.is_absolute():
            msg = "path must be absolute"
            raise ValueError(msg)
        if self.start_ms != 0:
            msg = "whole-source audio must begin at zero milliseconds"
            raise ValueError(msg)
        if self.end_ms <= self.start_ms:
            msg = "end_ms must be greater than start_ms"
            raise ValueError(msg)
        if self.size_bytes <= 0:
            msg = "size_bytes must be positive"
            raise ValueError(msg)
        if self.sample_rate_hz <= 0:
            msg = "sample_rate_hz must be positive"
            raise ValueError(msg)
        if self.channels <= 0:
            msg = "channels must be positive"
            raise ValueError(msg)
        for field_name in (
            "media_type",
            "codec",
            "downloader",
            "downloader_revision",
            "normalizer",
            "normalizer_revision",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None:
            msg = "acquired_at must be timezone-aware"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AudioAsset:
    """Durable identity and lifecycle state for one acquired audio artifact."""

    asset_id: UUID
    audio: AcquiredAudio
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.deleted_at is not None and (
            self.deleted_at.tzinfo is None or self.deleted_at.utcoffset() is None
        ):
            msg = "deleted_at must be timezone-aware"
            raise ValueError(msg)
        if self.deleted_at is not None and self.deleted_at < self.audio.acquired_at:
            msg = "deleted_at must not precede acquired_at"
            raise ValueError(msg)

    @property
    def is_available(self) -> bool:
        """Return whether the durable record still points to retained local media."""

        return self.deleted_at is None
