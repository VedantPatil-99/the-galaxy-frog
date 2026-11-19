"""Read and append provider policy versions in PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.provider_registry import builtin_profiles
from galaxy_frog.db.models import ProviderProfileVersionRow
from galaxy_frog.domain.providers import ProfileRef, ProviderProfile, ProviderProfileVersion


class ProfileVersionConflict(Exception):
    """The caller edited a profile version that is no longer current."""


def _version(row: ProviderProfileVersionRow) -> ProviderProfileVersion:
    return ProviderProfileVersion(
        profile_id=row.profile_id,
        version=row.version,
        policy=ProviderProfile.model_validate(row.policy),
        created_at=row.created_at,
    )


class PostgresProviderProfiles:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(self) -> tuple[ProviderProfileVersion, ...]:
        latest = {
            key: ProviderProfileVersion(profile_id=key, version=1, policy=policy)
            for key, policy in builtin_profiles().items()
        }
        rows = await self._session.scalars(
            select(ProviderProfileVersionRow)
            .distinct(ProviderProfileVersionRow.profile_id)
            .order_by(
                ProviderProfileVersionRow.profile_id, ProviderProfileVersionRow.version.desc()
            )
        )
        latest.update((row.profile_id, _version(row)) for row in rows)
        return tuple(latest[key] for key in sorted(latest))

    async def get(self, ref: ProfileRef) -> ProviderProfileVersion | None:
        if ref.version == 1 and (policy := builtin_profiles().get(ref.profile_id)) is not None:
            return ProviderProfileVersion(profile_id=ref.profile_id, version=1, policy=policy)
        row = await self._session.get(ProviderProfileVersionRow, (ref.profile_id, ref.version))
        return _version(row) if row is not None else None

    async def append(
        self, profile_id: str, expected_version: int, policy: ProviderProfile
    ) -> ProviderProfileVersion:
        stored = await self._session.scalar(
            select(func.max(ProviderProfileVersionRow.version)).where(
                ProviderProfileVersionRow.profile_id == profile_id
            )
        )
        current = stored if stored is not None else int(profile_id in builtin_profiles())
        if expected_version != current:
            raise ProfileVersionConflict
        inserted = await self._session.scalar(
            insert(ProviderProfileVersionRow)
            .values(
                profile_id=profile_id,
                version=current + 1,
                policy=policy.model_dump(mode="json"),
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing()
            .returning(ProviderProfileVersionRow)
        )
        await self._session.commit()
        if inserted is None:
            raise ProfileVersionConflict
        return _version(inserted)
