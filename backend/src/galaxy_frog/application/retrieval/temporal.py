"""Resolve temporal scope before retrieval; never guess a semantic event anchor."""

import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.evidence import (
    DEFAULT_EXPANSION,
    EvidenceGroup,
    ExpansionConfig,
    expand_evidence,
    validate_evidence,
)
from galaxy_frog.domain.retrieval.pipeline import (
    QueryAnalysis,
    RankedEvidence,
    RetrievalMode,
    RetrievalWarning,
    TextRetrievalResult,
)
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.retrieval.temporal import (
    TemporalIntent,
    TimeWindow,
    parse_temporal,
    relative_window,
)
from galaxy_frog.domain.videos.records import TranscriptRecord


class TranscriptReader(Protocol):
    async def get_transcript(self, video_id: UUID) -> TranscriptRecord | None: ...


class RetrievalStatus(StrEnum):
    RESOLVED = "resolved"
    ANCHOR_SELECTION_REQUIRED = "anchor_selection_required"
    ANCHOR_UNRESOLVED = "anchor_unresolved"


class InvalidAnchor(ValueError):
    """The selected anchor is stale or does not belong to this video/event."""


class RetrievalVideoNotFound(LookupError):
    """The video is absent from the transcript repository."""


@dataclass(frozen=True, slots=True)
class AnchorChoice:
    anchor_id: str
    start_ms: int
    end_ms: int
    unit_ids: tuple[str, ...]
    text: str
    match_kind: str


@dataclass(frozen=True, slots=True)
class _AnchorMoment:
    start_ms: int
    end_ms: int
    unit_ids: tuple[str, ...]
    text: str
    rank: int


@dataclass(frozen=True, slots=True)
class TemporalRetrievalResult:
    analysis: QueryAnalysis
    intent: TemporalIntent
    status: RetrievalStatus
    window: TimeWindow | None
    anchors: tuple[AnchorChoice, ...]
    selected_anchor: str | None
    anchor_retrieval: TextRetrievalResult | None
    retrieval: TextRetrievalResult | None
    groups: tuple[EvidenceGroup, ...]
    context_chars: int
    warnings: tuple[RetrievalWarning, ...]
    degraded: bool
    expansion: ExpansionConfig


def _anchor_choices(
    record: TranscriptRecord, event: str, result: TextRetrievalResult
) -> tuple[AnchorChoice, ...]:
    literal = re.compile(r"(?<!\w)" + re.escape(event) + r"(?!\w)", re.I)
    # Check the stored transcript too: top-k retrieval must not hide a second
    # literal mention and incorrectly auto-select the first matching moment.
    ranked = {hit.unit.unit_id: hit for hit in result.rankings}
    cue_by_id = {cue.cue_id: cue for cue in record.cues}
    exact: list[_AnchorMoment] = []
    for index, unit in enumerate(record.units):
        if not literal.search(unit.text):
            continue
        hit = ranked.get(unit.unit_id)
        rank = hit.fusion_rank if hit is not None else 61 + index
        matches = [
            cue_by_id[cue_id] for cue_id in unit.cue_ids if literal.search(cue_by_id[cue_id].text)
        ]
        if matches:
            exact.extend(
                _AnchorMoment(cue.start_ms, cue.end_ms, (unit.unit_id,), cue.text, rank)
                for cue in matches
            )
        else:
            # A phrase can span cues. Retain that original unit's interval.
            exact.append(
                _AnchorMoment(unit.start_ms, unit.end_ms, (unit.unit_id,), unit.text, rank)
            )
    moments: list[_AnchorMoment] = []
    if exact:
        for moment in sorted(exact, key=lambda item: (item.start_ms, item.end_ms, item.unit_ids)):
            if moments and moment.start_ms - moments[-1].end_ms < 5000:
                previous = moments[-1]
                moments[-1] = _AnchorMoment(
                    previous.start_ms,
                    max(previous.end_ms, moment.end_ms),
                    tuple(sorted(set((*previous.unit_ids, *moment.unit_ids)))),
                    previous.text,
                    min(previous.rank, moment.rank),
                )
            else:
                moments.append(moment)
    else:
        # Rank-based suppression avoids chaining adjacent semantic candidates
        # into one misleading event spanning the whole transcript.
        for hit in result.candidates:
            if any(
                TimeWindow(max(0, row.start_ms - 4999), row.end_ms + 4999).overlaps(
                    hit.unit.start_ms, hit.unit.end_ms
                )
                for row in moments
            ):
                continue
            moments.append(
                _AnchorMoment(
                    hit.unit.start_ms,
                    hit.unit.end_ms,
                    (hit.unit.unit_id,),
                    hit.unit.text,
                    hit.fusion_rank,
                )
            )
    choices: list[AnchorChoice] = []
    for moment in sorted(moments, key=lambda row: row.rank)[:5]:
        start, end, unit_ids = moment.start_ms, moment.end_ms, moment.unit_ids
        identity = "\x1f".join(
            (str(record.video.video_id), event.casefold(), str(start), str(end), *unit_ids)
        )
        choices.append(
            AnchorChoice(
                sha256(identity.encode()).hexdigest(),
                start,
                end,
                unit_ids,
                moment.text[:1000],
                "literal" if exact else "semantic",
            )
        )
    return tuple(choices)


class RetrieveTemporalEvidence:
    def __init__(
        self,
        *,
        videos: TranscriptReader,
        retrieval: RetrieveTranscript,
        expansion: ExpansionConfig = DEFAULT_EXPANSION,
    ) -> None:
        self._videos = videos
        self._retrieval = retrieval
        self._expansion = expansion

    @property
    def configuration(self) -> dict[str, object]:
        return {"retrieval": asdict(self._retrieval.config), "expansion": asdict(self._expansion)}

    async def execute(
        self,
        video_id: UUID,
        query: str,
        *,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        limit: int = 8,
        selected_anchor: str | None = None,
        allow_fallback: bool = True,
    ) -> TemporalRetrievalResult:
        if not 1 <= limit <= 8:
            raise ValueError("result limit must be between 1 and 8")
        mode = RetrievalMode(mode)
        analysis = analyze_query(query)
        intent = parse_temporal(analysis)
        if selected_anchor is not None and intent.event is None:
            raise InvalidAnchor("A selected anchor requires a named event query.")
        record = await self._videos.get_transcript(video_id)
        if record is None:
            raise RetrievalVideoNotFound("Video not found.")
        if record.video.video_id != video_id:
            raise RetrievalIntegrityError("Transcript repository returned another video.")
        anchors: tuple[AnchorChoice, ...] = ()
        anchor_retrieval = None
        retrieval = None
        warnings: list[RetrievalWarning] = []
        degraded = False
        window = intent.window
        status = RetrievalStatus.RESOLVED
        if intent.event is not None:
            anchor_retrieval = await self._retrieval.execute(
                video_id,
                f'"{intent.event}"',
                mode=RetrievalMode.HYBRID if mode == RetrievalMode.RERANKED else mode,
                allow_fallback=allow_fallback,
            )
            validate_evidence(record, anchor_retrieval.rankings)
            warnings.extend(anchor_retrieval.warnings)
            degraded = anchor_retrieval.degraded
            anchors = _anchor_choices(record, intent.event, anchor_retrieval)
            choice = next(
                (anchor for anchor in anchors if anchor.anchor_id == selected_anchor), None
            )
            if selected_anchor is not None and choice is None:
                raise InvalidAnchor("Select a current anchor from this video's matching moments.")
            if choice is None and len(anchors) == 1 and anchors[0].match_kind == "literal":
                choice = anchors[0]
            if choice is None:
                status = (
                    RetrievalStatus.ANCHOR_SELECTION_REQUIRED
                    if anchors
                    else RetrievalStatus.ANCHOR_UNRESOLVED
                )
            else:
                selected_anchor = choice.anchor_id
                window = relative_window(intent.relation, choice.start_ms, choice.end_ms)
        groups: tuple[EvidenceGroup, ...] = ()
        context_chars = 0
        if status == RetrievalStatus.RESOLVED:
            if intent.text_query:
                retrieval = await self._retrieval.execute(
                    video_id,
                    intent.text_query,
                    mode=mode,
                    limit=limit,
                    allow_fallback=allow_fallback,
                    window=window,
                )
                hits = retrieval.candidates
                warnings.extend(retrieval.warnings)
                degraded = degraded or retrieval.degraded
            else:
                assert window is not None
                units = sorted(
                    (unit for unit in record.units if window.overlaps(unit.start_ms, unit.end_ms)),
                    key=lambda unit: (unit.start_ms, unit.unit_id),
                )
                # Time-only browsing is chronological and has no relevance score.
                if len(units) > 30:
                    warnings.append(
                        RetrievalWarning(
                            "temporal_candidate_limit",
                            "Only the first 30 matching transcript units were considered.",
                        )
                    )
                hits = tuple(
                    RankedEvidence(video_id, unit, (), rank, 0.0)
                    for rank, unit in enumerate(units[:30], 1)
                )
            expanded = expand_evidence(
                record, hits, window=window, limit=limit, config=self._expansion
            )
            groups, context_chars = expanded.groups, expanded.context_chars
            warnings.extend(expanded.warnings)
        return TemporalRetrievalResult(
            analysis,
            intent,
            status,
            window,
            anchors,
            selected_anchor,
            anchor_retrieval,
            retrieval,
            groups,
            context_chars,
            tuple(warnings),
            degraded,
            self._expansion,
        )
