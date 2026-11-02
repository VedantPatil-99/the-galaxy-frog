"""Bounded worker acquisition, manifest validation, native isolation, and provider degradation."""

import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from tests.unit.db.test_visual_search import row

from galaxy_frog.adapters.media.process import CommandResult, CommandTimedOut
from galaxy_frog.adapters.vision.frame_pipeline import LocalFramePipeline
from galaxy_frog.adapters.vision.indexing import PersistVisionIndex
from galaxy_frog.adapters.vision.llamaparse import LlamaParseOCRAdapter
from galaxy_frog.adapters.vision.scene_detect import PySceneDetectAdapter
from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.application.ingestion.runner import IngestionStageError, StageContext, StageResult
from galaxy_frog.application.ingestion.vision_stage import (
    ContinueToVision,
    VisionIndex,
    VisionIndexingStage,
    VisionIndexResult,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionStage
from galaxy_frog.domain.videos.models import SafeVideoMetadata, SourceReference, VideoSourceKind
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.vision.errors import OCRExtractionError
from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    FrameSource,
    OCRExtractionRequest,
    SceneBoundary,
    SceneDetectionResult,
)
from galaxy_frog.domain.vision.visual import VisualFailureCode, VisualProviderError
from galaxy_frog.entrypoints import frames, worker


def video() -> VideoRecord:
    return VideoRecord(
        uuid4(),
        SafeVideoMetadata(
            SourceReference(
                VideoSourceKind.YOUTUBE,
                "abcdefghijk",
                "https://www.youtube.com/watch?v=abcdefghijk",
            ),
            "fixture",
            1000,
        ),
    )


class FrameRunner:
    def __init__(self, failure: str = "") -> None:
        self.failure = failure

    async def run(
        self, arguments: Sequence[str], *, cwd: Path, timeout_seconds: float
    ) -> CommandResult:
        return self.execute(arguments, cwd)

    def execute(self, arguments: Sequence[str], cwd: Path) -> CommandResult:
        if self.failure == "timeout":
            raise CommandTimedOut()
        if self.failure == "os":
            raise OSError("private")
        if "yt_dlp" in arguments:
            (cwd / "source.mp4").write_bytes(b"video")
            return CommandResult(tuple(arguments), int(self.failure == "download"), "", "")
        payload = json.loads(Path(arguments[-1]).read_text(encoding="utf-8"))
        value = {
            "scenes": [
                {"start_ms": 0, "end_ms": 1001 if self.failure == "scene" else 1000, "score": 1}
            ],
            "detector": "fake",
            "revision": "v1",
            "frames": [
                {
                    "sequence": 1 if self.failure == "sequence" else 0,
                    "start_ms": 0,
                    "capture_ms": 0,
                    "end_ms": 1001 if self.failure == "frame" else 1000,
                    "source": "periodic",
                    "kept": True,
                    "dhash": "0" * 16,
                }
            ],
        }
        Path(payload["result"]).write_text(
            "x" * 262145 if self.failure == "manifest" else json.dumps(value), encoding="utf-8"
        )
        (cwd / "frame_00000.jpg").write_bytes(b"jpeg")
        return CommandResult(tuple(arguments), int(self.failure == "native"), "", "")


@pytest.mark.asyncio
async def test_local_pipeline_retains_frames_and_cleans_source(tmp_path: Path) -> None:
    result = await LocalFramePipeline(
        Settings(vision_workspace_root=tmp_path), FrameRunner()
    ).extract(video())
    assert len(result[1]) == 1 and result[1][0].path.exists()
    assert not (result[1][0].path.parent / "source.mp4").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "os", "download", "native", "manifest", "sequence", "scene", "frame"]
)
async def test_pipeline_safe_failures(tmp_path: Path, failure: str) -> None:
    with pytest.raises(VisualProviderError):
        await LocalFramePipeline(
            Settings(vision_workspace_root=tmp_path), FrameRunner(failure)
        ).extract(video())


@pytest.mark.asyncio
async def test_pipeline_rejects_unsupported_or_oversized_video(tmp_path: Path) -> None:
    item = video()
    local = replace(
        item,
        metadata=replace(
            item.metadata,
            reference=SourceReference(VideoSourceKind.LOCAL_FILE, "local", "file:///local.json"),
        ),
    )
    for source in (local, replace(item, metadata=replace(item.metadata, duration_ms=7200001))):
        with pytest.raises(VisualProviderError):
            await LocalFramePipeline(Settings(vision_workspace_root=tmp_path)).extract(source)


def test_source_file_validation(tmp_path: Path) -> None:
    pipeline = LocalFramePipeline(Settings(media_max_download_bytes=4))
    for value in (None, b"", b"12345"):
        if value is not None:
            (tmp_path / "source.mp4").write_bytes(value)
        with pytest.raises(ValueError):
            pipeline._source(tmp_path)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "new",
        "extract_failure",
        "denied",
        "ocr_error",
        "ocr_saved",
        "embedding_error",
        "success",
    ],
)
async def test_index_retains_transcript_and_reports_providers(tmp_path: Path, mode: str) -> None:
    item = video()
    videos, store, pipeline, visual, ocr = (
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
        AsyncMock(),
    )
    videos.get_video.return_value = None if mode == "missing" else item
    original, duplicate = row(tmp_path, item.video_id), row(tmp_path, item.video_id, 1)
    duplicate.is_duplicate = True
    store.get_frames.return_value = (
        () if mode in {"new", "extract_failure"} else (original, duplicate)
    )
    store.save_frames.return_value = (original,)
    store.get_ocr_blocks.return_value = (Mock(),) if mode == "ocr_saved" else ()
    sample = FrameSample(item.video_id, 0, 0, 1000, Path(original.image_path), FrameSource.PERIODIC)
    pipeline.extract.return_value = (
        SceneDetectionResult(item.video_id, 1000, (SceneBoundary(0, 1000, 1),), "fake", "v1"),
        (sample,),
        DeduplicatedFrameSet((sample,), 0, "dhash", 4),
        {0: "0" * 16},
    )
    if mode == "extract_failure":
        pipeline.extract.side_effect = VisualProviderError(VisualFailureCode.TIMEOUT)
    if mode == "ocr_error":
        ocr.extract.side_effect = OCRExtractionError("private")
    if mode == "embedding_error":
        visual.ensure_indexed.side_effect = VisualProviderError(VisualFailureCode.UNAVAILABLE)
    visual.ensure_indexed.return_value = uuid4()
    index = PersistVisionIndex(
        videos=cast(SqlAlchemyVideoRepository, videos),
        vision=cast(SqlAlchemyVisionRepository, store),
        pipeline=cast(LocalFramePipeline, pipeline),
        visual=None if mode == "new" else visual,
        ocr=None if mode == "new" else ocr,
        ocr_permitted=mode != "denied",
    )
    if mode == "missing":
        with pytest.raises(ValueError):
            await index.execute(item.video_id, uuid4())
        return
    result = await index.execute(item.video_id, uuid4())
    assert result.frame_count >= 0
    if mode in {"denied", "extract_failure", "new", "embedding_error", "ocr_error"}:
        assert result.warnings
    if mode in {"denied", "ocr_saved"}:
        ocr.extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_durable_stage_and_missing_video() -> None:
    item = video()
    job = cast(IngestionJob, Mock(job_id=uuid4(), source=item.metadata.reference))
    context = StageContext(
        cast(IngestionRepository, Mock()), job.job_id, "worker", timedelta(seconds=120)
    )
    handler, videos, index = AsyncMock(), AsyncMock(), AsyncMock()
    handler.execute.return_value = StageResult(IngestionStage.CLEANUP)
    assert (
        await ContinueToVision(handler).execute(job, context)
    ).next_stage == IngestionStage.INDEXING
    videos.find_by_source.return_value = item
    index.execute.return_value = VisionIndexResult(2, None, ("visual_provider_unavailable",))
    stage = VisionIndexingStage(videos, cast(VisionIndex, index))
    result = await stage.execute(job, context)
    assert result.details and result.details["visual_degraded"]
    index.execute.assert_awaited_once_with(item.video_id, job.job_id)
    videos.find_by_source.return_value = None
    with pytest.raises(IngestionStageError):
        await stage.execute(job, context)


def test_worker_configuration_and_native_frame_budget(tmp_path: Path) -> None:
    for enabled in (False, True):
        worker.build_worker(
            settings=Settings(
                vision_ingestion_enabled=enabled,
                visual_embedding_enabled=enabled,
                llama_cloud_api_key=SecretStr("fake"),
            ),
            session=cast(AsyncSession, Mock()),
            worker_id="fixture",
        )
    with pytest.raises(ValueError):
        PySceneDetectAdapter(workspace_dir=tmp_path, max_frames=0)


@pytest.mark.asyncio
async def test_frame_budget_rejects_before_extraction(tmp_path: Path) -> None:
    adapter = PySceneDetectAdapter(workspace_dir=tmp_path, max_frames=1)
    with (
        patch("galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync", return_value=[]),
        pytest.raises(ValueError, match="frame budget"),
    ):
        await adapter.detect(tmp_path / "video.mp4", uuid4(), 30000)


def test_child_entry_success_and_safe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = video()
    sample = FrameSample(item.video_id, 0, 0, 1000, tmp_path / "frame.jpg", FrameSource.PERIODIC)
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {
                "workspace": str(tmp_path),
                "source": str(tmp_path / "source.mp4"),
                "video_id": str(item.video_id),
                "duration_ms": 1000,
                "max_frames": 1,
                "result": str(result),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["frames", str(request)])
    with (
        patch.object(
            frames,
            "PySceneDetectAdapter",
            return_value=Mock(
                detect=AsyncMock(
                    return_value=(
                        SceneDetectionResult(
                            item.video_id, 1000, (SceneBoundary(0, 1000, 1),), "fake", "v1"
                        ),
                        (sample,),
                    )
                )
            ),
        ),
        patch.object(
            frames,
            "OpenCVDHashDeduplicator",
            return_value=Mock(
                deduplicate=AsyncMock(return_value=DeduplicatedFrameSet((sample,), 0, "dhash", 4))
            ),
        ),
        patch.object(frames, "compute_dhash_sync", return_value=Mock(hex_digest="0" * 16)),
    ):
        frames.main()
    assert json.loads(result.read_text(encoding="utf-8"))["frames"][0]["kept"]
    request.write_text("invalid", encoding="utf-8")
    with pytest.raises(SystemExit):
        frames.main()


@pytest.mark.asyncio
async def test_ocr_credential_never_implies_frame_permission(tmp_path: Path) -> None:
    item = video()
    sample = FrameSample(item.video_id, 0, 0, 1000, tmp_path / "not-read.jpg", FrameSource.PERIODIC)
    with pytest.raises(OCRExtractionError, match="external_processing_not_permitted"):
        await LlamaParseOCRAdapter(api_key="fake").extract(
            OCRExtractionRequest(uuid4(), item.video_id, sample)
        )
