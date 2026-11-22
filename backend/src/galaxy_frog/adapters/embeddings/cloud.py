"""Bounded transport and vector normalization for cloud embedding adapters."""

import asyncio
from math import hypot, isfinite

import httpx

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure

MAX_EMBEDDING_RESPONSE_BYTES = 2 * 1024 * 1024


async def post_embedding(
    endpoint: str,
    payload: dict[str, object],
    headers: dict[str, str],
    transport: httpx.AsyncBaseTransport | None,
    *,
    max_bytes: int = MAX_EMBEDDING_RESPONSE_BYTES,
) -> bytearray:
    try:
        async with (
            asyncio.timeout(60),
            httpx.AsyncClient(
                timeout=60, trust_env=False, follow_redirects=False, transport=transport
            ) as client,
            client.stream("POST", endpoint, json=payload, headers=headers) as response,
        ):
            if not response.is_success:
                code = {
                    400: ProviderCode.INVALID_INPUT,
                    401: ProviderCode.AUTH_FAILED,
                    403: ProviderCode.AUTH_FAILED,
                    404: ProviderCode.SETUP_REQUIRED,
                    429: ProviderCode.RATE_LIMITED,
                }.get(response.status_code, ProviderCode.OUTCOME_UNKNOWN)
                raise ProviderFailure(code)
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16_384):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            return body
    except httpx.HTTPError, TimeoutError:
        raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from None


def normalize_embeddings(
    embeddings: tuple[tuple[float, ...], ...], expected: int
) -> tuple[tuple[float, ...], ...]:
    if len(embeddings) != expected:
        raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
    vectors: list[tuple[float, ...]] = []
    for vector in embeddings:
        magnitude = hypot(*vector)
        if not isfinite(magnitude) or magnitude == 0:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
        vectors.append(tuple(value / magnitude for value in vector))
    return tuple(vectors)
