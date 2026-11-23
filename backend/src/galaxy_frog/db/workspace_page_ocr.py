"""One dispatch receipt per authorized document version and page."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import DateTime, ForeignKey, String, Text, Update, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base
from galaxy_frog.domain.workspace_documents import WorkspaceDocumentSection
from galaxy_frog.domain.workspace_ocr import (
    OCR_LEDGER_TIMEOUT_SECONDS,
    PageOCRStatus,
    StoredOCRStatus,
    WorkspacePageOCR,
)
from galaxy_frog.domain.workspace_text import PreparedWorkspaceText, WorkspaceTextBlock


class WorkspacePageOCRRow(Base):
    __tablename__ = "workspace_page_ocr"

    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_documents.version_id", ondelete="RESTRICT"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(primary_key=True)
    approved_by: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_accounts.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_job_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[StoredOCRStatus] = mapped_column(String(12))
    text: Mapped[str | None] = mapped_column(Text)
    spans: Mapped[list[list[int]] | None] = mapped_column(JSONB(none_as_null=True))


class WorkspacePageOCRRepository:
    """Accept a section returned by the owner-scoped PDF repository."""

    def __init__(self, session: AsyncSession, section: WorkspaceDocumentSection) -> None:
        self.session = session
        self.section = section

    async def read(self, *, available: bool) -> WorkspacePageOCR:
        section = self.section
        row = await self.session.get(
            WorkspacePageOCRRow,
            (section.version_id, section.section_number),
            populate_existing=True,
        )
        if row is None:
            status: PageOCRStatus = (
                "not_needed" if section.text else "ready" if available else "unavailable"
            )
            return WorkspacePageOCR(
                source_id=section.source_id,
                version_id=section.version_id,
                page_number=section.section_number,
                status=status,
            )
        stored_status: PageOCRStatus = row.status
        if (
            row.status == "running"
            and (datetime.now(UTC) - row.approved_at).total_seconds() >= OCR_LEDGER_TIMEOUT_SECONDS
        ):
            stored_status = "unknown"
        namespace = uuid5(row.version_id, f"ocr:{row.page_number}")
        return WorkspacePageOCR(
            source_id=section.source_id,
            version_id=row.version_id,
            page_number=row.page_number,
            status=stored_status,
            approved_at=row.approved_at,
            completed_at=row.completed_at,
            text=row.text,
            blocks=tuple(
                WorkspaceTextBlock.from_span(namespace, row.text or "", start, end)
                for start, end in row.spans or ()
            ),
        )

    async def reserve(self, owner_id: UUID) -> bool:
        row = await self.session.scalar(
            insert(WorkspacePageOCRRow)
            .values(
                version_id=self.section.version_id,
                page_number=self.section.section_number,
                approved_by=owner_id,
                approved_at=datetime.now(UTC),
                status="running",
            )
            .on_conflict_do_nothing(index_elements=["version_id", "page_number"])
            .returning(WorkspacePageOCRRow.version_id)
        )
        await self.session.commit()
        return row is not None

    def _running(self) -> Update:
        row = WorkspacePageOCRRow
        return update(row).where(
            row.version_id == self.section.version_id,
            row.page_number == self.section.section_number,
            row.status == "running",
        )

    async def record_job(self, job_id: str) -> None:
        await self.session.execute(self._running().values(provider_job_id=job_id))
        await self.session.commit()

    async def finish(
        self, status: StoredOCRStatus, evidence: PreparedWorkspaceText | None = None
    ) -> WorkspacePageOCR:
        await self.session.execute(
            self._running().values(
                status=status,
                completed_at=datetime.now(UTC),
                text=evidence.text if evidence else None,
                spans=[list(span) for span in evidence.spans] if evidence else None,
            )
        )
        await self.session.commit()
        return await self.read(available=True)
