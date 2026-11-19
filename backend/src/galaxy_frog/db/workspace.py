"""Owner-scoped notebook persistence; callers own transaction commit/rollback."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base
from galaxy_frog.domain.workspace import Notebook, NotebookDraft, NotebookNotFound, WorkspaceAccount


class WorkspaceAccountRow(Base):
    __tablename__ = "workspace_accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceNotebookRow(Base):
    __tablename__ = "workspace_notebooks"
    __table_args__ = (
        CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 120", name="ck_workspace_notebook_title"
        ),
        Index("ix_workspace_notebooks_owner_updated", "owner_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _notebook(row: WorkspaceNotebookRow) -> Notebook:
    return Notebook(
        notebook_id=row.id,
        owner_id=row.owner_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class WorkspaceRepository:
    """Accept owner IDs only from a verified server identity, never from request bodies.

    No HTTP routes expose this repository until authentication and the legacy-resource
    authorization fence are implemented. Registering an account never claims videos,
    provider records, Study artifacts, or any other pre-existing application data.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register_account(self, account_id: UUID) -> WorkspaceAccount:
        """Register a verified subject idempotently without updating its creation time."""

        await self.session.execute(
            insert(WorkspaceAccountRow)
            .values(id=account_id, created_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=[WorkspaceAccountRow.id])
        )
        row = (
            await self.session.execute(
                select(WorkspaceAccountRow).where(WorkspaceAccountRow.id == account_id)
            )
        ).scalar_one()
        return WorkspaceAccount(account_id=row.id, created_at=row.created_at)

    async def create_notebook(self, owner_id: UUID, draft: NotebookDraft) -> Notebook:
        """Create for an already registered account; the database enforces that relation."""

        now = datetime.now(UTC)
        row = WorkspaceNotebookRow(
            id=uuid4(), owner_id=owner_id, title=draft.title, created_at=now, updated_at=now
        )
        self.session.add(row)
        await self.session.flush()
        return _notebook(row)

    async def list_notebooks(self, owner_id: UUID, *, limit: int = 50) -> tuple[Notebook, ...]:
        """Bound every listing and order ties deterministically."""

        if not 1 <= limit <= 100:
            raise ValueError("Notebook list limit must be between 1 and 100.")
        rows = (
            await self.session.execute(
                select(WorkspaceNotebookRow)
                .where(WorkspaceNotebookRow.owner_id == owner_id)
                .order_by(WorkspaceNotebookRow.updated_at.desc(), WorkspaceNotebookRow.id.desc())
                .limit(limit)
            )
        ).scalars()
        return tuple(_notebook(row) for row in rows)

    async def get_notebook(self, owner_id: UUID, notebook_id: UUID) -> Notebook:
        row = (
            await self.session.execute(
                select(WorkspaceNotebookRow).where(
                    WorkspaceNotebookRow.id == notebook_id,
                    WorkspaceNotebookRow.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotebookNotFound
        return _notebook(row)

    async def rename_notebook(
        self, owner_id: UUID, notebook_id: UUID, draft: NotebookDraft
    ) -> Notebook:
        row = (
            await self.session.execute(
                update(WorkspaceNotebookRow)
                .where(
                    WorkspaceNotebookRow.id == notebook_id,
                    WorkspaceNotebookRow.owner_id == owner_id,
                )
                .values(title=draft.title, updated_at=datetime.now(UTC))
                .returning(WorkspaceNotebookRow)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotebookNotFound
        return _notebook(row)
