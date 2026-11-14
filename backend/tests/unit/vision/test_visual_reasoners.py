"""Transport contract tests; live GPU acceptance is recorded separately using actual frames."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from tests.unit.vision.test_visual_contracts import frame

from galaxy_frog.adapters.vision.reasoning import (
    QWEN_VL_DIGEST,
    GeminiVisualReasoner,
    LocalReasoningFrameVerifier,
    OllamaQwenVisualReasoner,
)
from galaxy_frog.domain.vision.visual import VisualProviderError, VisualReasoningRequest


def request_value(tmp_path: Path) -> VisualReasoningRequest:
    path = tmp_path / "transport-fixture.jpg"
    data = b"transport fixture; no native inference"
    path.write_bytes(data)
    original = replace(frame(), path=path, content_sha256=sha256(data).hexdigest())
    return VisualReasoningRequest(original.video_id, "What is visible?", (original,), "")


class Transport:
    def __init__(self, value: VisualReasoningRequest, mode: str = "") -> None:
        self.value, self.mode = value, mode
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        answer = {
            "answer": "Visible answer",
            "cited_frame_ids": [str(self.value.frames[0].frame_id)],
            "insufficient_evidence": False,
        }
        if self.mode == "foreign":
            answer["cited_frame_ids"] = [str(uuid4())]
        if path.endswith("/api/tags"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3-vl:2b-instruct",
                            "digest": "changed" if self.mode == "digest" else QWEN_VL_DIGEST,
                        }
                    ]
                },
            )
        if path.endswith("/api/ps"):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "digest": QWEN_VL_DIGEST,
                            "size": 1024,
                            "size_vram": 0 if self.mode == "cpu" else 1024,
                            "context_length": 2048,
                        }
                    ]
                },
            )
        if path.endswith("/api/generate"):
            return httpx.Response(503 if self.mode == "cleanup" else 200, json={"done": True})
        if self.mode == "timeout":
            raise httpx.ReadTimeout("private provider diagnostics")
        if self.mode == "deadline":
            raise TimeoutError("private provider diagnostics")
        if self.mode == "http":
            return httpx.Response(503, text="private")
        if self.mode == "bytes":
            return httpx.Response(200, content=b"x" * 65537)
        if self.mode == "json":
            return httpx.Response(200, text="invalid")
        if path.endswith("/api/chat"):
            payload = json.loads(request.content)
            assert payload["options"]["num_gpu"] == 999 and payload["options"]["num_ctx"] == 2048
            assert payload["options"]["num_predict"] > 384
            assert payload["format"]["properties"]["answer"]["maxLength"] == 700
            assert "Keep the answer under 90 words" in payload["messages"][0]["content"]
            assert payload["messages"][0]["images"]
            return httpx.Response(
                200,
                json={
                    "model": "other" if self.mode == "model" else "qwen3-vl:2b-instruct",
                    "done": self.mode != "incomplete",
                    "message": {"content": json.dumps(answer)},
                },
            )
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "key=" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "modelVersion": "changed" if self.mode == "revision" else "pinned-v1",
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS" if self.mode == "incomplete" else "STOP",
                        "content": {"parts": [{"text": json.dumps(answer)}]},
                    }
                ],
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
async def test_reasoners_validate_original_citations_and_identity(
    tmp_path: Path, cloud: bool
) -> None:
    value = request_value(tmp_path)
    transport = Transport(value)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = (
            GeminiVisualReasoner(
                model="gemini-test",
                revision="pinned-v1",
                api_key="test-key",
                external_enabled=True,
                client=client,
            )
            if cloud
            else OllamaQwenVisualReasoner(base_url="http://127.0.0.1:11434", client=client)
        )
        result = await provider.reason(replace(value, external_permitted=cloud))
        assert result.cited_frame_ids == (value.frames[0].frame_id,)
        assert result.provider == provider.identity
        if not cloud:
            assert transport.calls[-1] == "/api/generate"
    await LocalReasoningFrameVerifier().verify(value.frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
@pytest.mark.parametrize(
    "mode", ["timeout", "deadline", "http", "bytes", "json", "foreign", "incomplete"]
)
async def test_safe_reasoner_failures(tmp_path: Path, cloud: bool, mode: str) -> None:
    value = request_value(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(Transport(value, mode))) as client:
        provider = (
            GeminiVisualReasoner(
                model="gemini-test",
                revision="pinned-v1",
                api_key="test-key",
                external_enabled=True,
                client=client,
            )
            if cloud
            else OllamaQwenVisualReasoner(base_url="http://localhost:11434", client=client)
        )
        with pytest.raises(VisualProviderError) as error:
            await provider.reason(replace(value, external_permitted=True))
        assert "private" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["digest", "cpu", "model", "cleanup"])
async def test_local_strict_provider_and_device_pin(tmp_path: Path, mode: str) -> None:
    value = request_value(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(Transport(value, mode))) as client:
        with pytest.raises(VisualProviderError):
            await OllamaQwenVisualReasoner(base_url="http://127.0.0.1:11434", client=client).reason(
                value
            )


@pytest.mark.asyncio
async def test_cloud_privacy_denies_before_file_reads_and_network(tmp_path: Path) -> None:
    value = request_value(tmp_path)
    transport = Transport(value)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        for enabled, permitted in ((False, False), (False, True), (True, False)):
            provider = GeminiVisualReasoner(
                model="gemini-test",
                revision="pinned-v1",
                api_key="test-key",
                external_enabled=enabled,
                client=client,
            )
            with pytest.raises(VisualProviderError, match="external_processing_not_permitted"):
                await provider.reason(replace(value, external_permitted=permitted))
        assert not transport.calls
        with pytest.raises(VisualProviderError):
            await GeminiVisualReasoner(
                model="gemini-test",
                revision="pinned-v1",
                api_key="test-key",
                external_enabled=True,
                client=httpx.AsyncClient(
                    transport=httpx.MockTransport(Transport(value, "revision"))
                ),
            ).reason(replace(value, external_permitted=True))


@pytest.mark.asyncio
async def test_frame_tampering_bounds_and_missing_files(tmp_path: Path) -> None:
    value = request_value(tmp_path)
    for content in (b"changed", b"x" * 4_194_305):
        value.frames[0].path.write_bytes(content)
        with pytest.raises(VisualProviderError):
            await LocalReasoningFrameVerifier().verify(value.frames)
    value.frames[0].path.unlink()
    with pytest.raises(VisualProviderError):
        await LocalReasoningFrameVerifier().verify(value.frames)
    provider = OllamaQwenVisualReasoner(base_url="http://127.0.0.1:11434")
    with pytest.raises(VisualProviderError):
        await provider.reason(replace(value, evidence="x" * 3001))
    other = replace(value.frames[0], frame_id=uuid4())
    with pytest.raises(VisualProviderError):
        await provider.reason(replace(value, frames=(*value.frames, other)))


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "http://example.com",
        "http://127.0.0.1/path",
        "http://u@localhost",
        "http://localhost?q=x",
        "http://localhost#x",
    ],
)
def test_local_origin_is_fail_closed(url: str) -> None:
    with pytest.raises(ValueError):
        OllamaQwenVisualReasoner(base_url=url)


def test_provider_configuration_is_explicit() -> None:
    for revision, timeout_value in (("main", 1), (QWEN_VL_DIGEST, 0), (QWEN_VL_DIGEST, 301)):
        with pytest.raises(ValueError):
            OllamaQwenVisualReasoner(
                base_url="http://localhost", revision=revision, timeout_seconds=timeout_value
            )
    for model, key, timeout_value in (
        ("other", "test", 1),
        ("gemini-test", "", 1),
        ("gemini-test", "key", 301),
    ):
        with pytest.raises(ValueError):
            GeminiVisualReasoner(
                model=model, revision="v1", api_key=key, timeout_seconds=timeout_value
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
async def test_reasoner_busy_has_no_unbounded_queue(tmp_path: Path, cloud: bool) -> None:
    value = replace(request_value(tmp_path), external_permitted=True)
    started, release = asyncio.Event(), asyncio.Event()
    transport = Transport(value)

    async def blocked(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return transport(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
        provider = (
            GeminiVisualReasoner(
                model="gemini-test",
                revision="pinned-v1",
                api_key="test-key",
                external_enabled=True,
                client=client,
            )
            if cloud
            else OllamaQwenVisualReasoner(base_url="http://localhost", client=client)
        )
        first = asyncio.create_task(provider.reason(value))
        await started.wait()
        with pytest.raises(VisualProviderError, match="visual_busy"):
            await provider.reason(value)
        release.set()
        await first


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
async def test_owned_transport_is_closed(tmp_path: Path, cloud: bool) -> None:
    value = replace(request_value(tmp_path), external_permitted=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(Transport(value)))
    with patch("galaxy_frog.adapters.vision.reasoning.httpx.AsyncClient", return_value=client):
        provider = (
            GeminiVisualReasoner(
                model="gemini-test", revision="pinned-v1", api_key="test-key", external_enabled=True
            )
            if cloud
            else OllamaQwenVisualReasoner(base_url="http://localhost")
        )
        await provider.reason(value)
    assert client.is_closed
