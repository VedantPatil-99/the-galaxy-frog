"""Immutable pasted text and version-scoped document citation coordinates."""

import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from galaxy_frog.domain.workspace import NotebookTitle

WorkspaceTextFormat = Literal["plain", "markdown"]
MAX_TEXT_BYTES = 128 * 1024
MAX_TEXT_CHARACTERS = 65_536
MAX_TEXT_BLOCKS = 2_048
_BLOCK_CHARACTERS = 4_096
_PARAGRAPH_BREAK = re.compile(r"(\n(?:[^\S\n]*\n)+)")


class WorkspaceTextDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: NotebookTitle = Field(pattern=r"^[^\x00]+$")
    format: WorkspaceTextFormat
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARACTERS)


class WorkspaceTextSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: UUID
    version_id: UUID
    title: NotebookTitle
    format: WorkspaceTextFormat
    created_at: datetime
    block_count: int


class WorkspaceTextBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    block_id: UUID
    start_char: int
    end_char: int
    text: str

    @classmethod
    def from_span(
        cls, version_id: UUID, text: str, start_char: int, end_char: int
    ) -> WorkspaceTextBlock:
        return cls(
            block_id=workspace_block_id(version_id, start_char, end_char),
            start_char=start_char,
            end_char=end_char,
            text=text[start_char:end_char],
        )


def workspace_block_id(namespace: UUID, start_char: int, end_char: int) -> UUID:
    return uuid5(namespace, f"{start_char}:{end_char}")


class WorkspaceTextVersion(WorkspaceTextSource):
    text: str
    blocks: tuple[WorkspaceTextBlock, ...]


class WorkspaceTextRejected(ValueError):
    """A paste cannot be represented within the source limits."""


class WorkspaceTextNotFound(Exception):
    """A source version is missing or inaccessible."""


@dataclass(frozen=True)
class PreparedWorkspaceText:
    text: str
    content_sha256: str
    spans: tuple[tuple[int, int], ...]


def prepare_workspace_text(text: str) -> PreparedWorkspaceText:
    """Keep the original content apart from normalizing line endings.

    Every block points into the stored Unicode string with half-open character offsets.
    Blank lines are separators and long paragraphs are divided into bounded excerpts.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise WorkspaceTextRejected("Paste some text to add this source.")
    if "\0" in normalized:
        raise WorkspaceTextRejected("The pasted text contains a null character.")
    try:
        contents = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise WorkspaceTextRejected("Enter valid Unicode text.") from None
    if len(normalized) > MAX_TEXT_CHARACTERS or len(contents) > MAX_TEXT_BYTES:
        raise WorkspaceTextRejected("This text exceeds the paste size limit.")

    spans: list[tuple[int, int]] = []
    offset = 0
    for paragraph in _PARAGRAPH_BREAK.split(normalized):
        paragraph_end = offset + len(paragraph)
        if paragraph.strip():
            for start in range(offset, paragraph_end, _BLOCK_CHARACTERS):
                end = min(start + _BLOCK_CHARACTERS, paragraph_end)
                if normalized[start:end].strip():
                    spans.append((start, end))
        offset = paragraph_end
    if len(spans) > MAX_TEXT_BLOCKS:
        raise WorkspaceTextRejected("This text has too many separate blocks.")
    return PreparedWorkspaceText(normalized, sha256(contents).hexdigest(), tuple(spans))
