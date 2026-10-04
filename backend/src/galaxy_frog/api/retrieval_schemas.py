"""FastAPI-owned retrieval contracts, retaining domain evidence intervals verbatim."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from galaxy_frog.application.retrieval.search import SearchExecution
from galaxy_frog.application.retrieval.temporal import AnchorChoice, RetrievalStatus
from galaxy_frog.domain.retrieval.evidence import EvidenceGroup
from galaxy_frog.domain.retrieval.pipeline import QueryAnalysis, RetrievalMode, RetrievalWarning
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.retrieval.traces import RetrievalTrace


class RetrievalOptions(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    mode: RetrievalMode = RetrievalMode.RERANKED
    limit: int = Field(default=8, ge=1, le=8)
    selected_anchor: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    allow_fallback: bool = True


class SearchRequest(RetrievalOptions):
    query: str = Field(min_length=1, max_length=2000)


class SearchResponse(BaseModel):
    video_id: UUID
    trace_id: UUID
    status: RetrievalStatus
    mode: RetrievalMode
    analysis: QueryAnalysis
    window: TimeWindow | None
    anchors: list[AnchorChoice]
    selected_anchor: str | None
    evidence: list[EvidenceGroup]
    context_chars: int
    warnings: list[RetrievalWarning]
    degraded: bool


class RetrievalTraceResponse(BaseModel):
    trace_id: UUID
    video_id: UUID
    created_at: datetime
    version: Literal[1] = 1
    payload: dict[str, JsonValue]


def search_response(execution: SearchExecution, mode: RetrievalMode) -> SearchResponse:
    result = execution.result
    return SearchResponse(
        video_id=execution.trace.video_id,
        trace_id=execution.trace.trace_id,
        status=result.status,
        mode=mode,
        analysis=result.analysis,
        window=result.window,
        anchors=list(result.anchors),
        selected_anchor=result.selected_anchor,
        evidence=list(result.groups),
        context_chars=result.context_chars,
        warnings=list(result.warnings),
        degraded=result.degraded,
    )


def trace_response(trace: RetrievalTrace) -> RetrievalTraceResponse:
    return RetrievalTraceResponse(
        trace_id=trace.trace_id,
        video_id=trace.video_id,
        created_at=trace.created_at,
        payload=trace.payload,
    )
