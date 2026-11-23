"""Private, immutable text sources, scoped to notebooks owned by verified callers."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base
from galaxy_frog.db.workspace import WorkspaceNotebookRow, WorkspaceRepository
from galaxy_frog.domain.workspace_text import (
    WorkspaceTextBlock,
    WorkspaceTextDraft,
    WorkspaceTextFormat,
    WorkspaceTextNotFound,
    WorkspaceTextSource,
    WorkspaceTextVersion,
    prepare_workspace_text,
)


class WorkspaceTextSourceRow(Base):
    __tablename__ = "workspace_text_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    version_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    notebook_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_notebooks.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    format: Mapped[WorkspaceTextFormat] = mapped_column(String(8), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    spans: Mapped[list[list[int]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _version(row: WorkspaceTextSourceRow) -> WorkspaceTextVersion:
    return WorkspaceTextVersion(
        source_id=row.id,
        version_id=row.version_id,
        title=row.title,
        format=row.format,
        created_at=row.created_at,
        block_count=len(row.spans),
        text=row.text,
        blocks=tuple(
            WorkspaceTextBlock.from_span(row.version_id, row.text, start, end)
            for start, end in row.spans
        ),
    )


class WorkspaceTextRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self, owner_id: UUID, notebook_id: UUID, draft: WorkspaceTextDraft
    ) -> WorkspaceTextVersion:
        await WorkspaceRepository(self.session).get_notebook(owner_id, notebook_id)
        prepared = prepare_workspace_text(draft.text)
        row = await self.session.scalar(
            insert(WorkspaceTextSourceRow)
            .values(
                id=uuid4(),
                version_id=uuid4(),
                notebook_id=notebook_id,
                title=draft.title,
                format=draft.format,
                text=prepared.text,
                content_sha256=prepared.content_sha256,
                spans=[[start, end] for start, end in prepared.spans],
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_workspace_text_paste")
            .returning(WorkspaceTextSourceRow)
        )
        if row is None:
            row = (
                await self.session.scalars(
                    select(WorkspaceTextSourceRow).where(
                        WorkspaceTextSourceRow.notebook_id == notebook_id,
                        WorkspaceTextSourceRow.title == draft.title,
                        WorkspaceTextSourceRow.format == draft.format,
                        WorkspaceTextSourceRow.content_sha256 == prepared.content_sha256,
                    )
                )
            ).one()
        return _version(row)

    async def list_sources(
        self, owner_id: UUID, notebook_id: UUID, *, after: UUID | None, limit: int
    ) -> tuple[WorkspaceTextSource, ...]:
        await WorkspaceRepository(self.session).get_notebook(owner_id, notebook_id)
        row = WorkspaceTextSourceRow
        query = select(
            row.id.label("source_id"),
            row.version_id,
            row.title,
            row.format,
            row.created_at,
            func.jsonb_array_length(row.spans).label("block_count"),
        ).where(row.notebook_id == notebook_id)
        if after is not None:
            query = query.where(row.id > after)
        summaries = await self.session.execute(query.order_by(row.id).limit(limit))
        return tuple(
            WorkspaceTextSource.model_validate(summary) for summary in summaries.mappings()
        )

    async def get_version(
        self,
        owner_id: UUID,
        notebook_id: UUID,
        source_id: UUID,
        version_id: UUID,
        *,
        lock: bool = False,
    ) -> WorkspaceTextVersion:
        query = (
            select(WorkspaceTextSourceRow)
            .join(
                WorkspaceNotebookRow, WorkspaceNotebookRow.id == WorkspaceTextSourceRow.notebook_id
            )
            .where(
                WorkspaceNotebookRow.owner_id == owner_id,
                WorkspaceTextSourceRow.notebook_id == notebook_id,
                WorkspaceTextSourceRow.id == source_id,
                WorkspaceTextSourceRow.version_id == version_id,
            )
        )
        row = await self.session.scalar(query.with_for_update(read=True) if lock else query)
        if row is None:
            raise WorkspaceTextNotFound
        return _version(row)
