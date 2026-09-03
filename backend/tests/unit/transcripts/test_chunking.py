"""Tests for deterministic temporal chunking and cue provenance."""

import pytest

from galaxy_frog.domain.transcripts import TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.pipelines.transcription import TemporalChunker, TemporalChunkingPolicy


def make_cue(order: int, text: str) -> TranscriptCue:
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    return TranscriptCue.from_source(
        source=source,
        track_id="manual:en",
        language_code="en",
        caption_kind=CaptionKind.MANUAL,
        cue=SourceCaptionCue(order, order * 1000, (order + 1) * 1000, text),
    )


def test_chunks_deterministically_and_preserves_ordered_cue_ids() -> None:
    cues = tuple(make_cue(index, f"Sentence {index}.") for index in range(6))
    chunker = TemporalChunker(
        TemporalChunkingPolicy(
            min_tokens=4,
            max_tokens=8,
            min_duration_ms=2000,
            max_duration_ms=4000,
            overlap_ms=1000,
        )
    )

    first = chunker.chunk(tuple(reversed(cues)))
    second = chunker.chunk(cues)

    assert first == second
    assert first[0].start_ms == 0
    assert first[0].cue_ids == (cues[0].cue_id, cues[1].cue_id)
    assert all(unit.start_ms < unit.end_ms for unit in first)
    assert all(unit.text for unit in first)


def test_returns_empty_output_for_empty_input() -> None:
    assert TemporalChunker().chunk(()) == ()


def test_does_not_duplicate_a_single_closed_final_chunk() -> None:
    cue = make_cue(0, "One.")
    chunker = TemporalChunker(
        TemporalChunkingPolicy(
            min_tokens=1,
            max_tokens=2,
            min_duration_ms=1,
            max_duration_ms=1000,
            overlap_ms=100,
        )
    )

    assert len(chunker.chunk((cue,))) == 1


def test_rejects_duplicate_cues() -> None:
    cue = make_cue(0, "One.")

    with pytest.raises(ValueError, match="unique"):
        TemporalChunker().chunk((cue, cue))


@pytest.mark.parametrize(
    "arguments",
    [
        {"min_tokens": 0},
        {"min_tokens": 5, "max_tokens": 4},
        {"min_duration_ms": 0},
        {"min_duration_ms": 5, "max_duration_ms": 4},
        {"overlap_ms": -1},
        {"overlap_ms": 45_000},
    ],
)
def test_policy_rejects_invalid_bounds(arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        TemporalChunkingPolicy(**arguments)
