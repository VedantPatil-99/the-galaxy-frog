"""BGE-M3 embeddings through a user-managed local Ollama service."""

from collections.abc import Mapping
from math import fsum, isfinite, sqrt
from typing import cast

import httpx

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec


class EmbeddingProviderError(RuntimeError):
    """Safe failure raised when the configured embedder cannot produce valid vectors."""


class OllamaBgeM3EmbeddingProvider:
    """Generate normalized 1,024-dimensional BGE-M3 vectors through Ollama."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "bge-m3",
        revision: str = "ollama",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.spec = EmbeddingCollectionSpec(
            provider="ollama",
            model=model,
            revision=revision,
            dimension=1024,
            normalization="l2",
        )
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        if any(not text.strip() for text in texts):
            raise EmbeddingProviderError("Embedding input must not be empty.")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60)
        try:
            response = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self.spec.model, "input": list(texts)},
            )
            response.raise_for_status()
            payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingProviderError(
                "The configured Ollama BGE-M3 embedding provider is unavailable."
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
        if not isinstance(payload, Mapping):
            raise EmbeddingProviderError("Ollama returned an invalid embedding response.")
        payload_mapping = cast(Mapping[str, object], payload)
        embeddings_value = payload_mapping.get("embeddings")
        if not isinstance(embeddings_value, list):
            raise EmbeddingProviderError("Ollama returned an invalid embedding response.")
        embeddings = cast(list[object], embeddings_value)
        if len(embeddings) != len(texts):
            raise EmbeddingProviderError("Ollama returned an unexpected number of embeddings.")
        return tuple(self._normalize_vector(value) for value in embeddings)

    def _normalize_vector(self, value: object) -> tuple[float, ...]:
        if not isinstance(value, list):
            raise EmbeddingProviderError("BGE-M3 returned an incompatible embedding dimension.")
        items = cast(list[object], value)
        if len(items) != self.spec.dimension:
            raise EmbeddingProviderError("BGE-M3 returned an incompatible embedding dimension.")
        values: list[float] = []
        for item in items:
            if not isinstance(item, int | float) or not isfinite(item):
                raise EmbeddingProviderError("BGE-M3 returned a non-finite embedding.")
            values.append(float(item))
        vector = tuple(values)
        magnitude = sqrt(fsum(item * item for item in vector))
        if magnitude == 0:
            raise EmbeddingProviderError("BGE-M3 returned a zero embedding.")
        return tuple(item / magnitude for item in vector)
