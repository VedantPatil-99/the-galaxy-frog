"""Private schemas for authenticated service-to-service dispatch messages."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from galaxy_frog.domain.ingestion.models import IngestionJobStatus, IngestionStage


class InternalDispatchMessage(BaseModel):
    """Identifier-only QStash callback payload with no embedded job data."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    requested_action: Literal["process"]


class InternalDispatchAccepted(BaseModel):
    """Current durable projection acknowledged without mutating job output."""

    job_id: UUID
    accepted: Literal[True] = True
    status: IngestionJobStatus
    stage: IngestionStage
