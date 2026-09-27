"""Offline CUDA BGE reranking using a bounded, serialized child process."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandRunner, CommandTimedOut
from galaxy_frog.domain.retrieval.reranking import (
    BGE_RERANKER_MODEL,
    BGE_RERANKER_REVISION,
    RerankCandidate,
    RerankerError,
    RerankerErrorCode,
    RerankResult,
    RerankScore,
)


class _Score(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    unit_id: str = Field(min_length=64, max_length=64)
    score: float
    original_tokens: int = Field(ge=1)
    input_tokens: int = Field(ge=1, le=512)
    truncated: bool
    inference_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_truncation(self) -> _Score:
        if self.original_tokens < self.input_tokens or self.truncated != (
            self.original_tokens > self.input_tokens
        ):
            raise ValueError("inconsistent truncation metadata")
        return self


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    scores: list[_Score] = Field(min_length=1, max_length=30)
    model: str
    revision: str
    torch_version: str
    transformers_version: str
    cuda_version: str = Field(min_length=1)
    gpu: str = Field(min_length=1)
    model_load_ms: float = Field(ge=0)
    processing_ms: float = Field(ge=0)
    peak_allocated_mib: float = Field(ge=0)
    peak_reserved_mib: float = Field(ge=0)


class _Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: RerankerErrorCode


class BgeTranscriptReranker:
    """One request per instance, no queue, no implicit runtime/model downloads or CPU path.

    A process owns the model for one request and exits afterward, releasing its VRAM. The existing
    process runner kills and reaps it on deadline or cancellation. Share one instance per API process.
    """

    def __init__(
        self,
        *,
        python: Path | None = None,
        timeout_seconds: float = 120,
        runner: CommandRunner | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= 600:
            raise ValueError("reranker timeout must be between zero and 600 seconds")
        self._python = (python or Path(sys.executable)).resolve()
        self._timeout_seconds = timeout_seconds
        self._runner = runner or AsyncSubprocessRunner()
        self._lock = asyncio.Lock()

    async def rerank(self, query: str, candidates: tuple[RerankCandidate, ...]) -> RerankResult:
        if not query.strip() or len(query) > 2000 or not 1 <= len(candidates) <= 30:
            raise ValueError("reranking requires a bounded query and 1 to 30 candidates")
        if len({candidate.unit_id for candidate in candidates}) != len(candidates):
            raise ValueError("rerank candidates must have unique IDs")
        if any(
            len(candidate.unit_id) != 64
            or not candidate.text.strip()
            or len(candidate.text) > 20000
            for candidate in candidates
        ):
            raise ValueError("rerank candidates require valid IDs and 1 to 20000 text characters")
        if self._lock.locked():
            raise RerankerError(RerankerErrorCode.BUSY)
        async with self._lock:
            started = perf_counter()
            try:
                with TemporaryDirectory(prefix="galaxy-frog-rerank-") as directory:
                    request_path = Path(directory) / "request.json"
                    request_path.write_text(
                        json.dumps(
                            {
                                "model": BGE_RERANKER_MODEL,
                                "revision": BGE_RERANKER_REVISION,
                                "query": query,
                                "candidates": [asdict(item) for item in candidates],
                            }
                        ),
                        encoding="utf-8",
                    )
                    result = await self._runner.run(
                        (
                            str(self._python),
                            str(Path(__file__).with_name("runtime.py")),
                            str(request_path),
                        ),
                        cwd=Path(directory),
                        timeout_seconds=self._timeout_seconds,
                    )
            except CommandTimedOut as exc:
                raise RerankerError(RerankerErrorCode.TIMEOUT) from exc
            except OSError as exc:
                raise RerankerError(RerankerErrorCode.DEPENDENCY_UNAVAILABLE) from exc
            if result.return_code != 0:
                raise RerankerError(RerankerErrorCode.EXECUTION_FAILED)
            try:
                failure = _Failure.model_validate_json(result.stdout)
            except ValidationError:
                pass
            else:
                raise RerankerError(failure.error)
            try:
                response = _Response.model_validate_json(result.stdout)
                if (
                    response.model,
                    response.revision,
                    response.torch_version,
                    response.transformers_version,
                ) != (BGE_RERANKER_MODEL, BGE_RERANKER_REVISION, "2.11.0+cu128", "5.17.0"):
                    raise ValueError("unexpected provider identity")
                if sorted(score.unit_id for score in response.scores) != sorted(
                    item.unit_id for item in candidates
                ):
                    raise ValueError("reranker changed the candidate identities")
            except (ValidationError, ValueError) as exc:
                raise RerankerError(RerankerErrorCode.INVALID_RESPONSE) from exc
            return RerankResult(
                scores=tuple(RerankScore(**item.model_dump()) for item in response.scores),
                elapsed_ms=(perf_counter() - started) * 1000,
                **response.model_dump(exclude={"scores"}),
            )
