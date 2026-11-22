"""Owner-checked chat library and draft endpoints."""

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.routes.workspace import Creator, Repository, notebook_not_found
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.workspace_auth import prevent_workspace_caching
from galaxy_frog.db.workspace_chats import WorkspaceChatRepository
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
