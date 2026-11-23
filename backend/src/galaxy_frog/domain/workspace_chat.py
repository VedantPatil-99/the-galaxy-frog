"""Private chats and their independently revisioned prompt drafts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ChatTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00]+$"),
]
PromptText = Annotated[str, StringConstraints(max_length=1000, pattern=r"^[^\x00]*$")]


class WorkspaceChatName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: ChatTitle


class WorkspaceChatCreate(WorkspaceChatName):
    chat_id: UUID


class WorkspacePromptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: PromptText
    revision: Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]


class WorkspaceChatSummary(WorkspaceChatName):
    chat_id: UUID
    has_draft: bool
    created_at: datetime
    updated_at: datetime


class WorkspaceChat(WorkspaceChatSummary):
    draft: WorkspacePromptDraft


class WorkspaceChatNotFound(Exception):
    """Missing and inaccessible chats have the same result."""


class WorkspaceDraftConflict(Exception):
    """The prompt changed after the editor loaded its revision."""
