"""Verify provider policy is enforced at the worker adapter boundaries."""

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from galaxy_frog.adapters.vision.llamaparse import LlamaParseOCRAdapter
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.db.ingestion_provider_routing import IngestionProviderRouting
from galaxy_frog.domain.ingestion.models import IngestionJob
from galaxy_frog.domain.providers import Capability
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.vision.errors import VisionProviderError
from galaxy_frog.domain.vision.models import OCRExtractionRequest
from galaxy_frog.entrypoints import worker_providers


@pytest.mark.parametrize(
    ("installed", "revision", "raises", "expected"),
    [
        ("digest", "digest", False, None),
        ("digest", "ollama", False, None),
        ("digest", "other", False, ProviderCode.INCOMPATIBLE),
        (None, "digest", False, ProviderCode.INCOMPATIBLE),
        ("digest", "digest", True, ProviderCode.UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_ingestion_embedding_obeys_the_declared_revision(
    monkeypatch: pytest.MonkeyPatch,
    installed: str | None,
    revision: str,
    raises: bool,
    expected: ProviderCode | None,
) -> None:
    job_id = uuid4()
    job = cast(IngestionJob, SimpleNamespace(job_id=job_id))
    model = "fixture-embedding"
    vectors = ((1.0, 0.0),)
    embed = AsyncMock(
        return_value=vectors,
        side_effect=EmbeddingProviderError("Embedding service unavailable.") if raises else None,
    )
    provider = SimpleNamespace(
        spec=EmbeddingCollectionSpec("ollama", model, revision, 2, "l2"), embed=embed
    )
    probe = AsyncMock(return_value={f"{model}:latest": installed} if installed else {})
    monkeypatch.setattr(worker_providers, "probe_ollama_models", probe)

    async def execute(
        run_id: UUID,
        capability: Capability,
        actions: dict[str, Callable[[], Awaitable[tuple[tuple[float, ...], ...]]]],
        *,
        timeout_seconds: float,
    ) -> tuple[tuple[float, ...], ...]:
        assert (run_id, capability, timeout_seconds) == (job_id, Capability.TEXT_EMBEDDING, 60)
        return await actions["ollama-embedding"]()

    origin = "http://127.0.0.1:11434"
    routing = cast(
        IngestionProviderRouting, SimpleNamespace(ollama_base_url=origin, execute=execute)
    )
    adapter = worker_providers.RoutedIngestionEmbedding(
        cast(TextEmbeddingProvider, provider), routing, job
    )
    if expected is None:
        assert await adapter.embed(("Caption",)) == vectors
    else:
        with pytest.raises(EmbeddingProviderError, match=expected.value):
            await adapter.embed(("Caption",))
    probe.assert_awaited_once_with(origin)
    if expected is ProviderCode.INCOMPATIBLE:
        embed.assert_not_awaited()
    else:
        embed.assert_awaited_once_with(("Caption",))


@pytest.mark.parametrize(
    ("cause", "expected"),
    [(OSError(), ProviderCode.UNAVAILABLE), (VisionProviderError(), ProviderCode.OUTCOME_UNKNOWN)],
)
@pytest.mark.asyncio
async def test_ocr_preserves_known_and_ambiguous_failure_categories(
    cause: Exception, expected: ProviderCode
) -> None:
    job_id = uuid4()
    request = cast(OCRExtractionRequest, SimpleNamespace(job_id=job_id))
    verify = AsyncMock()
    extract = AsyncMock(side_effect=cause)

    async def execute(
        run_id: UUID,
        capability: Capability,
        actions: dict[str, object],
        *,
        timeout_seconds: float,
        external: dict[str, Callable[[Callable[[], Awaitable[None]]], Awaitable[object]]],
    ) -> object:
        assert (run_id, capability, timeout_seconds) == (job_id, Capability.OCR, 180)
        assert not actions
        return await external["llamaparse-ocr"](verify)

    provider = cast(LlamaParseOCRAdapter, SimpleNamespace(spec=object(), extract=extract))
    routing = cast(IngestionProviderRouting, SimpleNamespace(execute=execute))
    with pytest.raises(ProviderFailure, match=expected.value):
        await worker_providers.RoutedOCR(provider, routing).extract(request)
    extract.assert_awaited_once_with(request, verify_consent=verify)
