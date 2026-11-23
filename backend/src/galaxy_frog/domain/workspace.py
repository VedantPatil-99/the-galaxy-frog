"""Private workspace records independent of HTTP and identity provider SDKs."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

NotebookTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class NotebookDraft(BaseModel):
    """Validated notebook name shared by creation and rename operations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: NotebookTitle


class WorkspaceAccount(BaseModel):
    """An explicitly registered, verified identity; registration transfers no legacy data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: UUID
    created_at: datetime


class Notebook(BaseModel):
    """An owned Galaxy notebook."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    notebook_id: UUID
    owner_id: UUID
    title: NotebookTitle
    created_at: datetime
    updated_at: datetime


class NotebookNotFound(Exception):
    """Missing and inaccessible notebooks deliberately have the same outcome."""


class VerifiedWorkspaceIdentity(BaseModel):
    """Auth-server identity and the expiry of its verified bearer, when available."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: UUID
    email: str
    token_expires_at: datetime | None = None
