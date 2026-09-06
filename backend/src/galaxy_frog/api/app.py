"""FastAPI application factory and CLI entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from galaxy_frog.adapters.dispatch.local import LocalJobDispatcher
from galaxy_frog.adapters.embeddings.ollama import OllamaBgeM3EmbeddingProvider
from galaxy_frog.adapters.generation.ollama import OllamaGenerationProvider
from galaxy_frog.adapters.video_sources.youtube import YouTubeSource
from galaxy_frog.api.errors import register_error_handlers
from galaxy_frog.api.middleware import correlation_id_middleware
from galaxy_frog.api.routes.health import router as health_router
from galaxy_frog.api.routes.jobs import router as jobs_router
from galaxy_frog.api.routes.videos import router as videos_router
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
    application.state.settings = resolved_settings
    application.state.video_sources = (YouTubeSource(),)
    application.state.job_dispatcher = LocalJobDispatcher()
    application.state.embedding_provider_factory = lambda: OllamaBgeM3EmbeddingProvider(
        base_url=resolved_settings.ollama_base_url,
        model=resolved_settings.embedding_model,
        revision=resolved_settings.embedding_model_revision,
    )
    application.state.generation_provider_factory = lambda: OllamaGenerationProvider(
        base_url=resolved_settings.ollama_base_url,
        model=resolved_settings.generation_model,
    )
    application.middleware("http")(correlation_id_middleware)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(jobs_router)
    application.include_router(videos_router)
    return application


app = create_app()
