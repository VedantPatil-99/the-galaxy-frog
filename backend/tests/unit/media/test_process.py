"""Behavior coverage for bounded shell-free subprocess execution."""

import asyncio
import sys
from pathlib import Path

import pytest

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandTimedOut


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
async def test_runner_returns_nonzero_process_status(tmp_path: Path) -> None:
    result = await AsyncSubprocessRunner().run(
        (sys.executable, "-c", "raise SystemExit(7)"),
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.return_code == 7


@pytest.mark.asyncio
async def test_runner_kills_a_process_at_its_deadline(tmp_path: Path) -> None:
    with pytest.raises(CommandTimedOut, match=Path(sys.executable).name):
        await AsyncSubprocessRunner().run(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            cwd=tmp_path,
            timeout_seconds=0.05,
        )


@pytest.mark.asyncio
async def test_runner_kills_a_process_when_the_caller_is_cancelled(tmp_path: Path) -> None:
    task = asyncio.create_task(
        AsyncSubprocessRunner().run(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            cwd=tmp_path,
            timeout_seconds=5,
        )
    )
    await asyncio.sleep(0.05)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


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
