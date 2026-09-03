"""Deterministic transcript chunking without losing cue provenance."""

import re
from dataclasses import dataclass
from hashlib import sha256

from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")


@dataclass(frozen=True, slots=True)
class TemporalChunkingPolicy:
    """Configurable boundaries for deterministic unit tests and production defaults."""

    min_tokens: int = 150
    max_tokens: int = 300
    min_duration_ms: int = 20_000
    max_duration_ms: int = 45_000
    overlap_ms: int = 5_000

    def __post_init__(self) -> None:
        if self.min_tokens <= 0 or self.max_tokens < self.min_tokens:
            msg = "token bounds must be positive and ordered"
            raise ValueError(msg)
        if self.min_duration_ms <= 0 or self.max_duration_ms < self.min_duration_ms:
            msg = "duration bounds must be positive and ordered"
            raise ValueError(msg)
        if self.overlap_ms < 0 or self.overlap_ms >= self.max_duration_ms:
            msg = "overlap_ms must be non-negative and below max_duration_ms"
            raise ValueError(msg)


class TemporalChunker:
    """Group timestamped cues while preserving their exact ordered identities."""

    def __init__(self, policy: TemporalChunkingPolicy | None = None) -> None:
        self.policy = policy or TemporalChunkingPolicy()

    @staticmethod
    def approximate_tokens(text: str) -> int:
        """Return a deterministic provider-independent token approximation."""

        return len(_TOKEN_PATTERN.findall(text))

    def chunk(self, cues: tuple[TranscriptCue, ...]) -> tuple[RetrievalUnit, ...]:
        """Create retrieval units from source-ordered cues."""

        if not cues:
            return ()
        ordered = tuple(sorted(cues, key=lambda cue: cue.source_order))
        if len({cue.cue_id for cue in ordered}) != len(ordered):
            msg = "cue IDs must be unique"
            raise ValueError(msg)

        chunks: list[tuple[TranscriptCue, ...]] = []
        current: list[TranscriptCue] = []
        index = 0
        while index < len(ordered):
            cue = ordered[index]
            current.append(cue)
            text = " ".join(item.text for item in current)
            token_count = self.approximate_tokens(text)
            duration_ms = max(item.end_ms for item in current) - min(
                item.start_ms for item in current
            )
            sentence_boundary = _SENTENCE_END.search(cue.text.rstrip()) is not None
            should_close = (
                token_count >= self.policy.max_tokens or duration_ms >= self.policy.max_duration_ms
            )
            preferred_close = (
                token_count >= self.policy.min_tokens
                and duration_ms >= self.policy.min_duration_ms
                and sentence_boundary
            )
            if should_close or preferred_close:
                chunks.append(tuple(current))
                overlap_floor = max(item.end_ms for item in current) - self.policy.overlap_ms
                overlap = [item for item in current if item.end_ms > overlap_floor]
                current = overlap[-1:] if overlap and overlap[-1] is cue else overlap
            index += 1

        if current:
            final = tuple(current)
            if not chunks or tuple(item.cue_id for item in final) != tuple(
                item.cue_id for item in chunks[-1]
            ):
                chunks.append(final)

        return tuple(self._build_unit(chunk) for chunk in chunks)

    @staticmethod
    def _build_unit(cues: tuple[TranscriptCue, ...]) -> RetrievalUnit:
        text = " ".join(cue.text for cue in cues).strip()
        cue_ids = tuple(cue.cue_id for cue in cues)
        start_ms = min(cue.start_ms for cue in cues)
        end_ms = max(cue.end_ms for cue in cues)
        identity = "\x1f".join((*cue_ids, str(start_ms), str(end_ms), text))
        return RetrievalUnit(
            unit_id=sha256(identity.encode()).hexdigest(),
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            cue_ids=cue_ids,
        )
