"""Verify the study HTTP contracts and structured failures without a database."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.app import create_app
from galaxy_frog.api.routes.study import get_study_repository
from galaxy_frog.config import Settings
from galaxy_frog.db.study_repository import StudyRepository
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.study import (
    RunState,
    StudyDocument,
    StudyError,
    StudyKind,
    StudyRun,
    StudySource,
    StudyVersion,
)
from galaxy_frog.domain.videos.models import VideoSourceKind


@pytest.mark.asyncio
async def test_start_poll_read_page_and_export_saved_artifact() -> None:
    app = create_app(Settings())
    repo = AsyncMock(spec=StudyRepository)
    app.dependency_overrides[get_study_repository] = lambda: repo
    now = datetime.now(UTC)
    video, artifact, run, request_id = (uuid4() for _ in range(4))
    stored = StudyVersion(
        artifact_id=artifact,
        kind=StudyKind.CHAPTERS,
        version=2,
        chapter_version_id=None,
        created_at=now,
        schema_version=1,
        prompt_version="study-v1",
        evidence_fingerprint="a" * 64,
        provider="ollama",
        model="fixture",
        document=StudyDocument(
            source=StudySource(
                video_id=video,
                kind=VideoSourceKind.YOUTUBE,
                external_id="abcdefghijk",
                canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
                title="Fixture",
                duration_ms=1000,
            )
        ),
    )
    queued = StudyRun(
        run_id=run,
        video_id=video,
        kind=StudyKind.CHAPTERS,
        state=RunState.QUEUED,
        chapter_version_id=None,
        result_id=None,
        completed_chapters=0,
        total_chapters=0,
        warnings=(),
        failure=None,
        created_at=now,
        updated_at=now,
    )
    repo.enqueue.return_value = queued
    repo.regenerate.return_value = queued
    repo.run.return_value = queued
    repo.latest_versions.return_value = (stored,)
    repo.latest_runs.return_value = (queued,)
    repo.history.return_value = ((stored,), 2)
    repo.artifact.return_value = stored
    path = f"/v1/videos/{video}"
    body = {"request_id": str(request_id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(f"{path}/chapters", json=body)).status_code == 202
        repo.enqueue.assert_awaited_with(video, StudyKind.CHAPTERS, request_id)
        result = await client.post(
            f"{path}/artifacts",
            json={
                **body,
                "kind": "quiz",
                "chapter_version_id": str(artifact),
            },
        )
        assert result.status_code == 202
        repo.enqueue.assert_awaited_with(video, StudyKind.QUIZ, request_id, artifact)
        assert (
            await client.post(f"/v1/artifacts/{artifact}/regenerate", json=body)
        ).status_code == 202
        assert (await client.get(f"{path}/artifact-runs/{run}")).json()["run_id"] == str(run)
        index = (await client.get(f"{path}/artifacts")).json()
        assert index["latest"][0]["artifact_id"] == str(artifact) and index["versions"] == []
        page = (await client.get(f"{path}/artifacts?kind=chapters&limit=1")).json()
        assert page["next_before_version"] == 2
        version = (await client.get(f"{path}/artifacts/{artifact}")).json()
        assert version["version"] == 2
        assert (
            await client.get(f"{path}/artifacts/{artifact}/export?format=json")
        ).json() == version
        markdown = await client.get(f"{path}/artifacts/{artifact}/export?format=markdown")
        assert markdown.headers["content-disposition"].endswith('chapters-v2.md"')
        assert "Fixture" in markdown.text

        repo.run.side_effect = StudyError("STUDY_RUN_NOT_FOUND", "Run unavailable.", status=404)
        missing = await client.get(f"{path}/artifact-runs/{run}")
        assert (
            missing.status_code == 404 and missing.json()["error"]["code"] == "STUDY_RUN_NOT_FOUND"
        )
        repo.require_video.side_effect = SQLAlchemyError("private details")
        outage = await client.get(f"{path}/artifacts")
        assert outage.status_code == 503 and outage.json()["error"]["retryable"]
        assert "private details" not in outage.text


def test_dependency_reuses_the_request_session() -> None:
    videos = AsyncMock(spec=SqlAlchemyVideoRepository)
    assert get_study_repository(videos).session is videos.session
