"""Safe HTTP contracts for the paused-playhead explanation."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from galaxy_frog.application.retrieval.explain_moment import MomentCitation, MomentExplanation
from galaxy_frog.application.retrieval.moment import MomentFrame
from galaxy_frog.domain.providers import ProviderSelection
from galaxy_frog.domain.retrieval.pipeline import RetrievalWarning
from galaxy_frog.domain.vision.models import FrameSource


class ExplainMomentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timestamp_ms: int = Field(ge=0)
    allow_external_frames: bool = False
    provider_selection: ProviderSelection | None = None


class ExaminedWindow(BaseModel):
    start_ms: int
    end_ms: int


class MomentFrameResponse(BaseModel):
    frame_id: UUID
    scene_id: UUID | None
    source: FrameSource
    start_ms: int
    end_ms: int
    capture_ms: int


class ExplainMomentResponse(BaseModel):
    video_id: UUID
    timestamp_ms: int
    window: ExaminedWindow
    explanation: str
    citations: list[MomentCitation]
    current_frame: MomentFrameResponse | None
    related_moments: list[MomentFrameResponse]
    warnings: list[RetrievalWarning]
    trace_id: UUID


def _frame_response(frame: MomentFrame) -> MomentFrameResponse:
    return MomentFrameResponse(
        frame_id=frame.frame_id,
        scene_id=frame.scene_id,
        source=frame.source,
        start_ms=frame.start_ms,
        end_ms=frame.end_ms,
        capture_ms=frame.capture_ms,
    )


def moment_response(result: MomentExplanation) -> ExplainMomentResponse:
    evidence = result.evidence
    assert evidence.window.end_ms is not None
    current = evidence.frames.current
    return ExplainMomentResponse(
        video_id=evidence.video_id,
        timestamp_ms=evidence.timestamp_ms,
        window=ExaminedWindow(start_ms=evidence.window.start_ms, end_ms=evidence.window.end_ms),
        explanation=result.explanation,
        citations=list(result.citations),
        current_frame=_frame_response(current) if current is not None else None,
        related_moments=[_frame_response(frame) for frame in result.related_moments],
        warnings=list(result.warnings),
        trace_id=result.trace_id,
    )
