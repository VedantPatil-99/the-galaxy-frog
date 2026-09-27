"""Versioned, size-bounded durable retrieval trace records and persistence port."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

type TraceValue = str | int | float | bool | list[TraceValue] | dict[str, TraceValue] | None

MAX_TRACE_BYTES = 262144
TRACE_VERSION = 1


class TraceLimitExceeded(ValueError):
    """Trace lineage exceeded the supported storage budget; never silently truncate it."""


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    trace_id: UUID
    video_id: UUID
    created_at: datetime
    payload: dict[str, TraceValue]
    version: int = TRACE_VERSION

    def __post_init__(self) -> None:
        if self.version != TRACE_VERSION:
            raise ValueError("unsupported retrieval trace version")
        if self.created_at.tzinfo is None:
            raise ValueError("trace timestamp must include a timezone")
        encoded = json.dumps(self.payload, allow_nan=False).encode()
        if len(encoded) > MAX_TRACE_BYTES:
            raise TraceLimitExceeded("Retrieval trace exceeds the 256 KiB record limit.")


class RetrievalTraceRepository(Protocol):
    async def save(self, trace: RetrievalTrace) -> None: ...

    async def get(self, video_id: UUID, trace_id: UUID) -> RetrievalTrace | None: ...
