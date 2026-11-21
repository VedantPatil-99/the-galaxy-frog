"""Immutable document sources and page- or block-scoped evidence."""

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from galaxy_frog.domain.workspace_text import WorkspaceTextBlock

DocumentFormat = Literal["pdf", "docx", "plain", "markdown"]
DOCUMENT_MAX_BYTES = 8 * 1024 * 1024
DOCUMENT_MAX_PAGES = 80
DOCUMENT_MAX_TEXT_BYTES = 512 * 1024
DOCUMENT_MAX_BLOCKS = 4096
_FORMATS: dict[str, DocumentFormat] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "plain",
    ".md": "markdown",
    ".markdown": "markdown",
}


class DocumentRejected(ValueError):
    """Unsupported, invalid, or oversized document."""


class DocumentNotFound(Exception):
    """The owning notebook cannot access this document section."""


class DocumentStorageUnavailable(Exception):
    """Private storage could not persist or read the original."""


def document_filename(value: str) -> tuple[str, DocumentFormat]:
    filename = value.strip()
    if (
        not filename
        or len(filename) > 160
        or any(char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise DocumentRejected("Use a filename of up to 160 characters without a path.")
    suffix = PurePosixPath(filename).suffix.lower()
    format = _FORMATS.get(suffix)
    if format is None:
        raise DocumentRejected("Choose a PDF, DOCX, TXT, or Markdown file.")
    return filename, format


class StoredDocumentSection(BaseModel):
    """Extraction coordinates are saved once, so later parsers cannot shift existing blocks."""

    model_config = ConfigDict(frozen=True)

    text: str
    spans: tuple[tuple[int, int], ...]


class ParsedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    sections: tuple[StoredDocumentSection, ...] = Field(min_length=1, max_length=DOCUMENT_MAX_PAGES)


class WorkspaceDocumentSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: UUID
    version_id: UUID
    filename: str
    format: DocumentFormat
    size_bytes: int
    section_count: int
    pages_without_text: int | None
    created_at: datetime


class WorkspaceDocumentSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: UUID
    version_id: UUID
    section_number: int
    page_number: int | None
    text: str
    blocks: tuple[WorkspaceTextBlock, ...]
