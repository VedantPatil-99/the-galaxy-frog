"""Check that a bounded provider history keeps recent attempts after retries."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import ProviderAttemptRow
from galaxy_frog.db.provider_runtime import provider_attempt_rows
from galaxy_frog.domain.providers import Capability, ProviderOperation

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires existing PostgreSQL/pgvector"
)


@pytest.mark.asyncio
async def test_bounded_provider_history_includes_latest_attempts_in_order() -> None:
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    run_id = uuid4()
    start = datetime.now(UTC)
    try:
        async with sessions.begin() as session:
            session.add_all(
                [
                    ProviderAttemptRow(
                        id=uuid4(),
                        operation=ProviderOperation.IMPORT,
                        run_id=run_id,
                        profile_id="local",
                        profile_version=1,
                        capability=Capability.TRANSCRIPTION,
                        payload={"sequence": sequence},
                        created_at=start + timedelta(milliseconds=sequence),
                    )
                    for sequence in range(502)
                ]
            )
        async with sessions() as session:
            rows = await provider_attempt_rows(session, ProviderOperation.IMPORT, run_id)
        assert [row.payload["sequence"] for row in rows] == list(range(2, 502))
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id == run_id)
            )
        await engine.dispose()
