"""Authoritative HTTP contracts for private notebook and account access."""

from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.engine import RowMapping

from galaxy_frog.api.video_schemas import TranscriptEvidenceResponse
from galaxy_frog.domain.transcripts.models import TranscriptOrigin
from galaxy_frog.domain.videos.models import VideoSourceKind
from galaxy_frog.domain.workspace import Notebook, WorkspaceAccount
from galaxy_frog.domain.workspace_documents import WorkspaceDocumentSource
from galaxy_frog.domain.workspace_text import (
    WorkspaceTextBlock,
    WorkspaceTextSource,
    workspace_block_id,
)


class WorkspaceStatusResponse(BaseModel):
    mode: Literal["local", "workspace"]
    authentication_configured: bool
    creator_access_configured: bool
    legacy_resources_available: bool


class WorkspaceSessionResponse(BaseModel):
    account: WorkspaceAccount
    email: str
    role: Literal["creator"] = "creator"


class NotebookListResponse(BaseModel):
    notebooks: tuple[Notebook, ...]


class WorkspaceVideoSourceResponse(BaseModel):
    video_id: UUID
    source_kind: VideoSourceKind
    title: str
    duration_ms: int = Field(gt=0)
    canonical_url: str | None


class WorkspaceSourceListResponse(BaseModel):
    sources: tuple[WorkspaceVideoSourceResponse, ...]
    next_after: UUID | None


class WorkspaceTextSourceListResponse(BaseModel):
    sources: tuple[WorkspaceTextSource, ...]
    next_after: UUID | None


class WorkspaceDocumentListResponse(BaseModel):
    sources: tuple[WorkspaceDocumentSource, ...]
    next_after: UUID | None


class WorkspaceTranscriptResponse(TranscriptEvidenceResponse):
    source: WorkspaceVideoSourceResponse


class WorkspaceFrameResponse(BaseModel):
    frame_id: UUID
    sequence: int
    start_ms: int
    end_ms: int
    capture_ms: int | None


class WorkspaceSearchQuery(BaseModel):
    query: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=160, pattern=r"^[^\x00]+$"
        ),
    ]


class WorkspaceSearchCue(BaseModel):
    cue_id: str
    origin: TranscriptOrigin
    track_id: str | None
    transcription_run_id: UUID | None


class WorkspaceVideoMatch(BaseModel):
    kind: Literal["video"]
    source: WorkspaceVideoSourceResponse
    text: str
    unit_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    cues: tuple[WorkspaceSearchCue, ...] = Field(min_length=1)


class WorkspaceTextMatch(BaseModel):
    kind: Literal["text"]
    source: WorkspaceTextSource
    block: WorkspaceTextBlock


class WorkspaceDocumentMatch(BaseModel):
    kind: Literal["document", "page_ocr"]
    source: WorkspaceDocumentSource
    section_number: int = Field(ge=1)
    page_number: int | None
    block: WorkspaceTextBlock


WorkspaceSearchHit = Annotated[
    WorkspaceVideoMatch | WorkspaceTextMatch | WorkspaceDocumentMatch, Field(discriminator="kind")
]


class WorkspaceSearchResponse(BaseModel):
    profile: Literal["postgres-keywords-v1"] = "postgres-keywords-v1"
    query_ms: float = Field(ge=0)
    hits: tuple[WorkspaceSearchHit, ...]


def workspace_search_hit(row: RowMapping) -> WorkspaceSearchHit:
    kind = row["kind"]
    if kind == "video":
        return WorkspaceVideoMatch.model_validate(row)
    namespace = UUID(str(row["version_id"]))
    if kind != "text":
        prefix = "ocr" if kind == "page_ocr" else "section"
        namespace = uuid5(namespace, f"{prefix}:{row['section_number']}")
    start, end = int(row["start_char"]), int(row["end_char"])
    block = WorkspaceTextBlock(
        block_id=workspace_block_id(namespace, start, end),
        start_char=start,
        end_char=end,
        text=row["text"],
    )
    model = WorkspaceTextMatch if kind == "text" else WorkspaceDocumentMatch
    return model.model_validate(dict(row, block=block))
