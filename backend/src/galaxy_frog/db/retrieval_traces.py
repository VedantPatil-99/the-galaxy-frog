"""Video-scoped persistence for bounded versioned retrieval traces."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import RetrievalTraceRow
from galaxy_frog.domain.retrieval.traces import RetrievalTrace


class PostgresRetrievalTraces:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, trace: RetrievalTrace) -> None:
        self._session.add(
            RetrievalTraceRow(
                id=trace.trace_id,
                video_id=trace.video_id,
                version=trace.version,
                created_at=trace.created_at,
                payload=trace.payload,
            )
        )
        await self._session.commit()

    async def get(self, video_id: UUID, trace_id: UUID) -> RetrievalTrace | None:
        row = await self._session.scalar(
            select(RetrievalTraceRow).where(
                RetrievalTraceRow.id == trace_id, RetrievalTraceRow.video_id == video_id
            )
        )
        if row is None:
            return None
        return RetrievalTrace(row.id, row.video_id, row.created_at, row.payload, row.version)
