"""Behavior tests for the FastAPI application foundation."""

from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from galaxy_frog.api import app as app_module
from galaxy_frog.api.app import app, create_app
from galaxy_frog.config import Settings


@pytest.mark.asyncio
async def test_create_app_uses_validated_settings_without_database() -> None:
    application = create_app(Settings(app_env="development", database_url=None))

    assert application.title == "Galaxy Frog API"
    assert application.version == "0.1.0"
    assert application.debug is False
    assert application.state.database_engine is None

    async with application.router.lifespan_context(application):
        assert application.state.database_engine is None


@pytest.mark.asyncio
async def test_lifespan_owns_and_disposes_database_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisposableEngine:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    fake_engine = DisposableEngine()

    def fake_create_database_engine(_settings: Settings) -> AsyncEngine:
        return cast(AsyncEngine, fake_engine)

    monkeypatch.setattr(
        app_module,
        "create_database_engine",
        fake_create_database_engine,
    )
    application = create_app(
        Settings(
            app_env="test",
            database_url=SecretStr(
                "postgresql+asyncpg://galaxy_frog:secret@localhost:5432/galaxy_frog"
            ),
        )
    )

    async with application.router.lifespan_context(application):
        assert application.state.database_engine is fake_engine

    assert fake_engine.disposed is True


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
    paths = cast(dict[str, object], schema["paths"])
    assert set(paths) == {
        "/health/live",
        "/health/ready",
        "/v1/videos/import",
        "/v1/videos/{video_id}",
        "/v1/videos/{video_id}/questions",
        "/v1/videos/{video_id}/transcript",
    }

    ready_path = cast(dict[str, object], paths["/health/ready"])
    ready_operation = cast(dict[str, object], ready_path["get"])
    ready_responses = cast(dict[str, object], ready_operation["responses"])
    unavailable_response = cast(dict[str, object], ready_responses["503"])
    unavailable_content = cast(dict[str, object], unavailable_response["content"])
    unavailable_json = cast(dict[str, object], unavailable_content["application/json"])
    unavailable_schema = cast(dict[str, object], unavailable_json["schema"])

    assert unavailable_schema["$ref"] == "#/components/schemas/ErrorResponse"
