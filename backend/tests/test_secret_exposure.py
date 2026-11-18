"""Synthetic secrets must not reach public responses, logs, or error tracebacks."""

import logging
import traceback
from typing import Annotated

import pytest
from fastapi import HTTPException, Query
from httpx import ASGITransport, AsyncClient
from pydantic import AfterValidator, ValidationError

from galaxy_frog.api.app import create_app
from galaxy_frog.config import Settings

SECRET = "synthetic-private-credential-for-regression"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unexpected", "framework", "validation"])
async def test_api_errors_hide_secret_messages_and_causes(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]

    async def fail() -> None:
        if failure == "framework":
            raise HTTPException(status_code=503, detail=SECRET)
        try:
            raise ValueError(SECRET)
        except ValueError as exc:
            raise RuntimeError("Provider failure: " + SECRET) from exc

    def invalid(_value: str) -> str:
        raise ValueError("Invalid credential " + SECRET)

    async def validate(value: Annotated[str, AfterValidator(invalid), Query()]) -> None:
        pass

    app.add_api_route("/fail", fail)
    app.add_api_route("/validate", validate)
    with caplog.at_level(logging.ERROR):
        # The exception must also be contained from the ASGI server, which would
        # otherwise log the original chained exception even after sending a 500.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/validate?value=sample" if failure == "validation" else "/fail"
            )
    assert response.status_code == (
        422 if failure == "validation" else 503 if failure == "framework" else 500
    )
    assert SECRET not in response.text
    assert SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    if failure == "unexpected":
        record = next(r for r in caplog.records if r.message == "Unhandled API error")
        assert record.correlation_id == response.headers["X-Correlation-ID"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "field,value",
    [
        ("llama_cloud_base_url", f"https://user:{SECRET}@api.cloud.llamaindex.ai"),
        ("ollama_base_url", f"http://user:{SECRET}@127.0.0.1:11434"),
        ("database_url", f"https://user:{SECRET}@database.invalid"),
    ],
)
def test_invalid_settings_do_not_echo_credentials(field: str, value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **{field: value})  # type: ignore[call-arg, arg-type]
    rendered = "".join(traceback.format_exception(caught.value))
    assert SECRET not in rendered
