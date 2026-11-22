"""Bounded draft contracts for answers over explicitly selected passages."""

from dataclasses import dataclass
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

WORKSPACE_ANSWER_MAX_PASSAGES = 6
WORKSPACE_ANSWER_MAX_PASSAGE_CHARS = 1_200


@dataclass(frozen=True, slots=True)
class WorkspaceAnswerPassage:
    reference: str
    kind: str
    text: str


class WorkspaceDraftCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: Annotated[str, StringConstraints(pattern=r"^E[1-6]$")]
    quote: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)]


class WorkspaceDraftBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)]
    citations: tuple[WorkspaceDraftCitation, ...] = Field(min_length=1, max_length=6)


class WorkspaceAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: tuple[WorkspaceDraftBlock, ...] = Field(max_length=4)


class WorkspaceModelMetrics(BaseModel):
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_duration: int | None = Field(default=None, ge=0)
    eval_duration: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)


@dataclass(frozen=True, slots=True)
class WorkspaceGeneratedDraft:
    model: str
    draft: WorkspaceAnswerDraft
    metrics: WorkspaceModelMetrics


class WorkspaceAnswerDraftInvalid(ValueError):
    """The local model returned an invalid answer document."""


class WorkspaceAnswerProvider(Protocol):
    async def generate_workspace_answer(
        self, question: str, passages: tuple[WorkspaceAnswerPassage, ...]
    ) -> WorkspaceGeneratedDraft: ...
