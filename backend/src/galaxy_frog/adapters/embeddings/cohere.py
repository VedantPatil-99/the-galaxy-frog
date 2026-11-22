"""Cohere Embed v4 text retrieval through the v2 Embed API."""

from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

from galaxy_frog.adapters.embeddings.cloud import normalize_embeddings, post_embedding
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec

COHERE_EMBEDDING_SPEC = EmbeddingCollectionSpec(
    "cohere", "embed-v4.0", "embed-v4.0-v2-asymmetric-text-v1", 1536, "l2"
)
_ENDPOINT = "https://api.cohere.com/v2/embed"
_Vector = Annotated[
    tuple[Annotated[float, Field(strict=True, allow_inf_nan=False)], ...],
    Field(min_length=1536, max_length=1536),
]


class _Embeddings(BaseModel):
    float_vectors: tuple[_Vector, ...] = Field(alias="float", min_length=1, max_length=16)


class _Batch(BaseModel):
    embeddings: _Embeddings


class CohereEmbeddingProvider:
    spec = COHERE_EMBEDDING_SPEC

    def __init__(
        self,
        key: SecretStr,
        input_type: Literal["search_document", "search_query"],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = key
        self._input_type = input_type
        self._transport = transport

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        body = await post_embedding(
            _ENDPOINT,
            {
                "model": self.spec.model,
                "texts": texts,
                "input_type": self._input_type,
                "embedding_types": ["float"],
                "output_dimension": self.spec.dimension,
                "truncate": "NONE",
            },
            {"Authorization": f"Bearer {self._key.get_secret_value()}"},
            self._transport,
        )
        try:
            result = _Batch.model_validate_json(body)
        except ValidationError:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from None
        return normalize_embeddings(result.embeddings.float_vectors, len(texts))
