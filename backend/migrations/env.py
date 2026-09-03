"""Alembic environment for async PostgreSQL migrations."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import POSTGRES_SERVER_SETTINGS

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def database_url() -> str:
    """Load the migration URL without storing credentials in Alembic configuration."""

    settings = Settings()
    if settings.database_url is None:
        msg = "DATABASE_URL is required to run database migrations"
        raise RuntimeError(msg)
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Run migrations without creating an Engine."""

    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def apply_migrations(connection: Connection) -> None:
    """Apply configured migrations through a synchronous SQLAlchemy connection."""

    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations on its synchronous bridge."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": POSTGRES_SERVER_SETTINGS},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(apply_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations through the asyncpg-backed SQLAlchemy engine."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
