"""Exercise cloud adapter contracts and fail-closed provider routing."""

import asyncio
import json
import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from galaxy_frog.adapters.generation.bedrock import BedrockGeneration
from galaxy_frog.adapters.transcription.cloudflare import CloudflareTranscription
from galaxy_frog.application.provider_policy import (
    ProviderAttempt,
    ProviderCode,
    ProviderFailure,
    ProviderRouting,
    retry_after_at,
)
from galaxy_frog.application.provider_registry import PricingSource, ProviderEntry, provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_runtime import estimate_list_cost
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.providers import Capability, Location, ProviderMode, ProviderProfile
from galaxy_frog.domain.transcription import TranscriptionRequest
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        (None, None),
        ("unavailable", None),
        ("17", 17),
        ("100000", 86400),
        ("Sat, 14 Nov 2026 00:01:00 GMT", 60),
    ],
)
def test_retry_after_returns_bounded_utc_deadline(header: str | None, seconds: int | None) -> None:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    assert retry_after_at(header, now) == (
        now + timedelta(seconds=seconds) if seconds is not None else None
    )


@pytest.mark.parametrize(("age", "estimate"), [(0, "0.000026"), (31, None)])
def test_cost_estimate_uses_only_recent_published_rates(age: int, estimate: str | None) -> None:
    pricing = PricingSource(
        source_url="https://example.test/pricing",
        billing_unit="Audio minute",
        tier_note="Fixture",
        list_rate_per_unit=Decimal("0.000513"),
        verified_at=datetime.now(UTC).date() - timedelta(days=age),
    )
    assert estimate_list_cost("cloudflare-whisper", 3000, pricing) == estimate


@pytest.mark.parametrize(
    ("failure", "compatible", "fallback", "expected", "statuses"),
    [
        (ProviderCode.UNAVAILABLE, None, True, "result", ["failed", "succeeded"]),
        (ProviderCode.UNAVAILABLE, None, False, "PROVIDER_UNAVAILABLE", ["failed"]),
        (ProviderCode.SAFETY_BLOCKED, None, True, "PROVIDER_SAFETY_BLOCKED", ["failed"]),
        (ProviderCode.OUTCOME_UNKNOWN, None, True, "PROVIDER_OUTCOME_UNKNOWN", ["failed"]),
        (ProviderCode.INVALID_INPUT, None, True, "PROVIDER_INVALID_INPUT", ["failed"]),
        (None, frozenset({"ollama-text"}), True, "result", ["skipped", "succeeded"]),
        (None, None, False, "cached", ["cached"]),
    ],
)
@pytest.mark.asyncio
async def test_manual_order_and_strict_failures(
    failure: ProviderCode | None,
    compatible: frozenset[str] | None,
    fallback: bool,
    expected: str,
    statuses: list[str],
) -> None:
    entries = tuple(
        entry.model_copy(update={"readiness": "configured"})
        for entry in provider_catalog(Settings())
    )
    attempts: list[ProviderAttempt] = []

    @asynccontextmanager
    async def admit(_entry: ProviderEntry) -> AsyncGenerator[None]:
        yield

    async def record(attempt: ProviderAttempt) -> None:
        attempts.append(attempt)

    async def primary() -> str:
        if failure:
            raise ProviderFailure(failure)
        return "cached"

    async def alternate() -> str:
        return "result"

    capability = Capability.GENERATION
    routing = ProviderRouting(
        ProviderProfile(
            mode=ProviderMode.MANUAL, candidates={capability: ("bedrock-nova-lite", "ollama-text")}
        ),
        entries,
        admit,
        record,
    )
    try:
        actual = await routing.execute(
            capability,
            {"bedrock-nova-lite": primary, "ollama-text": alternate},
            timeout_seconds=1,
            compatible=compatible,
            fallback=fallback,
            is_cached=lambda value: value == "cached",
        )
    except ProviderFailure as exc:
        actual = exc.code.value
    assert actual == expected
    assert [item.status for item in attempts] == statuses


@pytest.mark.parametrize(
    ("provider_id", "expected"),
    [("ollama-text", "PROVIDER_TIMEOUT"), ("bedrock-nova-lite", "PROVIDER_OUTCOME_UNKNOWN")],
)
@pytest.mark.asyncio
async def test_deadline_marks_ambiguous_hosted_outcome(provider_id: str, expected: str) -> None:
    @asynccontextmanager
    async def admit(_entry: ProviderEntry) -> AsyncGenerator[None]:
        yield

    async def record(_attempt: ProviderAttempt) -> None:
        pass

    async def wait() -> None:
        await asyncio.Event().wait()

    capability = Capability.GENERATION
    entries = tuple(
        entry.model_copy(update={"readiness": "configured"})
        for entry in provider_catalog(Settings())
    )
    routing = ProviderRouting(
        ProviderProfile(mode=ProviderMode.MANUAL, candidates={capability: (provider_id,)}),
        entries,
        admit,
        record,
    )
    with pytest.raises(ProviderFailure, match=expected):
        await routing.execute(capability, {provider_id: wait}, timeout_seconds=0.001)


@pytest.mark.asyncio
async def test_expiry_while_waiting_for_admission_prevents_hosted_dispatch() -> None:
    attempts: list[ProviderAttempt] = []

    @asynccontextmanager
    async def admit(_entry: ProviderEntry) -> AsyncGenerator[None]:
        await asyncio.sleep(0.01)
        yield

    async def record(attempt: ProviderAttempt) -> None:
        attempts.append(attempt)

    async def dispatch() -> None:
        pytest.fail("The expired route sent evidence to the hosted provider.")

    provider_id = "bedrock-nova-lite"
    routing = ProviderRouting(
        ProviderProfile(
            mode=ProviderMode.MANUAL, candidates={Capability.GENERATION: (provider_id,)}
        ),
        tuple(
            entry.model_copy(update={"readiness": "configured"})
            for entry in provider_catalog(Settings())
        ),
        admit,
        record,
    )
    with pytest.raises(ProviderFailure, match="PROVIDER_TIMEOUT"):
        await routing.execute(Capability.GENERATION, {provider_id: dispatch}, timeout_seconds=0.001)
    assert [(attempt.status, attempt.reason) for attempt in attempts] == [
        ("skipped", ProviderCode.TIMEOUT)
    ]


@pytest.mark.asyncio
async def test_local_only_never_admits_cloud_even_with_invalid_stored_policy() -> None:
    calls: list[str] = []
    attempts: list[ProviderAttempt] = []

    @asynccontextmanager
    async def admit(entry: ProviderEntry) -> AsyncGenerator[None]:
        assert entry.location is Location.LOCAL
        yield

    async def record(attempt: ProviderAttempt) -> None:
        attempts.append(attempt)

    async def local() -> str:
        calls.append("local")
        raise ProviderFailure(ProviderCode.UNAVAILABLE)

    async def cloud() -> str:
        calls.append("cloud")
        return "unexpected"

    routing = ProviderRouting(
        ProviderProfile(
            mode=ProviderMode.LOCAL_ONLY,
            candidates={Capability.GENERATION: ("ollama-text", "bedrock-nova-lite")},
        ),
        provider_catalog(Settings()),
        admit,
        record,
    )
    with pytest.raises(ProviderFailure, match="PROVIDER_INCOMPATIBLE"):
        await routing.execute(
            Capability.GENERATION,
            {"ollama-text": local, "bedrock-nova-lite": cloud},
            timeout_seconds=1,
        )
    assert calls == ["local"]
    assert [item.status for item in attempts] == ["failed", "skipped"]


def audio_request(tmp_path: Path) -> TranscriptionRequest:
    audio = tmp_path / "input.wav"
    audio.write_bytes(b"test audio")
    return TranscriptionRequest(
        audio_job_id=uuid4(),
        attempt=1,
        source=SourceReference(
            VideoSourceKind.YOUTUBE, "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ),
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_path=audio,
        audio_start_ms=0,
        audio_end_ms=1000,
    )


@pytest.mark.asyncio
async def test_cloudflare_uses_timestamped_segments_and_language_probability(
    tmp_path: Path,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"audio": "dGVzdCBhdWRpbw==", "task": "transcribe"}
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "segments": [
                        {"start": 0, "end": 0.1, "text": " "},
                        {"start": 0.1002, "end": 0.9002, "text": " Spoken "},
                    ],
                    "transcription_info": {"language": "en", "language_probability": 0.9},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await CloudflareTranscription("test", "test", client).transcribe(
            audio_request(tmp_path)
        )
    assert result.language_confidence == 0.9
    (cue,) = result.cues
    assert (cue.source_order, cue.start_ms, cue.end_ms, cue.text) == (0, 100, 901, "Spoken")
    assert cue.confidence is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream", "expected"), [(3036, "QUOTA_EXHAUSTED"), (3040, "RATE_LIMITED")]
)
async def test_cloudflare_maps_quota_and_capacity(
    tmp_path: Path, upstream: int, expected: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, json={"success": False, "errors": [{"code": upstream}]})
        )
    ) as client:
        with pytest.raises(ProviderFailure, match=expected) as raised:
            await CloudflareTranscription("test", "test", client).transcribe(
                audio_request(tmp_path)
            )
    assert raised.value.retry_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["bedrock", "cloudflare"])
@pytest.mark.parametrize("failure", ["network", "json"])
async def test_hosted_provider_errors_hide_secret_traceback_context(
    tmp_path: Path, provider_id: str, failure: str
) -> None:
    secret = "synthetic-hosted-provider-credential"

    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ReadTimeout(secret, request=request)
        return httpx.Response(200, text=secret)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderFailure) as caught:
            if provider_id == "bedrock":
                await BedrockGeneration("us-east-1", secret, client).generate("Question", ())
            else:
                await CloudflareTranscription("test", secret, client).transcribe(
                    audio_request(tmp_path)
                )
    assert secret not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("fenced", [False, True])
@pytest.mark.asyncio
async def test_bedrock_returns_validated_shape_and_usage(fenced: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
        assert request.headers["Authorization"] == "Bearer test"
        body = json.loads(request.content)
        assert body["inferenceConfig"]["maxTokens"] == 1024
        answer = json.dumps({"answer": "An answer", "confidence": "low", "citations": []})
        if fenced:
            answer = "```json\n" + answer + "\n```"
        return httpx.Response(
            200,
            json={
                "output": {"message": {"content": [{"text": answer}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 10, "outputTokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BedrockGeneration("us-east-1", "test", client)
        result = await provider.generate_moment("Explain a test moment", ())
    assert result.answer == "An answer"
    assert provider.usage == {"input_tokens": 10, "output_tokens": 4}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {}, "PROVIDER_AUTH_FAILED"),
        (403, {}, "PROVIDER_AUTH_FAILED"),
        (429, {}, "RATE_LIMITED"),
        (424, {}, "PROVIDER_OUTCOME_UNKNOWN"),
        (500, {}, "PROVIDER_OUTCOME_UNKNOWN"),
        (404, {}, "PROVIDER_UNAVAILABLE"),
        (200, {}, "PROVIDER_INVALID_OUTPUT"),
        (
            200,
            {
                "stopReason": "end_turn",
                "output": {"message": {"content": [{"text": "invalid JSON"}]}},
            },
            "PROVIDER_INVALID_OUTPUT",
        ),
        (
            200,
            {"stopReason": "content_filtered", "output": {"message": {"content": [{"text": "a"}]}}},
            "PROVIDER_SAFETY_BLOCKED",
        ),
        (
            200,
            {"stopReason": "max_tokens", "output": {"message": {"content": [{"text": "a"}]}}},
            "PROVIDER_INVALID_OUTPUT",
        ),
    ],
)
async def test_bedrock_normalizes_hosted_failures(
    status: int, payload: dict[str, object], expected: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json=payload, headers={"Retry-After": "3"})
        )
    ) as client:
        with pytest.raises(ProviderFailure, match=expected):
            await BedrockGeneration("us-east-1", "fixture", client).generate("Fixture", ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "data", "expected"),
    [
        (401, b"{}", "PROVIDER_AUTH_FAILED"),
        (413, b"{}", "PROVIDER_INPUT_LIMIT"),
        (500, b"{}", "PROVIDER_OUTCOME_UNKNOWN"),
        (429, b"", "RATE_LIMITED"),
        (200, b"{}", "PROVIDER_INVALID_OUTPUT"),
        (200, b'{"success":false}', "PROVIDER_INVALID_OUTPUT"),
        (
            200,
            b'{"success":true,"result":{"segments":[{"start":0.8,"end":0.7,"text":"x"}]}}',
            "PROVIDER_INVALID_OUTPUT",
        ),
    ],
)
async def test_cloudflare_normalizes_hosted_failures(
    tmp_path: Path, status: int, data: bytes, expected: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, content=data))
    ) as client:
        with pytest.raises(ProviderFailure, match=expected):
            await CloudflareTranscription("fixture", "fixture", client).transcribe(
                audio_request(tmp_path)
            )


@pytest.mark.asyncio
async def test_hosted_output_size_is_bounded(tmp_path: Path) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        limit = 65_536 if request.url.host.startswith("bedrock-runtime.") else 1024 * 1024
        return httpx.Response(200, content=b" " * (limit + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(ProviderFailure, match=ProviderCode.INVALID_OUTPUT.value):
            await BedrockGeneration("us-east-1", "test", client).generate("Fixture", ())
        with pytest.raises(ProviderFailure, match=ProviderCode.INVALID_OUTPUT.value):
            await CloudflareTranscription("fixture", "test", client).transcribe(
                audio_request(tmp_path)
            )


@pytest.mark.asyncio
async def test_cloudflare_rejects_out_of_bounds_and_unreadable_audio(tmp_path: Path) -> None:
    request = audio_request(tmp_path)

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Cloudflare received audio that failed local checks.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
        transcriber = CloudflareTranscription("fixture", "test", client)
        with pytest.raises(ProviderFailure, match=ProviderCode.INPUT_LIMIT.value):
            await transcriber.transcribe(replace(request, audio_end_ms=300_001))
        with pytest.raises(ProviderFailure, match=ProviderCode.UNAVAILABLE.value):
            await transcriber.transcribe(replace(request, audio_path=tmp_path / "missing.wav"))


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_error", [httpx.ReadTimeout, httpx.ReadError])
async def test_hosted_transport_failure_leaves_the_outcome_unknown(
    tmp_path: Path, transport_error: type[httpx.RequestError]
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise transport_error("Fixture lost the response.", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ProviderFailure, match=ProviderCode.OUTCOME_UNKNOWN.value):
            await BedrockGeneration("us-east-1", "test", client).generate("Fixture", ())
        with pytest.raises(ProviderFailure, match=ProviderCode.OUTCOME_UNKNOWN.value):
            await CloudflareTranscription("fixture", "test", client).transcribe(
                audio_request(tmp_path)
            )


@pytest.mark.asyncio
async def test_oversized_cloud_inputs_are_rejected_before_consent_or_dispatch(
    tmp_path: Path,
) -> None:
    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        pytest.fail("An oversized payload was sent to a provider.")

    async def unexpected_consent_check() -> None:
        pytest.fail("An oversized payload reached the dispatch consent check.")

    request = audio_request(tmp_path)
    request.audio_path.write_bytes(b"a" * (10 * 1024 * 1024 + 1))
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
        with pytest.raises(ProviderFailure, match=ProviderCode.INPUT_LIMIT.value):
            await BedrockGeneration(
                "us-east-1", "test", client, verify_consent=unexpected_consent_check
            ).generate("a" * 65_537, ())
        with pytest.raises(ProviderFailure, match=ProviderCode.INPUT_LIMIT.value):
            await CloudflareTranscription("fixture", "test", client).transcribe(
                request, verify_consent=unexpected_consent_check
            )
