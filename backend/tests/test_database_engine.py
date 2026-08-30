"""Behavior tests for database engine construction."""

import pytest
from pydantic import SecretStr

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import DatabaseConfigurationError, create_database_engine


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
