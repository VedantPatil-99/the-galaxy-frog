"""Behavior coverage for the local faster-whisper adapter."""

# pyright: reportPrivateUsage=false

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from galaxy_frog.adapters.transcription import FasterWhisperTranscriptionProvider
from galaxy_frog.adapters.transcription import faster_whisper as adapter_module
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionDevice,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionRequest,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind


@dataclass(frozen=True)
class FakeWord:
    probability: float


@dataclass(frozen=True)
class FakeSegment:
    start: float
    end: float
    text: str
    words: tuple[FakeWord, ...] | None = None


@dataclass(frozen=True)
class FakeInfo:
    language: str = "hi"
    language_probability: float = 0.87


class FakeModel:
    def __init__(
        self,
        segments: tuple[FakeSegment, ...],
        info: FakeInfo | None = None,
        error: Exception | None = None,
    ) -> None:
        self.segments = segments
        self.info = info or FakeInfo()
        self.error = error
        self.calls: list[tuple[str, str | None, int, bool, bool]] = []

    def transcribe(
        self,
        audio: str,
        *,
        language: str | None,
        beam_size: int,
        word_timestamps: bool,
        vad_filter: bool,
    ) -> tuple[Iterable[FakeSegment], FakeInfo]:
        self.calls.append((audio, language, beam_size, word_timestamps, vad_filter))
        if self.error is not None:
            raise self.error
        return iter(self.segments), self.info


class FakeFactory:
    def __init__(self, model: FakeModel | None = None, error: Exception | None = None) -> None:
        self.model = model or FakeModel(())
        self.error = error
        self.calls: list[tuple[str, str, str, str, int]] = []

    def __call__(
        self,
        model: str,
        *,
        device: str,
        compute_type: str,
        revision: str,
        num_workers: int,
    ) -> FakeModel:
        self.calls.append((model, device, compute_type, revision, num_workers))
        if self.error is not None:
            raise self.error
        return self.model


def request(audio_path: Path, *, language: str | None = None) -> TranscriptionRequest:
    return TranscriptionRequest(
        audio_job_id=uuid4(),
        attempt=2,
        source=SourceReference(
            VideoSourceKind.YOUTUBE,
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_path=audio_path,
        audio_start_ms=0,
        audio_end_ms=4000,
        language_code=language,
    )


def provider(
    factory: FakeFactory,
    *,
    device: TranscriptionDevice = TranscriptionDevice.CUDA,
    clocks: Iterable[float] = (10.0, 11.25),
) -> FasterWhisperTranscriptionProvider:
    clock_values = iter(clocks)
    return FasterWhisperTranscriptionProvider(
        model="small",
        model_revision="536b0662742c02347bc0e980a01041f333bce120",
        device=device,
        compute_type=(
            TranscriptionComputeType.INT8_FLOAT16
            if device is TranscriptionDevice.CUDA
            else TranscriptionComputeType.INT8
        ),
        max_concurrency=1,
        provider_revision="1.2.1",
        model_factory=factory,
        clock=lambda: next(clock_values),
        now=lambda: datetime(2026, 9, 7, 12, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_transcribes_multilingual_audio_with_exact_provenance(tmp_path: Path) -> None:
    audio_path = (tmp_path / "audio.wav").resolve()
    audio_path.write_bytes(b"RIFF")
    model = FakeModel(
        (
            FakeSegment(0.0004, 0.9991, " Namaste ", (FakeWord(0.8), FakeWord(1.0))),
            FakeSegment(1.0, 2.0, "  ", (FakeWord(0.5),)),
            FakeSegment(3.9, 4.5, "Galaxy Frog", (FakeWord(float("nan")),)),
        ),
        FakeInfo(language=" HI ", language_probability=0.87),
    )
    factory = FakeFactory(model)
    transcription_request = request(audio_path, language="hi")
    transcription = await provider(factory).transcribe(transcription_request)

    assert factory.calls == [
        (
            "small",
            "cuda",
            "int8_float16",
            "536b0662742c02347bc0e980a01041f333bce120",
            1,
        )
    ]
    assert model.calls == [(str(audio_path), "hi", 5, True, True)]
    assert transcription.language_code == "hi"
    assert transcription.language_confidence == 0.87
    assert transcription.language_confidence_method == "provider_language_probability"
    assert transcription.processing_seconds == 1.25
    assert transcription.transcribed_at == datetime(2026, 9, 7, 12, tzinfo=UTC)
    assert transcription.job_id == transcription_request.audio_job_id
    assert transcription.attempt == transcription_request.attempt
    assert transcription.source == transcription_request.source
    assert transcription.fallback_reason == transcription_request.fallback_reason
    assert (transcription.audio_start_ms, transcription.audio_end_ms) == (0, 4000)
    assert transcription.spec.provider_revision == "1.2.1"
    assert transcription.spec.model_revision == "536b0662742c02347bc0e980a01041f333bce120"
    assert [
        (cue.source_order, cue.start_ms, cue.end_ms, cue.text, cue.confidence)
        for cue in transcription.cues
    ] == [
        (0, 0, 1000, "Namaste", 0.9),
        (1, 3900, 4000, "Galaxy Frog", None),
    ]
    assert transcription.cues[0].confidence_method == "mean_word_probability"
    assert transcription.cues[1].confidence_method is None


@pytest.mark.asyncio
async def test_reuses_lazy_model_and_allows_language_detection(tmp_path: Path) -> None:
    audio_path = (tmp_path / "audio.wav").resolve()
    audio_path.write_bytes(b"RIFF")
    model = FakeModel((FakeSegment(0.0, 1.0, "hello"),))
    factory = FakeFactory(model)
    transcription_provider = provider(factory, clocks=(2.0, 1.0, 4.0, 4.5))

    first = await transcription_provider.transcribe(request(audio_path))
    second = await transcription_provider.transcribe(request(audio_path))

    assert len(factory.calls) == 1
    assert [call[1] for call in model.calls] == [None, None]
    assert first.processing_seconds == 0.0
    assert second.processing_seconds == 0.5
    assert first.language_confidence == 0.87


@pytest.mark.asyncio
async def test_rejects_missing_or_unreadable_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_path = (tmp_path / "missing.wav").resolve()
    factory = FakeFactory()
    transcription_provider = provider(factory)

    with pytest.raises(TranscriptionError) as missing:
        await transcription_provider.transcribe(request(audio_path))
    assert missing.value.code is TranscriptionErrorCode.INVALID_AUDIO

    def unreadable(_path: Path) -> bool:
        raise OSError("private path")

    monkeypatch.setattr(Path, "is_file", unreadable)
    with pytest.raises(TranscriptionError) as unreadable_error:
        await transcription_provider.transcribe(request(audio_path))
    assert unreadable_error.value.code is TranscriptionErrorCode.INVALID_AUDIO
    assert factory.calls == []


@pytest.mark.parametrize(
    ("error", "device", "code", "retryable"),
    [
        (
            ImportError("missing"),
            TranscriptionDevice.CUDA,
            TranscriptionErrorCode.DEPENDENCY_UNAVAILABLE,
            False,
        ),
        (
            RuntimeError("cublas64_12.dll missing"),
            TranscriptionDevice.CUDA,
            TranscriptionErrorCode.DEVICE_UNAVAILABLE,
            False,
        ),
        (
            OSError("network unavailable"),
            TranscriptionDevice.CUDA,
            TranscriptionErrorCode.MODEL_UNAVAILABLE,
            True,
        ),
        (
            RuntimeError("CUDA ignored on CPU"),
            TranscriptionDevice.CPU,
            TranscriptionErrorCode.MODEL_UNAVAILABLE,
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_normalizes_model_loading_failures(
    tmp_path: Path,
    error: Exception,
    device: TranscriptionDevice,
    code: TranscriptionErrorCode,
    retryable: bool,
) -> None:
    audio_path = (tmp_path / "audio.wav").resolve()
    audio_path.write_bytes(b"RIFF")

    with pytest.raises(TranscriptionError) as raised:
        await provider(FakeFactory(error=error), device=device).transcribe(request(audio_path))

    assert raised.value.code is code
    assert raised.value.retryable is retryable
    assert str(error) not in raised.value.message


@pytest.mark.parametrize(
    ("error", "device", "code", "retryable"),
    [
        (
            RuntimeError("GPU out of memory"),
            TranscriptionDevice.CUDA,
            TranscriptionErrorCode.DEVICE_UNAVAILABLE,
            False,
        ),
        (
            ValueError("audio decode failed"),
            TranscriptionDevice.CUDA,
            TranscriptionErrorCode.INVALID_AUDIO,
            False,
        ),
        (
            RuntimeError("unknown inference failure"),
            TranscriptionDevice.CUDA,
            TranscriptionErrorCode.EXECUTION_FAILED,
            True,
        ),
        (
            RuntimeError("CUDA text on CPU"),
            TranscriptionDevice.CPU,
            TranscriptionErrorCode.EXECUTION_FAILED,
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_normalizes_inference_failures(
    tmp_path: Path,
    error: Exception,
    device: TranscriptionDevice,
    code: TranscriptionErrorCode,
    retryable: bool,
) -> None:
    audio_path = (tmp_path / "audio.wav").resolve()
    audio_path.write_bytes(b"RIFF")
    model = FakeModel((FakeSegment(0.0, 1.0, "hello"),), error=error)

    with pytest.raises(TranscriptionError) as raised:
        await provider(FakeFactory(model), device=device).transcribe(request(audio_path))

    assert raised.value.code is code
    assert raised.value.retryable is retryable
    assert str(error) not in raised.value.message


@pytest.mark.asyncio
async def test_rejects_empty_and_invalid_provider_metadata(tmp_path: Path) -> None:
    audio_path = (tmp_path / "audio.wav").resolve()
    audio_path.write_bytes(b"RIFF")

    for model in (
        FakeModel(()),
        FakeModel((FakeSegment(float("nan"), 1.0, "bad"),)),
        FakeModel((FakeSegment(4.0, 4.0, "outside"),)),
        FakeModel((FakeSegment(0.0, 1.0, "hello"),), FakeInfo(language=" ")),
    ):
        with pytest.raises(TranscriptionError) as raised:
            await provider(FakeFactory(model)).transcribe(request(audio_path))
        assert raised.value.code is TranscriptionErrorCode.EXECUTION_FAILED


def test_validates_concurrency_and_reads_installed_provider_revision() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        FasterWhisperTranscriptionProvider(
            model="small",
            model_revision="revision",
            device=TranscriptionDevice.CPU,
            compute_type=TranscriptionComputeType.INT8,
            max_concurrency=0,
        )

    transcription_provider = FasterWhisperTranscriptionProvider(
        model="small",
        model_revision="revision",
        device=TranscriptionDevice.CPU,
        compute_type=TranscriptionComputeType.INT8,
        max_concurrency=1,
        model_factory=FakeFactory(),
    )
    assert transcription_provider.spec.provider_revision == "1.2.1"
    assert adapter_module._utc_now().tzinfo is UTC
    assert adapter_module.FasterWhisperTranscriptionProvider._score(True) is None
    assert adapter_module.FasterWhisperTranscriptionProvider._score("bad") is None


def test_default_factory_forwards_explicit_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Model:
        def __init__(self, model: str, **kwargs: object) -> None:
            captured["model"] = model
            captured.update(kwargs)

    monkeypatch.setattr(adapter_module, "_load_model_factory", lambda: Model)
    created = adapter_module._default_model_factory(
        "small",
        device="cuda",
        compute_type="int8_float16",
        revision="revision",
        num_workers=1,
    )

    assert cast(object, created) is not None
    assert captured == {
        "model": "small",
        "device": "cuda",
        "compute_type": "int8_float16",
        "revision": "revision",
        "num_workers": 1,
    }
