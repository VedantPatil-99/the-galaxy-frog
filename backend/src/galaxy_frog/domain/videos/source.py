"""Provider-independent video source protocol and normalized failures."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from galaxy_frog.domain.videos.models import (
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)


class VideoSourceErrorCode(StrEnum):
    """Stable source-stage failures suitable for API translation."""

    INVALID_SOURCE = "INVALID_SOURCE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_AUTH_REQUIRED = "SOURCE_AUTH_REQUIRED"
    UNSUPPORTED_VIDEO = "UNSUPPORTED_VIDEO"
    TRANSCRIPT_UNAVAILABLE = "TRANSCRIPT_UNAVAILABLE"


class VideoSourceError(RuntimeError):
    """A source failure with safe public metadata."""

    def __init__(
        self,
        code: VideoSourceErrorCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@runtime_checkable
class VideoSource(Protocol):
    """Asynchronous contract implemented by every video source adapter."""

    source_kind: VideoSourceKind

    def canonicalize(self, locator: str) -> SourceReference:
        """Validate a caller locator and return its stable source identity."""
        ...

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        """Fetch safe source metadata without downloading media."""
        ...

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        """Return available manual and automatic caption tracks."""
        ...

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        """Return ordered cues for one selected caption track."""
        ...
