"""Study materials and the exact evidence carried by each saved version."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from galaxy_frog.domain.transcripts.models import TranscriptOrigin
from galaxy_frog.domain.videos.models import CaptionKind, VideoSourceKind

MAX_VIDEO_MS = 7_200_000
SCHEMA_VERSION = 1
PROMPT_VERSION = "study-v2"


class StudyKind(StrEnum):
    CHAPTERS = "chapters"
    SUMMARY = "summary"
    NOTES = "notes"
    FLASHCARDS = "flashcards"
    QUIZ = "quiz"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StudyFailure(BaseModel):
    code: str
    message: str
    retryable: bool
    suggested_action: str


class StudyError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 409,
        retryable: bool = False,
        action: str = "Open the video workspace and check the available evidence.",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.failure = StudyFailure(
            code=code, message=message, retryable=retryable, suggested_action=action
        )


class StudySource(BaseModel):
    video_id: UUID
    kind: VideoSourceKind
    external_id: str
    canonical_url: str
    title: str
    duration_ms: int


class StudyCitation(BaseModel):
    source_id: str
    modality: Literal["transcript", "ocr"]
    quote: str
    start_ms: int
    end_ms: int
    seek_ms: int
    timestamp_url: str | None
    cue_id: str | None = None
    unit_ids: tuple[str, ...] = ()
    origin: TranscriptOrigin | None = None
    language_code: str | None = None
    caption_kind: CaptionKind | None = None
    track_id: str | None = None
    transcription_run_id: UUID | None = None
    ocr_block_id: UUID | None = None
    frame_id: UUID | None = None
    capture_ms: int | None = None
    confidence: float | None = None
    confidence_method: str | None = None
    ocr_provider: str | None = None
    ocr_revision: str | None = None


class Chapter(BaseModel):
    number: int
    start_ms: int
    end_ms: int
    timestamp_url: str | None
    title: str
    boundary_reasons: tuple[str, ...]
    citations: tuple[StudyCitation, ...]


class SummaryItem(BaseModel):
    kind: Literal["summary"] = "summary"
    text: str
    citations: tuple[StudyCitation, ...]


class NoteItem(BaseModel):
    kind: Literal["notes"] = "notes"
    title: str
    text: str
    citations: tuple[StudyCitation, ...]


class FlashcardItem(BaseModel):
    kind: Literal["flashcards"] = "flashcards"
    question: str
    answer: str
    citations: tuple[StudyCitation, ...]


class QuizItem(BaseModel):
    kind: Literal["quiz"] = "quiz"
    question: str
    choices: tuple[str, str, str, str]
    correct_index: int = Field(ge=0, le=3)
    citations: tuple[StudyCitation, ...]


StudyItem = Annotated[
    SummaryItem | NoteItem | FlashcardItem | QuizItem, Field(discriminator="kind")
]


class StudySection(BaseModel):
    chapter_number: int
    title: str
    start_ms: int
    end_ms: int
    timestamp_url: str | None
    items: tuple[StudyItem, ...]


class StudyDocument(BaseModel):
    source: StudySource
    chapter_version: int | None = None
    chapters: tuple[Chapter, ...] = ()
    sections: tuple[StudySection, ...] = ()
    warnings: tuple[str, ...] = ()


class StudyVersionSummary(BaseModel):
    artifact_id: UUID
    kind: StudyKind
    version: int
    chapter_version_id: UUID | None
    created_at: datetime


class StudyVersion(StudyVersionSummary):
    schema_version: int
    prompt_version: str
    evidence_fingerprint: str
    provider: str
    model: str
    document: StudyDocument


class StudyRun(BaseModel):
    run_id: UUID
    video_id: UUID
    kind: StudyKind
    state: RunState
    chapter_version_id: UUID | None
    result_id: UUID | None
    completed_chapters: int
    total_chapters: int
    warnings: tuple[str, ...]
    failure: StudyFailure | None
    created_at: datetime
    updated_at: datetime
