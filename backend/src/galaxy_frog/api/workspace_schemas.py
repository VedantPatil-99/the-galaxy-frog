"""Authoritative HTTP contracts for private notebook and account access."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from galaxy_frog.api.video_schemas import TranscriptEvidenceResponse
from galaxy_frog.domain.videos.models import VideoSourceKind
from galaxy_frog.domain.workspace import Notebook, WorkspaceAccount


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


class WorkspaceTranscriptResponse(TranscriptEvidenceResponse):
    source: WorkspaceVideoSourceResponse


class WorkspaceFrameResponse(BaseModel):
    frame_id: UUID
    sequence: int
    start_ms: int
    end_ms: int
    capture_ms: int | None
