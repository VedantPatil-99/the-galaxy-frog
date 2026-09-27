"""Reranker bounds, process lifecycle, offline runtime, provenance, and visible fallback."""

import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from galaxy_frog.adapters.media.process import CommandResult, CommandTimedOut
from galaxy_frog.adapters.reranking import runtime
from galaxy_frog.adapters.reranking.bge import BgeTranscriptReranker
from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.config import Settings
from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.pipeline import RetrievalMode
from galaxy_frog.domain.retrieval.reranking import (
    BGE_RERANKER_MODEL,
    BGE_RERANKER_REVISION,
    RerankCandidate,
    RerankerError,
    RerankerErrorCode,
    RerankResult,
    RerankScore,
)
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.transcripts.models import RetrievalUnit
from galaxy_frog.entrypoints import reranker_probe

ONE = RerankCandidate("a" * 64, "Pandas eat bamboo.")
TWO = RerankCandidate("b" * 64, "PostgreSQL stores text.")


def response(*ids: str) -> dict[str, object]:
    return {
        "scores": [
            {
                "unit_id": unit_id,
                "score": 3.0,
                "original_tokens": 600,
                "input_tokens": 512,
                "truncated": True,
                "inference_ms": 5.0,
            }
            for unit_id in ids
        ],
        "model": BGE_RERANKER_MODEL,
        "revision": BGE_RERANKER_REVISION,
        "torch_version": "2.11.0+cu128",
        "transformers_version": "5.17.0",
        "cuda_version": "12.8",
        "gpu": "GPU",
        "model_load_ms": 1000.0,
        "processing_ms": 1010.0,
        "peak_allocated_mib": 1100.0,
        "peak_reserved_mib": 1200.0,
    }


class RecordingRunner:
    def __init__(self, stdout: str | None = None, error: Exception | None = None) -> None:
        self.stdout = json.dumps(response(ONE.unit_id)) if stdout is None else stdout
        self.error = error
        self.paths: list[Path] = []
        self.return_code = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        assert len(arguments) == 3
        assert Path(arguments[0]).is_absolute()
        assert Path(arguments[1]).name == "runtime.py"
        assert timeout_seconds > 0
        path = Path(arguments[2])
        self.paths.append(path)
        assert path.parent == cwd
        payload = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        assert payload["model"] == BGE_RERANKER_MODEL
        assert payload["revision"] == BGE_RERANKER_REVISION
        self.started.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return CommandResult(tuple(arguments), self.return_code, self.stdout, "private diagnostics")


@pytest.mark.asyncio
async def test_adapter_records_identity_truncation_and_cleans_temporary_input() -> None:
    runner = RecordingRunner()
    result = await BgeTranscriptReranker(runner=runner).rerank("What eats bamboo?", (ONE,))
    assert result.scores[0].unit_id == ONE.unit_id
    assert result.scores[0].truncated
    assert result.scores[0].original_tokens == 600
    assert result.scores[0].input_tokens == 512
    assert result.device == "cuda" and result.dtype == "float16"
    assert result.model == BGE_RERANKER_MODEL
    assert result.elapsed_ms >= 0
    assert not runner.paths[0].parent.exists()


@pytest.mark.parametrize("timeout", [0, 601, float("nan")])
def test_adapter_rejects_invalid_deadline(timeout: float) -> None:
    with pytest.raises(ValueError):
        BgeTranscriptReranker(timeout_seconds=timeout)


@pytest.mark.parametrize(
    ("query", "candidates"),
    [
        (" ", (ONE,)),
        ("x" * 2001, (ONE,)),
        ("query", ()),
        ("query", (ONE,) * 31),
        ("query", (ONE, ONE)),
        ("query", (replace(ONE, unit_id="bad"),)),
        ("query", (replace(ONE, text=" "),)),
        ("query", (replace(ONE, text="x" * 20001),)),
    ],
)
@pytest.mark.asyncio
async def test_adapter_bounds_inputs(query: str, candidates: tuple[RerankCandidate, ...]) -> None:
    runner = RecordingRunner()
    with pytest.raises(ValueError):
        await BgeTranscriptReranker(runner=runner).rerank(query, candidates)
    assert runner.paths == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CommandTimedOut("python"), RerankerErrorCode.TIMEOUT),
        (OSError("private path"), RerankerErrorCode.DEPENDENCY_UNAVAILABLE),
    ],
)
async def test_adapter_translates_process_errors_and_removes_inputs(
    error: Exception,
    code: RerankerErrorCode,
) -> None:
    runner = RecordingRunner(error=error)
    with pytest.raises(RerankerError) as caught:
        await BgeTranscriptReranker(runner=runner).rerank("query", (ONE,))
    assert caught.value.code == code
    assert "private" not in str(caught.value)
    assert not runner.paths[0].exists()


@pytest.mark.asyncio
async def test_nonzero_exit_and_explicit_worker_failure_are_safe() -> None:
    runner = RecordingRunner()
    runner.return_code = 7
    with pytest.raises(RerankerError, match="execution_failed"):
        await BgeTranscriptReranker(runner=runner).rerank("query", (ONE,))
    runner.return_code = 0
    runner.stdout = json.dumps({"error": "reranker_cuda_unavailable"})
    with pytest.raises(RerankerError, match="cuda_unavailable"):
        await BgeTranscriptReranker(runner=runner).rerank("query", (ONE,))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["json", "identity", "ids", "tokens", "flag", "nan", "oversize"])
async def test_adapter_rejects_invalid_native_response(case: str) -> None:
    payload = response(ONE.unit_id)
    scores = cast(list[dict[str, object]], payload["scores"])
    if case == "identity":
        payload["revision"] = "unapproved"
    elif case == "ids":
        scores[0]["unit_id"] = TWO.unit_id
    elif case == "tokens":
        scores[0]["original_tokens"] = 1
    elif case == "flag":
        scores[0]["truncated"] = False
    elif case == "nan":
        scores[0]["score"] = float("nan")
    elif case == "oversize":
        scores[0]["input_tokens"] = 513
    runner = RecordingRunner("not json" if case == "json" else json.dumps(payload))
    with pytest.raises(RerankerError, match="invalid_response"):
        await BgeTranscriptReranker(runner=runner).rerank("query", (ONE,))


@pytest.mark.asyncio
async def test_concurrency_is_one_and_cancellation_releases_slot_and_files() -> None:
    runner = RecordingRunner()
    runner.release.clear()
    provider = BgeTranscriptReranker(runner=runner)
    first = asyncio.create_task(provider.rerank("query", (ONE,)))
    await runner.started.wait()
    with pytest.raises(RerankerError, match="busy"):
        await provider.rerank("second", (ONE,))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not runner.paths[0].exists()
    runner.release.set()
    await provider.rerank("third", (ONE,))


@pytest.fixture
def dependencies(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    torch = MagicMock(__version__="2.11.0+cu128")
    torch.cuda.is_available.return_value = True
    torch.cuda.max_memory_allocated.return_value = 1024**2
    torch.cuda.max_memory_reserved.return_value = 2 * 1024**2
    torch.cuda.get_device_name.return_value = "GPU"
    torch.version.cuda = "12.8"
    transformers = MagicMock(__version__="5.17.0")
    tokenizer = transformers.AutoTokenizer.from_pretrained.return_value
    tokenizer.encode.return_value = list(range(600))
    tensor = MagicMock(shape=(1, 512))
    tensor.to.return_value = tensor
    tokenizer.return_value = {"input_ids": tensor}
    model = transformers.AutoModelForSequenceClassification.from_pretrained.return_value
    model.to.return_value = model
    model.eval.return_value = model
    model.return_value.logits.flatten.return_value.float.return_value.item.return_value = 3.0
    modules = {"torch": torch, "transformers": transformers}

    def import_fixture(name: str) -> MagicMock:
        return modules[name]

    monkeypatch.setattr(runtime, "import_module", import_fixture)
    return torch, transformers


def request() -> dict[str, object]:
    return {
        "model": BGE_RERANKER_MODEL,
        "revision": BGE_RERANKER_REVISION,
        "query": "bamboo?",
        "candidates": [asdict(ONE)],
    }


def test_native_runtime_is_offline_fp16_batch_one_and_records_truncation(
    dependencies: tuple[MagicMock, MagicMock],
) -> None:
    torch, transformers = dependencies
    result = runtime.execute(request())
    assert cast(list[dict[str, object]], result["scores"])[0]["truncated"] is True
    assert result["peak_reserved_mib"] == 2
    kwargs = transformers.AutoModelForSequenceClassification.from_pretrained.call_args.kwargs
    assert kwargs["local_files_only"] is True
    assert kwargs["trust_remote_code"] is False
    assert kwargs["use_safetensors"] is True
    assert kwargs["dtype"] == torch.float16
    assert kwargs["revision"] == BGE_RERANKER_REVISION
    tokenizer = transformers.AutoTokenizer.from_pretrained.return_value
    assert tokenizer.call_args.kwargs["max_length"] == 512


@pytest.mark.parametrize("which", ["torch", "transformers", "cuda"])
def test_native_runtime_rejects_dependency_drift_and_missing_cuda(
    dependencies: tuple[MagicMock, MagicMock],
    which: str,
) -> None:
    torch, transformers = dependencies
    if which == "torch":
        torch.__version__ = "cpu"
    elif which == "transformers":
        transformers.__version__ = "other"
    else:
        torch.cuda.is_available.return_value = False
    with pytest.raises(runtime.RuntimeFailure):
        runtime.load_dependencies()


def test_missing_native_dependency_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "import_module", MagicMock(side_effect=ImportError("private")))
    with pytest.raises(runtime.RuntimeFailure, match="dependency_unavailable"):
        runtime.load_dependencies()


@pytest.mark.parametrize("case", ["success", "missing_model", "oom", "other", "nan"])
def test_worker_stdout_contains_only_safe_json(
    dependencies: tuple[MagicMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    _, transformers = dependencies
    factory = transformers.AutoModelForSequenceClassification.from_pretrained
    if case == "missing_model":
        factory.side_effect = OSError("private path")
    elif case == "oom":
        factory.side_effect = RuntimeError("CUDA out of memory private")
    elif case == "other":
        factory.side_effect = RuntimeError("private")
    elif case == "nan":
        factory.return_value.return_value.logits.flatten.return_value.float.return_value.item.return_value = float(
            "nan"
        )
    path = tmp_path / "input.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["runtime.py", str(path)])
    runtime.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert "private" not in output
    if case == "success":
        assert result["scores"][0]["score"] == 3.0
    else:
        expected = {
            "missing_model": "model_unavailable",
            "oom": "out_of_memory",
            "other": "execution_failed",
            "nan": "invalid_response",
        }
        assert result == {"error": "reranker_" + expected[case]}


def ranked_result() -> RerankResult:
    return RerankResult(
        (
            RerankScore(ONE.unit_id, -5, 20, 20, False, 1),
            RerankScore(TWO.unit_id, 3, 600, 512, True, 2),
        ),
        "2.11.0+cu128",
        "5.17.0",
        "12.8",
        "GPU",
        1000,
        1100,
        1200,
        1100,
        1200,
    )


class RetrievalFixture:
    async def search(
        self, video_id: UUID, query: str, *, limit: int, window: TimeWindow | None = None
    ) -> tuple[RetrievedEvidence, ...]:
        return tuple(
            RetrievedEvidence(
                video_id,
                RetrievalUnit(
                    candidate.unit_id, i * 1000, (i + 1) * 1000, candidate.text, (str(i) * 64,)
                ),
                0.9,
            )
            for i, candidate in enumerate((ONE, TWO))
        )


@pytest.mark.asyncio
async def test_reranked_mode_changes_order_without_changing_original_evidence() -> None:
    reranker = MagicMock(rerank=AsyncMock(return_value=ranked_result()))
    result = await RetrieveTranscript(
        lexical=RetrievalFixture(),
        dense=RetrievalFixture(),
        reranker=reranker,
    ).execute(uuid4(), "question", mode=RetrievalMode.RERANKED)
    assert [item.unit.unit_id for item in result.evidence] == [TWO.unit_id, ONE.unit_id]
    assert result.evidence[0].unit == result.rankings[1].unit
    assert result.evidence[0].stages == result.rankings[1].stages
    assert result.evidence[0].fusion_rank == 2
    assert result.evidence[0].rerank_rank == 1
    assert result.evidence[0].rerank_score == 3
    assert result.reranking is not None and result.reranking.result is not None
    assert result.reranking.result.scores[1].truncated
    assert not result.degraded


@pytest.mark.asyncio
async def test_equal_rerank_scores_preserve_fusion_tie_order() -> None:
    data = ranked_result()
    data = replace(data, scores=tuple(replace(score, score=1.0) for score in data.scores))
    result = await RetrieveTranscript(
        lexical=RetrievalFixture(),
        dense=RetrievalFixture(),
        reranker=MagicMock(rerank=AsyncMock(return_value=data)),
    ).execute(uuid4(), "question", mode=RetrievalMode.RERANKED)
    assert [item.unit for item in result.evidence] == [item.unit for item in result.rankings]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_reranker_failure_is_observable_and_strict_mode_fails(configured: bool) -> None:
    reranker = MagicMock(
        rerank=AsyncMock(side_effect=RerankerError(RerankerErrorCode.OUT_OF_MEMORY))
    )
    service = RetrieveTranscript(
        lexical=RetrievalFixture(),
        dense=RetrievalFixture(),
        reranker=reranker if configured else None,
    )
    result = await service.execute(uuid4(), "query", mode=RetrievalMode.RERANKED)
    assert result.degraded
    assert result.evidence == result.rankings
    assert result.reranking is not None
    assert result.reranking.result is None
    assert result.reranking.failure_code == result.warnings[0].code
    with pytest.raises(RerankerError):
        await service.execute(uuid4(), "query", mode=RetrievalMode.RERANKED, allow_fallback=False)


@pytest.mark.asyncio
async def test_reranker_cannot_introduce_foreign_evidence() -> None:
    data = replace(ranked_result(), scores=(RerankScore("c" * 64, 1, 1, 1, False, 1),))
    service = RetrieveTranscript(
        lexical=RetrievalFixture(),
        dense=RetrievalFixture(),
        reranker=MagicMock(rerank=AsyncMock(return_value=data)),
    )
    with pytest.raises(RetrievalIntegrityError):
        await service.execute(uuid4(), "query", mode=RetrievalMode.RERANKED)


def test_reranker_settings_resolve_explicit_runtime(tmp_path: Path) -> None:
    assert Settings(reranker_python=None).reranker_python is None
    assert Settings(reranker_python=tmp_path).reranker_python == tmp_path.resolve()
    path = Settings(reranker_python=Path("tmp/probe/python.exe")).reranker_python
    assert path is not None and path.is_absolute()
    assert path == Path(__file__).resolve().parents[4] / "tmp/probe/python.exe"
    assert isinstance(reranker_probe.build_reranker(Settings()), BgeTranscriptReranker)


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [True, False])
async def test_runtime_probe_exercises_ranking_truncation_and_candidate_limit(
    monkeypatch: pytest.MonkeyPatch,
    full: bool,
) -> None:
    result = ranked_result()
    result = replace(
        result, scores=(replace(result.scores[0], score=4), result.scores[1], result.scores[1])
    )
    rerank = AsyncMock(return_value=result)

    def build_fixture(settings: Settings) -> MagicMock:
        return MagicMock(rerank=rerank)

    monkeypatch.setattr(reranker_probe, "build_reranker", build_fixture)
    output = await reranker_probe.probe(full=full)
    assert output["model"] == BGE_RERANKER_MODEL
    assert len(rerank.call_args.args[1]) == (30 if full else 3)
    rerank.return_value = ranked_result()
    with pytest.raises(RuntimeError, match="sanity"):
        await reranker_probe.probe(settings=Settings())


def test_probe_cli_prints_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["probe", "--full"])
    monkeypatch.setattr(reranker_probe, "probe", AsyncMock(return_value={"verified": True}))
    reranker_probe.main()
    assert json.loads(capsys.readouterr().out) == {"verified": True}
