"""Canonical Pydantic response contracts for operational API behavior."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class HealthResponse(BaseModel):
    """Liveness response independent of external dependencies."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Readiness response returned after all required dependencies respond."""

    status: Literal["ready"] = "ready"
    dependencies: dict[str, Literal["ready"]] = Field(default_factory=lambda: {"database": "ready"})


class ErrorPayload(BaseModel):
    """Stable, human-safe API error information."""

    code: str
    message: str
    correlation_id: str
    retryable: bool
    suggested_action: str | None = None
    details: dict[str, JsonValue] | None = None


class ErrorResponse(BaseModel):
    """Top-level error envelope shared by every API failure."""

    error: ErrorPayload
