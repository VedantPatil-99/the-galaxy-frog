"""Verified workspace access and a fail-closed boundary around legacy resources."""

import re
from collections.abc import AsyncGenerator
from typing import Annotated

import httpx
from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from galaxy_frog.adapters.workspace_auth import SupabaseIdentityVerifier
from galaxy_frog.api.errors import ApiError
from galaxy_frog.application.workspace_auth import (
    IdentityVerificationError,
    WorkspaceIdentityVerifier,
)
from galaxy_frog.config import Settings
from galaxy_frog.domain.workspace import VerifiedWorkspaceIdentity

_bearer = HTTPBearer(auto_error=False, scheme_name="WorkspaceBearer")
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def workspace_settings(request: Request) -> Settings:
    return request.app.state.settings


def prevent_workspace_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def require_legacy_resource_access(request: Request) -> None:
    """Keep global pre-workspace routes outside the private API."""

    if workspace_settings(request).workspace_auth_required:
        raise ApiError(
            status_code=403,
            code="LEGACY_RESOURCES_LOCKED",
            message="This resource is unavailable in the private workspace.",
            retryable=False,
            suggested_action="Use notebook-scoped workspace routes for assigned sources.",
        )


async def get_workspace_identity_verifier(
    request: Request,
) -> AsyncGenerator[WorkspaceIdentityVerifier]:
    settings = workspace_settings(request)
    if not settings.workspace_auth_required:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_AUTH_DISABLED",
            message="Private workspace authentication is not enabled.",
            retryable=False,
            suggested_action="Configure Supabase and enable WORKSPACE_AUTH_ENABLED.",
        )
    if settings.supabase_url is None or settings.supabase_publishable_key is None:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_AUTH_NOT_CONFIGURED",
            message="Private workspace authentication is not configured.",
            retryable=False,
            suggested_action="Configure SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY on FastAPI.",
        )
    async with httpx.AsyncClient(timeout=settings.workspace_auth_timeout_seconds) as client:
        yield SupabaseIdentityVerifier(
            client,
            url=settings.supabase_url,
            publishable_key=settings.supabase_publishable_key.get_secret_value(),
        )


Verifier = Annotated[WorkspaceIdentityVerifier, Depends(get_workspace_identity_verifier)]


def _authentication_required() -> ApiError:
    return ApiError(
        status_code=401,
        code="WORKSPACE_AUTH_REQUIRED",
        message="A valid signed-in workspace session is required.",
        retryable=False,
        suggested_action="Sign in again and retry the request.",
    )


async def get_verified_workspace_creator(
    request: Request, credentials: Credentials, verifier: Verifier
) -> VerifiedWorkspaceIdentity:
    if (
        credentials is None
        or re.fullmatch(r"[A-Za-z0-9._~-]{1,8192}", credentials.credentials) is None
    ):
        raise _authentication_required()
    try:
        identity = await verifier.verify(credentials.credentials)
    except IdentityVerificationError as exc:
        if exc.reason == "invalid":
            raise _authentication_required() from None
        raise ApiError(
            status_code=503,
            code="WORKSPACE_AUTH_UNAVAILABLE",
            message="The authentication service is temporarily unavailable.",
            retryable=True,
            suggested_action="Retry after the authentication service recovers.",
        ) from None
    allowed = {email.casefold() for email in workspace_settings(request).workspace_creator_emails}
    if identity.email.casefold() not in allowed:
        raise ApiError(
            status_code=403,
            code="WORKSPACE_INVITATION_REQUIRED",
            message="This account does not have workspace creator access.",
            retryable=False,
            suggested_action="Ask the workspace operator for an invitation.",
        )
    expected = request.headers.get("x-workspace-account")
    if expected is not None and expected != str(identity.account_id):
        raise ApiError(
            status_code=409,
            code="WORKSPACE_SESSION_CHANGED",
            message="The workspace sign-in changed.",
            retryable=False,
            suggested_action="Reopen the workspace under the current account.",
        )
    return identity
