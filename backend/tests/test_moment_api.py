"""Public moment errors and safe serialization through the FastAPI route."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_video_repository
from galaxy_frog.api.routes.retrieval import get_moment_service
from galaxy_frog.application.retrieval.explain_moment import MomentCitation, MomentExplanation
from galaxy_frog.application.retrieval.moment import (
    InvalidMomentTimestamp,
    MomentEvidence,
    MomentFrame,
    MomentFrames,
)
from galaxy_frog.application.retrieval.temporal import RetrievalVideoNotFound
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.retrieval.traces import TraceLimitExceeded
from galaxy_frog.domain.vision.models import FrameSource


@pytest.mark.asyncio
@pytest.mark.parametrize("with_frame", [False, True])
async def test_moment_api_returns_bounded_window_and_safe_lineage(with_frame: bool) -> None:
    app = create_app()
    video, frame_id, trace = uuid4(), uuid4(), uuid4()
    frame = MomentFrame(
        frame_id, None, 1, 0, 20_000, 1000, FrameSource.PERIODIC, Path("private.jpg"), None, None
    )
    evidence = MomentEvidence(
        video,
        1200,
        TimeWindow(0, 16_200),
        (),
        (),
        MomentFrames(current=frame if with_frame else None),
    )
    citations = (
        (MomentCitation("frame", str(frame_id), 0, 20_000, 1000, frame_id=frame_id),)
        if with_frame
        else ()
    )
    service = AsyncMock()
    service.execute.return_value = MomentExplanation(
        evidence, "Explanation", citations, (frame,) if with_frame else (), (), trace
    )
    app.dependency_overrides[get_moment_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/v1/videos/{video}/explain-moment", json={"timestamp_ms": 1200}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["window"] == {"start_ms": 0, "end_ms": 16_200} and data["trace_id"] == str(trace)
    assert "private.jpg" not in response.text
    assert (data["current_frame"] is not None) == with_frame
    if with_frame:
        assert (
            data["current_frame"]["frame_id"] == str(frame_id)
            and data["citations"][0]["seek_ms"] == 1000
        )
    service.execute.assert_awaited_once_with(video, 1200, external_permission=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (RetrievalVideoNotFound(), 404, "VIDEO_NOT_FOUND"),
        (InvalidMomentTimestamp("bad timestamp"), 422, "INVALID_MOMENT_TIMESTAMP"),
        (TraceLimitExceeded(), 422, "RETRIEVAL_TRACE_LIMIT_EXCEEDED"),
        (SQLAlchemyError(), 503, "DATABASE_UNAVAILABLE"),
    ],
)
async def test_moment_error_mapping(error: Exception, status: int, code: str) -> None:
    app = create_app()
    service = AsyncMock()
    service.execute.side_effect = error
    app.dependency_overrides[get_moment_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/v1/videos/{uuid4()}/explain-moment", json={"timestamp_ms": 0}
        )
    assert response.status_code == status and response.json()["error"]["code"] == code


@pytest.mark.asyncio
async def test_moment_composition_checks_video_and_rejects_fractional_timestamp() -> None:
    app = create_app()
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = None
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, db))
    app.dependency_overrides[get_video_repository] = lambda: repository
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = f"/v1/videos/{uuid4()}/explain-moment"
        invalid = await client.post(path, json={"timestamp_ms": 0.5})
        missing = await client.post(path, json={"timestamp_ms": 0})
    assert invalid.status_code == 422 and missing.status_code == 404
