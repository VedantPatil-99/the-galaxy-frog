"""Bounded temporal display groups retaining original units and cue lineage."""

from dataclasses import dataclass

from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.pipeline import RankedEvidence, RetrievalWarning
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.records import TranscriptRecord


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    padding_ms: int = 15000
    merge_gap_ms: int = 5000
    max_context_chars: int = 12000

    def __post_init__(self) -> None:
        if not 0 <= self.padding_ms <= 60000 or not 0 <= self.merge_gap_ms <= 10000:
            raise ValueError("expansion intervals exceed supported bounds")
        if not 1 <= self.max_context_chars <= 30000:
            raise ValueError("context budget must be between 1 and 30000 characters")


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    start_ms: int
    end_ms: int
    hits: tuple[RankedEvidence, ...]
    units: tuple[RetrievalUnit, ...]
    cues: tuple[TranscriptCue, ...]


DEFAULT_EXPANSION = ExpansionConfig()


@dataclass(frozen=True, slots=True)
class ExpandedEvidence:
    groups: tuple[EvidenceGroup, ...]
    context_chars: int
    warnings: tuple[RetrievalWarning, ...]


def validate_evidence(record: TranscriptRecord, hits: tuple[RankedEvidence, ...]) -> None:
    units = {unit.unit_id: unit for unit in record.units}
    cues = {cue.cue_id: cue for cue in record.cues}
    for hit in hits:
        if hit.video_id != record.video.video_id or units.get(hit.unit.unit_id) != hit.unit:
            raise RetrievalIntegrityError("Retrieved evidence differs from the stored transcript.")
    if any(cue_id not in cues for unit in record.units for cue_id in unit.cue_ids):
        raise RetrievalIntegrityError("Transcript unit refers to a missing cue.")
    if any(cue.source != record.video.metadata.reference for cue in record.cues):
        raise RetrievalIntegrityError("Transcript cue belongs to another source.")


def expand_evidence(
    record: TranscriptRecord,
    hits: tuple[RankedEvidence, ...],
    *,
    window: TimeWindow | None = None,
    limit: int = 8,
    config: ExpansionConfig = DEFAULT_EXPANSION,
) -> ExpandedEvidence:
    """Merge nearby hits, reserve seed context first, then include whole neighbors.

    Original intervals/text are never cropped or synthesized. Boundary-straddling
    units are retained and flagged; display intervals alone are clipped to scope.
    """
    if not 1 <= limit <= 8:
        raise ValueError("result limit must be between 1 and 8")
    validate_evidence(record, hits)
    scope = window or TimeWindow()
    end = min(record.video.metadata.duration_ms, scope.end_ms or record.video.metadata.duration_ms)
    windows = sorted(
        (
            max(scope.start_ms, hit.unit.start_ms - config.padding_ms),
            min(end, hit.unit.end_ms + config.padding_ms),
            rank,
        )
        for rank, hit in enumerate(hits)
        if scope.overlaps(hit.unit.start_ms, hit.unit.end_ms) and hit.unit.start_ms < end
    )
    merged: list[tuple[int, int, list[int]]] = []
    for start, stop, rank in windows:
        if merged and (start <= merged[-1][1] or start - merged[-1][1] < config.merge_gap_ms):
            previous = merged[-1]
            merged[-1] = (previous[0], max(previous[1], stop), [*previous[2], rank])
        else:
            merged.append((start, stop, [rank]))
    selected = sorted(merged, key=lambda group: min(group[2]))[:limit]
    chosen: list[list[RetrievalUnit]] = [[] for _ in selected]
    seen: set[str] = set()
    remaining = config.max_context_chars
    limited = False

    def include(index: int, unit: RetrievalUnit) -> None:
        nonlocal remaining, limited
        if unit.unit_id in seen:
            return
        if len(unit.text) > remaining:
            limited = True
            return
        chosen[index].append(unit)
        seen.add(unit.unit_id)
        remaining -= len(unit.text)

    # Reserve matching units across groups before spending budget on neighbors.
    for index, (_, _, ranks) in enumerate(selected):
        for rank in sorted(ranks):
            include(index, hits[rank].unit)
    for index, (start, stop, _) in enumerate(selected):
        if chosen[index]:
            for unit in record.units:
                if TimeWindow(start, stop).overlaps(unit.start_ms, unit.end_ms) and scope.overlaps(
                    unit.start_ms, unit.end_ms
                ):
                    include(index, unit)
    groups: list[EvidenceGroup] = []
    seen_cues: set[str] = set()
    for index, (start, stop, ranks) in enumerate(selected):
        units = tuple(sorted(chosen[index], key=lambda unit: (unit.start_ms, unit.unit_id)))
        if not units:
            continue
        ids = {unit.unit_id for unit in units}
        cue_ids = {cue_id for unit in units for cue_id in unit.cue_ids}
        cues = tuple(
            cue for cue in record.cues if cue.cue_id in cue_ids and cue.cue_id not in seen_cues
        )
        seen_cues.update(cue_ids)
        groups.append(
            EvidenceGroup(
                start,
                stop,
                tuple(hits[rank] for rank in sorted(ranks) if hits[rank].unit.unit_id in ids),
                units,
                cues,
            )
        )
    warnings: list[RetrievalWarning] = []
    if limited:
        warnings.append(
            RetrievalWarning(
                "context_budget_exhausted",
                "Some whole transcript units were omitted to keep context bounded.",
            )
        )
    if window is not None and any(
        unit.start_ms < window.start_ms
        or (window.end_ms is not None and unit.end_ms > window.end_ms)
        for group in groups
        for unit in group.units
    ):
        warnings.append(
            RetrievalWarning(
                "temporal_boundary_overlap",
                "Some original evidence intervals cross the requested time boundary; their text and provenance remain unchanged.",
            )
        )
    return ExpandedEvidence(tuple(groups), config.max_context_chars - remaining, tuple(warnings))
