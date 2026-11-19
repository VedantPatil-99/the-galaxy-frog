"""Private notebook endpoints with verified ownership on every operation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from galaxy_frog.api.dependencies import get_workspace_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.workspace_auth import (
    get_verified_workspace_creator,
    prevent_workspace_caching,
    workspace_settings,
)
from galaxy_frog.api.workspace_schemas import (
    NotebookListResponse,
    WorkspaceSessionResponse,
    WorkspaceStatusResponse,
)
from galaxy_frog.db.workspace import WorkspaceRepository
from galaxy_frog.domain.workspace import (
    Notebook,
    NotebookDraft,
    NotebookNotFound,
    VerifiedWorkspaceIdentity,
)

router = APIRouter(
    prefix="/v1/workspace",
    tags=["workspace"],
    dependencies=[Depends(prevent_workspace_caching)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
)
Creator = Annotated[VerifiedWorkspaceIdentity, Depends(get_verified_workspace_creator)]
Repository = Annotated[WorkspaceRepository, Depends(get_workspace_repository)]


def _notebook_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="NOTEBOOK_NOT_FOUND",
        message="The requested notebook is unavailable.",
        retryable=False,
    )


@router.get("/status")
async def workspace_status(request: Request) -> WorkspaceStatusResponse:
    settings = workspace_settings(request)
    required = settings.workspace_auth_required
    return WorkspaceStatusResponse(
        mode="workspace" if required else "local",
        authentication_configured=(
            settings.supabase_url is not None and settings.supabase_publishable_key is not None
        ),
        creator_access_configured=bool(settings.workspace_creator_emails),
        legacy_resources_available=not required,
    )


@router.get("/session")
async def workspace_session(identity: Creator, repository: Repository) -> WorkspaceSessionResponse:
    account = await repository.register_account(identity.account_id)
    await repository.session.commit()
    return WorkspaceSessionResponse(account=account, email=identity.email)


@router.get("/notebooks")
async def list_notebooks(
    identity: Creator, repository: Repository, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> NotebookListResponse:
    await repository.register_account(identity.account_id)
    notebooks = await repository.list_notebooks(identity.account_id, limit=limit)
    await repository.session.commit()
    return NotebookListResponse(notebooks=notebooks)


@router.post("/notebooks", status_code=201)
async def create_notebook(
    draft: NotebookDraft, identity: Creator, repository: Repository
) -> Notebook:
    await repository.register_account(identity.account_id)
    notebook = await repository.create_notebook(identity.account_id, draft)
    await repository.session.commit()
    return notebook


@router.get("/notebooks/{notebook_id}")
async def get_notebook(notebook_id: UUID, identity: Creator, repository: Repository) -> Notebook:
    try:
        return await repository.get_notebook(identity.account_id, notebook_id)
    except NotebookNotFound:
        raise _notebook_not_found() from None


@router.patch("/notebooks/{notebook_id}")
async def rename_notebook(
    notebook_id: UUID, draft: NotebookDraft, identity: Creator, repository: Repository
) -> Notebook:
    try:
        notebook = await repository.rename_notebook(identity.account_id, notebook_id, draft)
    except NotebookNotFound:
        raise _notebook_not_found() from None
    await repository.session.commit()
    return notebook
