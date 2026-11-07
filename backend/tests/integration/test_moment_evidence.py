"""Verify moment selection against persisted PostgreSQL evidence."""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.test_postgres_lexical_search import (
    _seed,  # pyright: ignore[reportPrivateUsage]
)

from galaxy_frog.application.retrieval.moment import MomentFrames, RetrieveMomentEvidence
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import (
    OCRBlockRow,
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    SceneRow,
    TranscriptCueRow,
    VideoFrameRow,
    VideoRow,
)
from galaxy_frog.db.moment_evidence import SqlAlchemyMomentEvidenceReader
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.videos.models import SafeVideoMetadata, SourceReference, VideoSourceKind

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires existing PostgreSQL/pgvector"
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_moment_reads_original_evidence_inside_video_and_time_scope(tmp_path: Path) -> None:
    engine = create_database_engine(Settings())
    video_ids: list[UUID] = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            video_id, unit_ids = await _seed(session, ("Zero", "One", "Two", "Three", "Four"))
            video_ids.append(video_id)
            videos = SqlAlchemyVideoRepository(session)
            transcript = await videos.get_transcript(video_id)
            assert transcript is not None
            external_id = uuid4().hex[:11]
            source = SourceReference(
                VideoSourceKind.YOUTUBE,
                external_id,
                f"https://www.youtube.com/watch?v={external_id}",
            )
            other = await videos.save_import(
                SafeVideoMetadata(source, "Visual only", 50_000), (), ()
            )
            video_ids.append(other.video_id)

            retrieve = RetrieveMomentEvidence(SqlAlchemyMomentEvidenceReader(session))
            spoken = await retrieve.execute(video_id, 25_000)
            assert spoken.window == TimeWindow(10_000, 40_000)
            assert tuple(item.cue for item in spoken.cues) == transcript.cues[1:4]
            assert tuple(item.unit_ids for item in spoken.cues) == tuple(
                (unit_id,) for unit_id in unit_ids[1:4]
            )
            assert spoken.ocr == () and spoken.frames == MomentFrames()
            partial = await retrieve.execute(video_id, 23_000)
            assert partial.window == TimeWindow(8_000, 38_000)
            assert tuple(item.cue for item in partial.cues) == transcript.cues[:4]
            empty = await retrieve.execute(other.video_id, 25_000)
            assert (empty.cues, empty.ocr, empty.frames) == ((), (), MomentFrames())

            now = datetime.now(UTC)
            scene = SceneRow(
                id=uuid4(),
                video_id=video_id,
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

            def frame(
                owner: UUID, sequence: int, capture: int | None, *, duplicate: bool = False
            ) -> VideoFrameRow:
                return VideoFrameRow(
                    id=uuid4(),
                    video_id=owner,
                    scene_id=scene.id if owner == video_id else None,
                    sequence=sequence,
                    start_ms=0,
                    end_ms=50_000,
                    capture_ms=capture,
                    source="periodic",
                    image_path=str(tmp_path / "metadata-only.jpg"),
                    is_duplicate=duplicate,
                    created_at=now,
                )

            before_window = frame(video_id, 0, 9999)
            previous = frame(video_id, 1, 10_000)
            duplicate = frame(video_id, 2, 24_000, duplicate=True)
            tied = frame(video_id, 4, 25_000)
            current = frame(video_id, 3, 25_000)
            following = frame(video_id, 5, 26_000)
            extra_ocr_frame = frame(video_id, 6, 39_999)
            at_end = frame(video_id, 7, 40_000)
            unknown = frame(video_id, 8, None)
            foreign_frame = frame(other.video_id, 0, 25_000)
            session.add_all(
                [
                    before_window,
                    previous,
                    duplicate,
                    tied,
                    current,
                    following,
                    extra_ocr_frame,
                    at_end,
                    unknown,
                    foreign_frame,
                ]
            )
            await session.flush()

            def ocr(owner: UUID, source_frame: VideoFrameRow) -> OCRBlockRow:
                return OCRBlockRow(
                    id=uuid4(),
                    video_id=owner,
                    frame_id=source_frame.id,
                    text="Original slide text",
                    start_ms=0,
                    end_ms=50_000,
                    provider="fixture",
                    provider_revision="v1",
                    created_at=now,
                )

            lower = ocr(video_id, previous)
            extra = ocr(video_id, extra_ocr_frame)
            session.add_all(
                [
                    lower,
                    extra,
                    ocr(video_id, before_window),
                    ocr(video_id, at_end),
                    ocr(video_id, unknown),
                    ocr(video_id, foreign_frame),
                    ocr(other.video_id, current),
                ]
            )
            foreign_unit = RetrievalUnitRow(
                id=uuid4().hex,
                video_id=other.video_id,
                start_ms=10_000,
                end_ms=20_000,
                text="Foreign retrieval unit",
            )
            session.add(foreign_unit)
            await session.flush()
            session.add(
                RetrievalUnitCueRow(
                    retrieval_unit_id=foreign_unit.id, cue_id=transcript.cues[1].cue_id, cue_order=0
                )
            )
            await session.commit()

            result = await retrieve.execute(video_id, 25_000)
            assert result.cues == spoken.cues
            frames = result.frames
            assert frames.previous is not None and frames.previous.frame_id == previous.id
            assert frames.next is not None and frames.next.frame_id == following.id
            assert frames.current is not None and frames.current.frame_id == current.id
            assert frames.current.scene_id == scene.id and frames.current.source == "periodic"
            assert (frames.current.start_ms, frames.current.end_ms, frames.current.capture_ms) == (
                0,
                50_000,
                25_000,
            )
            assert frames.current.dhash is None and frames.current.content_sha256 is None
            assert [(item.block_id, item.frame_id, item.capture_ms) for item in result.ocr] == [
                (lower.id, previous.id, 10_000),
                (extra.id, extra_ocr_frame.id, 39_999),
            ]
            assert all(
                (item.block.start_ms, item.block.end_ms) == (0, 50_000) for item in result.ocr
            )
            assert result.ocr[0].block.provider_spec.provider == "fixture"

            visual = await retrieve.execute(other.video_id, 25_000)
            assert visual.cues == () and visual.ocr == ()
            assert visual.frames.current is not None
            assert visual.frames.current.frame_id == foreign_frame.id
            assert visual.frames.previous is None and visual.frames.next is None

            session.add(
                TranscriptCueRow(
                    id=uuid4().hex * 2,
                    video_id=other.video_id,
                    source_order=0,
                    start_ms=20_000,
                    end_ms=30_000,
                    text="Foreign caption",
                    origin="caption",
                    language_code="en",
                    track_id="manual:en",
                    caption_kind="manual",
                )
            )
            await session.flush()
            assert (await retrieve.execute(video_id, 25_000)).cues == spoken.cues
    finally:
        try:
            if video_ids:
                async with AsyncSession(engine) as cleanup:
                    await cleanup.execute(delete(VideoRow).where(VideoRow.id.in_(video_ids)))
                    await cleanup.commit()
        finally:
            await engine.dispose()
