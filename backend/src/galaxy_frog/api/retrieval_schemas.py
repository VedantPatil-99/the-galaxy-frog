"""FastAPI-owned retrieval contracts, retaining domain evidence intervals verbatim."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from galaxy_frog.application.retrieval.search import SearchExecution
from galaxy_frog.application.retrieval.temporal import AnchorChoice, RetrievalStatus
from galaxy_frog.domain.providers import ProviderSelection
from galaxy_frog.domain.retrieval.evidence import EvidenceGroup
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.pipeline import QueryAnalysis, RetrievalMode, RetrievalWarning
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.retrieval.traces import RetrievalTrace
from galaxy_frog.domain.vision.models import FrameSource, OCRProviderSpec
from galaxy_frog.domain.vision.policy import EscalationDecision, EvidenceSufficiency, VisualDecision
from galaxy_frog.domain.vision.visual import VisualFrame, VisualProviderIdentity


class RetrievalOptions(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    mode: RetrievalMode = RetrievalMode.RERANKED
    limit: int = Field(default=8, ge=1, le=8)
    selected_anchor: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    allow_fallback: bool = True
    allow_external_frames: bool = False
    provider_selection: ProviderSelection | None = None


class SearchRequest(RetrievalOptions):
    query: str = Field(min_length=1, max_length=2000)


class VisualFrameResponse(BaseModel):
    frame_id: UUID
    sequence: int
    start_ms: int
    end_ms: int
    capture_ms: int | None = None
    image_url: str
    ocr_text: str | None = None
    confidence: float | None = None
    is_duplicate: bool = False
    modality: Literal["ocr", "visual"] = "ocr"
    video_id: UUID | None = None
    scene_id: UUID | None = None
    source: FrameSource | None = None
    perceptual_hash: str | None = None
    content_sha256: str | None = None
    collection_id: UUID | None = None
    collection: EmbeddingCollectionSpec | None = None
    cosine_score: float | None = None
    rank: int | None = None


class OCREvidenceResponse(BaseModel):
    video_id: UUID
    block_id: UUID
    frame_id: UUID
    sequence: int
    start_ms: int
    end_ms: int
    capture_ms: int | None = None
    text: str
    provider: OCRProviderSpec
    confidence: float | None
    confidence_method: str | None
    modality: Literal["ocr"] = "ocr"


class VisualReasoningResponse(BaseModel):
    decision: VisualDecision
    reason: str
    provider: VisualProviderIdentity | None
    examined_frame_ids: list[UUID]
    cited_frame_ids: list[UUID]
    cache_hit: bool
    external_permitted: bool
    elapsed_ms: float
    insufficient_evidence: bool | None
    prompt_version: str = "visual-grounding-v1"
    schema_version: str = "visual-answer-v1"


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
    visual_frames: list[VisualFrameResponse] = Field(default_factory=list[VisualFrameResponse])
    ocr_evidence: list[OCREvidenceResponse] = Field(default_factory=list[OCREvidenceResponse])
    visual_decisions: list[EscalationDecision] = Field(default_factory=list[EscalationDecision])
    visual_sufficiency: EvidenceSufficiency | None = None
    visual_provider: VisualProviderIdentity | None = None
    visual_reasoning: VisualReasoningResponse | None = None
    context_chars: int
    warnings: list[RetrievalWarning]
    degraded: bool


class RetrievalTraceResponse(BaseModel):
    trace_id: UUID
    video_id: UUID
    created_at: datetime
    version: Literal[1] = 1
    payload: dict[str, JsonValue]


def search_response(
    execution: SearchExecution,
    mode: RetrievalMode,
    visual_frames: Sequence[VisualFrameResponse] | None = None,
) -> SearchResponse:
    result = execution.result
    visual = execution.visual
    reasoning = execution.reasoning
    frames = list(visual_frames or ())
    if visual is not None:
        frames = [
            visual_frame_response(hit.frame).model_copy(
                update={
                    "collection": hit.collection,
                    "collection_id": hit.collection_id,
                    "cosine_score": hit.score,
                    "rank": hit.rank,
                }
            )
            for hit in visual.hits
        ]
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
        visual_frames=frames,
        ocr_evidence=[
            OCREvidenceResponse(
                video_id=item.video_id,
                block_id=item.block_id,
                frame_id=item.frame_id,
                sequence=item.block.source_frame_sequence,
                start_ms=item.block.start_ms,
                end_ms=item.block.end_ms,
                text=item.block.text,
                capture_ms=item.capture_ms,
                provider=item.block.provider_spec,
                confidence=item.block.confidence,
                confidence_method=item.block.confidence_method,
            )
            for item in visual.ocr
        ]
        if visual
        else [],
        visual_decisions=list(visual.decisions) if visual else [],
        visual_sufficiency=visual.sufficiency if visual else None,
        visual_provider=visual.provider if visual else None,
        visual_reasoning=VisualReasoningResponse(
            decision=reasoning.decision,
            reason=reasoning.reason,
            provider=reasoning.provider,
            examined_frame_ids=[frame.frame_id for frame in reasoning.frames],
            cited_frame_ids=list(reasoning.result.cited_frame_ids) if reasoning.result else [],
            cache_hit=reasoning.cache_hit,
            external_permitted=reasoning.external_permitted,
            elapsed_ms=reasoning.elapsed_ms,
            insufficient_evidence=reasoning.result.insufficient_evidence
            if reasoning.result
            else None,
        )
        if reasoning
        else None,
        context_chars=result.context_chars,
        warnings=list(result.warnings),
        degraded=result.degraded,
    )


def visual_frame_response(frame: VisualFrame) -> VisualFrameResponse:
    return VisualFrameResponse(
        frame_id=frame.frame_id,
        video_id=frame.video_id,
        scene_id=frame.scene_id,
        sequence=frame.sequence,
        start_ms=frame.start_ms,
        end_ms=frame.end_ms,
        source=frame.source,
        image_url=f"/v1/videos/{frame.video_id}/frames/{frame.sequence}/image",
        modality="visual",
        perceptual_hash=frame.perceptual_hash.hex_digest,
        content_sha256=frame.content_sha256,
        capture_ms=frame.capture_ms,
    )


def trace_response(trace: RetrievalTrace) -> RetrievalTraceResponse:
    return RetrievalTraceResponse(
        trace_id=trace.trace_id,
        video_id=trace.video_id,
        created_at=trace.created_at,
        payload=trace.payload,
    )
