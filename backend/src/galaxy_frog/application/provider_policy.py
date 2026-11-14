"""Shared provider eligibility, consent and ordered failover."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from enum import StrEnum
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from galaxy_frog.application.provider_registry import ProviderEntry
from galaxy_frog.domain.providers import Capability, Location, ProviderMode, ProviderProfile


class ProviderCode(StrEnum):
    NOT_SELECTED = "PROVIDER_NOT_SELECTED"
    SETUP_REQUIRED = "PROVIDER_SETUP_REQUIRED"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INCOMPATIBLE = "PROVIDER_INCOMPATIBLE"
    CONSENT_REQUIRED = "PROVIDER_CONSENT_REQUIRED"
    CIRCUIT_OPEN = "PROVIDER_CIRCUIT_OPEN"
    BUSY = "PROVIDER_BUSY"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    TIMEOUT = "PROVIDER_TIMEOUT"
    INVALID_OUTPUT = "PROVIDER_INVALID_OUTPUT"
    INPUT_LIMIT = "PROVIDER_INPUT_LIMIT"
    SAFETY_BLOCKED = "PROVIDER_SAFETY_BLOCKED"
    OUTCOME_UNKNOWN = "PROVIDER_OUTCOME_UNKNOWN"


class ProviderFailure(RuntimeError):
    def __init__(self, code: ProviderCode, *, retry_at: datetime | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.retry_at = retry_at


class ProviderAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    provider: str
    model: str
    revision: str | None
    device: str | None
    status: str
    reason: ProviderCode | None = None
    processing_ms: int | None = None
    retry_at: datetime | None = None
    requested_mode: ProviderMode | None = None
    requested_candidates: tuple[str, ...] = ()


type Admit = Callable[[ProviderEntry], AbstractAsyncContextManager[None]]
type RecordAttempt = Callable[[ProviderAttempt], Awaitable[None]]

_STOP = frozenset(
    {
        ProviderCode.SAFETY_BLOCKED,
        ProviderCode.OUTCOME_UNKNOWN,
    }
)


class ProviderRouting:
    def __init__(
        self,
        profile: ProviderProfile,
        catalog: tuple[ProviderEntry, ...],
        admit: Admit,
        record: RecordAttempt,
    ) -> None:
        self.profile = profile
        self._entries = {entry.provider_id: entry for entry in catalog}
        self._admit = admit
        self._record = record

    async def execute[T](
        self,
        capability: Capability,
        actions: dict[str, Callable[[], Awaitable[T]]],
        *,
        timeout_seconds: float,
        compatible: frozenset[str] | None = None,
        fallback: bool = True,
    ) -> T:
        chain = self.profile.candidates.get(capability, ())
        if not fallback:
            chain = chain[:1]
        failures: list[ProviderFailure] = []
        for provider_id in chain:
            entry = self._entries.get(provider_id)
            if entry is None:
                raise ProviderFailure(ProviderCode.INCOMPATIBLE)
            reason = self._excluded(capability, entry, actions, compatible)
            started = perf_counter()
            status = "skipped"
            try:
                if reason is not None:
                    raise ProviderFailure(reason)
                async with self._admit(entry):
                    status = "failed"
                    async with asyncio.timeout(timeout_seconds):
                        result = await actions[provider_id]()
            except TimeoutError:
                failure = ProviderFailure(
                    ProviderCode.TIMEOUT
                    if entry.location is Location.LOCAL
                    else ProviderCode.OUTCOME_UNKNOWN
                )
            except ProviderFailure as exc:
                failure = exc
            else:
                await self._save(
                    entry, "cached" if getattr(result, "cache_hit", False) else "succeeded", started
                )
                return result
            await self._save(entry, status, started, failure)
            if failure.code in _STOP:
                raise failure
            failures.append(failure)
        if failures:
            raise failures[-1]
        raise ProviderFailure(ProviderCode.NOT_SELECTED)

    def _excluded(
        self,
        capability: Capability,
        entry: ProviderEntry,
        actions: Mapping[str, object],
        compatible: frozenset[str] | None,
    ) -> ProviderCode | None:
        if (
            entry.capability is not capability
            or (compatible is not None and entry.provider_id not in compatible)
            or (
                (self.profile.mode is ProviderMode.LOCAL_ONLY or self.profile.evaluation)
                and entry.location is not Location.LOCAL
            )
            or (
                self.profile.mode is ProviderMode.CLOUD_PERMITTED and entry.location is Location.AWS
            )
        ):
            return ProviderCode.INCOMPATIBLE
        if entry.readiness != "configured" or entry.provider_id not in actions:
            return ProviderCode.SETUP_REQUIRED
        return None

    async def _save(
        self,
        entry: ProviderEntry,
        status: str,
        started: float,
        failure: ProviderFailure | None = None,
    ) -> None:
        await self._record(
            ProviderAttempt(
                provider_id=entry.provider_id,
                provider=entry.provider,
                model=entry.model,
                revision=entry.revision,
                device=entry.device,
                status=status,
                reason=failure.code if failure else None,
                processing_ms=round((perf_counter() - started) * 1000)
                if status != "skipped"
                else None,
                retry_at=failure.retry_at if failure else None,
                requested_mode=self.profile.mode,
                requested_candidates=self.profile.candidates.get(entry.capability, ()),
            )
        )
