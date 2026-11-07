"""Deterministic timeline segmentation from persisted video signals."""

import itertools
from dataclasses import dataclass
from math import sqrt

_MIN_SPAN = 45_000
_COARSE_SPAN = 600_000
_MAX_CHAPTERS = 20


@dataclass(frozen=True, slots=True)
class TimedVector:
    start_ms: int
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ChapterSpan:
    start_ms: int
    end_ms: int
    reasons: tuple[str, ...]


def _cosine_distance(left: list[float], right: list[float]) -> float:
    ln = sqrt(sum(x * x for x in left))
    rn = sqrt(sum(x * x for x in right))
    if ln == 0 or rn == 0:
        return 0.0
    return 1 - sum(x * y for x, y in zip(left, right, strict=True)) / (ln * rn)


def _sum_vectors(vectors: tuple[TimedVector, ...]) -> list[float]:
    return [sum(xs) for xs in zip(*(item.vector for item in vectors), strict=True)]


def _topic_changes(vectors: tuple[TimedVector, ...]) -> set[int]:
    ordered = tuple(sorted(vectors, key=lambda item: item.start_ms))
    return {
        ordered[i].start_ms
        for i in range(1, len(ordered))
        if _cosine_distance(
            _sum_vectors(ordered[max(0, i - 3) : i]), _sum_vectors(ordered[i : i + 3])
        )
        >= 0.30
    }


def _cue_gaps(cues: tuple[tuple[int, int], ...]) -> set[int]:
    covered_until: int | None = None
    gaps: set[int] = set()
    for start, end in sorted(cues):
        if covered_until is not None and start - covered_until >= 3_000:
            gaps.add(start)
        covered_until = end if covered_until is None else max(covered_until, end)
    return gaps


def chapter_spans(
    duration_ms: int,
    cues: tuple[tuple[int, int], ...],
    vectors: tuple[TimedVector, ...],
    scenes: tuple[tuple[int, int], ...],
    *,
    has_ocr: bool,
) -> tuple[ChapterSpan, ...]:
    """Partition the entire video; scene cuts supplement speech unless OCR is the only text."""

    topic = _topic_changes(vectors)
    gaps = _cue_gaps(cues)
    scene_times = {start for start, _ in scenes}
    visual_only = {start for start, end in scenes if not cues and has_ocr and end - start >= 15_000}
    candidates: dict[int, tuple[int, tuple[str, ...]]] = {}
    for time in topic | gaps | visual_only:
        if not _MIN_SPAN <= time <= duration_ms - _MIN_SPAN:
            continue
        signals = (
            (time in topic, 3, "topic_change"),
            (time in gaps, 2, "transcript_gap"),
            (any(abs(time - scene) <= 3_000 for scene in scene_times), 1, "scene_change"),
        )
        candidates[time] = (
            sum(weight for present, weight, _ in signals if present),
            tuple(reason for present, _, reason in signals if present),
        )

    cuts: dict[int, tuple[str, ...]] = {0: ("video_start",), duration_ms: ()}
    for coarse in range(_COARSE_SPAN, duration_ms - _MIN_SPAN + 1, _COARSE_SPAN):
        nearby = [time for time in candidates if abs(time - coarse) <= 30_000]
        if nearby:
            cut = min(nearby, key=lambda time: (abs(time - coarse), -candidates[time][0], time))
            cuts[cut] = candidates[cut][1]
        else:
            cuts[coarse] = ("long_span",)

    for time in sorted(candidates, key=lambda time: (-candidates[time][0], time)):
        if len(cuts) - 1 >= _MAX_CHAPTERS:
            break
        if all(abs(time - boundary) >= _MIN_SPAN for boundary in cuts):
            cuts[time] = candidates[time][1]

    boundaries = sorted(cuts)
    return tuple(
        ChapterSpan(start, end, cuts[start]) for start, end in itertools.pairwise(boundaries)
    )
