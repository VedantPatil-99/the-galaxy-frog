"""Intermediate media values shared across downloader and normalizer adapters."""

from dataclasses import dataclass
from pathlib import Path


def _require_absolute_file_metadata(path: Path, size_bytes: int) -> None:
    if not path.is_absolute():
        msg = "media path must be absolute"
        raise ValueError(msg)
    if size_bytes <= 0:
        msg = "media size_bytes must be positive"
        raise ValueError(msg)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        msg = f"{field_name} must not be blank"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DownloadedAudio:
    """One source-native audio file with downloader provenance."""

    path: Path
    size_bytes: int
    downloader: str
    downloader_revision: str

    def __post_init__(self) -> None:
        _require_absolute_file_metadata(self.path, self.size_bytes)
        _require_text(self.downloader, "downloader")
        _require_text(self.downloader_revision, "downloader_revision")


@dataclass(frozen=True, slots=True)
class NormalizedAudio:
    """Verified audio normalized for a later transcription provider."""

    path: Path
    duration_ms: int
    size_bytes: int
    media_type: str
    codec: str
    sample_rate_hz: int
    channels: int
    normalizer: str
    normalizer_revision: str

    def __post_init__(self) -> None:
        _require_absolute_file_metadata(self.path, self.size_bytes)
        if self.duration_ms <= 0:
            msg = "duration_ms must be positive"
            raise ValueError(msg)
        if self.sample_rate_hz <= 0:
            msg = "sample_rate_hz must be positive"
            raise ValueError(msg)
        if self.channels <= 0:
            msg = "channels must be positive"
            raise ValueError(msg)
        for field_name in ("media_type", "codec", "normalizer", "normalizer_revision"):
            _require_text(getattr(self, field_name), field_name)
