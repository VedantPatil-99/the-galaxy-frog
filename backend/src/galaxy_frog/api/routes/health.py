"""Liveness and dependency-aware readiness endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import DatabaseProbe, get_database_probe
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.schemas import ErrorResponse, HealthResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """Report process liveness without consulting external dependencies."""

    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorResponse}},
)
async def ready(
    database_probe: Annotated[DatabaseProbe | None, Depends(get_database_probe)],
) -> ReadinessResponse:
    """Report readiness only after PostgreSQL responds to a real query."""

    if database_probe is None:
        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="The database dependency is not configured.",
            retryable=False,
            suggested_action="Configure DATABASE_URL before starting the API.",
            details={"dependency": "postgresql"},
        )

    try:
        await database_probe()
    except (OSError, SQLAlchemyError) as exc:
        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="The database dependency is unavailable.",
            retryable=True,
            suggested_action="Retry after PostgreSQL connectivity is restored.",
            details={"dependency": "postgresql"},
        ) from exc

    return ReadinessResponse()
