"""Standalone child runtime: stdlib plus optional torch/Transformers, no application imports.

Invoked by file path so an explicitly provisioned Python environment can host native dependencies
without needing FastAPI, database libraries, or another copy of the application. Input is a bounded
temporary JSON file; stdout is a single bounded result. All model loading is offline.
"""

import json
import sys
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager, redirect_stdout
from importlib import import_module
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast


class Tensor(Protocol):
    @property
    def shape(self) -> tuple[int, ...]: ...
    def to(self, device: str) -> Tensor: ...
    def flatten(self) -> Tensor: ...
    def float(self) -> Tensor: ...
    def item(self) -> float: ...


class ModelOutput(Protocol):
    @property
    def logits(self) -> Tensor: ...


class Model(Protocol):
    def to(self, device: str) -> Model: ...
    def eval(self) -> Model: ...
    def __call__(self, **inputs: object) -> ModelOutput: ...


class Tokenizer(Protocol):
    def encode(self, text: str, text_pair: str) -> list[int]: ...
    def __call__(self, text: str, text_pair: str, **kwargs: object) -> Mapping[str, Tensor]: ...


class ModelFactory(Protocol):
    def from_pretrained(self, model: str, **kwargs: object) -> Model: ...


class TokenizerFactory(Protocol):
    def from_pretrained(self, model: str, **kwargs: object) -> Tokenizer: ...


class Transformers(Protocol):
    __version__: str
    AutoTokenizer: TokenizerFactory
    AutoModelForSequenceClassification: ModelFactory


class Cuda(Protocol):
    def is_available(self) -> bool: ...
    def get_device_name(self, index: int) -> str: ...
    def synchronize(self) -> None: ...
    def reset_peak_memory_stats(self) -> None: ...
    def max_memory_allocated(self) -> int: ...
    def max_memory_reserved(self) -> int: ...


class TorchVersion(Protocol):
    cuda: str


class Torch(Protocol):
    __version__: str
    float16: object
    cuda: Cuda
    version: TorchVersion

    def inference_mode(self) -> AbstractContextManager[object]: ...


class RuntimeFailure(Exception):
    pass


def load_dependencies() -> tuple[Torch, Transformers]:
    try:
        torch = cast(Torch, import_module("torch"))
        transformers = cast(Transformers, import_module("transformers"))
    except (ImportError, OSError) as exc:
        raise RuntimeFailure("reranker_dependency_unavailable") from exc
    if torch.__version__ != "2.11.0+cu128" or transformers.__version__ != "5.17.0":
        raise RuntimeFailure("reranker_dependency_unavailable")
    if not torch.cuda.is_available():
        raise RuntimeFailure("reranker_cuda_unavailable")
    return torch, transformers


def execute(payload: Mapping[str, object]) -> dict[str, object]:
    torch, transformers = load_dependencies()
    model_id = cast(str, payload["model"])
    revision = cast(str, payload["revision"])
    query = cast(str, payload["query"])
    candidates = cast(Iterable[Mapping[str, str]], payload["candidates"])
    started = perf_counter()
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            local_files_only=True,
            trust_remote_code=False,
        )
        model = (
            transformers.AutoModelForSequenceClassification.from_pretrained(
                model_id,
                revision=revision,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
                dtype=torch.float16,
            )
            .to("cuda")
            .eval()
        )
    except OSError as exc:
        raise RuntimeFailure("reranker_model_unavailable") from exc
    torch.cuda.synchronize()
    load_ms = (perf_counter() - started) * 1000
    torch.cuda.reset_peak_memory_stats()
    scores: list[dict[str, object]] = []
    with torch.inference_mode():
        for candidate in candidates:
            original_tokens = len(tokenizer.encode(query, candidate["text"]))
            inputs = tokenizer(
                query,
                candidate["text"],
                padding=False,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            device_inputs = {key: value.to("cuda") for key, value in inputs.items()}
            input_tokens = inputs["input_ids"].shape[-1]
            torch.cuda.synchronize()
            inference_started = perf_counter()
            score = float(model(**device_inputs, return_dict=True).logits.flatten().float().item())
            torch.cuda.synchronize()
            if not isfinite(score):
                raise RuntimeFailure("reranker_invalid_response")
            scores.append(
                {
                    "unit_id": candidate["unit_id"],
                    "score": score,
                    "original_tokens": original_tokens,
                    "input_tokens": input_tokens,
                    "truncated": original_tokens > input_tokens,
                    "inference_ms": (perf_counter() - inference_started) * 1000,
                }
            )
    return {
        "scores": scores,
        "model": model_id,
        "revision": revision,
        "torch_version": torch.__version__,
        "transformers_version": transformers.__version__,
        "cuda_version": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "model_load_ms": load_ms,
        "processing_ms": (perf_counter() - started) * 1000,
        "peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
        "peak_reserved_mib": torch.cuda.max_memory_reserved() / 1024**2,
    }


def main() -> None:
    try:
        payload = cast(
            Mapping[str, object], json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        )
        with redirect_stdout(sys.stderr):
            result = execute(payload)
    except RuntimeFailure as exc:
        result = {"error": str(exc)}
    except Exception as exc:
        # Never return exception strings, local paths, or input text to the caller.
        code = (
            "reranker_out_of_memory"
            if "out of memory" in str(exc).lower()
            else "reranker_execution_failed"
        )
        result = {"error": code}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    main()
