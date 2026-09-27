"""Video-scoped PostgreSQL full-text retrieval with original cue provenance."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import RetrievalUnitCueRow, RetrievalUnitRow
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.transcripts.models import RetrievalUnit


class PostgresTranscriptLexicalSearch:
    """Retrieve transcript units without invoking ingestion or an AI provider.

    PostgreSQL web-search syntax treats quoted text as a phrase and accepts plain
    user text safely as a bound parameter. The simple dictionary preserves names
    across languages without assuming English stemming. Rank normalization 32
    maps the lexical score to [0, 1); it is not a calibrated probability and must
    not be averaged with dense scores.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self, video_id: UUID, query: str, *, limit: int = 30, window: TimeWindow | None = None
    ) -> tuple[RetrievedEvidence, ...]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 30:
            raise ValueError("limit must be between 1 and 30")
        terms = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank_cd(RetrievalUnitRow.search_vector, terms, 32).label("rank")
        statement = (
            select(RetrievalUnitRow, rank)
            .where(
                RetrievalUnitRow.video_id == video_id,
                RetrievalUnitRow.search_vector.bool_op("@@")(terms),
            )
            .order_by(rank.desc(), RetrievalUnitRow.id)
            .limit(limit)
        )
        if window is not None:
            if window.start_ms == window.end_ms:
                return ()
            statement = statement.where(RetrievalUnitRow.end_ms > window.start_ms)
            if window.end_ms is not None:
                statement = statement.where(RetrievalUnitRow.start_ms < window.end_ms)
        rows = (await self._session.execute(statement)).all()
        if not rows:
            return ()
        unit_ids = tuple(row[0].id for row in rows)
        links = (
            await self._session.scalars(
                select(RetrievalUnitCueRow)
                .where(RetrievalUnitCueRow.retrieval_unit_id.in_(unit_ids))
                .order_by(RetrievalUnitCueRow.retrieval_unit_id, RetrievalUnitCueRow.cue_order)
            )
        ).all()
        cue_ids: defaultdict[str, list[str]] = defaultdict(list)
        for link in links:
            cue_ids[link.retrieval_unit_id].append(link.cue_id)
        return tuple(
            RetrievedEvidence(
                video_id=video_id,
                unit=RetrievalUnit(
                    unit_id=row.id,
                    start_ms=row.start_ms,
                    end_ms=row.end_ms,
                    text=row.text,
                    cue_ids=tuple(cue_ids[row.id]),
                ),
                score=float(score),
            )
            for row, score in rows
        )
