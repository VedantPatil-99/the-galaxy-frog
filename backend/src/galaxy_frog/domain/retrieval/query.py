"""Deterministic query signals; temporal constraints require a separate resolution step."""

import re

from galaxy_frog.domain.retrieval.pipeline import QueryAnalysis, QueryKind

_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
_TEMPORAL = re.compile(
    r"\b\d{1,3}:\d{2}(?::\d{2})?\b"
    r"|\b\d+(?:\.\d+)?\s*(?:seconds?|minutes?|hours?|secs?|mins?)\b"
    r"|\b(?:before|after|during|around|between)\b",
    re.IGNORECASE,
)


def analyze_query(query: str) -> QueryAnalysis:
    if not query.strip() or len(query) > 2000:
        raise ValueError("query must contain between 1 and 2000 characters")
    normalized = " ".join(query.split())
    phrases = tuple(
        dict.fromkeys(
            " ".join((match.group(1) or match.group(2)).split())
            for match in _QUOTED.finditer(normalized)
            if (match.group(1) or match.group(2)).strip()
        )
    )
    # Quoted words are literal text, so e.g. "before" alone is not an event constraint.
    unquoted = _QUOTED.sub("", normalized)
    if _TEMPORAL.search(unquoted):
        kind = QueryKind.TEMPORAL
    elif phrases:
        kind = QueryKind.EXACT
    else:
        kind = QueryKind.SPOKEN
    lexical_query = " OR ".join(f'"{phrase}"' for phrase in phrases) if phrases else normalized
    return QueryAnalysis(query, normalized, lexical_query, phrases, kind)
