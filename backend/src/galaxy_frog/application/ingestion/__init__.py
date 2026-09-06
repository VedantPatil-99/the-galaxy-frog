"""Durable ingestion application contracts."""

from galaxy_frog.application.ingestion.caption_stages import caption_ingestion_handlers
from galaxy_frog.application.ingestion.create_job import (
    CreateIngestionJob,
    CreateIngestionJobResult,
    ingestion_input_fingerprint,
)
from galaxy_frog.application.ingestion.dispatch import (
    DispatchAction,
    DispatchMessage,
    DispatchReceipt,
    JobDispatcher,
)
from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.application.ingestion.runner import (
    IngestionJobRunner,
    IngestionStageError,
    IngestionStageHandler,
    StageContext,
    StageResult,
)
from galaxy_frog.application.ingestion.worker import ClaimedJobRunner, IngestionWorker

__all__ = [
    "ClaimedJobRunner",
    "CreateIngestionJob",
    "CreateIngestionJobResult",
    "DispatchAction",
    "DispatchMessage",
    "DispatchReceipt",
    "IngestionJobRunner",
    "IngestionRepository",
    "IngestionStageError",
    "IngestionStageHandler",
    "IngestionWorker",
    "JobDispatcher",
    "StageContext",
    "StageResult",
    "caption_ingestion_handlers",
    "ingestion_input_fingerprint",
]
