"""FastAPI dependencies that expose application-scoped infrastructure."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.application.ingestion.dispatch import DispatchSignatureVerifier, JobDispatcher
from galaxy_frog.db.engine import probe_database
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository

type DatabaseProbe = Callable[[], Awaitable[None]]


def get_job_dispatcher(request: Request) -> JobDispatcher:
    """Return the configured provider-independent dispatch adapter."""

    return cast(JobDispatcher, request.app.state.job_dispatcher)


def get_dispatch_signature_verifier(request: Request) -> DispatchSignatureVerifier:
    """Require the configured verifier for an internal authenticated callback."""

    verifier = cast(
        DispatchSignatureVerifier | None,
        getattr(request.app.state, "dispatch_signature_verifier", None),
    )
    if verifier is None:
        from galaxy_frog.api.errors import ApiError

        raise ApiError(
            status_code=503,
            code="QSTASH_NOT_CONFIGURED",
            message="The QStash callback is not configured.",
            retryable=False,
            suggested_action="Configure the QStash dispatcher and signing keys before delivery.",
        )
    return verifier


def _database_engine(request: Request) -> AsyncEngine:
    engine = cast(AsyncEngine | None, getattr(request.app.state, "database_engine", None))
    if engine is None:
        from galaxy_frog.api.errors import ApiError

        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="The database dependency is not configured.",
            retryable=False,
            suggested_action="Configure DATABASE_URL before importing videos.",
        )
    return engine


def get_database_probe(request: Request) -> DatabaseProbe | None:
    """Return a request-safe database probe without exposing the engine to handlers."""

    engine = cast(AsyncEngine | None, getattr(request.app.state, "database_engine", None))
    if engine is None:
        return None

    async def probe() -> None:
        await probe_database(engine)

    return probe


async def get_video_repository(request: Request) -> AsyncGenerator[SqlAlchemyVideoRepository]:
    """Yield a request-scoped repository without exposing sessions to route handlers."""

    factory = async_sessionmaker(
        _database_engine(request),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield SqlAlchemyVideoRepository(session)


async def get_ingestion_repository(
    request: Request,
) -> AsyncGenerator[PostgresIngestionRepository]:
    """Yield the durable job repository through the same request-scoped session boundary."""

    factory = async_sessionmaker(
        _database_engine(request),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield PostgresIngestionRepository(session)
