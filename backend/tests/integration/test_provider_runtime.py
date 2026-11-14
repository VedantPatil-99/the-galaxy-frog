"""Exercise provider policy against the real PostgreSQL admission and consent store."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import builtin_profiles, provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import (
    ProviderAttemptRow,
    ProviderConsentRow,
    ProviderProfileVersionRow,
    ProviderRuntimeRow,
)
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles, ProfileVersionConflict
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime, create_consent, locked_consent
from galaxy_frog.domain.providers import (
    ConsentScope,
    ExternalData,
    ProfileRef,
    ProviderConsentRequest,
    ProviderOperation,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires existing PostgreSQL/pgvector"
)


@pytest.mark.asyncio
async def test_profile_race_persists_one_immutable_version() -> None:
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    profile_id = f"test-{uuid4().hex}"
    policy = builtin_profiles()["local"]

    async def save() -> int:
        async with sessions() as session:
            return (await PostgresProviderProfiles(session).append(profile_id, 0, policy)).version

    try:
        results = await asyncio.gather(save(), save(), return_exceptions=True)
        assert results.count(1) == 1
        assert any(isinstance(result, ProfileVersionConflict) for result in results)
        async with sessions() as session:
            saved = await PostgresProviderProfiles(session).get(
                ProfileRef(profile_id=profile_id, version=1)
            )
            assert saved is not None and saved.policy == policy
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderProfileVersionRow).where(
                    ProviderProfileVersionRow.profile_id == profile_id
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_concurrency_quota_and_revocation_are_shared() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    profile = ProfileRef(profile_id="free_cloud", version=1)
    run_id = uuid4()
    source = str(uuid4())
    entry = next(
        entry for entry in provider_catalog(settings) if entry.provider_id == "cloudflare-whisper"
    )
    async with sessions() as session:
        grant = await create_consent(
            session,
            ProviderConsentRequest(
                profile=profile,
                operation=ProviderOperation.IMPORT,
                source=source,
                scopes=(ConsentScope(provider_id=entry.provider_id, data=ExternalData.AUDIO),),
            ),
        )
    runtime = PostgresProviderRuntime(
        sessions,
        engine=engine,
        profile=profile,
        operation=ProviderOperation.IMPORT,
        run_id=run_id,
        source=source,
        consent_id=grant.consent_id,
    )
    try:
        async with runtime.admit(entry):
            with pytest.raises(ProviderFailure, match="PROVIDER_BUSY"):
                async with runtime.admit(entry):
                    pytest.fail("A second call entered the same provider.")
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            async with runtime.admit(entry):
                raise ProviderFailure(
                    ProviderCode.QUOTA_EXHAUSTED, retry_at=datetime.now(UTC) + timedelta(seconds=60)
                )
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            async with runtime.admit(entry):
                pytest.fail("The exhausted provider admitted a call.")
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
            row = await locked_consent(session, grant.consent_id)
            assert row is not None
            row.revoked_at = datetime.now(UTC)
        with pytest.raises(ProviderFailure, match="PROVIDER_CONSENT_REQUIRED"):
            async with runtime.admit(entry):
                pytest.fail("A revoked grant admitted a call.")
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id == run_id)
            )
            await session.execute(
                delete(ProviderConsentRow).where(ProviderConsentRow.id == grant.consent_id)
            )
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
        await engine.dispose()
