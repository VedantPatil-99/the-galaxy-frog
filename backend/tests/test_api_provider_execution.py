"""Exercise safe error and audit contracts for API provider routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request

from galaxy_frog.api import provider_execution as execution
from galaxy_frog.api.app import create_app
from galaxy_frog.api.errors import ApiError
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import ProviderEntry
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.domain.generation.models import GenerationProviderError
from galaxy_frog.domain.providers import (
    Capability,
    ProfileRef,
    ProviderMode,
    ProviderOperation,
    ProviderProfile,
    ProviderSelection,
)
from galaxy_frog.domain.retrieval.reranking import RerankerError, RerankerErrorCode
from galaxy_frog.domain.vision.visual import VisualFailureCode, VisualProviderError


def provider_run(
    *, settings: Settings | None = None, admission_error: ProviderCode | None = None
) -> tuple[execution.ApiProviderRun, AsyncMock]:
    @asynccontextmanager
    async def admit(_entry: ProviderEntry) -> AsyncGenerator[None]:
        if admission_error is not None:
            raise ProviderFailure(admission_error)
        yield

    runtime = AsyncMock(spec=PostgresProviderRuntime)
    runtime.admit = admit
    run = execution.ApiProviderRun(
        settings or Settings(visual_embedding_enabled=True),
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={}),
        cast(PostgresProviderRuntime, runtime),
    )
    return run, runtime.record


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        ("replaced", ProviderCode.SETUP_REQUIRED),
        ("pinned", ProviderCode.UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_text_generation_audits_revision_checks_and_inference_failures(
    monkeypatch: pytest.MonkeyPatch, digest: str, expected: ProviderCode
) -> None:
    settings = Settings(generation_model_revision="pinned")
    run, record = provider_run(settings=settings)
    local = AsyncMock()
    local.generate.side_effect = GenerationProviderError("Private provider failure.")
    monkeypatch.setattr(
        execution,
        "probe_ollama_models",
        AsyncMock(return_value={settings.generation_model: digest}),
    )
    with pytest.raises(ProviderFailure, match=expected.value):
        await execution.RoutedTextGeneration(run, local).generate("What happened?", ())
    assert local.generate.await_count == int(digest == "pinned")
    call = record.await_args
    assert call is not None
    capability, attempt = call.args
    assert capability is Capability.GENERATION and attempt.reason is expected


@pytest.mark.parametrize("admission_error", [None, ProviderCode.BUSY])
@pytest.mark.asyncio
async def test_reranker_keeps_typed_runtime_and_admission_failures(
    admission_error: ProviderCode | None,
) -> None:
    run, record = provider_run(admission_error=admission_error)
    provider = AsyncMock()
    provider.rerank.side_effect = RerankerError(RerankerErrorCode.TIMEOUT)
    with pytest.raises(RerankerError) as failure:
        await execution.RoutedReranker(run, provider).rerank("query", ())
    assert failure.value.code is (
        RerankerErrorCode.BUSY if admission_error else RerankerErrorCode.TIMEOUT
    )
    record.assert_awaited_once()


@pytest.mark.parametrize("admission_error", [None, ProviderCode.BUSY])
@pytest.mark.asyncio
async def test_visual_embedding_keeps_typed_provider_failures(
    admission_error: ProviderCode | None,
) -> None:
    run, record = provider_run(admission_error=admission_error)
    provider = AsyncMock()
    provider.embed_frames.return_value = ()
    provider.embed_query.side_effect = VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
    adapter = execution.RoutedVisualQueryEmbedding(run, provider)
    assert await adapter.embed_frames(()) == ()
    with pytest.raises(VisualProviderError) as failure:
        await adapter.embed_query("query")
    assert failure.value.code is (
        VisualFailureCode.UNAVAILABLE if admission_error else VisualFailureCode.INVALID_OUTPUT
    )
    record.assert_awaited_once()


@pytest.mark.asyncio
async def test_selected_profile_requires_a_database() -> None:
    app = create_app(Settings())
    with pytest.raises(ApiError, match="requires PostgreSQL") as failure:
        await execution.api_provider_run(
            Request({"type": "http", "app": app}),
            object(),
            operation=ProviderOperation.QUESTION,
            selection=ProviderSelection(profile=ProfileRef(profile_id="local", version=1)),
            video_id=uuid4(),
            run_id=uuid4(),
        )
    assert failure.value.code == "DATABASE_UNAVAILABLE"
    assert failure.value.status_code == 503


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (ProviderCode.QUOTA_EXHAUSTED, 429, True),
        (ProviderCode.CONSENT_REQUIRED, 503, True),
        (ProviderCode.SAFETY_BLOCKED, 503, False),
    ],
)
def test_http_failure_keeps_correlation_and_safe_retry_guidance(
    code: ProviderCode, status: int, retryable: bool
) -> None:
    retry = datetime(2026, 11, 16, tzinfo=UTC)
    trace = uuid4()
    error = execution.provider_api_error(ProviderFailure(code, retry_at=retry), trace)
    assert (error.code, error.status_code, error.retryable) == (code.value, status, retryable)
    assert error.suggested_action
    assert error.details == {"trace_id": str(trace), "retry_at": retry.isoformat()}
