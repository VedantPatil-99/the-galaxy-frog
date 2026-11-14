"""Bounded Workers AI transcription for consented caption fallback."""

import asyncio
import base64
from datetime import UTC, datetime, time, timedelta
from math import ceil, floor
from time import perf_counter

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import CLOUDFLARE_MODEL
from galaxy_frog.domain.transcription.models import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_MS = 300_000


class _Error(BaseModel):
    code: int


class _Segment(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=4096)


class _Info(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)


class _Result(BaseModel):
    segments: tuple[_Segment, ...] = Field(min_length=1, max_length=2000)
    transcription_info: _Info | None = None


class _Envelope(BaseModel):
    success: bool
    result: _Result | None = None
    errors: tuple[_Error, ...] = ()


class CloudflareTranscription:
    spec = TranscriptionProviderSpec(
        provider="cloudflare",
        provider_revision="workers-ai-v4",
        model=CLOUDFLARE_MODEL,
        model_revision="unknown",
        device=TranscriptionDevice.CLOUD,
        compute_type=TranscriptionComputeType.UNKNOWN,
    )

    def __init__(self, account_id: str, token: str, client: httpx.AsyncClient) -> None:
        self._url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{CLOUDFLARE_MODEL}"
        )
        self._token = token
        self._client = client

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        started = perf_counter()
        if request.audio_end_ms - request.audio_start_ms > MAX_AUDIO_MS:
            raise ProviderFailure(ProviderCode.INPUT_LIMIT)
        try:

            def read_audio() -> bytes:
                with request.audio_path.open("rb") as stream:
                    return stream.read(MAX_AUDIO_BYTES + 1)

            audio = await asyncio.to_thread(read_audio)
        except OSError as exc:
            raise ProviderFailure(ProviderCode.UNAVAILABLE) from exc
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise ProviderFailure(ProviderCode.INPUT_LIMIT)
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"audio": base64.b64encode(audio).decode("ascii"), "task": "transcribe"},
            ) as response:
                payload = bytearray()
                async for part in response.aiter_bytes():
                    payload.extend(part)
                    if len(payload) > 1024 * 1024:
                        raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
                return self._convert(
                    response.status_code, response.headers, bytes(payload), request, started
                )
        except httpx.TimeoutException as exc:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from exc
        except httpx.RequestError as exc:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from exc

    def _convert(
        self,
        status: int,
        headers: httpx.Headers,
        payload: bytes,
        request: TranscriptionRequest,
        started: float,
    ) -> TranscriptionResult:
        if status in {401, 403}:
            raise ProviderFailure(ProviderCode.AUTH_FAILED)
        if status == 413:
            raise ProviderFailure(ProviderCode.INPUT_LIMIT)
        if status >= 500 or status == 408:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN)
        try:
            body = _Envelope.model_validate_json(payload)
        except ValidationError as exc:
            if status == 429:
                raise ProviderFailure(ProviderCode.RATE_LIMITED) from exc
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from exc
        if status == 429:
            now = datetime.now(UTC)
            if any(error.code == 3036 for error in body.errors):
                reset = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=UTC)
                raise ProviderFailure(ProviderCode.QUOTA_EXHAUSTED, retry_at=reset)
            retry_after = headers.get("retry-after", "")
            delay = (
                min(int(retry_after), 86_400)
                if retry_after.isascii() and retry_after.isdigit() and len(retry_after) < 8
                else 60
            )
            raise ProviderFailure(
                ProviderCode.RATE_LIMITED, retry_at=now + timedelta(seconds=delay)
            )
        if status != 200 or not body.success or body.result is None:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
        try:
            cues = tuple(
                TranscriptionCue(
                    source_order=index,
                    start_ms=floor(segment.start * 1000),
                    end_ms=min(request.audio_end_ms, ceil(segment.end * 1000)),
                    text=segment.text.strip(),
                    confidence=None,
                    confidence_method=None,
                )
                for index, segment in enumerate(
                    segment for segment in body.result.segments if segment.text.strip()
                )
            )
            info = body.result.transcription_info
            confidence = info.language_probability if info else None
            return TranscriptionResult(
                job_id=request.audio_job_id,
                attempt=request.attempt,
                source=request.source,
                fallback_reason=request.fallback_reason,
                audio_start_ms=request.audio_start_ms,
                audio_end_ms=request.audio_end_ms,
                spec=self.spec,
                language_code=(info.language if info else None) or request.language_code or "und",
                language_confidence=confidence,
                language_confidence_method="workers_ai_language_probability"
                if confidence is not None
                else None,
                cues=cues,
                processing_seconds=perf_counter() - started,
                transcribed_at=datetime.now(UTC),
            )
        except (ValueError, OverflowError) as exc:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from exc
