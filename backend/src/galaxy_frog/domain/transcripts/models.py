"""Normalized transcript and retrieval values with exact provenance."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from uuid import UUID

from galaxy_frog.domain.transcription.models import TranscriptionCue, TranscriptionResult
from galaxy_frog.domain.videos.models import CaptionKind, SourceCaptionCue, SourceReference


class TranscriptOrigin(StrEnum):
    """The evidence-producing path for a normalized transcript cue."""

    CAPTION = "caption"
    ASR = "asr"


@dataclass(frozen=True, slots=True)
class TranscriptCue:
    """Normalized caption or ASR cue with exact evidence lineage."""

    cue_id: str
    source: SourceReference
    language_code: str
    source_order: int
    start_ms: int
    end_ms: int
    text: str
    origin: TranscriptOrigin = TranscriptOrigin.CAPTION
    track_id: str | None = None
    caption_kind: CaptionKind | None = None
    transcription_run_id: UUID | None = None
    confidence: float | None = None
    confidence_method: str | None = None

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
            language_code=language_code,
            source_order=cue.source_order,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=cue.text.strip(),
            origin=TranscriptOrigin.CAPTION,
            track_id=track_id,
            caption_kind=caption_kind,
        )

    @classmethod
    def from_transcription(
        cls,
        *,
        run_id: UUID,
        result: TranscriptionResult,
        cue: TranscriptionCue,
    ) -> TranscriptCue:
        """Normalize one provider cue without discarding model or confidence linkage."""

        identity = "\x1f".join(
            (
                result.source.kind,
                result.source.external_id,
                TranscriptOrigin.ASR,
                result.spec.provider,
                result.spec.model,
                result.spec.model_revision,
                str(cue.source_order),
                str(cue.start_ms),
                str(cue.end_ms),
                cue.text,
            )
        )
        return cls(
            cue_id=sha256(identity.encode()).hexdigest(),
            source=result.source,
            language_code=result.language_code,
            source_order=cue.source_order,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            text=cue.text.strip(),
            origin=TranscriptOrigin.ASR,
            transcription_run_id=run_id,
            confidence=cue.confidence,
            confidence_method=cue.confidence_method,
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
        if not self.language_code.strip():
            msg = "language_code must not be empty"
            raise ValueError(msg)
        if self.origin is TranscriptOrigin.CAPTION:
            if self.track_id is None or not self.track_id.strip() or self.caption_kind is None:
                msg = "caption cues require track and caption-kind provenance"
                raise ValueError(msg)
            if self.transcription_run_id is not None:
                msg = "caption cues cannot reference a transcription run"
                raise ValueError(msg)
        elif self.origin is TranscriptOrigin.ASR:
            if self.transcription_run_id is None:
                msg = "ASR cues require a transcription run"
                raise ValueError(msg)
            if self.track_id is not None or self.caption_kind is not None:
                msg = "ASR cues cannot claim caption-track provenance"
                raise ValueError(msg)
        else:
            msg = "origin must be a supported transcript origin"
            raise ValueError(msg)
        if self.confidence is None:
            if self.confidence_method is not None:
                msg = "confidence_method requires confidence"
                raise ValueError(msg)
        elif (
            not isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
            or self.confidence_method is None
            or not self.confidence_method.strip()
        ):
            msg = "confidence requires a score from zero to one and a method"
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
