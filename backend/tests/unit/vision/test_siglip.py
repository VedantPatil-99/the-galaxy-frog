"""Offline visual embedding adapter acceptance with deterministic native/process fakes."""

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock

import pytest
from tests.unit.vision.test_visual_contracts import frame

from galaxy_frog.adapters.media.process import CommandResult, CommandTimedOut
from galaxy_frog.adapters.vision import siglip_runtime
from galaxy_frog.adapters.vision.siglip import (
    SIGLIP_MODEL,
    SIGLIP_REVISION,
    SiglipEmbeddingProvider,
)
from galaxy_frog.domain.vision.visual import VisualProviderError


class Runner:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        changes: dict[str, object] | None = None,
        return_code: int = 0,
    ) -> None:
        self.error = error
        self.changes = changes or {}
        self.return_code = return_code
        self.paths: list[Path] = []

    async def run(
        self, arguments: Sequence[str], *, cwd: Path, timeout_seconds: float
    ) -> CommandResult:
        self.paths.append(Path(arguments[-1]))
        payload = json.loads(self.paths[-1].read_text(encoding="utf-8"))
        assert (
            payload["device"] == "cpu" and cwd == self.paths[-1].parent and timeout_seconds == 120
        )
        if self.error:
            raise self.error
        value = {
            "model": SIGLIP_MODEL,
            "revision": SIGLIP_REVISION,
            "device": "cpu",
            "vectors": [[1.0, *([0.0] * 767)] for _ in payload.get("paths", ["query"])],
            **self.changes,
        }
        return CommandResult(tuple(arguments), self.return_code, json.dumps(value), "private error")


@pytest.mark.asyncio
async def test_parent_validates_and_cleans_query_and_frames() -> None:
    runner = Runner()
    provider = SiglipEmbeddingProvider(device="cpu", runner=runner)
    assert len(await provider.embed_query("diagram")) == 768
    original = frame()
    assert (await provider.embed_frames((original,)))[0].frame is original
    assert all(not path.exists() for path in runner.paths)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runner,code",
    [
        (Runner(error=CommandTimedOut()), "visual_timeout"),
        (Runner(error=OSError()), "visual_provider_unavailable"),
        (Runner(return_code=1), "visual_provider_unavailable"),
        (Runner(changes={"revision": "changed"}), "visual_invalid_output"),
        (Runner(changes={"vectors": []}), "visual_invalid_output"),
        (Runner(changes={"vectors": [[0.0]]}), "visual_invalid_output"),
    ],
)
async def test_parent_safe_failure(runner: Runner, code: str) -> None:
    with pytest.raises(VisualProviderError, match=code):
        await SiglipEmbeddingProvider(device="cpu", runner=runner).embed_query("diagram")
    assert all(not path.exists() for path in runner.paths)


@pytest.mark.asyncio
async def test_parent_bounds_and_busy() -> None:
    for deadline in (0, 601):
        with pytest.raises(ValueError):
            SiglipEmbeddingProvider(device="cpu", timeout_seconds=deadline)
    provider = SiglipEmbeddingProvider(device="cpu")
    for query in ("", "x" * 2001):
        with pytest.raises(ValueError):
            await provider.embed_query(query)
    original = frame()
    for frames in ((), (original, original), (original,) * 5):
        with pytest.raises(ValueError):
            await provider.embed_frames(frames)

    started, release = asyncio.Event(), asyncio.Event()

    class Blocking(Runner):
        async def run(
            self, arguments: Sequence[str], *, cwd: Path, timeout_seconds: float
        ) -> CommandResult:
            started.set()
            await release.wait()
            return await super().run(arguments, cwd=cwd, timeout_seconds=timeout_seconds)

    provider = SiglipEmbeddingProvider(device="cpu", runner=Blocking())
    first = asyncio.create_task(provider.embed_query("diagram"))
    await started.wait()
    with pytest.raises(VisualProviderError, match="visual_busy"):
        await provider.embed_query("diagram")
    release.set()
    await first


def native(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock, Mock]:
    torch, transformers, image = Mock(), Mock(), Mock()
    torch.__version__ = "2.11.0+cu128"
    transformers.__version__ = "5.17.0"
    torch.inference_mode.return_value = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    torch.nn.functional.normalize.return_value.float.return_value.cpu.return_value.tolist.return_value = [
        [1.0, 0.0]
    ]
    image.open.return_value = Mock(
        __enter__=Mock(return_value=Mock(width=224, height=224)), __exit__=Mock(return_value=False)
    )
    modules = {"torch": torch, "transformers": transformers, "PIL.Image": image}
    transformers.AutoProcessor.from_pretrained.return_value.return_value.to.return_value = {}

    def module(name: str) -> Mock:
        return modules[name]

    monkeypatch.setattr(siglip_runtime, "import_module", module)
    return torch, transformers, image


def payload() -> dict[str, object]:
    return {"model": SIGLIP_MODEL, "revision": SIGLIP_REVISION, "device": "cpu", "query": "diagram"}


def test_runtime_offline_query_and_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, transformers, _ = native(monkeypatch)
    assert siglip_runtime.execute(payload())["vectors"] == [[1.0, 0.0]]
    options = transformers.SiglipModel.from_pretrained.call_args.kwargs
    assert (
        options["local_files_only"]
        and options["use_safetensors"]
        and not options["trust_remote_code"]
    )
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"jpeg")
    values = payload()
    values.pop("query")
    values["paths"] = [str(path)]
    assert siglip_runtime.execute(values)["vectors"] == [[1.0, 0.0]]


@pytest.mark.parametrize("failure", ["torch", "transformers", "cuda", "device", "bytes", "pixels"])
def test_runtime_rejects_unverified_or_oversized_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    torch, transformers, image = native(monkeypatch)
    values = payload()
    if failure == "torch":
        torch.__version__ = "other"
    elif failure == "transformers":
        transformers.__version__ = "other"
    elif failure == "cuda":
        values["device"] = "cuda"
        torch.cuda.is_available.return_value = False
    elif failure == "device":
        values["device"] = "auto"
    else:
        path = tmp_path / "frame.jpg"
        path.write_bytes(b"x" * (4_194_305 if failure == "bytes" else 1))
        values.pop("query")
        values["paths"] = [str(path)]
        image.open.return_value.__enter__.return_value.width = 100000000
    with pytest.raises(ValueError):
        siglip_runtime.execute(values)


def test_runtime_main_success_and_safe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["runtime", str(path)])
    native(monkeypatch)
    siglip_runtime.main()
    assert json.loads(capsys.readouterr().out)["model"] == SIGLIP_MODEL
    path.write_text("bad json", encoding="utf-8")
    with pytest.raises(SystemExit):
        siglip_runtime.main()
    assert capsys.readouterr().out.strip() == '{"error":"visual_provider_unavailable"}'
