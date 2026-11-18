"""Selected Study providers preserve consent, provenance, and artifact consistency."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import httpx2
import pytest
import pytest_asyncio
from google import genai
from google.genai import types
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tests.test_gemini_generation import MODEL, result
from tests.unit.application.test_study_generation import cue, progress, source
from tests.unit.entrypoints.test_study_worker import claim

from galaxy_frog.adapters.generation.gemini import GeminiJSON
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import ProviderEntry
from galaxy_frog.application.study_generation import DraftBatch, StudyTimeline, generate_study
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.db.study_repository import StudyLeaseLost
from galaxy_frog.domain.providers import (
    Capability,
    ProfileRef,
    ProviderMode,
    ProviderProfile,
    ProviderProfileVersion,
    ProviderSelection,
)
from galaxy_frog.domain.study import StudyError, StudyKind
from galaxy_frog.entrypoints import study_providers


@pytest_asyncio.fixture
async def cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[list[httpx2.Request], AsyncMock]]:
    requests: list[httpx2.Request] = []

    def send(request: httpx2.Request) -> httpx2.Response:
        import json

        requests.append(request)
        return httpx2.Response(
            200,
            json=result(
                output_text=json.dumps(
                    {
                        "items": [
                            {
                                "title": "Optimization",
                                "citations": [{"source_id": "E1", "quote": "gradient descent"}],
                            }
                        ],
                    }
                )
            ),
        )

    transport = httpx2.AsyncClient(transport=httpx2.MockTransport(send))
    client = genai.Client(
        api_key="synthetic-study-key",
        vertexai=False,
        http_options=types.HttpOptions(
            api_version="v1",
            httpx_async_client=transport,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    def factory(key: str, model: str, **kwargs: object) -> GeminiJSON:
        assert key == "synthetic-study-key"
        kwargs["client"] = client
        return GeminiJSON(key, model, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(study_providers, "GeminiJSON", factory)
    try:
        yield requests, AsyncMock()
    finally:
        await client.aio.aclose()
        client.close()


def selected_runtime() -> tuple[PostgresProviderRuntime, AsyncMock]:
    @asynccontextmanager
    async def admit(_entry: ProviderEntry) -> AsyncGenerator[None]:
        yield

    runtime = AsyncMock(spec=PostgresProviderRuntime)
    runtime.admit = admit
    return cast(PostgresProviderRuntime, runtime), runtime


def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        gemini_api_key=SecretStr("different-interactive-key"),
        gemini_project_id="frog-interactive",
        gemini_study_api_key=SecretStr("synthetic-study-key"),
        gemini_study_project_id="frog-background",
        gemini_study_model=MODEL,
        gemini_study_enabled=True,
    )


@pytest.mark.asyncio
async def test_cloud_study_records_provider_and_validates_original_citations(
    cloud: tuple[list[httpx2.Request], AsyncMock],
) -> None:
    requests, local = cloud
    runtime, spy = selected_runtime()
    provider = study_providers.RoutedStudyGenerator(
        settings(),
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={Capability.STUDY: ("gemini-study",)}),
        runtime,
        local,
    )
    evidence = cue(0, 100_000)
    generated = await generate_study(
        StudyKind.CHAPTERS,
        StudyTimeline(source(), (evidence,), (), ()),
        None,
        provider,
        progress,
        evidence_characters=provider.evidence_characters,
        excerpt_characters=provider.excerpt_characters,
    )
    assert generated.provider == "google-gemini" and generated.model == MODEL
    assert generated.prompt_version == "study-gemini-v1"
    citation = generated.document.chapters[0].citations[0]
    assert (citation.source_id, citation.start_ms, citation.end_ms) == (
        evidence.source_id,
        0,
        100_000,
    )
    assert citation.quote == "gradient descent"
    assert len(requests) == 1
    spy.verify_consent.assert_awaited_once()
    assert spy.verify_consent.await_args.kwargs["dispatch"] is True
    assert spy.verify_consent.await_args.kwargs["input_tokens"] > 0
    call = spy.record.await_args
    assert call.args[1].provider == "google-gemini"
    assert call.kwargs["usage"]["input_tokens"] == 100
    local.draft.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ProviderFailure(ProviderCode.CONSENT_REQUIRED), StudyLeaseLost()]
)
async def test_revocation_or_lost_lease_prevents_the_next_cloud_dispatch(
    cloud: tuple[list[httpx2.Request], AsyncMock],
    failure: Exception,
) -> None:
    requests, local = cloud
    runtime, spy = selected_runtime()
    provider = study_providers.RoutedStudyGenerator(
        settings(),
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={Capability.STUDY: ("gemini-study",)}),
        runtime,
        local,
    )
    await provider.draft(StudyKind.CHAPTERS, (cue(0, 100_000),))
    spy.verify_consent.side_effect = failure
    with pytest.raises(StudyError if isinstance(failure, ProviderFailure) else StudyLeaseLost):
        await provider.draft(StudyKind.CHAPTERS, (cue(0, 100_000),))
    assert len(requests) == 1
    assert provider.chosen == "gemini-study"


@pytest.mark.asyncio
async def test_explicit_first_call_fallback_pins_the_local_provider_for_the_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, spy = selected_runtime()
    local = AsyncMock()
    local.draft.return_value = DraftBatch((), "local-model")
    monkeypatch.setattr(
        study_providers, "probe_ollama_models", AsyncMock(return_value={"qwen3:4b": "digest"})
    )
    config = settings().model_copy(update={"gemini_study_enabled": False})
    provider = study_providers.RoutedStudyGenerator(
        config,
        ProviderProfile(
            mode=ProviderMode.MANUAL,
            candidates={Capability.STUDY: ("gemini-study", "ollama-study")},
        ),
        runtime,
        local,
    )
    await provider.draft(StudyKind.CHAPTERS, (cue(0, 100_000),))
    await provider.draft(StudyKind.CHAPTERS, (cue(0, 100_000),))
    attempts = [call.args[1] for call in spy.record.await_args_list]
    assert [(item.provider_id, item.status) for item in attempts] == [
        ("gemini-study", "skipped"),
        ("ollama-study", "succeeded"),
        ("ollama-study", "succeeded"),
    ]
    assert attempts[-1].requested_candidates == ("gemini-study", "ollama-study")
    assert provider.evidence_characters == 1400
    spy.verify_consent.assert_not_awaited()


@pytest.mark.parametrize("revision", [None, "different", "pinned"])
@pytest.mark.asyncio
async def test_local_study_failure_is_safe_and_revision_is_verified(
    monkeypatch: pytest.MonkeyPatch, revision: str | None
) -> None:
    config = settings().model_copy(update={"generation_model_revision": "pinned"})
    runtime, spy = selected_runtime()
    local = AsyncMock()
    local.draft.side_effect = StudyError("PRIVATE", "private local response")
    monkeypatch.setattr(
        study_providers,
        "probe_ollama_models",
        AsyncMock(return_value={config.generation_model: revision} if revision else {}),
    )
    provider = study_providers.RoutedStudyGenerator(
        config,
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={Capability.STUDY: ("ollama-study",)}),
        runtime,
        local,
    )
    with pytest.raises(StudyError) as caught:
        await provider.draft(StudyKind.CHAPTERS, (cue(0, 100_000),))
    assert caught.value.failure.code == (
        ProviderCode.UNAVAILABLE.value
        if revision == "pinned"
        else ProviderCode.SETUP_REQUIRED.value
    )
    assert "private" not in str(caught.value)
    assert local.draft.await_count == int(revision == "pinned")
    spy.record.assert_awaited_once()


@pytest.mark.asyncio
async def test_quota_retry_guidance_is_preserved(
    cloud: tuple[list[httpx2.Request], AsyncMock],
) -> None:
    requests, local = cloud
    runtime, spy = selected_runtime()
    retry = datetime.now(UTC) + timedelta(hours=1)
    spy.verify_consent.side_effect = ProviderFailure(ProviderCode.QUOTA_EXHAUSTED, retry_at=retry)
    provider = study_providers.RoutedStudyGenerator(
        settings(),
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={Capability.STUDY: ("gemini-study",)}),
        runtime,
        local,
    )
    with pytest.raises(StudyError) as caught:
        await provider.draft(StudyKind.CHAPTERS, (cue(0, 100_000),))
    assert retry.isoformat() in caught.value.failure.suggested_action
    assert requests == []


@pytest.mark.parametrize("case", ["missing", "evaluation", "incompatible", "inherited", "selected"])
@pytest.mark.asyncio
async def test_study_factory_validates_profiles_and_preserves_lease(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from galaxy_frog.application.provider_registry import builtin_profiles

    policy = builtin_profiles()["local"]
    if case == "evaluation":
        policy = policy.model_copy(update={"evaluation": True, "mode": ProviderMode.MANUAL})
    elif case == "incompatible":
        policy = ProviderProfile(
            mode=ProviderMode.MANUAL, candidates={Capability.STUDY: ("ollama-text",)}
        )
    elif case == "inherited":
        policy = ProviderProfile(mode=ProviderMode.MANUAL, candidates={})
    version = ProviderProfileVersion(profile_id="custom", version=1, policy=policy)
    monkeypatch.setattr(
        study_providers.PostgresProviderProfiles,
        "get",
        AsyncMock(return_value=None if case == "missing" else version),
    )
    claimed = claim()
    claimed.run.provider_selection = ProviderSelection(
        profile=ProfileRef(profile_id="custom", version=1), consent_id=claimed.token
    )
    sessions = async_sessionmaker(class_=AsyncSession)
    engine = cast(AsyncEngine, object())
    errors = {
        "missing": "PROVIDER_PROFILE_NOT_FOUND",
        "evaluation": "PROVIDER_EVALUATION_UNVERIFIABLE",
        "incompatible": "PROVIDER_PROFILE_INVALID",
    }
    if case in errors:
        with pytest.raises(StudyError) as caught:
            await study_providers.selected_study_generator(
                settings(), sessions, engine, claimed, AsyncMock()
            )
        assert caught.value.failure.code == errors[case]
    else:
        selected = await study_providers.selected_study_generator(
            settings(), sessions, engine, claimed, AsyncMock()
        )
        assert selected.policy.candidates[Capability.STUDY] == ("ollama-study",)
        assert selected.runtime.study_lease_token == claimed.token
        assert selected.runtime.consent_id == claimed.token


@pytest.mark.asyncio
async def test_study_rejects_invalid_envelope_without_publishing() -> None:
    from galaxy_frog.adapters.generation.study_gemini import GeminiStudyGenerator

    provider = AsyncMock(spec=GeminiJSON)
    provider.generate.return_value = {"items": [None] * 6}
    with pytest.raises(ProviderFailure, match=ProviderCode.INVALID_OUTPUT.value):
        await GeminiStudyGenerator(provider).draft(StudyKind.NOTES, (cue(0, 100_000),))


@pytest.mark.parametrize("evidence,excerpt", [(1399, 320), (48001, 320), (1400, 319), (1400, 4001)])
@pytest.mark.asyncio
async def test_invalid_evidence_budget_prevents_generation(evidence: int, excerpt: int) -> None:
    provider = AsyncMock()
    with pytest.raises(ValueError):
        await generate_study(
            StudyKind.CHAPTERS,
            StudyTimeline(source(), (cue(0, 100_000),), (), ()),
            None,
            provider,
            progress,
            evidence_characters=evidence,
            excerpt_characters=excerpt,
        )
    provider.draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_ingestion_lease_prevents_dispatch_before_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import uuid4

    from galaxy_frog.application.provider_registry import provider_catalog
    from galaxy_frog.domain.providers import ProviderOperation

    sessions = async_sessionmaker(class_=AsyncSession)
    monkeypatch.setattr(AsyncSession, "scalar", AsyncMock(return_value=None))
    config = settings()
    runtime = PostgresProviderRuntime(
        sessions,
        engine=cast(AsyncEngine, object()),
        profile=ProfileRef(profile_id="local", version=1),
        operation=ProviderOperation.IMPORT,
        run_id=uuid4(),
        source="fixture",
        worker_id="replaced-worker",
    )
    entry = next(
        item for item in provider_catalog(config) if item.provider_id == "cloudflare-whisper"
    )
    with pytest.raises(ProviderFailure, match=ProviderCode.UNAVAILABLE.value):
        await runtime.verify_consent(entry, dispatch=True)
