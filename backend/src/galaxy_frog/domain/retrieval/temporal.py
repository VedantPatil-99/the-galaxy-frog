"""Deterministic temporal intent and half-open evidence constraints."""

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from galaxy_frog.domain.retrieval.errors import TemporalResolutionRequired
from galaxy_frog.domain.retrieval.pipeline import QueryAnalysis, QueryKind


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start_ms: int = 0
    end_ms: int | None = None

    def __post_init__(self) -> None:
        if self.start_ms < 0 or (self.end_ms is not None and self.end_ms < self.start_ms):
            raise ValueError("time window must be ordered and non-negative")

    def overlaps(self, start_ms: int, end_ms: int) -> bool:
        return (
            self.start_ms != self.end_ms
            and end_ms > self.start_ms
            and (self.end_ms is None or start_ms < self.end_ms)
        )


class TemporalRelation(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    AROUND = "around"


@dataclass(frozen=True, slots=True)
class TemporalIntent:
    text_query: str
    window: TimeWindow | None = None
    event: str | None = None
    relation: TemporalRelation = TemporalRelation.AROUND


_TIME = r"(?<![\w:.])(?:\d{1,3}:\d{2}(?::\d{2})?|\d+(?:\.\d+)?\s*(?:seconds?|minutes?|hours?|secs?|mins?))(?![\w:])"
_RANGE = re.compile(
    rf"(?:(?:between|from)\s+)?(?P<start>{_TIME})\s*(?:to|and|[-\u2013])\s*(?P<end>{_TIME})[?.!]*$",
    re.IGNORECASE,
)
_POINT = re.compile(
    rf"(?:(?P<relation>before|after|during|around|at)\s+)?(?P<time>{_TIME})[?.!]*$",
    re.IGNORECASE,
)
_EVENT = re.compile(r"\b(?P<relation>before|after|during|around)\s+(?P<event>.+?)[?.!]*$", re.I)
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”')
_SIGNAL = re.compile(rf"{_TIME}|\b(?:before|after|during|around|between)\b", re.I)
_GENERIC = re.compile(r"^(?:what (?:happened|was said)|show(?: me)?|find|search|at|during)?$", re.I)


def _milliseconds(value: str) -> int:
    if ":" in value:
        parts = [int(part) for part in value.split(":")]
        if any(part >= 60 for part in parts[1:]):
            raise TemporalResolutionRequired("Timestamp seconds and minutes must be below 60.")
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        if seconds * 1000 > 2147483647:
            raise TemporalResolutionRequired("Timestamp exceeds the supported transcript interval.")
        return seconds * 1000
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(\w+)", value)
    assert match is not None
    scale = (
        3600000
        if match[2].lower().startswith("hour")
        else 60000
        if match[2].lower().startswith("min")
        else 1000
    )
    value_ms = Decimal(match[1]) * scale
    if value_ms > 2147483647:
        raise TemporalResolutionRequired("Timestamp exceeds the supported transcript interval.")
    return int(value_ms)


def relative_window(
    relation: TemporalRelation, start_ms: int, end_ms: int, *, padding_ms: int = 15000
) -> TimeWindow:
    if relation == TemporalRelation.BEFORE:
        return TimeWindow(0, start_ms)
    if relation == TemporalRelation.AFTER:
        return TimeWindow(end_ms)
    if relation == TemporalRelation.DURING:
        return TimeWindow(start_ms, end_ms)
    return TimeWindow(max(0, start_ms - padding_ms), end_ms + padding_ms)


def parse_temporal(analysis: QueryAnalysis) -> TemporalIntent:
    """Accept a single trailing constraint; reject uncertain grammar explicitly.

    Quotes protect literal timestamps/operators. Named events may themselves be
    quoted. A point at 01:00 means a +/-15 second neighborhood, not an exact cue.
    """
    query = analysis.normalized
    if analysis.kind != QueryKind.TEMPORAL:
        return TemporalIntent(query)
    unquoted = _QUOTED.sub(lambda match: " " * len(match[0]), query)
    match = _RANGE.search(unquoted) or _POINT.search(unquoted)
    if match is not None:
        prefix = query[: match.start()].strip(" ,")
        if _SIGNAL.search(_QUOTED.sub("", prefix)):
            raise TemporalResolutionRequired("Use one temporal constraint per query.")
        if "start" in match.groupdict():
            start, end = _milliseconds(match["start"]), _milliseconds(match["end"])
            if start >= end:
                raise TemporalResolutionRequired("Timestamp range must have a later end.")
            window = TimeWindow(start, end)
            relation = TemporalRelation.DURING
        else:
            point = _milliseconds(match["time"])
            relation = (
                TemporalRelation(match["relation"].lower())
                if match["relation"] and match["relation"].lower() != "at"
                else TemporalRelation.AROUND
            )
            window = relative_window(
                relation, point, point + (1 if relation == TemporalRelation.DURING else 0)
            )
        return TemporalIntent(
            "" if _GENERIC.fullmatch(prefix) else prefix, window, relation=relation
        )
    operator = re.search(r"\b(?:before|after|during|around)\b", unquoted, re.I)
    event_match = _EVENT.match(query, operator.start()) if operator is not None else None
    if event_match is None:
        raise TemporalResolutionRequired("Use a trailing timestamp, range, or named event.")
    prefix = query[: event_match.start()].strip(" ,")
    event = re.sub(r"^(?:the|a|an)\s+", "", event_match["event"], flags=re.I).strip(' "“”')
    if (
        not event
        or event.lower() in {"the", "a", "an"}
        or len(event) > 200
        or _SIGNAL.search(_QUOTED.sub("", prefix))
        or _SIGNAL.search(event)
    ):
        raise TemporalResolutionRequired("Use one unambiguous event name per query.")
    return TemporalIntent(
        "" if _GENERIC.fullmatch(prefix) else prefix,
        event=event,
        relation=TemporalRelation(event_match["relation"].lower()),
    )
