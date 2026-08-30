"""Behavior tests for application-scoped FastAPI dependencies."""

from typing import cast

import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from galaxy_frog.api import dependencies
from galaxy_frog.api.dependencies import get_database_probe


def make_request(engine: AsyncEngine | None) -> Request:
    """Build the smallest HTTP request needed by the dependency."""

    application = FastAPI()
    application.state.database_engine = engine
    return Request({"type": "http", "app": application, "headers": []})


def test_database_probe_dependency_is_absent_without_engine() -> None:
    assert get_database_probe(make_request(None)) is None


@pytest.mark.asyncio
async def test_database_probe_dependency_closes_over_application_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    observed: list[AsyncEngine] = []

    async def fake_probe(received: AsyncEngine) -> None:
        observed.append(received)

    monkeypatch.setattr(dependencies, "probe_database", fake_probe)

    probe = get_database_probe(make_request(engine))

    assert probe is not None
    await probe()
    assert observed == [engine]
