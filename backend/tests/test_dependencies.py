"""Behavior tests for application-scoped FastAPI dependencies."""

from typing import cast

import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from galaxy_frog.api import dependencies
from galaxy_frog.api.dependencies import (
    get_database_probe,
    get_ingestion_repository,
    get_video_repository,
)
from galaxy_frog.api.errors import ApiError
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository


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


@pytest.mark.asyncio
async def test_video_repository_requires_database_engine() -> None:
    generator = get_video_repository(make_request(None))

    with pytest.raises(ApiError, match="database dependency"):
        await anext(generator)


@pytest.mark.asyncio
async def test_ingestion_repository_requires_database_engine() -> None:
    generator = get_ingestion_repository(make_request(None))

    with pytest.raises(ApiError, match="database dependency"):
        await anext(generator)


@pytest.mark.asyncio
async def test_video_repository_owns_a_request_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    session = cast(AsyncSession, object())
    exited = False

    class SessionContext:
        async def __aenter__(self) -> AsyncSession:
            return session

        async def __aexit__(self, *_args: object) -> None:
            nonlocal exited
            exited = True

    class SessionFactory:
        def __call__(self) -> SessionContext:
            return SessionContext()

    def session_factory(*_args: object, **_kwargs: object) -> SessionFactory:
        return SessionFactory()

    monkeypatch.setattr(dependencies, "async_sessionmaker", session_factory)
    generator = get_video_repository(make_request(engine))

    repository = await anext(generator)
    assert repository.session is session
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert exited is True


@pytest.mark.asyncio
async def test_ingestion_repository_owns_a_request_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = cast(AsyncEngine, object())
    session = cast(AsyncSession, object())
    exited = False

    class SessionContext:
        async def __aenter__(self) -> AsyncSession:
            return session

        async def __aexit__(self, *_args: object) -> None:
            nonlocal exited
            exited = True

    class SessionFactory:
        def __call__(self) -> SessionContext:
            return SessionContext()

    def session_factory(*_args: object, **_kwargs: object) -> SessionFactory:
        return SessionFactory()

    monkeypatch.setattr(dependencies, "async_sessionmaker", session_factory)
    generator = get_ingestion_repository(make_request(engine))

    repository = await anext(generator)
    assert isinstance(repository, PostgresIngestionRepository)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert exited is True
