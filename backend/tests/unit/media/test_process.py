"""Behavior coverage for bounded shell-free subprocess execution."""

import asyncio
import sys
from pathlib import Path

import pytest

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandTimedOut


class WaitingProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self.killed = False
        self.waiting = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self) -> int:
        self.waiting.set()
        await self.finished.wait()
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.finished.set()


def install_waiting_process(
    monkeypatch: pytest.MonkeyPatch,
    process: WaitingProcess,
) -> None:
    async def create_subprocess_exec(*_arguments: str, **_options: object) -> object:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)


@pytest.mark.asyncio
async def test_runner_returns_exit_status_and_bounded_output(tmp_path: Path) -> None:
    runner = AsyncSubprocessRunner(max_output_bytes=4)
    arguments = (
        sys.executable,
        "-c",
        "import sys; print('abcdef', end=''); print('uvwxyz', end='', file=sys.stderr)",
    )

    result = await runner.run(arguments, cwd=tmp_path, timeout_seconds=5)

    assert result.arguments == arguments
    assert result.return_code == 0
    assert result.stdout == "abcd"
    assert result.stderr == "uvwx"


@pytest.mark.asyncio
async def test_runner_drains_output_after_the_retained_limit(tmp_path: Path) -> None:
    result = await AsyncSubprocessRunner(max_output_bytes=4).run(
        (sys.executable, "-c", "print('x' * 100000, end='')"),
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.stdout == "xxxx"


@pytest.mark.asyncio
async def test_runner_returns_nonzero_process_status(tmp_path: Path) -> None:
    result = await AsyncSubprocessRunner().run(
        (sys.executable, "-c", "raise SystemExit(7)"),
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.return_code == 7


@pytest.mark.asyncio
async def test_runner_kills_a_process_at_its_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = WaitingProcess()
    install_waiting_process(monkeypatch, process)

    with pytest.raises(CommandTimedOut, match="ffmpeg"):
        await AsyncSubprocessRunner().run(
            ("ffmpeg", "-version"),
            cwd=tmp_path,
            timeout_seconds=0.01,
        )

    assert process.killed is True


@pytest.mark.asyncio
async def test_runner_kills_a_process_when_the_caller_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = WaitingProcess()
    install_waiting_process(monkeypatch, process)
    task = asyncio.create_task(
        AsyncSubprocessRunner().run(
            ("ffmpeg", "-version"),
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )
    await process.waiting.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True


@pytest.mark.parametrize(
    ("arguments", "deadline_seconds", "message"),
    [
        ((), 1.0, "arguments"),
        (("python", ""), 1.0, "arguments"),
        (("python",), 0.0, "timeout_seconds"),
    ],
)
@pytest.mark.asyncio
async def test_runner_rejects_invalid_invocations(
    tmp_path: Path,
    arguments: tuple[str, ...],
    deadline_seconds: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        await AsyncSubprocessRunner().run(
            arguments,
            cwd=tmp_path,
            timeout_seconds=deadline_seconds,
        )


def test_runner_requires_a_positive_output_limit() -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        AsyncSubprocessRunner(max_output_bytes=0)
