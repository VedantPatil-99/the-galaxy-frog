"""Behavior tests for the FastAPI application foundation."""

from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient, Response

from galaxy_frog.api.app import app, create_app
from galaxy_frog.config import Settings


def test_create_app_uses_validated_settings() -> None:
    application = create_app(Settings(app_env="production"))

    assert application.title == "Galaxy Frog API"
    assert application.version == "0.1.0"
    assert application.debug is False


@pytest.mark.asyncio
async def test_docs_and_openapi_are_available() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        docs_response: Response = await client.get("/docs")
        schema_response: Response = await client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert schema_response.status_code == 200

    schema = cast(dict[str, object], schema_response.json())
    info = cast(dict[str, object], schema["info"])

    assert info["title"] == "Galaxy Frog API"
    assert info["version"] == "0.1.0"
    assert schema["paths"] == {}
