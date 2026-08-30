"""Request middleware for stable correlation identifiers."""

import re
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response

CORRELATION_ID_HEADER = "X-Correlation-ID"
_CORRELATION_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")


def resolve_correlation_id(candidate: str | None) -> str:
    """Preserve a safe caller identifier or generate a UUID."""

    if candidate is not None:
        normalized = candidate.strip()
        if _CORRELATION_ID_PATTERN.fullmatch(normalized):
            return normalized

    return str(uuid4())


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach one correlation identifier to request state and response headers."""

    correlation_id = resolve_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
    request.state.correlation_id = correlation_id

    response = await call_next(request)
    response.headers[CORRELATION_ID_HEADER] = correlation_id
    return response
