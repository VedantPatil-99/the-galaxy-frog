"""Exercise study generation, locking, provenance, and export with real PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.integration.test_postgres_lexical_search import (
    _seed,  # pyright: ignore[reportPrivateUsage]
)

from galaxy_frog.api.app import create_app
from galaxy_frog.application.study_generation import DraftBatch
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import OCRBlockRow, SceneRow, VideoFrameRow, VideoRow
from galaxy_frog.db.study_repository import StudyLeaseLost, StudyRepository, StudyRunRow
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.study import StudyCitation, StudyError, StudyKind
from galaxy_frog.entrypoints.study_worker import (  # pyright: ignore[reportPrivateUsage]
    execute_study_run,
    process_study,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires existing PostgreSQL/pgvector"
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")


class FixtureGenerator:
    async def draft(self, kind: StudyKind, evidence: tuple[StudyCitation, ...]) -> DraftBatch:
        item = (
            next((item for item in evidence if item.modality == "ocr"), evidence[0])
            if kind == StudyKind.NOTES
            else evidence[0]
        )
        citation = {"source_id": item.source_id, "quote": item.quote}
        return DraftBatch(
            (
                {
                    "title": "Test title",
                    "text": item.quote,
                    "citations": [citation],
                },
            ),
            "fixture:local",
        )


class Unavailable:
    async def draft(self, kind: StudyKind, evidence: tuple[StudyCitation, ...]) -> DraftBatch:
        raise StudyError("STUDY_PROVIDER_UNAVAILABLE", "Provider unavailable.", retryable=True)


async def add_ocr(session: AsyncSession, video: UUID) -> UUID:
    now = datetime.now(UTC)
    scene = SceneRow(
        id=uuid4(),
        video_id=video,
        scene_order=0,
        start_ms=0,
        end_ms=50_000,
        score=0.9,
        detector="fixture",
        detector_revision="v1",
        created_at=now,
    )
    session.add(scene)
    await session.flush()
    valid_id = uuid4()
    for sequence, capture in enumerate((1_234, None)):
        frame = VideoFrameRow(
            id=uuid4(),
            video_id=video,
            scene_id=scene.id,
            sequence=sequence,
            start_ms=0,
            end_ms=50_000,
            capture_ms=capture,
            source="scene_boundary",
            image_path="fixture.jpg",
            is_duplicate=False,
            created_at=now,
        )
        session.add(frame)
        await session.flush()
        session.add(
            OCRBlockRow(
                id=valid_id if capture is not None else uuid4(),
                video_id=video,
                frame_id=frame.id,
                start_ms=0,
                end_ms=50_000,
                text="On-screen concept" if capture is not None else "Unknown time",
                provider="fixture",
                provider_revision="v1",
                created_at=now,
            )
        )
    await session.commit()
    return valid_id


@pytest.mark.asyncio
async def test_http_study_jobs_pin_versions_and_export_exact_evidence() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[UUID] = []
    spec = EmbeddingCollectionSpec(
        "ollama", settings.embedding_model, settings.embedding_model_revision, 1024, "l2"
    )
    try:
        async with sessions() as session:
            video, _ = await _seed(session, ("Alpha", "Beta", "Gamma"))
            ids.append(video)
            other, _ = await _seed(session, ("Do not cite me",))
            ids.append(other)
            block = await add_ocr(session, video)
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            url = f"/v1/videos/{video}"
            request = {"request_id": str(uuid4())}
            missing = await client.post(
                f"{url}/artifacts",
                json={
                    **request,
                    "kind": "notes",
                    "chapter_version_id": str(uuid4()),
                },
            )
            assert missing.status_code == 409
            assert missing.json()["error"]["code"] == "STUDY_CHAPTERS_REQUIRED"

            queued, replay = await asyncio.gather(
                *(client.post(f"{url}/chapters", json=request) for _ in range(2))
            )
            assert queued.status_code == replay.status_code == 202
            run_id = queued.json()["run_id"]
            assert replay.json()["run_id"] == run_id
            conflict = await client.post(f"{url}/chapters", json={"request_id": str(uuid4())})
            assert conflict.status_code == 409

            async def process(provider: FixtureGenerator | Unavailable) -> None:
                async def execute(claim: object) -> None:
                    from galaxy_frog.db.study_repository import ClaimedStudyRun

                    assert isinstance(claim, ClaimedStudyRun)
                    await execute_study_run(claim, sessions, spec, provider)

                assert await process_study(sessions, execute)

            await process(FixtureGenerator())
            done = await client.get(f"{url}/artifact-runs/{run_id}")
            assert done.status_code == 200 and done.json()["state"] == "succeeded"
            chapter_id = done.json()["result_id"]
            assert (
                await client.get(f"/v1/videos/{other}/artifact-runs/{run_id}")
            ).status_code == 404
            assert (
                await client.get(f"/v1/videos/{other}/artifacts/{chapter_id}")
            ).status_code == 404
            assert (
                await client.post(
                    f"/v1/videos/{other}/artifacts",
                    json={
                        "request_id": str(uuid4()),
                        "kind": "notes",
                        "chapter_version_id": chapter_id,
                    },
                )
            ).status_code == 409

            notes = await client.post(
                f"{url}/artifacts",
                json={
                    "request_id": str(uuid4()),
                    "kind": "notes",
                    "chapter_version_id": chapter_id,
                },
            )
            assert notes.status_code == 202
            await process(FixtureGenerator())
            notes_done = (await client.get(f"{url}/artifact-runs/{notes.json()['run_id']}")).json()
            notes_v1 = notes_done["result_id"]
            assert notes_done["state"] == "succeeded"
            artifact = (await client.get(f"{url}/artifacts/{notes_v1}")).json()
            citation = artifact["document"]["sections"][0]["items"][0]["citations"][0]
            assert citation["ocr_block_id"] == str(block) and citation["seek_ms"] == 1_234
            assert citation["start_ms"] == 0 and citation["end_ms"] == 50_000
            assert artifact["chapter_version_id"] == chapter_id
            assert "Unknown time" not in str(artifact)

            regenerated = await client.post(
                f"/v1/artifacts/{notes_v1}/regenerate", json={"request_id": str(uuid4())}
            )
            assert regenerated.status_code == 202
            await process(FixtureGenerator())
            again = await client.post(
                f"/v1/artifacts/{notes_v1}/regenerate", json={"request_id": str(uuid4())}
            )
            assert again.status_code == 202
            await process(Unavailable())
            failure = (await client.get(f"{url}/artifact-runs/{again.json()['run_id']}")).json()
            assert failure["state"] == "failed" and failure["failure"]["retryable"]
            latest = (await client.get(f"{url}/artifacts?kind=notes&limit=1")).json()
            assert [item["version"] for item in latest["versions"]] == [2]
            old = (
                await client.get(
                    f"{url}/artifacts?kind=notes&before_version={latest['next_before_version']}"
                )
            ).json()
            assert [item["version"] for item in old["versions"]] == [1]
            exported = await client.get(f"{url}/artifacts/{notes_v1}/export?format=json")
            assert exported.json() == artifact
            markdown = await client.get(f"{url}/artifacts/{notes_v1}/export?format=markdown")
            assert (
                markdown.status_code == 200
                and "notes-v1.md" in markdown.headers["Content-Disposition"]
            )
            assert "seek 1234 ms" in markdown.text and "t=1s" in markdown.text
    finally:
        async with sessions() as session:
            await session.execute(delete(VideoRow).where(VideoRow.id.in_(ids)))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_lease_fences_old_worker_and_recovers_once() -> None:
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[UUID] = []
    try:
        async with sessions() as session:
            video, _ = await _seed(session, ("Source evidence",))
            ids.append(video)
            run = await StudyRepository(session).enqueue(video, StudyKind.CHAPTERS, uuid4())

        async def claim():
            async with sessions() as session:
                return await StudyRepository(session).claim()

        first, idle = await asyncio.gather(claim(), claim())
        first, idle = (idle, first) if first is None else (first, idle)
        assert first is not None and idle is None

        async def expire() -> None:
            async with sessions() as session:
                await session.execute(
                    update(StudyRunRow)
                    .where(StudyRunRow.id == run.run_id)
                    .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
                await session.commit()

        await expire()
        reclaimed = await claim()
        assert reclaimed is not None and reclaimed.token != first.token
        async with sessions() as session:
            with pytest.raises(StudyLeaseLost):
                await StudyRepository(session).progress(first, 1, 1, ())
        await expire()
        assert await claim() is None
        async with sessions() as session:
            failed = await StudyRepository(session).run(video, run.run_id)
            assert failed.failure is not None and failed.failure.code == "STUDY_WORKER_INTERRUPTED"
            assert (
                await StudyRepository(session).enqueue(video, StudyKind.CHAPTERS, uuid4())
            ).run_id != run.run_id
    finally:
        async with sessions() as session:
            await session.execute(delete(VideoRow).where(VideoRow.id.in_(ids)))
            await session.commit()
        await engine.dispose()
