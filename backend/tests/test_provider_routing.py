"""Exercise cloud adapter contracts and fail-closed provider routing."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
)
from galaxy_frog.application.provider_registry import ProviderEntry, provider_catalog
from galaxy_frog.config import Settings
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.providers import Capability, Location, ProviderMode, ProviderProfile
from galaxy_frog.domain.transcription import TranscriptionRequest
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind


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
async def test_bedrock_returns_validated_shape_and_usage() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
        assert request.headers["Authorization"] == "Bearer test"
        body = json.loads(request.content)
        assert body["inferenceConfig"]["maxTokens"] == 1024
        return httpx.Response(
            200,
            json={
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "answer": "An answer",
                                        "confidence": "low",
                                        "citations": [],
                                    }
                                )
                            }
                        ]
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 10, "outputTokens": 4},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BedrockGeneration("us-east-1", "test", client)
        result = await provider.generate_moment("Explain a test moment", ())
    assert result.answer == "An answer"
    assert provider.usage == {"input_tokens": 10, "output_tokens": 4}
