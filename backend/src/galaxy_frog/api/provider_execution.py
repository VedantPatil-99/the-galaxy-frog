"""Bind synchronous API operations to their provider policy and run records."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import cast
from uuid import UUID

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from galaxy_frog.adapters.generation.bedrock import BedrockGeneration
from galaxy_frog.api.errors import ApiError
from galaxy_frog.application.provider_policy import (
    ProviderAttempt,
    ProviderCode,
    ProviderFailure,
    ProviderRouting,
)
from galaxy_frog.application.provider_registry import (
    builtin_profiles,
    probe_ollama_models,
    provider_catalog,
)
from galaxy_frog.application.retrieval.explain_moment import MomentTextProvider
from galaxy_frog.config import Settings
from galaxy_frog.db.models import EmbeddingCollectionRow
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.domain.generation.models import (
    GenerationDraft,
    GenerationProviderError,
    GroundingText,
)
from galaxy_frog.domain.generation.ports import GenerationProvider
from galaxy_frog.domain.providers import (
    Capability,
    ProfileRef,
    ProviderOperation,
    ProviderProfile,
    ProviderSelection,
)
from galaxy_frog.domain.retrieval.models import RetrievedEvidence


class ApiProviderRun:
    def __init__(
        self, settings: Settings, policy: ProviderProfile, runtime: PostgresProviderRuntime
    ) -> None:
        self.settings = settings
        self.policy = policy
        self.runtime = runtime
        self.catalog = provider_catalog(settings)
        self.attempts: list[ProviderAttempt] = []
        self.usage: dict[str, int] | None = None

    def router(self, capability: Capability) -> ProviderRouting:
        async def record(attempt: ProviderAttempt) -> None:
            entry = next(item for item in self.catalog if item.provider_id == attempt.provider_id)
            await self.runtime.record(
                capability,
                attempt,
                pricing=entry.pricing,
                usage=self.usage if entry.provider_id == "bedrock-nova-lite" else None,
            )
            self.attempts.append(attempt)

        policy = self.policy
        if capability not in policy.candidates and not policy.evaluation:
            default = builtin_profiles()["local"].candidates.get(capability)
            if default:
                policy = policy.model_copy(update={"candidates": {capability: default}})
        return ProviderRouting(policy, self.catalog, self.runtime.admit, record)


async def api_provider_run(
    request: Request,
    repository: object,
    *,
    operation: ProviderOperation,
    selection: ProviderSelection | None,
    video_id: UUID,
    run_id: UUID,
) -> ApiProviderRun | None:
    engine: AsyncEngine | None = request.app.state.database_engine
    if engine is None and selection is None:
        return None
    if engine is None:
        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Provider policy requires PostgreSQL.",
            retryable=True,
        )
    profile = selection.profile if selection else ProfileRef(profile_id="local", version=1)
    from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository

    session = cast(SqlAlchemyVideoRepository, repository).session
    version = await PostgresProviderProfiles(session).get(profile)
    if version is None:
        raise ApiError(
            status_code=422,
            code="PROVIDER_PROFILE_NOT_FOUND",
            message="Select an available provider profile version.",
            retryable=False,
        )
    if version.policy.evaluation:
        settings: Settings = request.app.state.settings
        models = await probe_ollama_models(settings.ollama_base_url)
        pins = version.policy.pinned_revisions or {}
        selected_ids = {item for chain in version.policy.candidates.values() for item in chain}
        entries = {entry.provider_id: entry for entry in provider_catalog(settings)}
        invalid = set(pins) != selected_ids or any(
            (entry := entries.get(provider_id)) is None
            or entry.readiness != "configured"
            or entry.revision != pins[provider_id]
            or entry.revision in {None, "ollama"}
            or (entry.provider == "ollama" and models.get(entry.model) != entry.revision)
            for provider_id in pins
        )
        collections = version.policy.embedding_collections or {}
        for capability, dimension in (
            (Capability.TEXT_EMBEDDING, 1024),
            (Capability.VISUAL_EMBEDDING, 768),
        ):
            collection_id = collections.get(capability)
            collection = (
                await session.get(EmbeddingCollectionRow, collection_id) if collection_id else None
            )
            candidate_ids = version.policy.candidates.get(capability, ())
            entry = entries.get(candidate_ids[0]) if candidate_ids else None
            if (
                collection is None
                or entry is None
                or (
                    collection.provider,
                    collection.model,
                    collection.revision,
                    collection.dimension,
                    collection.normalization,
                )
                != (entry.provider, entry.model, entry.revision, dimension, "l2")
            ):
                invalid = True
        if invalid:
            raise ApiError(
                status_code=422,
                code="PROVIDER_EVALUATION_UNVERIFIABLE",
                message="This evaluation profile contains an unverified runtime revision or collection.",
                retryable=False,
            )
    runtime = PostgresProviderRuntime(
        async_sessionmaker(engine, expire_on_commit=False),
        engine=engine,
        profile=profile,
        operation=operation,
        run_id=run_id,
        source=str(video_id),
        consent_id=selection.consent_id if selection else None,
    )
    return ApiProviderRun(request.app.state.settings, version.policy, runtime)


class RoutedTextGeneration:
    def __init__(self, run: ApiProviderRun, local: GenerationProvider | MomentTextProvider) -> None:
        self._run, self._local = run, local

    async def generate(
        self, question: str, evidence: tuple[RetrievedEvidence, ...]
    ) -> GenerationDraft:
        return await self._generate(
            partial(cast(GenerationProvider, self._local).generate, question, evidence),
            lambda provider: provider.generate(question, evidence),
        )

    async def generate_moment(
        self, question: str, evidence: tuple[GroundingText, ...]
    ) -> GenerationDraft:
        return await self._generate(
            partial(cast(MomentTextProvider, self._local).generate_moment, question, evidence),
            lambda provider: provider.generate_moment(question, evidence),
        )

    async def _generate(
        self,
        local: Callable[[], Awaitable[GenerationDraft]],
        cloud: Callable[[BedrockGeneration], Awaitable[GenerationDraft]],
    ) -> GenerationDraft:
        async def local_action() -> GenerationDraft:
            models = await probe_ollama_models(self._run.settings.ollama_base_url)
            name = self._run.settings.generation_model
            observed_revision = models.get(name) or models.get(f"{name}:latest")
            if observed_revision is None or (
                self._run.settings.generation_model_revision
                and self._run.settings.generation_model_revision != observed_revision
            ):
                raise ProviderFailure(ProviderCode.SETUP_REQUIRED)
            try:
                return await local()
            except GenerationProviderError as exc:
                raise ProviderFailure(ProviderCode.UNAVAILABLE) from exc

        async def cloud_action() -> GenerationDraft:
            settings = self._run.settings
            assert settings.aws_bearer_token_bedrock is not None
            async with httpx.AsyncClient(timeout=75, trust_env=False) as client:
                provider = BedrockGeneration(
                    settings.bedrock_region,
                    settings.aws_bearer_token_bedrock.get_secret_value(),
                    client,
                )
                try:
                    return await cloud(provider)
                finally:
                    self._run.usage = provider.usage

        return await self._run.router(Capability.GENERATION).execute(
            Capability.GENERATION,
            {"ollama-text": local_action, "bedrock-nova-lite": cloud_action},
            timeout_seconds=90,
        )


def provider_api_error(error: ProviderFailure, trace_id: UUID) -> ApiError:
    return ApiError(
        status_code=429
        if error.code in {ProviderCode.RATE_LIMITED, ProviderCode.QUOTA_EXHAUSTED}
        else 503,
        code=error.code.value,
        message="The selected provider route could not complete.",
        retryable=error.code not in {ProviderCode.SAFETY_BLOCKED, ProviderCode.INCOMPATIBLE},
        suggested_action="Review provider health and consent before retrying.",
        details={
            "trace_id": str(trace_id),
            "retry_at": error.retry_at.isoformat() if error.retry_at else None,
        },
    )
