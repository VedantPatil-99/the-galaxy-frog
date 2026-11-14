"""Exercise provider policy against the real PostgreSQL admission and consent store."""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from pydantic import SecretStr
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.integration.test_postgres_lexical_search import (
    _seed,  # pyright: ignore[reportPrivateUsage]
)

from galaxy_frog.adapters.generation.bedrock import BedrockGeneration
from galaxy_frog.adapters.transcription.cloudflare import CloudflareTranscription
from galaxy_frog.api import provider_execution as execution
from galaxy_frog.api.app import create_app
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import (
    ProviderEntry,
    builtin_profiles,
    provider_catalog,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.ingestion_provider_routing import IngestionProviderRouting
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.models import (
    EmbeddingCollectionRow,
    IngestionJobRow,
    ProviderAttemptRow,
    ProviderConsentRow,
    ProviderProfileVersionRow,
    ProviderRuntimeRow,
    VideoRow,
)
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles, ProfileVersionConflict
from galaxy_frog.db.provider_runtime import (
    PostgresProviderRuntime,
    create_consent,
    locked_consent,
    provider_attempt_rows,
)
from galaxy_frog.domain.generation.models import (
    AnswerConfidence,
    GenerationCitation,
    GenerationDraft,
    GroundingText,
)
from galaxy_frog.domain.ingestion.models import IngestionJobStatus
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.providers import (
    Capability,
    ConsentScope,
    ExternalData,
    ProfileRef,
    ProviderConsentRequest,
    ProviderOperation,
    ProviderSelection,
)
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.transcription import TranscriptionRequest, TranscriptionResult
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires existing PostgreSQL/pgvector"
)


@pytest.mark.asyncio
async def test_profile_race_persists_one_immutable_version() -> None:
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    profile_id = f"test-{uuid4().hex}"
    policy = builtin_profiles()["local"]

    async def save() -> int:
        async with sessions() as session:
            return (await PostgresProviderProfiles(session).append(profile_id, 0, policy)).version

    try:
        results = await asyncio.gather(save(), save(), return_exceptions=True)
        assert results.count(1) == 1
        assert any(isinstance(result, ProfileVersionConflict) for result in results)
        async with sessions() as session:
            saved = await PostgresProviderProfiles(session).get(
                ProfileRef(profile_id=profile_id, version=1)
            )
            assert saved is not None and saved.policy == policy
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderProfileVersionRow).where(
                    ProviderProfileVersionRow.profile_id == profile_id
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_concurrency_quota_and_revocation_are_shared(tmp_path: Path) -> None:
    settings = Settings(
        cloudflare_asr_enabled=True,
        cloudflare_account_id="e" * 32,
        cloudflare_api_token=SecretStr("fixture-token"),
    )
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    profile = ProfileRef(profile_id="free_cloud", version=1)
    source = uuid4().hex[:11]
    reference = SourceReference(
        VideoSourceKind.YOUTUBE, source, f"https://www.youtube.com/watch?v={source}"
    )
    fingerprint = uuid4().hex * 2
    entry = next(
        entry for entry in provider_catalog(settings) if entry.provider_id == "cloudflare-whisper"
    )
    async with sessions() as session:
        grant = await create_consent(
            session,
            ProviderConsentRequest(
                profile=profile,
                operation=ProviderOperation.IMPORT,
                source=source,
                scopes=(ConsentScope(provider_id=entry.provider_id, data=ExternalData.AUDIO),),
            ),
        )
        jobs = PostgresIngestionRepository(session)
        job, created = await jobs.create_or_get(
            reference,
            fingerprint,
            provider_selection=ProviderSelection(profile=profile, consent_id=grant.consent_id),
        )
        assert created
        duplicate, created = await jobs.create_or_get(reference, fingerprint)
        assert not created and duplicate.job_id == job.job_id
        await session.execute(
            update(IngestionJobRow)
            .where(IngestionJobRow.id == job.job_id)
            .values(
                status=IngestionJobStatus.RUNNING,
                lease_owner="phase8-test",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
                heartbeat_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                attempt=1,
            )
        )
        await session.commit()
    run_id = job.job_id
    runtime = PostgresProviderRuntime(
        sessions,
        engine=engine,
        profile=profile,
        operation=ProviderOperation.IMPORT,
        run_id=run_id,
        source=source,
        consent_id=grant.consent_id,
        worker_id="phase8-test",
    )
    routing = IngestionProviderRouting(engine, settings, "phase8-test")
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"fixture")
    request = TranscriptionRequest(
        run_id,
        1,
        reference,
        AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio,
        0,
        3000,
    )
    hosted_calls = 0

    def hosted(_request: Request) -> Response:
        nonlocal hosted_calls
        hosted_calls += 1
        return Response(
            200,
            json={
                "success": True,
                "result": {"segments": [{"start": 0.25, "end": 1.5, "text": "Grounded speech"}]},
            },
        )

    async def local() -> TranscriptionResult:
        raise ProviderFailure(ProviderCode.UNAVAILABLE)

    async def cloud(verify: Callable[[], Awaitable[None]]) -> TranscriptionResult:
        async with AsyncClient(transport=MockTransport(hosted)) as client:
            return await CloudflareTranscription("fixture", "fixture", client).transcribe(
                request, verify_consent=verify
            )

    async def transcribe() -> TranscriptionResult:
        return await routing.execute(
            run_id,
            Capability.TRANSCRIPTION,
            {"faster-whisper": local},
            timeout_seconds=10,
            input_duration_ms=3000,
            external={"cloudflare-whisper": cloud},
        )

    try:
        result = await transcribe()
        assert (result.spec.provider, result.cues[0].start_ms, result.cues[0].end_ms) == (
            "cloudflare",
            250,
            1500,
        )
        assert hosted_calls == 1
        async with sessions() as session:
            history = await provider_attempt_rows(session, ProviderOperation.IMPORT, run_id)
        success = history[-1].payload
        assert success["status"] == "succeeded" and success["input_duration_ms"] == 3000
        assert success["estimated_cost"] == "0.000026"
        async with runtime.admit(entry):
            await runtime.verify_consent(entry, dispatch=True)
            with pytest.raises(ProviderFailure, match="PROVIDER_BUSY"):
                async with runtime.admit(entry):
                    pytest.fail("A second call entered the same provider.")
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            async with runtime.admit(entry):
                await runtime.verify_consent(entry, dispatch=True)
                raise ProviderFailure(
                    ProviderCode.QUOTA_EXHAUSTED, retry_at=datetime.now(UTC) + timedelta(seconds=60)
                )
        with pytest.raises(ProviderFailure, match="QUOTA_EXHAUSTED"):
            await transcribe()
        assert hosted_calls == 1
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
            row = await locked_consent(session, grant.consent_id)
            assert row is not None
            assert row.dispatch_count == 3
            row.revoked_at = datetime.now(UTC)
        with pytest.raises(ProviderFailure, match="PROVIDER_CONSENT_REQUIRED"):
            await transcribe()
        assert hosted_calls == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id == run_id)
            )
            await session.execute(
                delete(ProviderConsentRow).where(ProviderConsentRow.id == grant.consent_id)
            )
            await session.execute(delete(IngestionJobRow).where(IngestionJobRow.id == run_id))
            await session.execute(
                delete(ProviderRuntimeRow).where(
                    ProviderRuntimeRow.provider_id == entry.provider_id
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_routes_report_revocation_and_block_wrong_scope() -> None:
    app = create_app(Settings())
    profile = {"profile_id": "aws", "version": 1}
    source = str(uuid4())
    body = {
        "profile": profile,
        "source": source,
        "operation": "question",
        "scopes": [{"provider_id": "bedrock-nova-lite", "data": "paid_text"}],
    }
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        health = await client.get("/v1/providers/health")
        assert health.status_code == 200
        options = await client.get(
            "/v1/provider-consents/options",
            params={**profile, "operation": "question"},
        )
        assert options.json()["scopes"] == body["scopes"]
        invalid = await client.post("/v1/provider-consents", json={**body, "operation": "search"})
        assert invalid.status_code == 422
        response = await client.post("/v1/provider-consents", json=body)
        assert response.status_code == 201
        consent = response.json()["consent_id"]
        revoked = await client.delete(f"/v1/provider-consents/{consent}")
        assert revoked.json()["revoked_at"] is not None
        assert revoked.json()["dispatch_count"] == 0
        assert (await client.delete(f"/v1/provider-consents/{uuid4()}")).status_code == 404
        assert (await client.get(f"/v1/provider-runs/question/{uuid4()}")).json() == []


@pytest.mark.asyncio
async def test_routed_search_and_answers_share_preserved_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    quote = "ferrite absorbs neutron flux"
    async with sessions() as session:
        video_id, unit_ids = await _seed(session, (quote,))

    class Generator:
        calls = 0

        async def generate(
            self, _question: str, evidence: tuple[RetrievedEvidence, ...]
        ) -> GenerationDraft:
            self.calls += 1
            assert (evidence[0].unit.start_ms, evidence[0].unit.end_ms) == (0, 10_000)
            return GenerationDraft(
                quote, AnswerConfidence.HIGH, (GenerationCitation(unit_ids[0], quote),)
            )

        async def generate_moment(
            self, _question: str, evidence: tuple[GroundingText, ...]
        ) -> GenerationDraft:
            self.calls += 1
            return GenerationDraft(
                quote, AnswerConfidence.HIGH, (GenerationCitation(evidence[0].source_id, quote),)
            )

    generator = Generator()
    app = create_app(settings)
    app.state.generation_provider_factory = lambda: generator
    monkeypatch.setattr(
        execution,
        "probe_ollama_models",
        AsyncMock(return_value={settings.generation_model: "fixture-digest"}),
    )
    trace_ids: list[str] = []
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            base = f"/v1/videos/{video_id}"
            lexical = await client.post(
                f"{base}/search", json={"query": "ferrite", "mode": "lexical"}
            )
            assert lexical.status_code == 200 and generator.calls == 0
            trace_ids.append(lexical.json()["trace_id"])
            dense = await client.post(
                f"{base}/search",
                json={"query": "ferrite", "mode": "dense", "allow_fallback": False},
            )
            assert dense.status_code == 503 and generator.calls == 0
            trace = dense.json()["error"]["details"]["trace_id"]
            trace_ids.append(trace)
            attempts = (await client.get(f"/v1/provider-runs/search/{trace}")).json()
            assert attempts[0]["reason"] == "PROVIDER_INCOMPATIBLE"
            answer = await client.post(
                f"{base}/questions", json={"question": "ferrite", "mode": "lexical"}
            )
            assert answer.status_code == 200, answer.text
            result = answer.json()
            trace = result["retrieval"]["trace_id"]
            trace_ids.append(trace)
            (citation,) = result["evidence"]
            assert (citation["retrieval_unit_id"], citation["start_ms"], citation["end_ms"]) == (
                unit_ids[0],
                0,
                10_000,
            )
            attempts = (await client.get(f"/v1/provider-runs/question/{trace}")).json()
            assert attempts[-1]["revision"] == "fixture-digest"
            assert attempts[-1]["status"] == "succeeded"
            moment = await client.post(f"{base}/explain-moment", json={"timestamp_ms": 5000})
            assert moment.status_code == 200
            trace_ids.append(moment.json()["trace_id"])
            assert moment.json()["citations"][0]["seek_ms"] == 0
            assert generator.calls == 2
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
            for trace in trace_ids:
                await session.execute(
                    delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id == UUID(trace))
                )
        await engine.dispose()


@pytest.mark.asyncio
async def test_evaluation_rejects_pinned_collections_without_video_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = f"fixture-{uuid4().hex}"
    settings = Settings(embedding_model_revision=revision)
    entries = {
        entry.provider_id: entry.model_copy(
            update={"revision": revision, "readiness": "configured"}
        )
        for entry in provider_catalog(settings)
    }

    def fixed_catalog(_settings: Settings) -> tuple[ProviderEntry, ...]:
        return tuple(entries.values())

    monkeypatch.setattr(execution, "provider_catalog", fixed_catalog)
    monkeypatch.setattr(
        execution,
        "probe_ollama_models",
        AsyncMock(return_value={entry.model: revision for entry in entries.values()}),
    )
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    policy = builtin_profiles()["evaluation"]
    profile_id = f"test-{uuid4().hex}"
    collections: dict[Capability, UUID] = {}
    async with sessions() as session:
        video_id, _ = await _seed(session, ("Evaluation evidence",))
        for capability, dimension in (
            (Capability.TEXT_EMBEDDING, 1024),
            (Capability.VISUAL_EMBEDDING, 768),
        ):
            entry = entries[policy.candidates[capability][0]]
            collection_id = uuid4()
            collections[capability] = collection_id
            session.add(
                EmbeddingCollectionRow(
                    id=collection_id,
                    provider=entry.provider,
                    model=entry.model,
                    revision=revision,
                    dimension=dimension,
                    normalization="l2",
                    created_at=datetime.now(UTC),
                )
            )
        saved = await PostgresProviderProfiles(session).append(
            profile_id,
            0,
            policy.model_copy(
                update={
                    "pinned_revisions": {
                        chain[0]: revision for chain in policy.candidates.values()
                    },
                    "embedding_collections": collections,
                }
            ),
        )
    app = create_app(settings)
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            response = await client.post(
                f"/v1/videos/{video_id}/search",
                json={
                    "query": "Evaluation evidence",
                    "mode": "hybrid",
                    "allow_fallback": True,
                    "provider_selection": {
                        "profile": {"profile_id": saved.profile_id, "version": saved.version}
                    },
                },
            )
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "PROVIDER_EVALUATION_UNVERIFIABLE"
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
            await session.execute(
                delete(EmbeddingCollectionRow).where(
                    EmbeddingCollectionRow.id.in_(collections.values())
                )
            )
            await session.execute(
                delete(ProviderProfileVersionRow).where(
                    ProviderProfileVersionRow.profile_id == profile_id
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_bedrock_route_requires_current_consent_and_saves_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        bedrock_text_enabled=True, aws_bearer_token_bedrock=SecretStr("fixture-token")
    )
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    quote = "The observation is confirmed."
    async with sessions() as session:
        video_id, (unit_id,) = await _seed(session, (quote,))
    calls = 0

    def hosted(request: Request) -> Response:
        nonlocal calls
        calls += 1
        assert request.url.host == f"bedrock-runtime.{settings.bedrock_region}.amazonaws.com"
        assert quote.encode() in request.content
        return Response(
            200,
            json={
                "stopReason": "end_turn",
                "usage": {"inputTokens": 50, "outputTokens": 20},
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "answer": quote,
                                        "confidence": "high",
                                        "citations": [
                                            {"retrieval_unit_id": unit_id, "quote": quote}
                                        ],
                                    }
                                )
                            }
                        ]
                    }
                },
            },
        )

    app = create_app(settings)
    profile = {"profile_id": "aws", "version": 1}
    payload = {
        "question": "confirmed",
        "mode": "lexical",
        "provider_selection": {"profile": profile},
    }
    grant_ids: list[UUID] = []
    trace_ids: list[UUID] = []
    try:
        async with (
            AsyncClient(transport=MockTransport(hosted)) as fake_bedrock,
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):

            def build_bedrock(
                region: str,
                token: str,
                _client: AsyncClient,
                *,
                verify_consent: Callable[[], Awaitable[None]],
            ) -> BedrockGeneration:
                return BedrockGeneration(region, token, fake_bedrock, verify_consent=verify_consent)

            monkeypatch.setattr(execution, "BedrockGeneration", build_bedrock)
            path = f"/v1/videos/{video_id}/questions"
            denied = await client.post(path, json=payload)
            assert denied.json()["error"]["code"] == "PROVIDER_CONSENT_REQUIRED"
            trace_ids.append(UUID(denied.json()["error"]["details"]["trace_id"]))
            consent = await client.post(
                "/v1/provider-consents",
                json={
                    "profile": profile,
                    "source": str(video_id),
                    "operation": "question",
                    "scopes": [{"provider_id": "bedrock-nova-lite", "data": "paid_text"}],
                },
            )
            assert consent.status_code == 201
            grant_id = UUID(consent.json()["consent_id"])
            grant_ids.append(grant_id)
            body = {
                **payload,
                "provider_selection": {"profile": profile, "consent_id": str(grant_id)},
            }
            permitted = await client.post(path, json=body)
            assert permitted.status_code == 200, permitted.text
            assert permitted.json()["evidence"][0]["retrieval_unit_id"] == unit_id
            trace = UUID(permitted.json()["retrieval"]["trace_id"])
            trace_ids.append(trace)
            attempts = (await client.get(f"/v1/provider-runs/question/{trace}")).json()
            assert attempts[-1]["usage"] == {"input_tokens": 50, "output_tokens": 20}
            assert attempts[-1]["actual_cost"] is None
            revoked = await client.delete(f"/v1/provider-consents/{grant_id}")
            assert revoked.json()["dispatch_count"] == 1
            denied = await client.post(path, json=body)
            assert denied.json()["error"]["code"] == "PROVIDER_CONSENT_REQUIRED"
            trace_ids.append(UUID(denied.json()["error"]["details"]["trace_id"]))
            assert calls == 1
    finally:
        async with sessions.begin() as session:
            await session.execute(
                delete(ProviderConsentRow).where(ProviderConsentRow.id.in_(grant_ids))
            )
            await session.execute(
                delete(ProviderAttemptRow).where(ProviderAttemptRow.run_id.in_(trace_ids))
            )
            await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
        await engine.dispose()
