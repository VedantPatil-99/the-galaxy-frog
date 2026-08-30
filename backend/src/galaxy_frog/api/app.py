"""FastAPI application factory and CLI entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from galaxy_frog.api.errors import register_error_handlers
from galaxy_frog.api.middleware import correlation_id_middleware
from galaxy_frog.api.routes.health import router as health_router
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application from validated settings."""

    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        engine: AsyncEngine | None = None
        if resolved_settings.database_url is not None:
            engine = create_database_engine(resolved_settings)
        application.state.database_engine = engine

        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    application = FastAPI(
        title="Galaxy Frog API",
        version=version("galaxy-frog"),
        debug=False,
        lifespan=lifespan,
    )
    application.state.database_engine = None
    application.middleware("http")(correlation_id_middleware)
    register_error_handlers(application)
    application.include_router(health_router)
    return application


app = create_app()
