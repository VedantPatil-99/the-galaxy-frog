"""Verify a provider's failure state survives a renewed runtime instance."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import ProviderAttemptRow, ProviderConsentRow, ProviderRuntimeRow
from galaxy_frog.db.provider_runtime import (
    CONSENT_DATA,
    PostgresProviderRuntime,
    create_consent,
    provider_attempt_rows,
)
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
async def test_circuit_recovers_after_the_next_successful_probe() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entry = next(
        item for item in provider_catalog(settings) if item.provider_id == "ollama-text"
    ).model_copy(update={"provider_id": f"test-{uuid4().hex}"})

    def runtime() -> PostgresProviderRuntime:
        source = str(uuid4())
        return PostgresProviderRuntime(
            sessions,
            engine=engine,
            profile=ProfileRef(profile_id="local", version=1),
            operation=ProviderOperation.QUESTION,
            run_id=uuid4(),
            source=source,
        )

    try:
        for _ in range(3):
            with pytest.raises(TimeoutError):
                async with runtime().admit(entry):
                    raise TimeoutError
        with pytest.raises(ProviderFailure, match=ProviderCode.CIRCUIT_OPEN.value) as failure:
            async with runtime().admit(entry):
                pytest.fail("An open circuit admitted a request.")
        assert failure.value.retry_at is not None
        async with sessions.begin() as session:
            await session.execute(
                update(ProviderRuntimeRow)
                .where(ProviderRuntimeRow.provider_id == entry.provider_id)
                .values(blocked_until=datetime.now(UTC) - timedelta(seconds=1))
            )
        async with runtime().admit(entry):
            pass
        async with sessions() as session:
            status = await session.get(ProviderRuntimeRow, entry.provider_id)
        assert status is not None
        assert status.state == "healthy" and status.failure_count == 0
        assert status.blocked_until is None
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_hosted_outcome_cannot_redispatch_using_the_same_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entry = next(
        item for item in provider_catalog(settings) if item.provider_id == "bedrock-nova-lite"
    ).model_copy(update={"provider_id": f"test-{uuid4().hex}"})
    monkeypatch.setitem(CONSENT_DATA, entry.provider_id, ExternalData.PAID_TEXT)
    request = ProviderConsentRequest(
        profile=ProfileRef(profile_id="aws", version=1),
        operation=ProviderOperation.QUESTION,
        source=str(uuid4()),
        scopes=(ConsentScope(provider_id=entry.provider_id, data=ExternalData.PAID_TEXT),),
    )
    first_run, new_run = uuid4(), uuid4()
    grants: list[UUID] = []

    async def grant() -> UUID:
        async with sessions() as session:
            receipt = await create_consent(session, request)
        grants.append(receipt.consent_id)
        return receipt.consent_id

    def runtime(run_id: UUID, consent_id: UUID) -> PostgresProviderRuntime:
        return PostgresProviderRuntime(
            sessions,
            engine=engine,
            profile=request.profile,
            operation=request.operation,
            run_id=run_id,
            source=request.source,
            consent_id=consent_id,
        )

    try:
        consent = await grant()
        first = runtime(first_run, consent)
        with pytest.raises(TimeoutError):
            async with first.admit(entry):
                await first.verify_consent(entry, dispatch=True)
                raise TimeoutError
        with pytest.raises(ProviderFailure, match=ProviderCode.OUTCOME_UNKNOWN.value):
            async with runtime(first_run, consent).admit(entry):
                pytest.fail("The same run tried to dispatch an unresolved operation again.")
        with pytest.raises(ProviderFailure, match=ProviderCode.CONSENT_REQUIRED.value):
            async with runtime(new_run, consent).admit(entry):
                pytest.fail("A second run reused an existing consent grant.")

        second = runtime(new_run, await grant())
        async with second.admit(entry):
            await second.verify_consent(entry, dispatch=True)

        async with sessions() as session:
            unknown = await provider_attempt_rows(session, request.operation, first_run)
            completed = await provider_attempt_rows(session, request.operation, new_run)
        assert [row.payload["status"] for row in unknown] == ["dispatch_started"]
        assert [row.payload["status"] for row in completed] == [
            "dispatch_started",
            "dispatch_finished",
        ]
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderAttemptRow).where(
                    ProviderAttemptRow.run_id.in_((first_run, new_run))
                )
            )
            await session.execute(
                delete(ProviderConsentRow).where(ProviderConsentRow.id.in_(grants))
            )
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
        await engine.dispose()
