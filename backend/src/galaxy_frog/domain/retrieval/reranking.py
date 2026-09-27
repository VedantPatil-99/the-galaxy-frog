"""Provider-independent reranking values and observable failure reasons."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

BGE_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
BGE_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


class RerankerErrorCode(StrEnum):
    NOT_CONFIGURED = "reranker_not_configured"
    BUSY = "reranker_busy"
    TIMEOUT = "reranker_timeout"
    DEPENDENCY_UNAVAILABLE = "reranker_dependency_unavailable"
    CUDA_UNAVAILABLE = "reranker_cuda_unavailable"
    MODEL_UNAVAILABLE = "reranker_model_unavailable"
    OUT_OF_MEMORY = "reranker_out_of_memory"
    EXECUTION_FAILED = "reranker_execution_failed"
    INVALID_RESPONSE = "reranker_invalid_response"


class RerankerError(RuntimeError):
    def __init__(self, code: RerankerErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    unit_id: str
    text: str


@dataclass(frozen=True, slots=True)
class RerankScore:
    unit_id: str
    score: float
    original_tokens: int
    input_tokens: int
    truncated: bool
    inference_ms: float


@dataclass(frozen=True, slots=True)
class RerankResult:
    scores: tuple[RerankScore, ...]
    torch_version: str
    transformers_version: str
    cuda_version: str
    gpu: str
    model_load_ms: float
    processing_ms: float
    elapsed_ms: float
    peak_allocated_mib: float
    peak_reserved_mib: float
    model: str = BGE_RERANKER_MODEL
    revision: str = BGE_RERANKER_REVISION
    provider: str = "transformers"
    device: str = "cuda"
    dtype: str = "float16"
    batch_size: int = 1
    max_tokens: int = 512


class TextReranker(Protocol):
    async def rerank(self, query: str, candidates: tuple[RerankCandidate, ...]) -> RerankResult: ...


@dataclass(frozen=True, slots=True)
class RerankingRun:
    result: RerankResult | None
    failure_code: RerankerErrorCode | None = None
