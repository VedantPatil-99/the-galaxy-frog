"""Gemini Embedding 2 text retrieval through the documented single HTTP batch endpoint."""

from typing import Annotated

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from galaxy_frog.adapters.embeddings.cloud import (
    MAX_EMBEDDING_RESPONSE_BYTES,
    normalize_embeddings,
    post_embedding,
)
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
_MAX_RESPONSE_BYTES = MAX_EMBEDDING_RESPONSE_BYTES


class _Embedding(BaseModel):
    values: tuple[Annotated[float, Field(strict=True, allow_inf_nan=False)], ...] = Field(
        min_length=1536, max_length=1536
    )


class _Batch(BaseModel):
    embeddings: tuple[_Embedding, ...] = Field(min_length=1, max_length=16)


class GeminiEmbeddingProvider:
    spec = GEMINI_EMBEDDING_SPEC

    def __init__(
        self, key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = key
        self._transport = transport

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        payload: dict[str, object] = {
            "requests": [
                {
                    "model": f"models/{self.spec.model}",
                    "outputDimensionality": self.spec.dimension,
                    "content": {"parts": [{"text": content}]},
                }
                for content in texts
            ]
        }
        body = await post_embedding(
            _ENDPOINT,
            payload,
            {"x-goog-api-key": self._key.get_secret_value()},
            self._transport,
            max_bytes=_MAX_RESPONSE_BYTES,
        )
        try:
            result = _Batch.model_validate_json(body)
        except ValidationError:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from None
        return normalize_embeddings(tuple(item.values for item in result.embeddings), len(texts))
