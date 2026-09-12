"""Provider-independent dispatch contract for durable ingestion wake-up hints."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class DispatchAction(StrEnum):
    """Identifier-only actions accepted by ingestion dispatch adapters."""

    PROCESS = "process"


@dataclass(frozen=True, slots=True)
class DispatchMessage:
    """Minimal message that points a worker back to durable PostgreSQL state."""

    job_id: UUID
    requested_action: DispatchAction = DispatchAction.PROCESS


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    """Provider-neutral acknowledgement for an accepted wake-up hint."""

    dispatcher: str
    external_message_id: str | None = None


class JobDispatchError(RuntimeError):
    """Raised when an optional dispatcher cannot accept a wake-up hint."""


class DispatchSignatureError(RuntimeError):
    """Raised when an authenticated dispatch message cannot be verified."""


class JobDispatcher(Protocol):
    """Send identifier-only wake-up hints without owning durable job state."""

    async def dispatch(self, message: DispatchMessage) -> DispatchReceipt: ...


class DispatchSignatureVerifier(Protocol):
    """Verify a provider signature over the exact raw identifier message."""

    def verify(self, *, signature: str, body: str) -> None: ...
