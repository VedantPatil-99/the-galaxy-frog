"""Resolve the canonical ingestion job's pinned provider policy for each operation."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.application.provider_policy import (
    ProviderAttempt,
    ProviderCode,
    ProviderFailure,
    ProviderRouting,
)
from galaxy_frog.application.provider_registry import builtin_profiles, provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.models import IngestionJobRow
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.domain.providers import (
    Capability,
    ProfileRef,
    ProviderOperation,
    ProviderSelection,
)


class IngestionProviderRouting:
    def __init__(self, engine: AsyncEngine, settings: Settings, worker_id: str) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        self._catalog = provider_catalog(settings)
        self._worker_id = worker_id

    async def execute[T](
        self,
        job_id: UUID,
        capability: Capability,
        actions: dict[str, Callable[[], Awaitable[T]]],
        *,
        timeout_seconds: float,
        input_duration_ms: int | None = None,
    ) -> T:
        async with self._sessions() as session:
            job = await session.get(IngestionJobRow, job_id)
            if job is None:
                raise ProviderFailure(ProviderCode.UNAVAILABLE)
            source = job.external_id
            selection = (
                ProviderSelection.model_validate(job.provider_selection)
                if job.provider_selection is not None
                else ProviderSelection(profile=ProfileRef(profile_id="local", version=1))
            )
            version = await PostgresProviderProfiles(session).get(selection.profile)
            if version is None:
                raise ProviderFailure(ProviderCode.INCOMPATIBLE)
            policy = version.policy
        runtime = PostgresProviderRuntime(
            self._sessions,
            engine=self._engine,
            profile=selection.profile,
            operation=ProviderOperation.IMPORT,
            run_id=job_id,
            source=source,
            consent_id=selection.consent_id,
            worker_id=self._worker_id,
        )

        async def record(attempt: ProviderAttempt) -> None:
            entry = next(item for item in self._catalog if item.provider_id == attempt.provider_id)
            await runtime.record(
                capability, attempt, input_duration_ms=input_duration_ms, pricing=entry.pricing
            )

        if capability is Capability.OCR and capability not in policy.candidates:
            entry = next(item for item in self._catalog if item.capability is capability)
            await record(
                ProviderAttempt(
                    provider_id=entry.provider_id,
                    provider=entry.provider,
                    model=entry.model,
                    revision=entry.revision,
                    device=entry.device,
                    status="skipped",
                    reason=ProviderCode.NOT_SELECTED,
                    requested_mode=policy.mode,
                )
            )

        if capability not in policy.candidates and not policy.evaluation:
            default = builtin_profiles()["local"].candidates.get(capability)
            if default:
                policy = policy.model_copy(update={"candidates": {capability: default}})
        return await ProviderRouting(policy, self._catalog, runtime.admit, record).execute(
            capability, actions, timeout_seconds=timeout_seconds
        )
