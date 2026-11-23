"""Persisted OCR evidence for a single immutable document page."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from galaxy_frog.domain.workspace_text import WorkspaceTextBlock

StoredOCRStatus = Literal["running", "succeeded", "no_text", "failed", "unknown"]
PageOCRStatus = Literal["unavailable", "not_needed", "ready"] | StoredOCRStatus
OCR_TIMEOUT_SECONDS = 60
OCR_LEDGER_TIMEOUT_SECONDS = 75
OCR_PROVIDER = "llamaparse"
OCR_PROFILE = "parse-v2-fast"


class WorkspacePageOCRConsent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allow_external_page_image: Literal[True]

    @field_validator("allow_external_page_image", mode="before")
    @classmethod
    def require_explicit_approval(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("Explicit approval is required to send the page image.")
        return True


class WorkspacePageOCR(BaseModel):
    source_id: UUID
    version_id: UUID
    page_number: int
    status: PageOCRStatus
    provider: Literal["llamaparse"] = OCR_PROVIDER
    profile: Literal["parse-v2-fast"] = OCR_PROFILE
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    text: str | None = None
    blocks: tuple[WorkspaceTextBlock, ...] = ()


@dataclass(frozen=True)
class PageOCRResult:
    text: str


class PageOCRFailed(Exception):
    """Provider reported a terminal failure."""


class PageOCROutcomeUnknown(Exception):
    """The provider may have processed the request."""


class WorkspacePageOCRProvider(Protocol):
    async def extract(
        self, png: bytes, *, record_job: Callable[[str], Awaitable[None]]
    ) -> PageOCRResult: ...
