"""Behavior tests for database engine construction and probing."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import DatabaseConfigurationError, create_database_engine, probe_database


def test_database_engine_requires_configuration() -> None:
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        create_database_engine(Settings(database_url=None))


@pytest.mark.asyncio
async def test_database_engine_uses_asyncpg_without_exposing_password() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://galaxy_frog:secret@localhost:5432/galaxy_frog")
    )

    engine = create_database_engine(settings)

    assert engine.driver == "asyncpg"
    assert engine.url.render_as_string(hide_password=True) == (
        "postgresql+asyncpg://galaxy_frog:***@localhost:5432/galaxy_frog"
    )

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_probe_executes_a_real_query() -> None:
    connection = AsyncMock()
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=None)
    engine = MagicMock(spec=AsyncEngine)
    engine.connect.return_value = connection_context

    await probe_database(engine)

    statement = connection.execute.await_args.args[0]
    assert str(statement) == "SELECT 1"
