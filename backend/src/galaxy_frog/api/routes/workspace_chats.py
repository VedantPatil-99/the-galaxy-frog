"""Owner-checked chat library and draft endpoints."""

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.routes.workspace import Creator, Repository, notebook_not_found
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.workspace_auth import prevent_workspace_caching, workspace_settings
from galaxy_frog.db.workspace_chats import WorkspaceChatRepository
from galaxy_frog.db.workspace_runs import (
    WorkspaceActiveRun,
    WorkspaceRunAuthorizationExpired,
    WorkspaceRunRepository,
    WorkspaceSubmissionConflict,
    WorkspaceTemporalQuestion,
)
from galaxy_frog.domain.workspace import NotebookNotFound
from galaxy_frog.domain.workspace_chat import (
    WorkspaceChat,
    WorkspaceChatCreate,
    WorkspaceChatName,
    WorkspaceChatNotFound,
    WorkspaceChatSummary,
    WorkspaceDraftConflict,
    WorkspacePromptDraft,
)
from galaxy_frog.domain.workspace_run import (
    WorkspaceLocalRunSettings,
    WorkspaceTurnsPage,
    WorkspaceTurnSubmit,
    WorkspaceTurnView,
)
from galaxy_frog.domain.workspace_text import WorkspaceTextNotFound

router = APIRouter(
    prefix="/v1/workspace/notebooks/{notebook_id}/chats",
    tags=["workspace"],
    dependencies=[Depends(prevent_workspace_caching)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 503)},
)


class WorkspaceChatList(BaseModel):
    chats: tuple[WorkspaceChatSummary, ...]
    next_after: UUID | None


async def _authorized_chats(
    notebook_id: UUID, identity: Creator, repository: Repository
) -> AsyncGenerator[WorkspaceChatRepository]:
    try:
        await repository.get_notebook(identity.account_id, notebook_id)
    except NotebookNotFound:
        raise notebook_not_found() from None
    try:
        yield WorkspaceChatRepository(repository.session, notebook_id)
    except WorkspaceChatNotFound:
        raise ApiError(
            status_code=404,
            code="WORKSPACE_CHAT_NOT_FOUND",
            message="The requested chat is unavailable.",
            retryable=False,
        ) from None


Chats = Annotated[WorkspaceChatRepository, Depends(_authorized_chats)]


@router.get("")
async def list_chats(
    chats: Chats, after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> WorkspaceChatList:
    items, next_after = await chats.list(after, limit)
    return WorkspaceChatList(chats=items, next_after=next_after)


@router.post("", status_code=201)
async def create_chat(body: WorkspaceChatCreate, chats: Chats) -> WorkspaceChat:
    chat = await chats.create(body)
    await chats.session.commit()
    return chat


@router.get("/{chat_id}")
async def get_chat(chat_id: UUID, chats: Chats) -> WorkspaceChat:
    return await chats.get(chat_id)


@router.patch("/{chat_id}")
async def rename_chat(chat_id: UUID, body: WorkspaceChatName, chats: Chats) -> WorkspaceChatSummary:
    chat = await chats.rename(chat_id, body.title)
    await chats.session.commit()
    return chat


@router.put("/{chat_id}/draft", responses={409: {"model": ErrorResponse}})
async def save_draft(
    chat_id: UUID, draft: WorkspacePromptDraft, chats: Chats
) -> WorkspacePromptDraft:
    try:
        saved = await chats.save_draft(chat_id, draft)
    except WorkspaceDraftConflict:
        raise ApiError(
            status_code=409,
            code="WORKSPACE_DRAFT_CONFLICT",
            message="This draft changed in another tab or device. Review the saved version before replacing it.",
            retryable=False,
        ) from None
    await chats.session.commit()
    return saved


@router.post("/{chat_id}/turns", status_code=202, responses={409: {"model": ErrorResponse}})
async def submit_workspace_turn(
    chat_id: UUID,
    body: WorkspaceTurnSubmit,
    chats: Chats,
    identity: Creator,
    request: Request,
) -> WorkspaceTurnView:
    settings = workspace_settings(request)
    runs = WorkspaceRunRepository(chats.session, chats.notebook_id, identity)
    try:
        turn = await runs.submit(
            chat_id,
            body,
            WorkspaceLocalRunSettings(
                model=settings.generation_model,
                required_revision=settings.generation_model_revision,
            ),
        )
    except WorkspaceSubmissionConflict:
        raise _turn_conflict(
            "WORKSPACE_SUBMISSION_CONFLICT",
            "This submission key already belongs to a different question, passage, or draft.",
        ) from None
    except WorkspaceDraftConflict:
        raise _turn_conflict(
            "WORKSPACE_DRAFT_CONFLICT", "The saved draft changed. Review it before sending."
        ) from None
    except WorkspaceTemporalQuestion:
        raise ApiError(
            status_code=422,
            code="WORKSPACE_TEMPORAL_SCOPE_REQUIRED",
            message="Choose a time-specific video workflow for this question.",
            retryable=False,
        ) from None
    except WorkspaceRunAuthorizationExpired:
        raise ApiError(
            status_code=401,
            code="WORKSPACE_AUTH_REQUIRED",
            message="Sign in again before starting a saved answer.",
            retryable=False,
        ) from None
    except WorkspaceActiveRun:
        raise _turn_conflict(
            "WORKSPACE_RUN_ACTIVE", "An answer is already in progress in this chat."
        ) from None
    except WorkspaceTextNotFound:
        raise _turn_conflict(
            "WORKSPACE_EVIDENCE_SELECTION_STALE",
            "This pasted-text passage is unavailable. Select an available passage.",
        ) from None
    await chats.session.commit()
    return turn


def _turn_conflict(code: str, message: str) -> ApiError:
    return ApiError(status_code=409, code=code, message=message, retryable=False)


@router.get("/{chat_id}/turns")
async def list_workspace_turns(
    chat_id: UUID,
    chats: Chats,
    identity: Creator,
    before: Annotated[int | None, Query(gt=0, le=9_007_199_254_740_991)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> WorkspaceTurnsPage:
    return await WorkspaceRunRepository(chats.session, chats.notebook_id, identity).list(
        chat_id, before, limit
    )
