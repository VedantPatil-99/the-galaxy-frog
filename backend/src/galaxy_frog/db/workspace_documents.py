"""Private document metadata and immutable extraction snapshots."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from galaxy_frog.db.models import Base
from galaxy_frog.db.workspace import WorkspaceNotebookRow, WorkspaceRepository
from galaxy_frog.domain.workspace_documents import (
    DocumentFormat,
    DocumentNotFound,
    ParsedDocument,
    StoredDocumentSection,
    WorkspaceDocumentSection,
    WorkspaceDocumentSource,
)
from galaxy_frog.domain.workspace_text import WorkspaceTextBlock


class WorkspaceDocumentRow(Base):
    __tablename__ = "workspace_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    version_id: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    notebook_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_notebooks.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(160), nullable=False)
    format: Mapped[DocumentFormat] = mapped_column(String(8), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int]
    section_count: Mapped[int]
    pages_without_text: Mapped[int | None]
    sections: Mapped[list[JsonValue]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class DocumentImport:
    filename: str
    format: DocumentFormat
    digest: str
    size_bytes: int
    content: ParsedDocument


_DOCUMENT = WorkspaceDocumentRow
_SUMMARY = (
    _DOCUMENT.id.label("source_id"),
    _DOCUMENT.version_id,
    _DOCUMENT.filename,
    _DOCUMENT.format,
    _DOCUMENT.size_bytes,
    _DOCUMENT.section_count,
    _DOCUMENT.pages_without_text,
    _DOCUMENT.created_at,
)


def _summary(row: WorkspaceDocumentRow) -> WorkspaceDocumentSource:
    return WorkspaceDocumentSource(
        source_id=row.id,
        version_id=row.version_id,
        filename=row.filename,
        format=row.format,
        size_bytes=row.size_bytes,
        section_count=row.section_count,
        pages_without_text=row.pages_without_text,
        created_at=row.created_at,
    )


class WorkspaceDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self, owner_id: UUID, notebook_id: UUID, document: DocumentImport
    ) -> WorkspaceDocumentSource:
        await WorkspaceRepository(self.session).get_notebook(owner_id, notebook_id)
        sections = document.content.sections
        row = await self.session.scalar(
            insert(_DOCUMENT)
            .values(
                id=uuid4(),
                version_id=uuid4(),
                notebook_id=notebook_id,
                filename=document.filename,
                format=document.format,
                file_sha256=document.digest,
                size_bytes=document.size_bytes,
                section_count=len(sections),
                pages_without_text=sum(not section.spans for section in sections)
                if document.format == "pdf"
                else None,
                sections=document.content.model_dump(mode="json")["sections"],
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_workspace_document")
            .returning(_DOCUMENT)
        )
        if row is None:
            row = (
                await self.session.scalars(
                    select(_DOCUMENT).where(
                        _DOCUMENT.notebook_id == notebook_id,
                        _DOCUMENT.filename == document.filename,
                        _DOCUMENT.file_sha256 == document.digest,
                    )
                )
            ).one()
        return _summary(row)

    async def list_sources(
        self, owner_id: UUID, notebook_id: UUID, *, after: UUID | None, limit: int
    ) -> tuple[WorkspaceDocumentSource, ...]:
        await WorkspaceRepository(self.session).get_notebook(owner_id, notebook_id)
        query = select(*_SUMMARY).where(_DOCUMENT.notebook_id == notebook_id)
        if after is not None:
            query = query.where(_DOCUMENT.id > after)
        rows = await self.session.execute(query.order_by(_DOCUMENT.id).limit(limit))
        return tuple(WorkspaceDocumentSource.model_validate(row) for row in rows.mappings())

    @staticmethod
    def _owned_version(
        owner_id: UUID, notebook_id: UUID, source_id: UUID, version_id: UUID
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            WorkspaceNotebookRow.owner_id == owner_id,
            _DOCUMENT.notebook_id == notebook_id,
            _DOCUMENT.id == source_id,
            _DOCUMENT.version_id == version_id,
        )

    async def section(
        self,
        owner_id: UUID,
        notebook_id: UUID,
        source_id: UUID,
        version_id: UUID,
        section_number: int,
    ) -> WorkspaceDocumentSection:
        row = (
            await self.session.execute(
                select(_DOCUMENT.format, _DOCUMENT.sections[section_number - 1])
                .join(WorkspaceNotebookRow, WorkspaceNotebookRow.id == _DOCUMENT.notebook_id)
                .where(
                    *self._owned_version(owner_id, notebook_id, source_id, version_id),
                    _DOCUMENT.section_count >= section_number,
                )
            )
        ).one_or_none()
        if row is None:
            raise DocumentNotFound
        format, stored = row
        section = StoredDocumentSection.model_validate(stored)
        namespace = uuid5(version_id, f"section:{section_number}")
        return WorkspaceDocumentSection(
            source_id=source_id,
            version_id=version_id,
            section_number=section_number,
            page_number=section_number if format == "pdf" else None,
            text=section.text,
            blocks=tuple(
                WorkspaceTextBlock.from_span(namespace, section.text, start, end)
                for start, end in section.spans
            ),
        )

    async def pdf_digest(
        self,
        owner_id: UUID,
        notebook_id: UUID,
        source_id: UUID,
        version_id: UUID,
        page_number: int,
    ) -> str:
        digest = await self.session.scalar(
            select(_DOCUMENT.file_sha256)
            .join(WorkspaceNotebookRow, WorkspaceNotebookRow.id == _DOCUMENT.notebook_id)
            .where(
                *self._owned_version(owner_id, notebook_id, source_id, version_id),
                _DOCUMENT.format == "pdf",
                _DOCUMENT.section_count >= page_number,
            )
        )
        if digest is None:
            raise DocumentNotFound
        return digest
