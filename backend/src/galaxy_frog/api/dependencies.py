"""FastAPI dependencies that expose application-scoped infrastructure."""

from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from galaxy_frog.db.engine import probe_database

type DatabaseProbe = Callable[[], Awaitable[None]]


def get_database_probe(request: Request) -> DatabaseProbe | None:
    """Return a request-safe database probe without exposing the engine to handlers."""

    engine = cast(AsyncEngine | None, getattr(request.app.state, "database_engine", None))
    if engine is None:
        return None

    async def probe() -> None:
        await probe_database(engine)

    return probe
