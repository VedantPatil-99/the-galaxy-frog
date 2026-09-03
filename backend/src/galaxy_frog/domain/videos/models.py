"""Immutable source values that retain identity and temporal provenance."""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class VideoSourceKind(StrEnum):
    """Supported source families for the transcript-first slice."""

    YOUTUBE = "youtube"
    LOCAL_FILE = "local_file"


class CaptionKind(StrEnum):
    """Whether a caption track was authored or generated automatically."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Canonical provider identity for one video source."""

    kind: VideoSourceKind
    external_id: str
    canonical_url: str

    def __post_init__(self) -> None:
        _require_text(self.external_id, "external_id")
        parsed = urlsplit(self.canonical_url)
        if parsed.scheme not in {"https", "file"}:
            msg = "canonical_url must use https or file"
            raise ValueError(msg)
        if parsed.scheme == "https" and not parsed.hostname:
            msg = "canonical_url must include a host"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SafeVideoMetadata:
    """Provider metadata safe to persist and expose through the API."""

    reference: SourceReference
    title: str
    duration_ms: int
    channel_name: str | None = None
    thumbnail_url: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.title, "title")
        if self.duration_ms <= 0:
            msg = "duration_ms must be positive"
            raise ValueError(msg)
        if self.thumbnail_url is not None:
            parsed = urlsplit(self.thumbnail_url)
            if parsed.scheme != "https" or not parsed.hostname:
                msg = "thumbnail_url must be an absolute https URL"
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    """Stable descriptor used to select a provider caption track."""

    track_id: str
    language_code: str
    kind: CaptionKind
    label: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.track_id, "track_id")
        _require_text(self.language_code, "language_code")


@dataclass(frozen=True, slots=True)
class SourceCaptionCue:
    """A source-native caption cue using a half-open millisecond interval."""

    source_order: int
    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        if self.source_order < 0:
            msg = "source_order must be non-negative"
            raise ValueError(msg)
        if self.start_ms < 0:
            msg = "start_ms must be non-negative"
            raise ValueError(msg)
        if self.end_ms <= self.start_ms:
            msg = "end_ms must be greater than start_ms"
            raise ValueError(msg)
        _require_text(self.text, "text")
