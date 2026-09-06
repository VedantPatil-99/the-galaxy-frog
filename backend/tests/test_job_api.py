"""HTTP behavior tests for durable ingestion job projections and actions."""

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_ingestion_repository
from galaxy_frog.db.ingestion_repository import (
    IngestionRepositoryError,
    PostgresIngestionRepository,
)
from galaxy_frog.domain.ingestion.models import (
    IngestionEvent,
    IngestionEventType,
    IngestionJob,
    IngestionJobStatus,
    IngestionStage,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 6, 15, tzinfo=UTC)


def queued_job() -> IngestionJob:
    return IngestionJob(
        job_id=uuid4(),
        source=SourceReference(
            VideoSourceKind.YOUTUBE,
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        input_fingerprint="a" * 64,
        status=IngestionJobStatus.QUEUED,
        stage=IngestionStage.SOURCE_RESOLUTION,
        attempt=0,
        created_at=NOW,
        updated_at=NOW,
    )


class MemoryIngestionRepository:
    def __init__(self, job: IngestionJob) -> None:
        self.job = job
        self.events: tuple[IngestionEvent, ...] = ()
        self.transition_error: IngestionRepositoryError | None = None

    async def get(self, job_id: UUID) -> IngestionJob | None:
        return self.job if self.job.job_id == job_id else None

    async def list_events(self, job_id: UUID) -> tuple[IngestionEvent, ...]:
        assert job_id == self.job.job_id
        return self.events

    async def retry(self, job_id: UUID) -> IngestionJob:
        assert job_id == self.job.job_id
        if self.transition_error is not None:
            raise self.transition_error
        self.job = replace(
            self.job,
            status=IngestionJobStatus.QUEUED,
            completed_at=None,
            last_error_code=None,
            last_error_message=None,
            last_error_retryable=None,
            updated_at=NOW,
        )
        return self.job

    async def request_cancellation(self, job_id: UUID) -> IngestionJob:
        assert job_id == self.job.job_id
        if self.transition_error is not None:
            raise self.transition_error
        self.job = replace(
            self.job,
            status=IngestionJobStatus.CANCELLED,
            cancel_requested_at=NOW,
            completed_at=NOW,
            updated_at=NOW,
        )
        return self.job


@pytest.fixture
def job_api() -> tuple[FastAPI, MemoryIngestionRepository]:
    application = create_app()
    repository = MemoryIngestionRepository(queued_job())

    async def repository_dependency() -> AsyncGenerator[PostgresIngestionRepository]:
        yield cast(PostgresIngestionRepository, repository)

    application.dependency_overrides[get_ingestion_repository] = repository_dependency
    return application, repository


@pytest.mark.asyncio
async def test_job_detail_and_ordered_events_preserve_provenance(
    job_api: tuple[FastAPI, MemoryIngestionRepository],
) -> None:
    application, repository = job_api
    repository.events = (
        IngestionEvent(
            event_id=uuid4(),
            job_id=repository.job.job_id,
            sequence=1,
            event_type=IngestionEventType.CREATED,
            stage=IngestionStage.SOURCE_RESOLUTION,
            attempt=0,
            occurred_at=NOW,
            details={"source_kind": "youtube", "external_id": "dQw4w9WgXcQ"},
        ),
        IngestionEvent(
            event_id=uuid4(),
            job_id=repository.job.job_id,
            sequence=2,
            event_type=IngestionEventType.CLAIMED,
            stage=IngestionStage.SOURCE_RESOLUTION,
            attempt=1,
            occurred_at=NOW,
        ),
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        detail = await client.get(f"/v1/jobs/{repository.job.job_id}")
        events = await client.get(f"/v1/jobs/{repository.job.job_id}/events")

    assert detail.status_code == 200
    assert detail.json()["canonical_url"] == repository.job.source.canonical_url
    assert detail.json()["created_at"] == "2026-09-06T15:00:00Z"
    assert detail.json()["last_error_code"] is None
    assert [event["sequence"] for event in events.json()["events"]] == [1, 2]
    assert events.json()["events"][0]["details"] == {
        "source_kind": "youtube",
        "external_id": "dQw4w9WgXcQ",
    }
    assert events.json()["events"][1]["details"] is None


@pytest.mark.asyncio
async def test_retry_and_cancel_return_the_current_projection(
    job_api: tuple[FastAPI, MemoryIngestionRepository],
) -> None:
    application, repository = job_api
    repository.job = replace(
        repository.job,
        status=IngestionJobStatus.FAILED,
        completed_at=NOW,
        last_error_code="SOURCE_UNAVAILABLE",
        last_error_message="The source is temporarily unavailable.",
        last_error_retryable=True,
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        retried = await client.post(f"/v1/jobs/{repository.job.job_id}/retry")
        cancelled = await client.post(f"/v1/jobs/{repository.job.job_id}/cancel")

    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert retried.json()["last_error_retryable"] is None
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested_at"] == "2026-09-06T15:00:00Z"


@pytest.mark.parametrize("suffix", ["", "/events", "/retry", "/cancel"])
@pytest.mark.asyncio
async def test_unknown_job_returns_one_stable_error(
    job_api: tuple[FastAPI, MemoryIngestionRepository],
    suffix: str,
) -> None:
    application, _repository = job_api
    method = "POST" if suffix in {"/retry", "/cancel"} else "GET"
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, f"/v1/jobs/{uuid4()}{suffix}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INGESTION_JOB_NOT_FOUND"


@pytest.mark.parametrize("suffix", ["/retry", "/cancel"])
@pytest.mark.asyncio
async def test_invalid_transition_returns_a_safe_conflict(
    job_api: tuple[FastAPI, MemoryIngestionRepository],
    suffix: str,
) -> None:
    application, repository = job_api
    repository.transition_error = IngestionRepositoryError("private transition detail")
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/v1/jobs/{repository.job.job_id}{suffix}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INGESTION_JOB_TRANSITION_REJECTED"
    assert "private transition detail" not in response.text
