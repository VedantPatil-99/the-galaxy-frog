"""Shared cooldown, application budgets, and health use real scoped PostgreSQL rows."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import SecretStr
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.api.app import create_app
from galaxy_frog.api.routes import providers as routes
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db import provider_runtime as runtime_module
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import ProviderAttemptRow, ProviderConsentRow, ProviderRuntimeRow
from galaxy_frog.db.provider_quota import ProviderQuotaReservationRow
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime, create_consent
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.providers import (
    ConsentScope,
    ExternalData,
    ProfileRef,
    ProviderConsentRequest,
    ProviderOperation,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires configured PostgreSQL"
)


@pytest.mark.asyncio
async def test_project_cooldown_health_and_recovery_are_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Settings(
        gemini_api_key=SecretStr("synthetic-key"),
        gemini_project_id="test-" + uuid4().hex[:16],
        gemini_generation_enabled=True,
        gemini_generation_model="gemini-3.5-flash-lite",
    )
    engine = create_database_engine(config)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entry = next(e for e in provider_catalog(config) if e.provider_id == "gemini-text")
    first = entry.model_copy(update={"provider_id": "test-first-" + uuid4().hex})
    second = entry.model_copy(update={"provider_id": "test-second-" + uuid4().hex})
    assert entry.quota_group is not None
    ids = [first.provider_id, second.provider_id, entry.quota_group]
    for item in (first, second):
        monkeypatch.setitem(runtime_module.CONSENT_DATA, item.provider_id, ExternalData.TEXT)
    profile = ProfileRef(profile_id="gemini", version=1)
    run_id, source = uuid4(), str(uuid4())
    consent_id = None
    try:
        async with sessions() as session:
            consent = await create_consent(
                session,
                ProviderConsentRequest(
                    profile=profile,
                    operation=ProviderOperation.QUESTION,
                    source=source,
                    scopes=tuple(
                        ConsentScope(provider_id=e.provider_id, data=ExternalData.TEXT)
                        for e in (first, second)
                    ),
                ),
            )
            consent_id = consent.consent_id
        runtime = PostgresProviderRuntime(
            sessions,
            engine=engine,
            profile=profile,
            operation=ProviderOperation.QUESTION,
            run_id=run_id,
            source=source,
            consent_id=consent_id,
        )
        async with runtime.admit(first):
            await runtime.verify_consent(first, dispatch=True, input_tokens=100)
        # Invalid output remains observable without opening a transient-failure circuit.
        with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_OUTPUT"):
            async with runtime.admit(first):
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
        # A provider daily-quota error without Retry-After uses midnight Pacific.
        failure = ProviderFailure(ProviderCode.QUOTA_EXHAUSTED)
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            async with runtime.admit(first):
                raise failure
        assert failure.retry_at is not None and failure.retry_at > datetime.now(UTC)
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            async with runtime.admit(second):
                pytest.fail("A different model in the exhausted project was admitted.")
        monkeypatch.setattr(routes, "capabilities", AsyncMock(return_value=(first, second)))
        monkeypatch.setattr(routes, "probe_ollama_models", AsyncMock(return_value={}))
        request = Request({"type": "http", "app": create_app(config)})
        async with sessions() as session:
            health = await routes.provider_health(request, SqlAlchemyVideoRepository(session))
        assert all(row.state == "QUOTA_EXHAUSTED" for row in health)
        assert health[0].quota_usage == health[1].quota_usage
        assert health[0].quota_usage is not None and health[0].quota_usage.requests_today == 1
        async with sessions() as session:
            await session.execute(
                update(ProviderRuntimeRow)
                .where(ProviderRuntimeRow.provider_id.in_(ids))
                .values(blocked_until=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        async with runtime.admit(second):
            await runtime.verify_consent(second, dispatch=False)
        async with sessions() as session:
            health = await routes.provider_health(request, SqlAlchemyVideoRepository(session))
        assert health[1].state == "healthy"
    finally:
        async with sessions() as session:
            await session.execute(
                delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id == run_id)
            )
            if consent_id:
                await session.execute(
                    delete(ProviderConsentRow).where(ProviderConsentRow.id == consent_id)
                )
            await session.execute(
                delete(ProviderRuntimeRow).where(ProviderRuntimeRow.provider_id.in_(ids))
            )
            await session.execute(
                delete(ProviderQuotaReservationRow).where(
                    ProviderQuotaReservationRow.group_id == entry.quota_group
                )
            )
            await session.commit()
        await engine.dispose()
