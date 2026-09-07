"""Timestamp-preserving multilingual transcription through faster-whisper."""

import asyncio
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from importlib import metadata
from math import ceil, floor, fsum, isfinite
from time import perf_counter
from typing import Protocol, cast

from faster_whisper import WhisperModel  # pyright: ignore[reportMissingTypeStubs]

from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)

_CUE_CONFIDENCE_METHOD = "mean_word_probability"
_LANGUAGE_CONFIDENCE_METHOD = "provider_language_probability"


class _Word(Protocol):
    @property
    def probability(self) -> float: ...


class _Segment(Protocol):
    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...

    @property
    def text(self) -> str: ...

    @property
    def words(self) -> Sequence[_Word] | None: ...


class _TranscriptionInfo(Protocol):
    @property
    def language(self) -> str: ...

    @property
    def language_probability(self) -> float: ...


class _Model(Protocol):
    def transcribe(
        self,
        audio: str,
        *,
        language: str | None,
        beam_size: int,
        word_timestamps: bool,
        vad_filter: bool,
    ) -> tuple[Iterable[_Segment], _TranscriptionInfo]: ...


class _ModelFactory(Protocol):
    def __call__(
        self,
        model: str,
        *,
        device: str,
        compute_type: str,
        revision: str,
        num_workers: int,
    ) -> _Model: ...


def _default_model_factory(
    model: str,
    *,
    device: str,
    compute_type: str,
    revision: str,
    num_workers: int,
) -> _Model:
    return cast(
        _Model,
        WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            revision=revision,
            num_workers=num_workers,
        ),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FasterWhisperTranscriptionProvider:
    """Run one explicitly configured local faster-whisper model."""

    def __init__(
        self,
        *,
        model: str,
        model_revision: str,
        device: TranscriptionDevice,
        compute_type: TranscriptionComputeType,
        max_concurrency: int,
        provider_revision: str | None = None,
        model_factory: _ModelFactory = _default_model_factory,
        clock: Callable[[], float] = perf_counter,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if max_concurrency < 1:
            msg = "max_concurrency must be at least one"
            raise ValueError(msg)
        self.spec = TranscriptionProviderSpec(
            provider="faster-whisper",
            provider_revision=provider_revision or metadata.version("faster-whisper"),
            model=model,
            model_revision=model_revision,
            device=device,
            compute_type=compute_type,
        )
        self._max_concurrency = max_concurrency
        self._model_factory = model_factory
        self._clock = clock
        self._now = now
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._model_lock = asyncio.Lock()
        self._model: _Model | None = None

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Transcribe one readable normalized artifact without changing provider silently."""

        try:
            is_file = await asyncio.to_thread(request.audio_path.is_file)
        except OSError as exc:
            raise self._invalid_audio_error() from exc
        if not is_file:
            raise self._invalid_audio_error()

        async with self._semaphore:
            started_at = self._clock()
            model = await self._get_model()
            try:
                segments, info = await asyncio.to_thread(self._run_model, model, request)
            except Exception as exc:
                raise self._inference_error(exc) from exc
            processing_seconds = max(0.0, self._clock() - started_at)

        try:
            cues = self._convert_cues(segments, request)
            if not cues:
                raise TranscriptionError(
                    TranscriptionErrorCode.EXECUTION_FAILED,
                    "The transcription provider returned no timestamped speech.",
                )
            language_code = info.language.strip().casefold()
            if not language_code:
                raise ValueError("provider language is blank")
            language_confidence = self._score(info.language_probability)
            return TranscriptionResult(
                job_id=request.audio_job_id,
                attempt=request.attempt,
                source=request.source,
                fallback_reason=request.fallback_reason,
                audio_start_ms=request.audio_start_ms,
                audio_end_ms=request.audio_end_ms,
                spec=self.spec,
                language_code=language_code,
                language_confidence=language_confidence,
                language_confidence_method=(
                    _LANGUAGE_CONFIDENCE_METHOD if language_confidence is not None else None
                ),
                cues=cues,
                processing_seconds=processing_seconds,
                transcribed_at=self._now(),
            )
        except TranscriptionError:
            raise
        except (OverflowError, TypeError, ValueError) as exc:
            raise TranscriptionError(
                TranscriptionErrorCode.EXECUTION_FAILED,
                "The transcription provider returned invalid evidence metadata.",
            ) from exc

    async def _get_model(self) -> _Model:
        async with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = await asyncio.to_thread(
                    self._model_factory,
                    self.spec.model,
                    device=self.spec.device.value,
                    compute_type=self.spec.compute_type.value,
                    revision=self.spec.model_revision,
                    num_workers=self._max_concurrency,
                )
            except (ImportError, ModuleNotFoundError) as exc:
                raise TranscriptionError(
                    TranscriptionErrorCode.DEPENDENCY_UNAVAILABLE,
                    "The configured transcription runtime is unavailable.",
                ) from exc
            except Exception as exc:
                raise self._model_error(exc) from exc
            return self._model

    @staticmethod
    def _run_model(
        model: _Model,
        request: TranscriptionRequest,
    ) -> tuple[tuple[_Segment, ...], _TranscriptionInfo]:
        segments, info = model.transcribe(
            str(request.audio_path),
            language=request.language_code,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )
        return tuple(segments), info

    @classmethod
    def _convert_cues(
        cls,
        segments: Sequence[_Segment],
        request: TranscriptionRequest,
    ) -> tuple[TranscriptionCue, ...]:
        cues: list[TranscriptionCue] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            start_seconds = float(segment.start)
            end_seconds = float(segment.end)
            if not isfinite(start_seconds) or not isfinite(end_seconds):
                raise ValueError("provider interval is not finite")
            start_ms = max(
                request.audio_start_ms,
                request.audio_start_ms + floor(start_seconds * 1000),
            )
            end_ms = min(
                request.audio_end_ms,
                request.audio_start_ms + ceil(end_seconds * 1000),
            )
            if start_ms >= request.audio_end_ms or end_ms <= start_ms:
                continue
            confidence = cls._word_confidence(segment.words)
            cues.append(
                TranscriptionCue(
                    source_order=len(cues),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    confidence=confidence,
                    confidence_method=(_CUE_CONFIDENCE_METHOD if confidence is not None else None),
                )
            )
        return tuple(cues)

    @classmethod
    def _word_confidence(cls, words: Sequence[_Word] | None) -> float | None:
        if not words:
            return None
        scores = tuple(
            score for word in words if (score := cls._score(word.probability)) is not None
        )
        return fsum(scores) / len(scores) if scores else None

    @staticmethod
    def _score(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        score = float(value)
        return score if isfinite(score) and 0 <= score <= 1 else None

    def _model_error(self, error: Exception) -> TranscriptionError:
        if self._is_device_error(error):
            return self._device_error()
        return TranscriptionError(
            TranscriptionErrorCode.MODEL_UNAVAILABLE,
            "The configured transcription model could not be loaded.",
            retryable=True,
        )

    def _inference_error(self, error: Exception) -> TranscriptionError:
        if self._is_device_error(error):
            return self._device_error()
        message = str(error).casefold()
        if any(token in message for token in ("audio", "decode", "av.error", "invalid data")):
            return self._invalid_audio_error()
        return TranscriptionError(
            TranscriptionErrorCode.EXECUTION_FAILED,
            "The transcription provider could not process the audio.",
            retryable=True,
        )

    def _is_device_error(self, error: Exception) -> bool:
        if self.spec.device is not TranscriptionDevice.CUDA:
            return False
        message = str(error).casefold()
        return any(
            token in message for token in ("cuda", "cublas", "cudnn", "gpu", "out of memory")
        )

    @staticmethod
    def _invalid_audio_error() -> TranscriptionError:
        return TranscriptionError(
            TranscriptionErrorCode.INVALID_AUDIO,
            "The normalized audio artifact is missing or unreadable.",
        )

    @staticmethod
    def _device_error() -> TranscriptionError:
        return TranscriptionError(
            TranscriptionErrorCode.DEVICE_UNAVAILABLE,
            "The configured transcription device is unavailable.",
        )
