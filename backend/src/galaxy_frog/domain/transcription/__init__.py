"""Provider-independent transcription contracts and provenance values."""

from galaxy_frog.domain.transcription.models import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)
from galaxy_frog.domain.transcription.ports import TranscriptionProvider

__all__ = [
    "TranscriptionComputeType",
    "TranscriptionCue",
    "TranscriptionDevice",
    "TranscriptionError",
    "TranscriptionErrorCode",
    "TranscriptionProvider",
    "TranscriptionProviderSpec",
    "TranscriptionRequest",
    "TranscriptionResult",
]
