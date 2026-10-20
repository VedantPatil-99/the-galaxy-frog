"""Unit tests for SqlAlchemyVisionRepository."""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from galaxy_frog.db.models import OCRBlockRow, SceneRow, VideoFrameRow
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    FrameSource,
    OCRBlock,
    OCRExtractionRequest,
    OCRExtractionResult,
    OCRProviderSpec,
    SceneBoundary,
    SceneDetectionResult,
)


class AllResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeSession:
    def __init__(self) -> None:
        self.scalars_values: deque[list[object]] = deque()
        self.added_groups: list[tuple[object, ...]] = []
        self.commits = 0
        self.statements: list[object] = []

    async def scalars(self, statement: object) -> AllResult:
        self.statements.append(statement)
        return AllResult(self.scalars_values.popleft())

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        vals = self.scalars_values.popleft()
        return vals[0] if vals else None

    def add_all(self, values: Iterable[object]) -> None:
        self.added_groups.append(tuple(values))

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def repo(session: FakeSession) -> SqlAlchemyVisionRepository:
    return SqlAlchemyVisionRepository(session)  # pyright: ignore[reportArgumentType]


def test_session_property(repo: SqlAlchemyVisionRepository, session: FakeSession) -> None:
    assert repo.session is session


@pytest.mark.asyncio
async def test_save_scenes_existing_returns_early(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    existing_row = SceneRow(
        id=uuid4(),
        video_id=video_id,
        scene_order=0,
        start_ms=0,
        end_ms=5000,
        score=0.9,
        detector="test",
        detector_revision="1",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([existing_row])
    result = SceneDetectionResult(
        video_id=video_id,
        duration_ms=5000,
        boundaries=(SceneBoundary(0, 5000, 0.9),),
        detector="test",
        detector_revision="1",
    )
    saved = await repo.save_scenes(video_id, result)
    assert saved == (existing_row,)
    assert session.commits == 0
    assert len(session.added_groups) == 0


@pytest.mark.asyncio
async def test_save_scenes_new(repo: SqlAlchemyVisionRepository, session: FakeSession) -> None:
    video_id = uuid4()
    session.scalars_values.append([])
    result = SceneDetectionResult(
        video_id=video_id,
        duration_ms=10000,
        boundaries=(
            SceneBoundary(0, 4000, 0.8),
            SceneBoundary(4000, 10000, 0.95),
        ),
        detector="pyscenedetect",
        detector_revision="0.6.5",
    )
    saved = await repo.save_scenes(video_id, result)
    assert len(saved) == 2
    assert session.commits == 1
    assert len(session.added_groups) == 1
    assert saved[0].scene_order == 0
    assert saved[0].start_ms == 0
    assert saved[0].end_ms == 4000
    assert saved[1].scene_order == 1
    assert saved[1].start_ms == 4000
    assert saved[1].end_ms == 10000


@pytest.mark.asyncio
async def test_save_frames_existing_returns_early(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    existing_frame = VideoFrameRow(
        id=uuid4(),
        video_id=video_id,
        scene_id=None,
        sequence=0,
        start_ms=0,
        end_ms=1000,
        source="periodic",
        dhash=None,
        is_duplicate=False,
        image_path="/tmp/0.jpg",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([existing_frame])
    frames = [
        FrameSample(
            video_id=video_id,
            sequence=0,
            start_ms=0,
            end_ms=1000,
            path=Path("C:/tmp/0.jpg"),
            source=FrameSource.PERIODIC,
        )
    ]
    saved = await repo.save_frames(video_id, frames)
    assert saved == (existing_frame,)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_save_frames_with_scene_matching_and_deduplication(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    scene_id = uuid4()
    # 1. get_frames returns empty
    session.scalars_values.append([])
    # 2. get_scenes returns one scene covering 0-5000
    scene_row = SceneRow(
        id=scene_id,
        video_id=video_id,
        scene_order=0,
        start_ms=0,
        end_ms=5000,
        score=0.9,
        detector="test",
        detector_revision="1",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([scene_row])

    frame0 = FrameSample(
        video_id=video_id,
        sequence=0,
        start_ms=0,
        end_ms=2500,
        path=Path("C:/tmp/0.jpg"),
        source=FrameSource.SCENE_BOUNDARY,
    )
    frame1 = FrameSample(
        video_id=video_id,
        sequence=1,
        start_ms=2500,
        end_ms=5000,
        path=Path("C:/tmp/1.jpg"),
        source=FrameSource.PERIODIC,
    )
    frame2 = FrameSample(
        video_id=video_id,
        sequence=2,
        start_ms=6000,
        end_ms=8000,
        path=Path("C:/tmp/2.jpg"),
        source=FrameSource.PERIODIC,
    )
    dedup = DeduplicatedFrameSet(
        kept=(frame0, frame2),
        dropped_count=1,
        hash_algorithm="dhash",
        hamming_threshold=4,
    )
    dhashes = {0: "0123456789abcdef", 1: "0123456789abcdef"}

    saved = await repo.save_frames(
        video_id,
        [frame0, frame1, frame2],
        deduplication=dedup,
        dhashes=dhashes,
    )

    assert len(saved) == 3
    assert session.commits == 1
    # Frame 0 matches scene and is kept
    assert saved[0].scene_id == scene_id
    assert saved[0].is_duplicate is False
    assert saved[0].dhash == "0123456789abcdef"
    # Frame 1 matches scene and is duplicate
    assert saved[1].scene_id == scene_id
    assert saved[1].is_duplicate is True
    assert saved[1].dhash == "0123456789abcdef"
    # Frame 2 has start_ms=6000 outside scene, so scene_id is None, no dhash provided
    assert saved[2].scene_id is None
    assert saved[2].is_duplicate is False
    assert saved[2].dhash is None


@pytest.mark.asyncio
async def test_get_frames_all_vs_only_kept(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    f0 = VideoFrameRow(
        id=uuid4(),
        video_id=video_id,
        scene_id=None,
        sequence=0,
        start_ms=0,
        end_ms=1000,
        source="periodic",
        dhash=None,
        is_duplicate=False,
        image_path="/tmp/0.jpg",
        created_at=datetime.now(UTC),
    )
    f1 = VideoFrameRow(
        id=uuid4(),
        video_id=video_id,
        scene_id=None,
        sequence=1,
        start_ms=1000,
        end_ms=2000,
        source="periodic",
        dhash=None,
        is_duplicate=True,
        image_path="/tmp/1.jpg",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([f0, f1])
    all_frames = await repo.get_frames(video_id, only_kept=False)
    assert len(all_frames) == 2
    stmt_all = str(session.statements[-1])
    assert "WHERE video_frames.is_duplicate" not in stmt_all

    session.scalars_values.append([f0])
    kept_frames = await repo.get_frames(video_id, only_kept=True)
    assert len(kept_frames) == 1
    stmt_kept = str(session.statements[-1])
    assert "video_frames.is_duplicate is false" in stmt_kept.lower()


@pytest.mark.asyncio
async def test_get_frame_found_and_not_found(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    f0 = VideoFrameRow(
        id=uuid4(),
        video_id=video_id,
        scene_id=None,
        sequence=0,
        start_ms=0,
        end_ms=1000,
        source="periodic",
        dhash=None,
        is_duplicate=False,
        image_path="/tmp/0.jpg",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([f0])
    frame = await repo.get_frame(video_id, 0)
    assert frame == f0

    session.scalars_values.append([])
    not_found = await repo.get_frame(video_id, 99)
    assert not_found is None


@pytest.mark.asyncio
async def test_save_ocr_results_existing_returns_early(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    existing_block = OCRBlockRow(
        id=uuid4(),
        video_id=video_id,
        frame_id=uuid4(),
        text="text",
        start_ms=0,
        end_ms=1000,
        confidence=0.9,
        confidence_method="average",
        provider="llamaparse",
        provider_revision="1",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([existing_block])
    saved = await repo.save_ocr_results(video_id, [])
    assert saved == (existing_block,)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_save_ocr_results_missing_frame_sequence_raises(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    # 1. get_ocr_blocks returns empty
    session.scalars_values.append([])
    # 2. get_frames returns empty
    session.scalars_values.append([])

    spec = OCRProviderSpec(provider="llamaparse", provider_revision="1")
    frame = FrameSample(
        video_id=video_id,
        sequence=5,
        start_ms=5000,
        end_ms=6000,
        path=Path("C:/tmp/5.jpg"),
        source=FrameSource.PERIODIC,
    )
    request = OCRExtractionRequest(job_id=uuid4(), video_id=video_id, frame=frame)
    result = OCRExtractionResult(
        request=request,
        blocks=(
            OCRBlock(
                text="Title",
                start_ms=5000,
                end_ms=6000,
                source_frame_sequence=5,
                provider_spec=spec,
            ),
        ),
        provider_spec=spec,
        extracted_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="Frame with sequence 5 not found"):
        await repo.save_ocr_results(video_id, [result])


@pytest.mark.asyncio
async def test_save_ocr_results_success(
    repo: SqlAlchemyVisionRepository, session: FakeSession
) -> None:
    video_id = uuid4()
    frame_id = uuid4()
    # 1. get_ocr_blocks returns empty
    session.scalars_values.append([])
    # 2. get_frames returns frame with sequence 0
    frame_row = VideoFrameRow(
        id=frame_id,
        video_id=video_id,
        scene_id=None,
        sequence=0,
        start_ms=0,
        end_ms=1000,
        source="periodic",
        dhash=None,
        is_duplicate=False,
        image_path="/tmp/0.jpg",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([frame_row])

    spec = OCRProviderSpec(provider="llamaparse", provider_revision="1")
    frame = FrameSample(
        video_id=video_id,
        sequence=0,
        start_ms=0,
        end_ms=1000,
        path=Path("C:/tmp/0.jpg"),
        source=FrameSource.PERIODIC,
    )
    request = OCRExtractionRequest(job_id=uuid4(), video_id=video_id, frame=frame)
    now = datetime.now(UTC)
    result = OCRExtractionResult(
        request=request,
        blocks=(
            OCRBlock(
                text="Slide text",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=0,
                provider_spec=spec,
                confidence=0.92,
                confidence_method="average",
            ),
        ),
        provider_spec=spec,
        extracted_at=now,
    )

    saved = await repo.save_ocr_results(video_id, [result])
    assert len(saved) == 1
    assert session.commits == 1
    assert saved[0].frame_id == frame_id
    assert saved[0].text == "Slide text"
    assert saved[0].confidence == 0.92
    assert saved[0].confidence_method == "average"
    assert saved[0].provider == "llamaparse"
    assert saved[0].created_at == now
