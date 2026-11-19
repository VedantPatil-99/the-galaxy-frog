"""Missing jobs and optional capabilities fail closed at worker admission."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import ProviderEntry
from galaxy_frog.config import Settings
from galaxy_frog.db import ingestion_provider_routing as module
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.domain.providers import (
    Capability,
    ProviderMode,
    ProviderProfile,
    ProviderProfileVersion,
)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("missing_job", ProviderCode.UNAVAILABLE),
        ("missing_profile", ProviderCode.INCOMPATIBLE),
        ("unselected_ocr", ProviderCode.NOT_SELECTED),
        ("inherited_embedding", None),
    ],
)
@pytest.mark.asyncio
async def test_ingestion_route_recovery(
    monkeypatch: pytest.MonkeyPatch, case: str, expected: ProviderCode | None
) -> None:
    sessions = async_sessionmaker(class_=AsyncSession)
    monkeypatch.setattr(module, "async_sessionmaker", Mock(return_value=sessions))
    monkeypatch.setattr(
        AsyncSession,
        "get",
        AsyncMock(
            return_value=None
            if case == "missing_job"
            else SimpleNamespace(external_id="fixture", provider_selection=None)
        ),
    )
    version = ProviderProfileVersion(
        profile_id="local",
        version=1,
        policy=ProviderProfile(mode=ProviderMode.MANUAL, candidates={}),
    )
    monkeypatch.setattr(
        module.PostgresProviderProfiles,
        "get",
        AsyncMock(return_value=None if case == "missing_profile" else version),
    )
    runtime = AsyncMock(spec=PostgresProviderRuntime)

    @asynccontextmanager
    async def admit(_entry: ProviderEntry) -> AsyncGenerator[None]:
        yield

    runtime.admit = admit
    monkeypatch.setattr(module, "PostgresProviderRuntime", Mock(return_value=runtime))
    action = AsyncMock(return_value="vectors")
    capability = Capability.OCR if case == "unselected_ocr" else Capability.TEXT_EMBEDDING
    routing = module.IngestionProviderRouting(
        cast(AsyncEngine, object()), Settings(), "test-worker"
    )
    if expected:
        with pytest.raises(ProviderFailure, match=expected.value):
            await routing.execute(
                uuid4(), capability, {"ollama-embedding": action}, timeout_seconds=1
            )
        action.assert_not_awaited()
    else:
        assert (
            await routing.execute(
                uuid4(), capability, {"ollama-embedding": action}, timeout_seconds=1
            )
            == "vectors"
        )
        action.assert_awaited_once()
    if case == "unselected_ocr":
        assert runtime.record.await_args is not None
        assert runtime.record.await_args.args[1].reason is ProviderCode.NOT_SELECTED
