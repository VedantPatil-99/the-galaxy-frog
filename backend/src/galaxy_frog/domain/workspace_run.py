"""Immutable local chat intent and the public projection of a saved turn."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from galaxy_frog.domain.generation.workspace_answer import WorkspaceAnswerDraft
from galaxy_frog.domain.workspace_chat import PromptText
from galaxy_frog.domain.workspace_text import WorkspaceTextSource

RunStatus = Literal["queued", "running", "completed", "failed", "uncertain"]
RunFailure = Literal[
    "access_changed",
    "evidence_unavailable",
    "model_unavailable",
    "answer_unverified",
    "dispatch_unknown",
]


class WorkspacePassagePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: UUID
    version_id: UUID
    block_id: UUID


class WorkspaceTurnSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_key: UUID
    question: PromptText
    draft_revision: Annotated[int, Field(strict=True, ge=0, lt=2_147_483_647)]
    passage: WorkspacePassagePin

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("Enter a question.")
        return text

    def fingerprint(self) -> str:
        intent = self.model_dump_json(exclude={"submission_key"})
        return sha256(intent.encode("utf-8")).hexdigest()


class WorkspaceLocalRunSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal["ollama"] = "ollama"
    policy: Literal["local-pasted-text-v1"] = "local-pasted-text-v1"
    model: str
    required_revision: str | None


class WorkspaceRunEvidence(BaseModel):
    source: WorkspaceTextSource
    block_id: UUID
    start_char: int
    end_char: int
    excerpt: str


class WorkspaceRunView(BaseModel):
    run_id: UUID
    status: RunStatus
    revision: int
    settings: WorkspaceLocalRunSettings
    model_revision_at_dispatch: str | None
    failure: RunFailure | None
    evidence: WorkspaceRunEvidence | None
    answer: WorkspaceAnswerDraft | None


class WorkspaceTurnView(BaseModel):
    turn_id: UUID
    sequence: int
    question: str
    passage: WorkspacePassagePin
    created_at: datetime
    run: WorkspaceRunView


class WorkspaceTurnsPage(BaseModel):
    turns: tuple[WorkspaceTurnView, ...]
    next_before: int | None
