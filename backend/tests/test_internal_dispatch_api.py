"""HTTP tests for authenticated identifier-only QStash delivery."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_ingestion_repository
from galaxy_frog.application.ingestion.dispatch import DispatchSignatureError
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionJobStatus, IngestionStage
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 6, 18, tzinfo=UTC)


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


class ReadOnlyRepository:
    def __init__(self, job: IngestionJob | None) -> None:
        self.job = job
        self.get_calls = 0

    async def get(self, job_id: UUID) -> IngestionJob | None:
        self.get_calls += 1
        return self.job if self.job is not None and self.job.job_id == job_id else None


class Verifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.reject = False

    def verify(self, *, signature: str, body: str) -> None:
        self.calls.append((signature, body))
        if self.reject:
            raise DispatchSignatureError("private verification detail")


@pytest.fixture
def internal_api() -> tuple[FastAPI, ReadOnlyRepository, Verifier, IngestionJob]:
    application = create_app()
    job = queued_job()
    repository = ReadOnlyRepository(job)
    verifier = Verifier()

    async def repository_dependency() -> AsyncGenerator[PostgresIngestionRepository]:
        yield cast(PostgresIngestionRepository, repository)

    application.dependency_overrides[get_ingestion_repository] = repository_dependency
    application.state.dispatch_signature_verifier = verifier
    return application, repository, verifier, job


@pytest.mark.asyncio
async def test_duplicate_authenticated_delivery_is_a_read_only_acknowledgement(
    internal_api: tuple[FastAPI, ReadOnlyRepository, Verifier, IngestionJob],
) -> None:
    application, repository, verifier, job = internal_api
    raw_body = f'{{"job_id":"{job.job_id}","requested_action":"process"}}'
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/internal/qstash/dispatch",
            content=raw_body,
            headers={"Upstash-Signature": "signed"},
        )
        duplicate = await client.post(
            "/internal/qstash/dispatch",
            content=raw_body,
            headers={"Upstash-Signature": "signed"},
        )

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert (
        first.json()
        == duplicate.json()
        == {
            "job_id": str(job.job_id),
            "accepted": True,
            "status": "queued",
            "stage": "source_resolution",
        }
    )
    assert repository.get_calls == 2
    assert verifier.calls == [("signed", raw_body), ("signed", raw_body)]


@pytest.mark.asyncio
async def test_signature_is_verified_before_payload_validation_or_database_access(
    internal_api: tuple[FastAPI, ReadOnlyRepository, Verifier, IngestionJob],
) -> None:
    application, repository, verifier, _job = internal_api
    verifier.reject = True
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/qstash/dispatch",
            content="not-json",
            headers={"Upstash-Signature": "invalid"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "QSTASH_SIGNATURE_INVALID"
    assert "private verification detail" not in response.text
    assert repository.get_calls == 0

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_encoding = await client.post(
            "/internal/qstash/dispatch",
            content=b"\xff",
            headers={"Upstash-Signature": "signed"},
        )
    assert invalid_encoding.status_code == 422
    assert invalid_encoding.json()["error"]["code"] == "INVALID_DISPATCH_MESSAGE"
    assert repository.get_calls == 0


@pytest.mark.asyncio
async def test_authenticated_message_forbids_embedded_data_and_unknown_jobs(
    internal_api: tuple[FastAPI, ReadOnlyRepository, Verifier, IngestionJob],
) -> None:
    application, repository, _verifier, job = internal_api
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        embedded = await client.post(
            "/internal/qstash/dispatch",
            json={
                "job_id": str(job.job_id),
                "requested_action": "process",
                "transcript": "must never be transported",
            },
            headers={"Upstash-Signature": "signed"},
        )
        missing = await client.post(
            "/internal/qstash/dispatch",
            json={"job_id": str(uuid4()), "requested_action": "process"},
            headers={"Upstash-Signature": "signed"},
        )

    assert embedded.status_code == 422
    assert embedded.json()["error"]["code"] == "INVALID_DISPATCH_MESSAGE"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "INGESTION_JOB_NOT_FOUND"
    assert repository.get_calls == 1


@pytest.mark.asyncio
async def test_internal_callback_requires_signature_and_qstash_configuration(
    internal_api: tuple[FastAPI, ReadOnlyRepository, Verifier, IngestionJob],
) -> None:
    application, repository, _verifier, job = internal_api
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unsigned = await client.post(
            "/internal/qstash/dispatch",
            json={"job_id": str(job.job_id), "requested_action": "process"},
        )
    assert unsigned.status_code == 401
    assert unsigned.json()["error"]["code"] == "QSTASH_SIGNATURE_INVALID"
    assert repository.get_calls == 0

    application.state.dispatch_signature_verifier = None
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unconfigured = await client.post(
            "/internal/qstash/dispatch",
            json={"job_id": str(job.job_id), "requested_action": "process"},
            headers={"Upstash-Signature": "signed"},
        )
    assert unconfigured.status_code == 503
    assert unconfigured.json()["error"]["code"] == "QSTASH_NOT_CONFIGURED"
    assert repository.get_calls == 0
