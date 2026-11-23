"""Check the documented synchronous batch contract without contacting Gemini."""

import json
from math import hypot

import httpx
import pytest
from pydantic import SecretStr

from galaxy_frog.adapters.embeddings import gemini
from galaxy_frog.adapters.embeddings.gemini import (
    GEMINI_DOCUMENT_PREFIX,
    GEMINI_QUERY_PREFIX,
    GeminiEmbeddingProvider,
)
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure


@pytest.mark.asyncio
async def test_gemini_batch_embeds_each_text_separately_and_normalizes() -> None:
    texts = (GEMINI_DOCUMENT_PREFIX + "test source", GEMINI_QUERY_PREFIX + "query terms")
    sent = 0

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        assert request.url == httpx.URL(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents"
        )
        assert request.headers["x-goog-api-key"] == "test-key"
        payload = json.loads(request.content)
        assert payload == {
            "requests": [
                {
                    "model": "models/gemini-embedding-2",
                    "outputDimensionality": 1536,
                    "content": {"parts": [{"text": value}]},
                }
                for value in texts
            ]
        }
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [2] + [0] * 1535}, {"values": [0, -3] + [0] * 1534}]},
        )

    provider = GeminiEmbeddingProvider(
        SecretStr("test-key"), transport=httpx.MockTransport(response)
    )
    vectors = await provider.embed(texts)
    assert len(vectors) == 2 and sent == 1
    assert hypot(*vectors[0]) == 1 and vectors[1][1] == -1


@pytest.mark.parametrize(
    ("status", "code"), [(429, ProviderCode.RATE_LIMITED), (503, ProviderCode.OUTCOME_UNKNOWN)]
)
@pytest.mark.asyncio
async def test_gemini_errors_are_safe(status: int, code: ProviderCode) -> None:
    def response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="private upstream body")

    with pytest.raises(ProviderFailure) as caught:
        await GeminiEmbeddingProvider(
            SecretStr("fixture"), transport=httpx.MockTransport(response)
        ).embed(("one",))
    assert caught.value.code == code and "private upstream body" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape", ["wrong-dimension", "extra-vector", "zero-vector", "too-large", "network"]
)
async def test_gemini_rejects_invalid_or_ambiguous_results(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def response(request: httpx.Request) -> httpx.Response:
        if shape == "network":
            raise httpx.ReadTimeout("fixture", request=request)
        if shape == "too-large":
            return httpx.Response(200, content=b"x" * 40)
        values = [1.0] + [0.0] * 1535
        if shape == "wrong-dimension":
            values = [1.0]
        if shape == "zero-vector":
            values[0] = 0.0
        return httpx.Response(
            200, json={"embeddings": [{"values": values}] * (2 if shape == "extra-vector" else 1)}
        )

    if shape == "too-large":
        monkeypatch.setattr(gemini, "_MAX_RESPONSE_BYTES", 30)
    with pytest.raises(ProviderFailure) as caught:
        await GeminiEmbeddingProvider(
            SecretStr("fixture"), transport=httpx.MockTransport(response)
        ).embed(("one",))
    assert caught.value.code == (
        ProviderCode.OUTCOME_UNKNOWN if shape == "network" else ProviderCode.INVALID_OUTPUT
    )
