"""Provider capability and profile endpoints."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import get_video_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.application.provider_policy import ProviderAttempt
from galaxy_frog.application.provider_registry import (
    PricingSource,
    ProviderEntry,
    probe_ollama_models,
    profile_validation_error,
    provider_catalog,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.models import ProviderRuntimeRow
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles, ProfileVersionConflict
from galaxy_frog.db.provider_runtime import (
    CONSENT_DATA,
    consent_response,
    create_consent,
    locked_consent,
    provider_attempt_rows,
)
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.providers import (
    Capability,
    ConsentScope,
    ProfileId,
    ProfileRef,
    ProviderConsent,
    ProviderConsentRequest,
    ProviderOperation,
    ProviderProfileEdit,
    ProviderProfileVersion,
)

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


class ProviderHealth(BaseModel):
    provider_id: str
    state: str
    observed_at: datetime | None = None
    blocked_until: datetime | None = None


class ProviderRunAttempt(ProviderAttempt):
    profile: ProfileRef
    capability: Capability
    created_at: datetime
    input_duration_ms: int | None = None
    pricing: PricingSource | None = None
    usage: dict[str, int] | None = None
    estimated_cost: str | None = None
    actual_cost: str | None = None


@router.get("/provider-runs/{operation}/{run_id}", responses={503: {"model": ErrorResponse}})
async def read_provider_run(
    operation: ProviderOperation, run_id: UUID, repository: Profiles
) -> tuple[ProviderRunAttempt, ...]:
    try:
        rows = await provider_attempt_rows(repository.session, operation, run_id)
        return tuple(
            ProviderRunAttempt.model_validate(
                {
                    **row.payload,
                    "profile": {"profile_id": row.profile_id, "version": row.profile_version},
                    "capability": row.capability,
                    "created_at": row.created_at,
                }
            )
            for row in rows
        )
    except SQLAlchemyError as exc:
        raise _database_failure() from exc


@router.get("/providers/health", responses={503: {"model": ErrorResponse}})
async def provider_health(request: Request, repository: Profiles) -> tuple[ProviderHealth, ...]:
    try:
        rows = await repository.session.scalars(select(ProviderRuntimeRow))
        observed = {row.provider_id: row for row in rows}
    except SQLAlchemyError as exc:
        raise _database_failure() from exc
    settings: Settings = request.app.state.settings
    local_models = await probe_ollama_models(settings.ollama_base_url)
    return tuple(
        ProviderHealth(
            provider_id=entry.provider_id,
            state=row.state,
            observed_at=row.observed_at,
            blocked_until=row.blocked_until,
        )
        if (row := observed.get(entry.provider_id))
        else ProviderHealth(
            provider_id=entry.provider_id,
            state=(
                "model_listed"
                if entry.provider == "ollama" and entry.model in local_models
                else "unknown"
            ),
        )
        for entry in await capabilities(request)
    )


_CONSENT_PROVIDERS = {
    ProviderOperation.IMPORT: {"cloudflare-whisper", "llamaparse-ocr"},
    ProviderOperation.QUESTION: {"gemini-vision", "bedrock-nova-lite"},
    ProviderOperation.MOMENT: {"gemini-vision", "bedrock-nova-lite"},
}


class ProviderConsentOptions(BaseModel):
    scopes: tuple[ConsentScope, ...]


@router.get("/provider-consents/options", responses={404: {"model": ErrorResponse}})
async def provider_consent_options(
    profile_id: ProfileId,
    version: Annotated[int, Query(ge=1)],
    operation: ProviderOperation,
    repository: Profiles,
) -> ProviderConsentOptions:
    profile = await PostgresProviderProfiles(repository.session).get(
        ProfileRef(profile_id=profile_id, version=version)
    )
    if profile is None:
        raise ApiError(
            status_code=404,
            code="PROVIDER_PROFILE_NOT_FOUND",
            message="The provider profile version was not found.",
            retryable=False,
        )
    return ProviderConsentOptions(
        scopes=tuple(
            ConsentScope(provider_id=provider_id, data=CONSENT_DATA[provider_id])
            for chain in profile.policy.candidates.values()
            for provider_id in chain
            if provider_id in _CONSENT_PROVIDERS.get(operation, set())
        )
    )


@router.post("/provider-consents", status_code=201, responses={422: {"model": ErrorResponse}})
async def grant_provider_consent(
    body: ProviderConsentRequest, repository: Profiles
) -> ProviderConsent:
    try:
        profile = await PostgresProviderProfiles(repository.session).get(body.profile)
        allowed = _CONSENT_PROVIDERS.get(body.operation, set())
        chain: set[str] = (
            {provider_id for values in profile.policy.candidates.values() for provider_id in values}
            if profile
            else set()
        )
        if any(
            scope.provider_id not in allowed
            or scope.provider_id not in chain
            or CONSENT_DATA.get(scope.provider_id) != scope.data
            for scope in body.scopes
        ):
            raise ApiError(
                status_code=422,
                code="PROVIDER_CONSENT_INVALID",
                message="The selected profile and operation do not support this consent scope.",
                retryable=False,
            )
        return await create_consent(repository.session, body)
    except SQLAlchemyError as exc:
        raise _database_failure() from exc


@router.delete("/provider-consents/{consent_id}", responses={404: {"model": ErrorResponse}})
async def revoke_provider_consent(consent_id: UUID, repository: Profiles) -> ProviderConsent:
    try:
        row = await locked_consent(repository.session, consent_id)
        if row is None:
            raise ApiError(
                status_code=404,
                code="PROVIDER_CONSENT_NOT_FOUND",
                message="The cloud consent grant was not found.",
                retryable=False,
            )
        row.revoked_at = row.revoked_at or datetime.now(UTC)
        receipt = consent_response(row)
        await repository.session.commit()
        return receipt
    except SQLAlchemyError as exc:
        raise _database_failure() from exc


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
