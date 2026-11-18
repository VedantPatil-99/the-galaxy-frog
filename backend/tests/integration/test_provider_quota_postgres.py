"""Real PostgreSQL proves shared quota admission across concurrent workers."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.provider_quota import ProviderQuotaReservationRow, pacific_day, reserve_quota
from galaxy_frog.domain.providers import QuotaLimits

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires configured PostgreSQL"
)


@pytest.mark.asyncio
async def test_shared_project_limits_are_atomic_and_recoverable() -> None:
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    group, other = uuid4().hex, uuid4().hex
    limits = QuotaLimits(requests_per_minute=2, input_tokens_per_minute=10, requests_per_day=3)

    async def reserve(group_id: str, tokens: int) -> ProviderCode | None:
        try:
            async with sessions() as session:
                await reserve_quota(session, group_id, limits, tokens)
                await session.commit()
        except ProviderFailure as exc:
            assert exc.retry_at is not None
            return exc.code
        return None

    try:
        results = await asyncio.gather(reserve(group, 6), reserve(group, 6))
        assert results.count(None) == 1 and ProviderCode.RATE_LIMITED in results
        assert await reserve(other, 6) is None
        assert await reserve(other, 3) is None
        # An eight-token request needs both earlier reservations to expire.
        assert await reserve(other, 8) is ProviderCode.RATE_LIMITED
        async with sessions() as session:
            # Previous calls still consume today's count after their minute expires.
            await session.execute(
                update(ProviderQuotaReservationRow)
                .where(
                    ProviderQuotaReservationRow.group_id == group,
                )
                .values(created_at=datetime.now(UTC) - timedelta(seconds=61))
            )
            await session.commit()
        assert await reserve(group, 1) is None
        assert await reserve(group, 1) is None
        assert await reserve(group, 1) is ProviderCode.QUOTA_EXHAUSTED
        async with sessions() as session:
            assert (
                len(
                    tuple(
                        await session.scalars(
                            select(ProviderQuotaReservationRow).where(
                                ProviderQuotaReservationRow.group_id == group
                            )
                        )
                    )
                )
                == 3
            )
            # Midnight Pacific and DST boundaries are resolved by PostgreSQL's timezone database.
            start, reset = await pacific_day(session, datetime(2026, 3, 8, 12, tzinfo=UTC))
            assert reset - start == timedelta(hours=23)
            start, reset = await pacific_day(session, datetime(2026, 11, 1, 12, tzinfo=UTC))
            assert reset - start == timedelta(hours=25)
    finally:
        async with sessions() as session:
            await session.execute(
                delete(ProviderQuotaReservationRow).where(
                    ProviderQuotaReservationRow.group_id.in_([group, other])
                )
            )
            await session.commit()
        await engine.dispose()
