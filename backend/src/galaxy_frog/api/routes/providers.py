"""Provider capability and profile endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import get_video_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.application.provider_registry import (
    ProviderEntry,
    profile_validation_error,
    provider_catalog,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles, ProfileVersionConflict
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.providers import ProfileId, ProviderProfileEdit, ProviderProfileVersion

router = APIRouter(prefix="/v1", tags=["providers"])
Profiles = Annotated[SqlAlchemyVideoRepository, Depends(get_video_repository)]


@router.get("/providers/capabilities")
async def capabilities(request: Request) -> tuple[ProviderEntry, ...]:
    settings: Settings = request.app.state.settings
    return provider_catalog(settings)


def _database_failure() -> ApiError:
    return ApiError(
        status_code=503,
        code="DATABASE_UNAVAILABLE",
        message="Provider profiles require a working PostgreSQL database.",
        retryable=True,
        suggested_action="Start PostgreSQL and apply the current migrations.",
    )


@router.get("/provider-profiles", responses={503: {"model": ErrorResponse}})
async def provider_profiles(repository: Profiles) -> tuple[ProviderProfileVersion, ...]:
    try:
        return await PostgresProviderProfiles(repository.session).latest()
    except SQLAlchemyError as exc:
        raise _database_failure() from exc


@router.put(
    "/provider-profiles/{profile_id}",
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def save_provider_profile(
    profile_id: ProfileId, body: ProviderProfileEdit, request: Request, repository: Profiles
) -> ProviderProfileVersion:
    settings: Settings = request.app.state.settings
    if error := profile_validation_error(body.policy, provider_catalog(settings)):
        raise ApiError(
            status_code=422,
            code="PROVIDER_PROFILE_INVALID",
            message=error,
            retryable=False,
        )
    try:
        return await PostgresProviderProfiles(repository.session).append(
            profile_id, body.expected_version, body.policy
        )
    except ProfileVersionConflict as exc:
        raise ApiError(
            status_code=409,
            code="PROVIDER_PROFILE_VERSION_CONFLICT",
            message="The profile changed after it was loaded.",
            retryable=True,
            suggested_action="Reload the profile and apply the edit to its latest version.",
        ) from exc
    except SQLAlchemyError as exc:
        raise _database_failure() from exc
