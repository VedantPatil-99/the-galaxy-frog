"""Notebook authorization for migrated video sources."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base, VideoRow
from galaxy_frog.db.workspace import WorkspaceNotebookRow, WorkspaceRepository


class WorkspaceLegacySourceRow(Base):
    __tablename__ = "workspace_legacy_sources"
    __table_args__ = (
        Index("ix_workspace_legacy_sources_notebook_video", "notebook_id", "video_id"),
    )

    video_id: Mapped[UUID] = mapped_column(
        ForeignKey("videos.id", ondelete="RESTRICT"), primary_key=True
    )
    notebook_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_notebooks.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LegacySourceAssignmentError(Exception):
    """A selected video is missing or already assigned to another notebook."""


class WorkspaceSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assign_legacy_videos(
        self, owner_id: UUID, notebook_id: UUID, video_ids: Sequence[UUID]
    ) -> int:
        """Assign the explicit batch atomically; an existing assignment cannot be moved."""
        ordered = sorted(set(video_ids))
        if not 1 <= len(ordered) <= 100 or len(ordered) != len(video_ids):
            raise LegacySourceAssignmentError("Select between 1 and 100 distinct video IDs.")
        await WorkspaceRepository(self.session).get_notebook(owner_id, notebook_id)

        async with self.session.begin_nested():
            existing = await self.session.scalars(
                select(VideoRow.id)
                .where(VideoRow.id.in_(ordered))
                .order_by(VideoRow.id)
                .with_for_update(read=True, key_share=True)
            )
            if existing.all() != ordered:
                raise LegacySourceAssignmentError("One or more selected videos do not exist.")

            assigned_at = datetime.now(UTC)
            inserted = await self.session.scalars(
                insert(WorkspaceLegacySourceRow)
                .values(
                    [
                        dict(video_id=video_id, notebook_id=notebook_id, assigned_at=assigned_at)
                        for video_id in ordered
                    ]
                )
                .on_conflict_do_nothing(index_elements=[WorkspaceLegacySourceRow.video_id])
                .returning(WorkspaceLegacySourceRow.video_id)
            )
            count = len(inserted.all())
            conflicting_id = await self.session.scalar(
                select(WorkspaceLegacySourceRow.video_id)
                .where(
                    WorkspaceLegacySourceRow.video_id.in_(ordered),
                    WorkspaceLegacySourceRow.notebook_id != notebook_id,
                )
                .limit(1)
            )
            if conflicting_id is not None:
                raise LegacySourceAssignmentError(
                    "A selected video is already assigned to another notebook."
                )
        return count

    @staticmethod
    def _owned_videos(owner_id: UUID, notebook_id: UUID) -> Select[tuple[VideoRow]]:
        return (
            select(VideoRow)
            .join(WorkspaceLegacySourceRow, WorkspaceLegacySourceRow.video_id == VideoRow.id)
            .join(
                WorkspaceNotebookRow,
                WorkspaceNotebookRow.id == WorkspaceLegacySourceRow.notebook_id,
            )
            .where(
                WorkspaceNotebookRow.id == notebook_id, WorkspaceNotebookRow.owner_id == owner_id
            )
        )

    async def list_videos(
        self, owner_id: UUID, notebook_id: UUID, *, after: UUID | None, limit: int
    ) -> Sequence[VideoRow]:
        await WorkspaceRepository(self.session).get_notebook(owner_id, notebook_id)
        query = self._owned_videos(owner_id, notebook_id)
        if after is not None:
            query = query.where(VideoRow.id > after)
        return (await self.session.scalars(query.order_by(VideoRow.id).limit(limit))).all()

    async def get_video(self, owner_id: UUID, notebook_id: UUID, video_id: UUID) -> VideoRow | None:
        return await self.session.scalar(
            self._owned_videos(owner_id, notebook_id).where(VideoRow.id == video_id)
        )
