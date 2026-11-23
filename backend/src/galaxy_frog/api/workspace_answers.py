"""Project grounded blocks with citations from the server-owned search snapshot."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from galaxy_frog.api.workspace_schemas import WorkspaceSearchHit, WorkspaceSearchQuery
from galaxy_frog.domain.generation.workspace_answer import (
    WORKSPACE_ANSWER_MAX_PASSAGE_CHARS,
    WORKSPACE_ANSWER_MAX_PASSAGES,
    WorkspaceAnswerPassage,
    WorkspaceGeneratedDraft,
    WorkspaceModelMetrics,
    validate_workspace_citations,
)


class WorkspaceAnswerRequest(WorkspaceSearchQuery):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=1_000, pattern=r"^[^\x00]+$"
        ),
    ]
    evidence_ids: tuple[
        Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")],
        ...,
    ] = Field(min_length=1, max_length=WORKSPACE_ANSWER_MAX_PASSAGES)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Choose distinct passages.")
        return value


class WorkspaceAnswerCitation(BaseModel):
    quote: str
    evidence: WorkspaceSearchHit


class WorkspaceAnswerBlock(BaseModel):
    text: str
    citations: tuple[WorkspaceAnswerCitation, ...] = Field(min_length=1)


class WorkspaceAnswerTimings(BaseModel):
    evidence_ms: float = Field(ge=0)
    generation_ms: float = Field(ge=0)
    validation_ms: float = Field(ge=0)


class WorkspaceAnswerResponse(BaseModel):
    provider: Literal["ollama"] = "ollama"
    model: str
    blocks: tuple[WorkspaceAnswerBlock, ...]
    timings: WorkspaceAnswerTimings
    metrics: WorkspaceModelMetrics


def workspace_evidence_id(hit: WorkspaceSearchHit) -> str:
    return hit.unit_id if hit.kind == "video" else str(hit.block.block_id)


def workspace_answer_passages(
    evidence: tuple[WorkspaceSearchHit, ...],
) -> tuple[WorkspaceAnswerPassage, ...]:
    return tuple(
        WorkspaceAnswerPassage(
            reference=f"E{index}",
            kind=hit.kind,
            text=(hit.text if hit.kind == "video" else hit.block.text)[
                :WORKSPACE_ANSWER_MAX_PASSAGE_CHARS
            ],
        )
        for index, hit in enumerate(evidence, start=1)
    )


def validated_workspace_blocks(
    generated: WorkspaceGeneratedDraft,
    passages: tuple[WorkspaceAnswerPassage, ...],
    evidence: tuple[WorkspaceSearchHit, ...],
) -> tuple[WorkspaceAnswerBlock, ...]:
    validate_workspace_citations(generated.draft, passages)
    by_reference = {passage.reference: hit for passage, hit in zip(passages, evidence, strict=True)}
    return tuple(
        WorkspaceAnswerBlock(
            text=block.text,
            citations=tuple(
                WorkspaceAnswerCitation(
                    quote=citation.quote, evidence=by_reference[citation.reference]
                )
                for citation in block.citations
            ),
        )
        for block in generated.draft.blocks
    )
