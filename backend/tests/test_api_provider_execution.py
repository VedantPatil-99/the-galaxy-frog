"""Exercise safe error and audit contracts for API provider routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request
from pydantic import SecretStr

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


@pytest.mark.parametrize("moment", [False, True])
@pytest.mark.asyncio
async def test_selected_gemini_uses_interactive_key_and_dispatch_admission(
    monkeypatch: pytest.MonkeyPatch, moment: bool
) -> None:
    settings = Settings(
        gemini_api_key=SecretStr("synthetic-interactive-key"),
        gemini_project_id="test-interactive",
        gemini_generation_enabled=True,
        gemini_generation_model="gemini-3.5-flash-lite",
        gemini_study_api_key=SecretStr("separate-study-key"),
        gemini_study_project_id="test-study",
    )
    run, record = provider_run(settings=settings)
    run.policy = ProviderProfile(
        mode=ProviderMode.MANUAL, candidates={Capability.GENERATION: ("gemini-text",)}
    )
    original = execution.GeminiJSON

    def factory(key: str, model: str, **kwargs: object) -> object:
        assert key == "synthetic-interactive-key" and model == settings.gemini_generation_model
        provider = original(key, model)

        async def generate(_prompt: str, _schema: dict[str, object]) -> object:
            from galaxy_frog.adapters.generation.gemini import BeforeDispatch

            await cast(BeforeDispatch, kwargs["before_dispatch"])(123)
            provider.usage = {"input_tokens": 45, "output_tokens": 12}
            return {"answer": "Grounded test answer", "confidence": "low", "citations": []}

        monkeypatch.setattr(provider, "generate", generate)
        return provider

    monkeypatch.setattr(execution, "GeminiJSON", factory)
    local = AsyncMock()
    generator = execution.RoutedTextGeneration(run, local)
    answer = await (
        generator.generate_moment("Explain", ()) if moment else generator.generate("Ask", ())
    )
    assert answer.answer == "Grounded test answer"
    local.generate.assert_not_awaited()
    local.generate_moment.assert_not_awaited()
    call = record.await_args
    assert call is not None and call.args[1].provider_id == "gemini-text"
    assert call.kwargs["usage"] == {"input_tokens": 45, "output_tokens": 12}
    runtime = cast(AsyncMock, run.runtime)
    runtime.verify_consent.assert_awaited_once()
    assert runtime.verify_consent.await_args.kwargs == {"dispatch": True, "input_tokens": 123}


@pytest.mark.asyncio
async def test_missing_optional_capability_has_no_implicit_cloud_default() -> None:
    run, record = provider_run()
    with pytest.raises(ProviderFailure, match=ProviderCode.NOT_SELECTED.value):
        await run.router(Capability.OCR).execute(Capability.OCR, {}, timeout_seconds=1)
    record.assert_not_awaited()


@pytest.mark.parametrize("evaluation", [False, True])
@pytest.mark.asyncio
async def test_missing_profile_or_evaluation_collections_fail_closed(
    monkeypatch: pytest.MonkeyPatch, evaluation: bool
) -> None:
    from galaxy_frog.application.provider_registry import builtin_profiles
    from galaxy_frog.domain.providers import ProviderProfileVersion

    app = create_app(Settings())
    app.state.database_engine = object()
    repository = AsyncMock()
    repository.session.get.return_value = None
    profile = ProfileRef(profile_id="test-profile", version=1)
    version = ProviderProfileVersion(
        **profile.model_dump(), policy=builtin_profiles()["evaluation"]
    )
    monkeypatch.setattr(
        execution.PostgresProviderProfiles,
        "get",
        AsyncMock(return_value=version if evaluation else None),
    )
    monkeypatch.setattr(execution, "probe_ollama_models", AsyncMock(return_value={}))
    with pytest.raises(ApiError) as caught:
        await execution.api_provider_run(
            Request({"type": "http", "app": app}),
            repository,
            operation=ProviderOperation.QUESTION,
            selection=ProviderSelection(profile=profile),
            video_id=uuid4(),
            run_id=uuid4(),
        )
    assert caught.value.code == (
        "PROVIDER_EVALUATION_UNVERIFIABLE" if evaluation else "PROVIDER_PROFILE_NOT_FOUND"
    )


@pytest.mark.asyncio
async def test_incompatible_query_embedding_revision_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError
    from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec

    run, record = provider_run()
    provider = AsyncMock()
    provider.spec = EmbeddingCollectionSpec("ollama", "fixture", "pinned", 1024, "l2")
    monkeypatch.setattr(execution, "probe_ollama_models", AsyncMock(return_value={}))
    with pytest.raises(EmbeddingProviderError, match=ProviderCode.INCOMPATIBLE.value):
        await execution.RoutedQueryEmbedding(run, provider).embed(("query",))
    provider.embed.assert_not_awaited()
    record.assert_awaited_once()


@pytest.mark.asyncio
async def test_traced_quota_failure_preserves_trace_and_http_guidance() -> None:
    from types import SimpleNamespace

    from galaxy_frog.api import retrieval
    from galaxy_frog.application.retrieval.search import TracedRetrievalFailure
    from galaxy_frog.domain.retrieval.traces import RetrievalTrace

    trace_id = uuid4()
    repository, service = AsyncMock(), AsyncMock()
    service.execute.side_effect = TracedRetrievalFailure(
        cast(RetrievalTrace, SimpleNamespace(trace_id=trace_id)),
        ProviderFailure(ProviderCode.RATE_LIMITED),
        "RATE_LIMITED",
    )
    with pytest.raises(ApiError) as caught:
        await retrieval.run_search(
            uuid4(), "query", retrieval.RetrievalOptions(), repository, service
        )
    assert caught.value.status_code == 429 and caught.value.details == {
        "trace_id": str(trace_id),
        "retry_at": None,
    }


@pytest.mark.asyncio
async def test_import_without_grant_does_not_return_a_receipt() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from galaxy_frog.api.routes.providers import import_provider_consent
    from galaxy_frog.db.models import IngestionJobRow
    from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository

    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = IngestionJobRow(
        provider_selection=ProviderSelection(
            profile=ProfileRef(profile_id="local", version=1)
        ).model_dump(mode="json")
    )
    assert await import_provider_consent(uuid4(), SqlAlchemyVideoRepository(db)) is None
    db.get.assert_awaited_once()
