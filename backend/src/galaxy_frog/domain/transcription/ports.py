"""Application-facing transcription provider protocol."""

from typing import Protocol, runtime_checkable

from galaxy_frog.domain.transcription.models import (
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)


@runtime_checkable
class TranscriptionProvider(Protocol):
    """Transcribe normalized audio in one declared provider/model environment."""

    spec: TranscriptionProviderSpec

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...
