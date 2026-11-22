"""Gemini Embedding 2 text retrieval through the documented single HTTP batch endpoint."""

import asyncio
from math import hypot, isfinite
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec

GEMINI_EMBEDDING_SPEC = EmbeddingCollectionSpec(
    "gemini", "gemini-embedding-2", "gemini-embedding-2-asymmetric-search-v1", 1536, "l2"
)
GEMINI_DOCUMENT_PREFIX = "title: none | text: "
GEMINI_QUERY_PREFIX = "task: search result | query: "
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents"
)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _Embedding(BaseModel):
    values: tuple[Annotated[float, Field(strict=True, allow_inf_nan=False)], ...] = Field(
        min_length=1536, max_length=1536
    )


class _Batch(BaseModel):
    embeddings: tuple[_Embedding, ...] = Field(min_length=1, max_length=16)


def _check_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    code = {
        400: ProviderCode.INVALID_INPUT,
        401: ProviderCode.AUTH_FAILED,
        403: ProviderCode.AUTH_FAILED,
        404: ProviderCode.SETUP_REQUIRED,
        429: ProviderCode.RATE_LIMITED,
    }.get(response.status_code, ProviderCode.OUTCOME_UNKNOWN)
    raise ProviderFailure(code)


class GeminiEmbeddingProvider:
    spec = GEMINI_EMBEDDING_SPEC

    def __init__(
        self, key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = key
        self._transport = transport

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        payload = {
            "requests": [
                {
                    "model": f"models/{self.spec.model}",
                    "outputDimensionality": self.spec.dimension,
                    "content": {"parts": [{"text": content}]},
                }
                for content in texts
            ]
        }
        try:
            async with (
                asyncio.timeout(60),
                httpx.AsyncClient(
                    timeout=60, trust_env=False, follow_redirects=False, transport=self._transport
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    _ENDPOINT,
                    json=payload,
                    headers={"x-goog-api-key": self._key.get_secret_value()},
                ) as response:
                    _check_status(response)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16_384):
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
                result = _Batch.model_validate_json(body)
        except httpx.HTTPError, TimeoutError:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from None
        except ValidationError:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from None
        if len(result.embeddings) != len(texts):
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
        vectors: list[tuple[float, ...]] = []
        for item in result.embeddings:
            magnitude = hypot(*item.values)
            if not isfinite(magnitude) or magnitude == 0:
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            vectors.append(tuple(value / magnitude for value in item.values))
        return tuple(vectors)
