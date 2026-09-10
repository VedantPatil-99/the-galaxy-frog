"""Behavior coverage for the durable worker process wiring."""

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.application.ingestion.worker import IngestionWorker
from galaxy_frog.config import Settings
from galaxy_frog.domain.media import AudioAcquirer
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionDevice,
    TranscriptionProvider,
)
from galaxy_frog.entrypoints import worker as worker_entrypoint


def settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "app_env": "test",
            "database_url": "postgresql+asyncpg://galaxy_frog:test@localhost/galaxy_frog",
            **overrides,
        }
    )


def test_resolve_worker_id_honors_explicit_or_process_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        worker_entrypoint.resolve_worker_id(settings(ingestion_worker_id="worker-a")) == "worker-a"
    )

    monkeypatch.setattr(worker_entrypoint.socket, "gethostname", lambda: "frog-host")
    monkeypatch.setattr(worker_entrypoint.os, "getpid", lambda: 42)
    assert worker_entrypoint.resolve_worker_id(settings()) == "frog-host-42"


def test_build_worker_composes_the_caption_pipeline() -> None:
    result = worker_entrypoint.build_worker(
        settings=settings(ingestion_lease_seconds=180, ingestion_poll_seconds=0.5),
        session=cast(AsyncSession, object()),
        worker_id="worker-a",
    )

    assert isinstance(result, IngestionWorker)


def test_build_audio_acquirer_composes_bounded_local_providers(tmp_path: Path) -> None:
    result = worker_entrypoint.build_audio_acquirer(
        settings(
            media_workspace_root=tmp_path,
            media_max_duration_seconds=60,
            media_max_download_bytes=1024,
            media_max_output_bytes=2048,
            media_timeout_seconds=30,
            media_max_concurrency=2,
            media_retain_on_success=True,
            ffmpeg_executable="C:/Tools/ffmpeg.exe",
            ffprobe_executable="C:/Tools/ffprobe.exe",
        )
    )

    assert isinstance(result, AudioAcquirer)


def test_build_transcription_provider_uses_the_explicit_model_and_device() -> None:
    result = worker_entrypoint.build_transcription_provider(
        settings(
            asr_model="small",
            asr_model_revision="immutable-revision",
            asr_device="cuda",
            asr_compute_type="int8_float16",
            asr_max_concurrency=1,
        )
    )

    assert isinstance(result, TranscriptionProvider)
    assert result.spec.model == "small"
    assert result.spec.model_revision == "immutable-revision"
    assert result.spec.device is TranscriptionDevice.CUDA
    assert result.spec.compute_type is TranscriptionComputeType.INT8_FLOAT16


class RecordingEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class RecordingWorker:
    def __init__(self) -> None:
        self.stop_event: asyncio.Event | None = None

    async def run_until_stopped(self, stop_event: asyncio.Event) -> int:
        self.stop_event = stop_event
        return 3


@pytest.mark.asyncio
async def test_run_worker_uses_one_session_and_disposes_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = RecordingEngine()
    recording_worker = RecordingWorker()
    stop_event = asyncio.Event()
    received: dict[str, object] = {}

    def build_worker(**arguments: object) -> IngestionWorker:
        received.update(arguments)
        return cast(IngestionWorker, recording_worker)

    monkeypatch.setattr(worker_entrypoint, "build_worker", build_worker)

    result = await worker_entrypoint.run_worker(
        settings(ingestion_worker_id="worker-a"),
        stop_event,
        engine_factory=lambda _settings: cast(AsyncEngine, engine),
        session_factory=async_sessionmaker(class_=AsyncSession, expire_on_commit=False),
    )

    assert result == 3
    assert received["worker_id"] == "worker-a"
    assert isinstance(received["session"], AsyncSession)
    assert recording_worker.stop_event is stop_event
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_run_worker_creates_a_stop_event_when_not_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = RecordingEngine()
    recording_worker = RecordingWorker()
    sessions = async_sessionmaker(class_=AsyncSession, expire_on_commit=False)

    def build_worker(**_arguments: object) -> IngestionWorker:
        return cast(IngestionWorker, recording_worker)

    def session_factory(
        *_arguments: object,
        **_keywords: object,
    ) -> async_sessionmaker[AsyncSession]:
        return sessions

    monkeypatch.setattr(
        worker_entrypoint,
        "build_worker",
        build_worker,
    )
    monkeypatch.setattr(
        worker_entrypoint,
        "async_sessionmaker",
        session_factory,
    )

    assert (
        await worker_entrypoint.run_worker(
            settings(),
            engine_factory=lambda _settings: cast(AsyncEngine, engine),
        )
        == 3
    )
    assert recording_worker.stop_event is not None
    assert engine.disposed is True


def test_main_runs_the_async_worker_and_allows_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(coroutine: Coroutine[Any, Any, int]) -> int:
        nonlocal calls
        calls += 1
        coroutine.close()
        if calls == 2:
            raise KeyboardInterrupt
        return 0

    monkeypatch.setattr(worker_entrypoint.asyncio, "run", run)

    worker_entrypoint.main()
    worker_entrypoint.main()

    assert calls == 2
