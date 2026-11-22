"""Scope chat persistence to a notebook already authorized by the API."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base
from galaxy_frog.domain.workspace_chat import (
    WorkspaceChat,
    WorkspaceChatCreate,
    WorkspaceChatNotFound,
    WorkspaceChatSummary,
    WorkspaceDraftConflict,
    WorkspacePromptDraft,
)


class WorkspaceChatRow(Base):
    __tablename__ = "workspace_chats"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_workspace_chat_title"),
        CheckConstraint("draft_revision >= 0", name="ck_workspace_chat_draft_revision"),
        Index("ix_workspace_chats_notebook_updated", "notebook_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    notebook_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_notebooks.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(120))
    draft_text: Mapped[str] = mapped_column(String(1000))
    draft_revision: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _summary(row: WorkspaceChatRow) -> WorkspaceChatSummary:
    return WorkspaceChatSummary(
        chat_id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
    )


def _draft(row: WorkspaceChatRow) -> WorkspacePromptDraft:
    return WorkspacePromptDraft(text=row.draft_text, revision=row.draft_revision)


class WorkspaceChatRepository:
    def __init__(self, session: AsyncSession, notebook_id: UUID) -> None:
        self.session = session
        self.notebook_id = notebook_id

    async def _get_row(self, chat_id: UUID) -> WorkspaceChatRow:
        row = await self.session.scalar(
            select(WorkspaceChatRow).where(
                WorkspaceChatRow.id == chat_id, WorkspaceChatRow.notebook_id == self.notebook_id
            )
        )
        if row is None:
            raise WorkspaceChatNotFound
        return row

    async def get(self, chat_id: UUID) -> WorkspaceChat:
        row = await self._get_row(chat_id)
        return WorkspaceChat(**_summary(row).model_dump(), draft=_draft(row))

    async def list(
        self, after: UUID | None, limit: int
    ) -> tuple[tuple[WorkspaceChatSummary, ...], UUID | None]:
        row = WorkspaceChatRow
        query = select(row).where(row.notebook_id == self.notebook_id)
        if after is not None:
            cursor = await self._get_row(after)
            query = query.where(tuple_(row.updated_at, row.id) < (cursor.updated_at, cursor.id))
        rows = (
            await self.session.scalars(
                query.order_by(row.updated_at.desc(), row.id.desc()).limit(limit + 1)
            )
        ).all()
        return tuple(_summary(item) for item in rows[:limit]), rows[limit - 1].id if len(
            rows
        ) > limit else None

    async def create(self, chat: WorkspaceChatCreate) -> WorkspaceChat:
        now = datetime.now(UTC)
        await self.session.execute(
            insert(WorkspaceChatRow)
            .values(
                id=chat.chat_id,
                notebook_id=self.notebook_id,
                title=chat.title,
                draft_text="",
                draft_revision=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[WorkspaceChatRow.id])
        )
        return await self.get(chat.chat_id)

    async def rename(self, chat_id: UUID, title: str) -> WorkspaceChatSummary:
        row = WorkspaceChatRow
        updated = await self.session.scalar(
            update(row)
            .where(row.id == chat_id, row.notebook_id == self.notebook_id)
            .values(title=title, updated_at=datetime.now(UTC))
            .returning(row)
        )
        if updated is None:
            raise WorkspaceChatNotFound
        return _summary(updated)

    async def save_draft(self, chat_id: UUID, draft: WorkspacePromptDraft) -> WorkspacePromptDraft:
        row = WorkspaceChatRow
        updated = await self.session.scalar(
            update(row)
            .where(
                row.id == chat_id,
                row.notebook_id == self.notebook_id,
                row.draft_revision == draft.revision,
                row.draft_text != draft.text,
            )
            .values(draft_text=draft.text, draft_revision=row.draft_revision + 1)
            .returning(row)
        )
        if updated is not None:
            return _draft(updated)
        current = await self._get_row(chat_id)
        if current.draft_text == draft.text:
            return _draft(current)
        raise WorkspaceDraftConflict
