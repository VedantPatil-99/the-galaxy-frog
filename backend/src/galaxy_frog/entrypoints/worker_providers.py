"""Adapt ingestion provider ports to job-scoped policy."""

from collections.abc import Awaitable, Callable
from functools import partial

import httpx

from galaxy_frog.adapters.transcription.cloudflare import CloudflareTranscription
from galaxy_frog.adapters.vision.llamaparse import LlamaParseOCRAdapter
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import probe_ollama_models
from galaxy_frog.config import Settings
from galaxy_frog.db.ingestion_provider_routing import IngestionProviderRouting
from galaxy_frog.domain.ingestion.models import IngestionJob
from galaxy_frog.domain.providers import Capability
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.transcription import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionRequest,
    TranscriptionResult,
)
from galaxy_frog.domain.vision.errors import VisionProviderError
from galaxy_frog.domain.vision.models import OCRExtractionRequest, OCRExtractionResult


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

        async def cloud(verify: Callable[[], Awaitable[None]]) -> TranscriptionResult:
            settings = self._settings
            assert settings.cloudflare_account_id and settings.cloudflare_api_token is not None
            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                return await CloudflareTranscription(
                    settings.cloudflare_account_id,
                    settings.cloudflare_api_token.get_secret_value(),
                    client,
                ).transcribe(request, verify_consent=verify)

        return await self._routing.execute(
            request.audio_job_id,
            Capability.TRANSCRIPTION,
            {"faster-whisper": local},
            timeout_seconds=120,
            input_duration_ms=request.audio_end_ms - request.audio_start_ms,
            external={"cloudflare-whisper": cloud},
        )


class RoutedIngestionEmbedding:
    def __init__(
        self, provider: TextEmbeddingProvider, routing: IngestionProviderRouting, job: IngestionJob
    ) -> None:
        self._provider, self._routing, self._job = provider, routing, job
        self.spec = provider.spec

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        async def embed() -> tuple[tuple[float, ...], ...]:
            models = await probe_ollama_models(self._routing.ollama_base_url)
            digest = models.get(self.spec.model) or models.get(f"{self.spec.model}:latest")
            if not digest or self.spec.revision not in {"ollama", digest}:
                raise ProviderFailure(ProviderCode.INCOMPATIBLE)
            try:
                return await self._provider.embed(texts)
            except EmbeddingProviderError as exc:
                raise ProviderFailure(ProviderCode.UNAVAILABLE) from exc

        try:
            return await self._routing.execute(
                self._job.job_id,
                Capability.TEXT_EMBEDDING,
                {"ollama-embedding": embed},
                timeout_seconds=60,
            )
        except ProviderFailure as exc:
            raise EmbeddingProviderError(exc.code.value) from exc


class RoutedOCR:
    def __init__(self, provider: LlamaParseOCRAdapter, routing: IngestionProviderRouting) -> None:
        self.spec = provider.spec
        self._provider, self._routing = provider, routing

    async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult:
        return await self._routing.execute(
            request.job_id,
            Capability.OCR,
            {},
            timeout_seconds=180,
            external={"llamaparse-ocr": partial(self._extract, request)},
        )

    async def _extract(
        self, request: OCRExtractionRequest, verify: Callable[[], Awaitable[None]]
    ) -> OCRExtractionResult:
        try:
            return await self._provider.extract(request, verify_consent=verify)
        except OSError as exc:
            raise ProviderFailure(ProviderCode.UNAVAILABLE) from exc
        except VisionProviderError as exc:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from exc
