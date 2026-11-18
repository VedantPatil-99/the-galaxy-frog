"""Project-scoped Gemini budgets shared by API and background workers."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, DateTime, Index, String, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.db.models import Base
from galaxy_frog.domain.providers import QuotaLimits


class ProviderQuotaReservationRow(Base):
    __tablename__ = "provider_quota_reservations"
    __table_args__ = (Index("ix_provider_quota_group_time", "group_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_tokens: Mapped[int] = mapped_column(BigInteger)


class QuotaUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    requests_last_minute: int = Field(ge=0)
    reserved_input_tokens_last_minute: int = Field(ge=0)
    requests_today: int = Field(ge=0)
    daily_reset_at: datetime


async def pacific_day(session: AsyncSession, now: datetime) -> tuple[datetime, datetime]:
    row = (
        await session.execute(
            text("""
            SELECT
              date_trunc('day', CAST(:now AS timestamptz) AT TIME ZONE 'America/Los_Angeles')
                AT TIME ZONE 'America/Los_Angeles',
              (date_trunc('day', CAST(:now AS timestamptz) AT TIME ZONE 'America/Los_Angeles')
                + interval '1 day') AT TIME ZONE 'America/Los_Angeles'
        """),
            {"now": now},
        )
    ).one()
    return row[0], row[1]


async def quota_rows(
    session: AsyncSession,
    group_id: str,
    now: datetime,
    day_start: datetime,
) -> tuple[ProviderQuotaReservationRow, ...]:
    rows = await session.scalars(
        select(ProviderQuotaReservationRow)
        .where(
            ProviderQuotaReservationRow.group_id == group_id,
            ProviderQuotaReservationRow.created_at >= min(day_start, now - timedelta(seconds=60)),
        )
        .order_by(ProviderQuotaReservationRow.created_at)
    )
    return tuple(rows)


def quota_usage(
    rows: tuple[ProviderQuotaReservationRow, ...],
    now: datetime,
    day_start: datetime,
    reset_at: datetime,
) -> QuotaUsage:
    minute = tuple(row for row in rows if row.created_at > now - timedelta(seconds=60))
    return QuotaUsage(
        requests_last_minute=len(minute),
        reserved_input_tokens_last_minute=sum(row.input_tokens for row in minute),
        requests_today=sum(row.created_at >= day_start for row in rows),
        daily_reset_at=reset_at,
    )


async def reserve_quota(
    session: AsyncSession,
    group_id: str,
    limits: QuotaLimits,
    input_tokens: int,
) -> None:
    """Reserve conservatively before dispatch; uncertain calls are never refunded."""
    if not 0 < input_tokens <= limits.input_tokens_per_minute:
        raise ProviderFailure(ProviderCode.INPUT_LIMIT)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 818))"), {"key": group_id}
    )
    now = datetime.now(UTC)
    day_start, reset_at = await pacific_day(session, now)
    rows = await quota_rows(session, group_id, now, day_start)
    usage = quota_usage(rows, now, day_start, reset_at)
    if usage.requests_today >= limits.requests_per_day:
        raise ProviderFailure(ProviderCode.QUOTA_EXHAUSTED, retry_at=reset_at)
    active = [row for row in rows if row.created_at > now - timedelta(seconds=60)]
    tokens = sum(row.input_tokens for row in active)
    if (
        len(active) >= limits.requests_per_minute
        or tokens + input_tokens > limits.input_tokens_per_minute
    ):
        # The bounded input fits once all prior reservations expire, so this
        # loop always raises before its natural exit.
        for index, row in enumerate(active):  # pragma: no branch
            tokens -= row.input_tokens
            if (
                len(active) - index - 1 < limits.requests_per_minute
                and tokens + input_tokens <= limits.input_tokens_per_minute
            ):
                raise ProviderFailure(
                    ProviderCode.RATE_LIMITED, retry_at=row.created_at + timedelta(seconds=60)
                )
    await session.execute(
        delete(ProviderQuotaReservationRow).where(
            ProviderQuotaReservationRow.group_id == group_id,
            ProviderQuotaReservationRow.created_at < now - timedelta(days=2),
        )
    )
    session.add(
        ProviderQuotaReservationRow(
            id=uuid4(),
            group_id=group_id,
            created_at=now,
            input_tokens=input_tokens,
        )
    )
