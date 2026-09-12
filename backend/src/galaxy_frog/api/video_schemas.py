"""FastAPI-owned contracts for transcript-first video operations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from galaxy_frog.api.job_schemas import IngestionJobResponse


class ImportVideoRequest(BaseModel):
    """A caller-supplied video locator accepted by the source registry."""

    source_url: HttpUrl


class VideoResponse(BaseModel):
    """Safe canonical metadata returned by import and detail endpoints."""

    video_id: UUID
    source_kind: str
    external_id: str
    canonical_url: str
    title: str
    duration_ms: int = Field(gt=0)
    channel_name: str | None = None
    thumbnail_url: str | None = None
    transcript_ready: bool = True
    index_ready: bool


class ImportVideoResponse(BaseModel):
    """Prompt durable-import acknowledgement with idempotent job reuse."""

    job: IngestionJobResponse
    reused: bool


class TranscriptCueResponse(BaseModel):
    """One exact normalized caption or ASR cue."""

    cue_id: str
    source_order: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    language_code: str
    origin: Literal["caption", "asr"]
    track_id: str | None
    caption_kind: Literal["manual", "automatic"] | None
    transcription_run_id: UUID | None
    confidence: float | None = Field(ge=0, le=1)
    confidence_method: str | None


class TranscriptionRunResponse(BaseModel):
    """Safe execution evidence for the ASR run that produced this transcript."""

    run_id: UUID
    job_id: UUID
    audio_asset_id: UUID
    audio_attempt: int = Field(gt=0)
    fallback_reason: Literal["captions_unavailable", "captions_unusable"]
    audio_start_ms: int = Field(ge=0)
    audio_end_ms: int = Field(gt=0)
    provider: str
    provider_revision: str
    model: str
    model_revision: str
    device: Literal["cpu", "cuda"]
    compute_type: Literal["int8", "int8_float16", "float16", "float32"]
    language_code: str
    language_confidence: float | None = Field(ge=0, le=1)
    language_confidence_method: str | None
    processing_seconds: float = Field(ge=0)
    transcribed_at: datetime


class RetrievalUnitResponse(BaseModel):
    """One retrieval interval with ordered cue provenance."""

    retrieval_unit_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    cue_ids: list[str]


class TranscriptResponse(BaseModel):
    """Complete transcript projection used by the Phase 1 UI."""

    video: VideoResponse
    cues: list[TranscriptCueResponse]
    retrieval_units: list[RetrievalUnitResponse]
    transcription: TranscriptionRunResponse | None


class QuestionRequest(BaseModel):
    """A non-empty transcript question."""

    question: str = Field(min_length=1, max_length=2000)


class EvidenceResponse(BaseModel):
    """One validated transcript citation returned with an answer."""

    video_id: UUID
    retrieval_unit_id: str
    cue_ids: list[str]
    quote: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    modality: Literal["transcript"] = "transcript"


class AnswerResponse(BaseModel):
    """Evidence-grounded answer contract."""

    answer: str
    confidence: Literal["low", "medium", "high"]
    evidence: list[EvidenceResponse]
    warnings: list[str]
    degraded_mode: bool
