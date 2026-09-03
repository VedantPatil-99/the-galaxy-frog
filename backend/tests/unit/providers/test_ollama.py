"""Tests for strict Ollama embedding and generation response handling."""

# pyright: reportPrivateUsage=false

import json
from math import isclose

import httpx
import pytest

from galaxy_frog.adapters.embeddings.ollama import (
    EmbeddingProviderError,
    OllamaBgeM3EmbeddingProvider,
)
from galaxy_frog.adapters.generation.ollama import (
    GenerationProviderError,
    OllamaGenerationProvider,
)
from galaxy_frog.domain.generation import AnswerConfidence
from galaxy_frog.domain.retrieval import RetrievedEvidence
from galaxy_frog.domain.transcripts import RetrievalUnit


def mock_client(payload: object, *, status: int = 200) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "localhost"
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_normalizes_bge_m3_embeddings() -> None:
    vector = [1.0] * 1024
    client = mock_client({"embeddings": [vector]})
    provider = OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434/", client=client)

    result = await provider.embed(("hello",))
    await client.aclose()

    assert provider.spec.dimension == 1024
    assert len(result[0]) == 1024
    assert isclose(sum(value * value for value in result[0]), 1.0)


@pytest.mark.asyncio
async def test_embedding_provider_handles_empty_input_and_rejects_invalid_values() -> None:
    provider = OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434")
    assert await provider.embed(()) == ()
    with pytest.raises(EmbeddingProviderError):
        await provider.embed((" ",))

    invalid_payloads: list[object] = [
        {},
        {"embeddings": []},
        {"embeddings": [[1.0]]},
        {"embeddings": [[float("nan")] * 1024]},
        {"embeddings": [[0.0] * 1024]},
    ]
    for payload in invalid_payloads:
        client = mock_client(payload)
        invalid = OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434", client=client)
        with pytest.raises(EmbeddingProviderError):
            await invalid.embed(("text",))
        await client.aclose()

    client = mock_client([])
    invalid = OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434", client=client)
    with pytest.raises(EmbeddingProviderError):
        await invalid.embed(("text",))
    await client.aclose()


@pytest.mark.asyncio
async def test_embedding_provider_normalizes_http_failures() -> None:
    client = mock_client({}, status=503)
    provider = OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434", client=client)

    with pytest.raises(EmbeddingProviderError):
        await provider.embed(("text",))

    await client.aclose()


@pytest.mark.asyncio
async def test_embedding_provider_owns_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = mock_client({"embeddings": [[1.0] * 1024]})

    def client_factory(**_kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    result = await OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434").embed(("text",))

    assert len(result[0]) == 1024
    assert client.is_closed


@pytest.mark.parametrize(
    "value",
    ["not-a-vector", ["x", *([0.0] * 1023)], [float("nan"), *([0.0] * 1023)]],
)
def test_embedding_vector_validation_branches(value: object) -> None:
    provider = OllamaBgeM3EmbeddingProvider(base_url="http://localhost:11434")
    with pytest.raises(EmbeddingProviderError):
        provider._normalize_vector(value)


def retrieved_evidence() -> RetrievedEvidence:
    from uuid import uuid4

    return RetrievedEvidence(
        video_id=uuid4(),
        unit=RetrievalUnit(
            unit_id="a" * 64,
            start_ms=0,
            end_ms=1000,
            text="Exact cited text.",
            cue_ids=("b" * 64,),
        ),
        score=0.8,
    )


@pytest.mark.asyncio
async def test_generation_provider_parses_structured_json() -> None:
    body = {
        "answer": "Grounded answer.",
        "confidence": "high",
        "citations": [{"retrieval_unit_id": "a" * 64, "quote": "Exact cited text."}],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        request_body = json.loads(request.content)
        assert request_body["model"] == "qwen3:4b"
        assert request_body["stream"] is False
        assert request_body["format"] == "json"
        assert request_body["think"] is False
        assert request_body["options"] == {"temperature": 0}
        return httpx.Response(200, json={"response": json.dumps(body)})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaGenerationProvider(
        base_url="http://localhost:11434/", model="qwen3:4b", client=client
    )

    draft = await provider.generate("Question?", (retrieved_evidence(),))
    await client.aclose()

    assert draft.answer == "Grounded answer."
    assert draft.confidence is AnswerConfidence.HIGH
    assert draft.citations[0].retrieval_unit_id == "a" * 64


@pytest.mark.asyncio
async def test_generation_provider_owns_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    body: dict[str, object] = {"answer": "Answer.", "confidence": "low", "citations": []}
    client = mock_client({"response": json.dumps(body)})

    def client_factory(**_kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    draft = await OllamaGenerationProvider(
        base_url="http://localhost:11434", model="qwen"
    ).generate("Question?", (retrieved_evidence(),))

    assert draft.answer == "Answer."
    assert client.is_closed


@pytest.mark.asyncio
async def test_generation_provider_rejects_non_object_payload() -> None:
    client = mock_client([])
    provider = OllamaGenerationProvider(
        base_url="http://localhost:11434", model="qwen", client=client
    )

    with pytest.raises(GenerationProviderError):
        await provider.generate("Question?", (retrieved_evidence(),))

    await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"response": "not json"},
        {"response": json.dumps([])},
        {"response": json.dumps({"answer": "", "confidence": "low", "citations": []})},
        {"response": json.dumps({"answer": "x", "confidence": "low", "citations": {}})},
        {"response": json.dumps({"answer": "x", "confidence": "low", "citations": [1]})},
        {"response": json.dumps({"answer": "x", "confidence": "low", "citations": [{"quote": 1}]})},
        {"response": json.dumps({"answer": "x", "confidence": "unknown", "citations": []})},
    ],
)
@pytest.mark.asyncio
async def test_generation_provider_rejects_invalid_documents(payload: object) -> None:
    client = mock_client(payload)
    provider = OllamaGenerationProvider(
        base_url="http://localhost:11434", model="qwen3:4b", client=client
    )

    with pytest.raises(GenerationProviderError):
        await provider.generate("Question", (retrieved_evidence(),))

    await client.aclose()


@pytest.mark.asyncio
async def test_generation_provider_normalizes_http_failures() -> None:
    client = mock_client({}, status=500)
    provider = OllamaGenerationProvider(
        base_url="http://localhost:11434", model="qwen3:4b", client=client
    )

    with pytest.raises(GenerationProviderError):
        await provider.generate("Question", (retrieved_evidence(),))

    await client.aclose()
