"""Normalized transcript and retrieval values with exact provenance."""

from dataclasses import dataclass
from hashlib import sha256

from galaxy_frog.domain.videos.models import CaptionKind, SourceCaptionCue, SourceReference


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    """Normalized caption cue tied to its canonical source and caption track."""

    cue_id: str
    source: SourceReference
    track_id: str
    language_code: str
    caption_kind: CaptionKind
    source_order: int
    start_ms: int
    end_ms: int
    text: str

    @classmethod
    def from_source(
        cls,
        *,
        source: SourceReference,
        track_id: str,
        language_code: str,
        caption_kind: CaptionKind,
        cue: SourceCaptionCue,
    ) -> TranscriptCue:
        """Build a deterministic normalized cue from source-native data."""

        identity = "\x1f".join(
            (
                source.kind,
                source.external_id,
                track_id,
                str(cue.source_order),
                str(cue.start_ms),
                str(cue.end_ms),
                cue.text,
            )
        )
        return cls(
            cue_id=sha256(identity.encode()).hexdigest(),
            source=source,
            track_id=track_id,
            language_code=language_code,
            caption_kind=caption_kind,
            source_order=cue.source_order,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=cue.text.strip(),
        )

    def __post_init__(self) -> None:
        if len(self.cue_id) != 64:
            msg = "cue_id must be a SHA-256 hexadecimal digest"
            raise ValueError(msg)
        if self.source_order < 0 or self.start_ms < 0 or self.end_ms <= self.start_ms:
            msg = "transcript cue interval and order must be valid"
            raise ValueError(msg)
        if not self.text:
            msg = "text must not be empty"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RetrievalUnit:
    """A searchable transcript interval with ordered cue provenance."""

    unit_id: str
    start_ms: int
    end_ms: int
    text: str
    cue_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.unit_id) != 64:
            msg = "unit_id must be a SHA-256 hexadecimal digest"
            raise ValueError(msg)
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            msg = "retrieval unit interval must be valid"
            raise ValueError(msg)
        if not self.text or not self.cue_ids:
            msg = "retrieval unit must contain text and cue provenance"
            raise ValueError(msg)
