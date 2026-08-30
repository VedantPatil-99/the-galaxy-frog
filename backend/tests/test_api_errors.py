"""Behavior tests for canonical API error normalization."""

from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient, Response

from galaxy_frog.api.app import create_app
from galaxy_frog.api.errors import correlation_id_from_request
from galaxy_frog.config import Settings


def test_missing_request_state_generates_a_correlation_id() -> None:
    application = create_app(Settings(app_env="test", database_url=None))
    bare_request = Request({"type": "http", "app": application, "headers": []})
    UUID(correlation_id_from_request(bare_request))


async def request(application: FastAPI, path: str) -> Response:
    """Issue one request while retaining canonical 500 responses."""

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_not_found_uses_the_error_envelope() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    response = await request(application, "/missing")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Not Found",
        "correlation_id": response.headers["X-Correlation-ID"],
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_validation_error_omits_submitted_values() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    async def number(value: int) -> dict[str, int]:
        return {"value": value}

    application.add_api_route("/number", number, methods=["GET"])
    response = await request(application, "/number?value=private-value")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_INPUT"
    assert error["details"]["errors"][0]["location"] == "query.value"
    assert "private-value" not in response.text


@pytest.mark.asyncio
async def test_framework_error_uses_generic_code_and_retry_guidance() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    async def maintenance() -> None:
        raise HTTPException(status_code=503, detail="Maintenance in progress.")

    application.add_api_route("/maintenance", maintenance, methods=["GET"])
    response = await request(application, "/maintenance")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "HTTP_ERROR"
    assert response.json()["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_framework_error_does_not_serialize_non_string_detail() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    async def unsafe_detail() -> None:
        raise HTTPException(status_code=400, detail={"private": "value"})

    application.add_api_route("/unsafe-detail", unsafe_detail, methods=["GET"])
    response = await request(application, "/unsafe-detail")

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "The request could not be completed."
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_unexpected_error_is_safe_and_correlated() -> None:
    application = create_app(Settings(app_env="test", database_url=None))

    async def unexpected() -> None:
        raise RuntimeError("private implementation detail")

    application.add_api_route("/unexpected", unexpected, methods=["GET"])
    response = await request(application, "/unexpected")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred.",
        "correlation_id": response.headers["X-Correlation-ID"],
        "retryable": False,
        "suggested_action": "Retry later or contact support with the correlation ID.",
    }
    assert "private implementation detail" not in response.text
