"""Authoritative HTTP contracts for private notebook and account access."""

from typing import Literal

from pydantic import BaseModel

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
