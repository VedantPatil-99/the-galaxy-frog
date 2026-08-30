"""FastAPI application factory and CLI entry point."""

from importlib.metadata import version

from fastapi import FastAPI

from galaxy_frog.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application from validated settings."""

    resolved_settings = settings or Settings()

    return FastAPI(
        title="Galaxy Frog API",
        version=version("galaxy-frog"),
        debug=resolved_settings.app_env == "development",
    )


app = create_app()
