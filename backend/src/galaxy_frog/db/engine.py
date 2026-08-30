"""SQLAlchemy engine construction without module-level database clients."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from galaxy_frog.config import Settings


class DatabaseConfigurationError(RuntimeError):
    """Raised when database infrastructure is requested without configuration."""


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create an async engine from validated application settings."""

    if settings.database_url is None:
        msg = "DATABASE_URL is required for database operations"
        raise DatabaseConfigurationError(msg)

    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
