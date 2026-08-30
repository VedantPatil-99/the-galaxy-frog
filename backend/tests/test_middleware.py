"""Behavior tests for correlation-ID validation and propagation."""

from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from galaxy_frog.api.app import create_app
from galaxy_frog.api.middleware import resolve_correlation_id
from galaxy_frog.config import Settings


def test_correlation_id_validation() -> None:
    assert resolve_correlation_id(" request-123 ") == "request-123"

    for candidate in (None, "", "contains spaces", "x" * 129):
        UUID(resolve_correlation_id(candidate))


@pytest.mark.asyncio
async def test_safe_caller_correlation_id_is_preserved() -> None:
    application = create_app(Settings(app_env="test", database_url=None))
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health/live",
            headers={"X-Correlation-ID": "caller.request-123"},
        )

    assert response.headers["X-Correlation-ID"] == "caller.request-123"
