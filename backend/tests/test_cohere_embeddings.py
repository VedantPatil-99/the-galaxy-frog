"""Exercise Cohere's text-only Embed v4 contract with a local HTTP fixture."""

import json
from math import hypot
from typing import Literal

import httpx
import pytest
from pydantic import SecretStr

from galaxy_frog.adapters.embeddings.cohere import CohereEmbeddingProvider
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure


@pytest.mark.parametrize(
    ("input_type", "texts"),
    [("search_document", ("one", "two")), ("search_query", ("café",))],
)
@pytest.mark.asyncio
async def test_cohere_uses_v4_and_the_selected_retrieval_role(
    input_type: Literal["search_document", "search_query"], texts: tuple[str, ...]
) -> None:
    sent = 0

    def response(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        assert request.url == httpx.URL("https://api.cohere.com/v2/embed")
        assert request.headers["Authorization"] == "Bearer fixture"
        assert json.loads(request.content) == {
            "model": "embed-v4.0",
            "texts": list(texts),
            "input_type": input_type,
            "embedding_types": ["float"],
            "output_dimension": 1536,
            "truncate": "NONE",
        }
        vectors = [[3, 4] + [0] * 1534, [-4, 3] + [0] * 1534]
        return httpx.Response(200, json={"embeddings": {"float": vectors[: len(texts)]}})

    provider = CohereEmbeddingProvider(
        SecretStr("fixture"), input_type, transport=httpx.MockTransport(response)
    )
    vectors = await provider.embed(texts)
    assert sent == 1 and len(vectors) == len(texts)
    assert vectors[0][:2] == (0.6, 0.8)
    assert all(hypot(*vector) == 1 for vector in vectors)


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (200, {"embeddings": {"float": [[1]]}}, ProviderCode.INVALID_OUTPUT),
        (200, {"embeddings": {"float": [["1"] + [0] * 1535]}}, ProviderCode.INVALID_OUTPUT),
        (200, {"embeddings": {"float": [[0] * 1536]}}, ProviderCode.INVALID_OUTPUT),
        (200, {"embeddings": {"float": [[1] + [0] * 1535] * 2}}, ProviderCode.INVALID_OUTPUT),
        (400, {"message": "private"}, ProviderCode.INVALID_INPUT),
        (401, {"message": "private"}, ProviderCode.AUTH_FAILED),
        (404, {"message": "private"}, ProviderCode.SETUP_REQUIRED),
        (429, {"message": "private"}, ProviderCode.RATE_LIMITED),
        (503, {"message": "private"}, ProviderCode.OUTCOME_UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_cohere_rejects_malformed_vectors_and_hides_provider_errors(
    status: int, body: dict[str, object], code: ProviderCode
) -> None:
    provider = CohereEmbeddingProvider(
        SecretStr("fixture"),
        "search_query",
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(ProviderFailure) as caught:
        await provider.embed(("one",))
    assert caught.value.code == code
    assert "private" not in str(caught.value)
