"""Exercise provider policy against the real PostgreSQL admission and consent store."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.api.app import create_app
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import builtin_profiles, provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.models import (
    IngestionJobRow,
    ProviderAttemptRow,
    ProviderConsentRow,
    ProviderProfileVersionRow,
    ProviderRuntimeRow,
)
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles, ProfileVersionConflict
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime, create_consent, locked_consent
from galaxy_frog.domain.ingestion.models import IngestionJobStatus
from galaxy_frog.domain.providers import (
    ConsentScope,
    ExternalData,
    ProfileRef,
    ProviderConsentRequest,
    ProviderOperation,
    ProviderSelection,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

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
    source = uuid4().hex[:11]
    reference = SourceReference(
        VideoSourceKind.YOUTUBE, source, f"https://www.youtube.com/watch?v={source}"
    )
    fingerprint = uuid4().hex * 2
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
        jobs = PostgresIngestionRepository(session)
        job, created = await jobs.create_or_get(
            reference,
            fingerprint,
            provider_selection=ProviderSelection(profile=profile, consent_id=grant.consent_id),
        )
        assert created
        duplicate, created = await jobs.create_or_get(reference, fingerprint)
        assert not created and duplicate.job_id == job.job_id
        await session.execute(
            update(IngestionJobRow)
            .where(IngestionJobRow.id == job.job_id)
            .values(
                status=IngestionJobStatus.RUNNING,
                lease_owner="phase8-test",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
                heartbeat_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                attempt=1,
            )
        )
        await session.commit()
    run_id = job.job_id
    runtime = PostgresProviderRuntime(
        sessions,
        engine=engine,
        profile=profile,
        operation=ProviderOperation.IMPORT,
        run_id=run_id,
        source=source,
        consent_id=grant.consent_id,
        worker_id="phase8-test",
    )
    try:
        async with runtime.admit(entry):
            await runtime.verify_consent(entry, dispatch=True)
            with pytest.raises(ProviderFailure, match="PROVIDER_BUSY"):
                async with runtime.admit(entry):
                    pytest.fail("A second call entered the same provider.")
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            async with runtime.admit(entry):
                await runtime.verify_consent(entry, dispatch=True)
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
            assert row.dispatch_count == 2
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
            await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id == run_id))
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_routes_report_revocation_and_block_wrong_scope() -> None:
    app = create_app(Settings())
    profile = {"profile_id": "aws", "version": 1}
    source = str(uuid4())
    body = {
        "profile": profile,
        "source": source,
        "operation": "question",
        "scopes": [{"provider_id": "bedrock-nova-lite", "data": "paid_text"}],
    }
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        health = await client.get("/v1/providers/health")
        assert health.status_code == 200
        options = await client.get(
            "/v1/provider-consents/options",
            params={**profile, "operation": "question"},
        )
        assert options.json()["scopes"] == body["scopes"]
        invalid = await client.post("/v1/provider-consents", json={**body, "operation": "search"})
        assert invalid.status_code == 422
        response = await client.post("/v1/provider-consents", json=body)
        assert response.status_code == 201
        consent = response.json()["consent_id"]
        revoked = await client.delete(f"/v1/provider-consents/{consent}")
        assert revoked.json()["revoked_at"] is not None
        assert revoked.json()["dispatch_count"] == 0
        assert (await client.delete(f"/v1/provider-consents/{uuid4()}")).status_code == 404
        assert (await client.get(f"/v1/provider-runs/question/{uuid4()}")).json() == []
