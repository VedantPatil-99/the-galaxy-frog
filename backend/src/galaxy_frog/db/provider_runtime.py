"""Cross-process admission and safe provider run records."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.application.provider_policy import ProviderAttempt, ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import PricingSource, ProviderEntry
from galaxy_frog.db.models import (
    IngestionJobRow,
    ProviderAttemptRow,
    ProviderConsentRow,
    ProviderRuntimeRow,
)
from galaxy_frog.domain.providers import (
    Capability,
    ConsentScope,
    ExternalData,
    Location,
    ProfileRef,
    ProviderConsent,
    ProviderConsentRequest,
    ProviderOperation,
)

CONSENT_DATA = {
    "cloudflare-whisper": ExternalData.AUDIO,
    "llamaparse-ocr": ExternalData.FRAMES,
    "gemini-vision": ExternalData.FRAMES_AND_TEXT,
    "bedrock-nova-lite": ExternalData.PAID_TEXT,
}


def consent_response(row: ProviderConsentRow) -> ProviderConsent:
    return ProviderConsent(
        consent_id=row.id,
        profile=ProfileRef(profile_id=row.profile_id, version=row.profile_version),
        operation=ProviderOperation(row.operation),
        source=row.source,
        scopes=tuple(ConsentScope.model_validate(scope) for scope in row.scopes),
        run_id=row.run_id,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        dispatch_count=row.dispatch_count,
    )


async def create_consent(session: AsyncSession, body: ProviderConsentRequest) -> ProviderConsent:
    row = ProviderConsentRow(
        id=uuid4(),
        profile_id=body.profile.profile_id,
        profile_version=body.profile.version,
        source=body.source,
        operation=body.operation,
        scopes=[
            {"provider_id": scope.provider_id, "data": scope.data.value} for scope in body.scopes
        ],
        created_at=datetime.now(UTC),
        dispatch_count=0,
    )
    session.add(row)
    await session.flush()
    receipt = consent_response(row)
    await session.commit()
    return receipt


async def locked_consent(session: AsyncSession, consent_id: UUID) -> ProviderConsentRow | None:
    return await session.scalar(
        select(ProviderConsentRow).where(ProviderConsentRow.id == consent_id).with_for_update()
    )


async def provider_attempt_rows(
    session: AsyncSession, operation: ProviderOperation, run_id: UUID
) -> tuple[ProviderAttemptRow, ...]:
    return tuple(
        await session.scalars(
            select(ProviderAttemptRow)
            .where(ProviderAttemptRow.operation == operation, ProviderAttemptRow.run_id == run_id)
            .order_by(ProviderAttemptRow.created_at, ProviderAttemptRow.id)
            .limit(500)
        )
    )


class PostgresProviderRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine,
        profile: ProfileRef,
        operation: ProviderOperation,
        run_id: UUID,
        source: str,
        consent_id: UUID | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.sessions = sessions
        self.engine = engine
        self.profile = profile
        self.operation = operation
        self.run_id = run_id
        self.source = source
        self.consent_id = consent_id
        self.worker_id = worker_id

    @asynccontextmanager
    async def admit(self, entry: ProviderEntry) -> AsyncGenerator[None]:
        now = datetime.now(UTC)
        # Hold a physical connection for the cross-process lock without locking rows during inference.
        async with self.sessions() as session, self.engine.connect() as connection:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 817))"),
                {"key": entry.provider_id},
            )
            if not acquired:
                raise ProviderFailure(ProviderCode.BUSY)
            try:
                await self._start(session, entry, now)
                try:
                    yield
                except TimeoutError:
                    await self._observe(
                        session,
                        entry,
                        ProviderFailure(
                            ProviderCode.TIMEOUT
                            if entry.location is Location.LOCAL
                            else ProviderCode.OUTCOME_UNKNOWN
                        ),
                    )
                    raise
                except ProviderFailure as exc:
                    await self._observe(session, entry, exc)
                    raise
                else:
                    await self._observe(session, entry, None)
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 817))"),
                    {"key": entry.provider_id},
                )

    async def _start(self, session: AsyncSession, entry: ProviderEntry, now: datetime) -> None:
        row = await session.get(ProviderRuntimeRow, entry.provider_id)
        if row and row.blocked_until is not None and row.blocked_until > now:
            raise ProviderFailure(ProviderCode(row.state), retry_at=row.blocked_until)
        if entry.location is not Location.LOCAL:
            if self.worker_id is not None:
                job = await session.scalar(
                    select(IngestionJobRow)
                    .where(IngestionJobRow.id == self.run_id)
                    .with_for_update()
                )
                if (
                    job is None
                    or job.lease_owner != self.worker_id
                    or job.lease_expires_at is None
                    or job.lease_expires_at <= datetime.now(UTC)
                    or job.cancel_requested_at is not None
                ):
                    raise ProviderFailure(ProviderCode.UNAVAILABLE)
            grant = await self._authorize(session, entry)
            if await self._outstanding_dispatch(session, entry):
                raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN)
            grant.dispatch_count += 1
            session.add(self._dispatch_record(entry, "dispatch_started"))
        await session.commit()

    async def _outstanding_dispatch(self, session: AsyncSession, entry: ProviderEntry) -> bool:
        rows = await session.scalars(
            select(ProviderAttemptRow)
            .where(
                ProviderAttemptRow.operation == self.operation,
                ProviderAttemptRow.run_id == self.run_id,
            )
            .order_by(ProviderAttemptRow.created_at.desc())
        )
        for row in rows:
            if row.payload.get("provider_id") == entry.provider_id and row.payload.get(
                "consent_id"
            ) == str(self.consent_id):
                return row.payload.get("status") == "dispatch_started"
        return False

    def _dispatch_record(self, entry: ProviderEntry, status: str) -> ProviderAttemptRow:
        return ProviderAttemptRow(
            id=uuid4(),
            operation=self.operation,
            run_id=self.run_id,
            profile_id=self.profile.profile_id,
            profile_version=self.profile.version,
            capability=entry.capability,
            payload={
                "provider_id": entry.provider_id,
                "provider": entry.provider,
                "model": entry.model,
                "revision": entry.revision,
                "device": entry.device,
                "status": status,
                "consent_id": str(self.consent_id),
            },
            created_at=datetime.now(UTC),
        )

    async def _authorize(self, session: AsyncSession, entry: ProviderEntry) -> ProviderConsentRow:
        if self.consent_id is None:
            raise ProviderFailure(ProviderCode.CONSENT_REQUIRED)
        grant = await locked_consent(session, self.consent_id)
        if (
            grant is None
            or grant.revoked_at is not None
            or grant.operation != self.operation
            or grant.source != self.source
            or grant.profile_id != self.profile.profile_id
            or grant.profile_version != self.profile.version
            or grant.run_id not in {None, self.run_id}
            or {"provider_id": entry.provider_id, "data": CONSENT_DATA[entry.provider_id].value}
            not in grant.scopes
        ):
            raise ProviderFailure(ProviderCode.CONSENT_REQUIRED)
        grant.run_id = self.run_id
        return grant

    async def verify_consent(self, entry: ProviderEntry) -> None:
        async with self.sessions() as session:
            await self._authorize(session, entry)
            await session.commit()

    async def _observe(
        self, session: AsyncSession, entry: ProviderEntry, failure: ProviderFailure | None
    ) -> None:
        now = datetime.now(UTC)
        row = await session.get(ProviderRuntimeRow, entry.provider_id)
        count = row.failure_count if row else 0
        blocked_until = None
        state = failure.code.value if failure else "healthy"
        if failure is None:
            count = 0
        elif failure.code in {ProviderCode.QUOTA_EXHAUSTED, ProviderCode.RATE_LIMITED}:
            blocked_until = failure.retry_at or now + timedelta(seconds=60)
        elif failure.code in {
            ProviderCode.UNAVAILABLE,
            ProviderCode.TIMEOUT,
            ProviderCode.OUTCOME_UNKNOWN,
            ProviderCode.AUTH_FAILED,
        }:
            count += 1
            if count >= 3:
                state = ProviderCode.CIRCUIT_OPEN.value
                blocked_until = now + timedelta(seconds=60)
        values = dict(
            failure_count=count, blocked_until=blocked_until, state=state, observed_at=now
        )
        await session.execute(
            insert(ProviderRuntimeRow)
            .values(provider_id=entry.provider_id, **values)
            .on_conflict_do_update(index_elements=[ProviderRuntimeRow.provider_id], set_=values)
        )
        if entry.location is not Location.LOCAL and (
            failure is None or failure.code != ProviderCode.OUTCOME_UNKNOWN
        ):
            session.add(self._dispatch_record(entry, "dispatch_finished"))
        await session.commit()

    async def record(
        self,
        capability: Capability,
        attempt: ProviderAttempt,
        *,
        input_duration_ms: int | None = None,
        pricing: PricingSource | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        async with self.sessions() as session:
            session.add(
                ProviderAttemptRow(
                    id=uuid4(),
                    operation=self.operation,
                    run_id=self.run_id,
                    profile_id=self.profile.profile_id,
                    profile_version=self.profile.version,
                    capability=capability,
                    payload={
                        **attempt.model_dump(mode="json"),
                        "input_duration_ms": input_duration_ms,
                        "pricing": pricing.model_dump(mode="json", exclude={"stale"})
                        if pricing
                        else None,
                        "usage": usage,
                    },
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
