"""Adapt ingestion provider ports to job-scoped policy."""

from functools import partial

import httpx

from galaxy_frog.adapters.transcription.cloudflare import CloudflareTranscription
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.config import Settings
from galaxy_frog.db.ingestion_provider_routing import IngestionProviderRouting
from galaxy_frog.domain.providers import Capability
from galaxy_frog.domain.transcription import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionRequest,
    TranscriptionResult,
)
from galaxy_frog.domain.vision.errors import VisionProviderError
from galaxy_frog.domain.vision.models import OCRExtractionRequest, OCRExtractionResult
from galaxy_frog.domain.vision.ports import OCRProvider


class RoutedTranscription:
    def __init__(
        self, local: TranscriptionProvider, settings: Settings, routing: IngestionProviderRouting
    ) -> None:
        self.spec = local.spec
        self._local, self._settings, self._routing = local, settings, routing

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        async def local() -> TranscriptionResult:
            try:
                return await self._local.transcribe(request)
            except TranscriptionError as exc:
                raise ProviderFailure(ProviderCode.UNAVAILABLE) from exc

        async def cloud() -> TranscriptionResult:
            settings = self._settings
            assert settings.cloudflare_account_id and settings.cloudflare_api_token is not None
            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                return await CloudflareTranscription(
                    settings.cloudflare_account_id,
                    settings.cloudflare_api_token.get_secret_value(),
                    client,
                ).transcribe(request)

        return await self._routing.execute(
            request.audio_job_id,
            Capability.TRANSCRIPTION,
            {"faster-whisper": local, "cloudflare-whisper": cloud},
            timeout_seconds=120,
            input_duration_ms=request.audio_end_ms - request.audio_start_ms,
        )


class RoutedOCR:
    def __init__(self, provider: OCRProvider, routing: IngestionProviderRouting) -> None:
        self.spec = provider.spec
        self._provider, self._routing = provider, routing

    async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult:
        return await self._routing.execute(
            request.job_id,
            Capability.OCR,
            {"llamaparse-ocr": partial(self._extract, request)},
            timeout_seconds=180,
        )

    async def _extract(self, request: OCRExtractionRequest) -> OCRExtractionResult:
        try:
            return await self._provider.extract(request)
        except OSError as exc:
            raise ProviderFailure(ProviderCode.UNAVAILABLE) from exc
        except VisionProviderError as exc:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from exc
