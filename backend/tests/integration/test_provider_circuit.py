"""Verify a provider's failure state survives a renewed runtime instance."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import ProviderRuntimeRow
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.domain.providers import ProfileRef, ProviderOperation

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
