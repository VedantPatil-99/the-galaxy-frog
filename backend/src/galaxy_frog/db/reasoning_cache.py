"""Bounded, video-scoped durable reasoning results; no frame bytes or credentials."""

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base
from galaxy_frog.domain.retrieval.traces import TraceValue
from galaxy_frog.domain.vision.visual import (
    VisualFailureCode,
    VisualProviderError,
    VisualReasoningResult,
)

_RESULT = TypeAdapter(VisualReasoningResult)


class VisualReasoningCacheRow(Base):
    __tablename__ = "visual_reasoning_cache"
    __table_args__ = (
        CheckConstraint("version = 1", name="ck_visual_cache_version"),
        CheckConstraint("octet_length(payload::text) <= 65536", name="ck_visual_cache_size"),
    )

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, TraceValue]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostgresVisualReasoningCache:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, video_id: UUID, key: str) -> VisualReasoningResult | None:
        row = await self._session.scalar(
            select(VisualReasoningCacheRow).where(
                VisualReasoningCacheRow.video_id == video_id,
                VisualReasoningCacheRow.cache_key == key,
                VisualReasoningCacheRow.version == 1,
            )
        )
        try:
            return _RESULT.validate_python(row.payload) if row else None
        except ValueError as exc:
            raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT) from exc

    async def put(self, video_id: UUID, key: str, result: VisualReasoningResult) -> None:
        payload = cast(dict[str, TraceValue], _RESULT.dump_python(result, mode="json"))
        if len(json.dumps(payload, allow_nan=False).encode()) > 65536:
            raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
        await self._session.execute(
            insert(VisualReasoningCacheRow)
            .values(
                cache_key=key,
                video_id=video_id,
                version=1,
                payload=payload,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
        await self._session.commit()
