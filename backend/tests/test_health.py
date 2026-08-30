"""Behavior tests for liveness and database-aware readiness."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_database_probe
from galaxy_frog.config import Settings


async def request(application: FastAPI, path: str) -> Response:
    """Issue one in-process request to a FastAPI application."""

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_liveness_does_not_require_database_configuration() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    response = await request(application, "/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_missing_database_configuration() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    response = await request(application, "/health/ready")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "DATABASE_UNAVAILABLE",
        "message": "The database dependency is not configured.",
        "correlation_id": response.headers["X-Correlation-ID"],
        "retryable": False,
        "suggested_action": "Configure DATABASE_URL before starting the API.",
        "details": {"dependency": "postgresql"},
    }


@pytest.mark.asyncio
async def test_readiness_reports_healthy_database() -> None:
    application = create_app(Settings(app_env="test", database_url=None))
    called = False

    async def successful_probe() -> None:
        nonlocal called
        called = True

    application.dependency_overrides[get_database_probe] = lambda: successful_probe

    response = await request(application, "/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dependencies": {"database": "ready"}}
    assert called is True


@pytest.mark.asyncio
async def test_readiness_reports_unavailable_database() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    async def failing_probe() -> None:
        raise OSError("connection refused")

    application.dependency_overrides[get_database_probe] = lambda: failing_probe

    response = await request(application, "/health/ready")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "DATABASE_UNAVAILABLE"
    assert error["message"] == "The database dependency is unavailable."
    assert error["retryable"] is True
    assert error["correlation_id"] == response.headers["X-Correlation-ID"]
    assert "connection refused" not in response.text
