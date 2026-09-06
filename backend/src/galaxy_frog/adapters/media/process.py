"""Bounded asynchronous subprocess execution for local media tools."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded observable result from one argument-array command."""

    arguments: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str


class CommandTimedOut(TimeoutError):
    """Raised after a child process is terminated at its hard deadline."""


class CommandRunner(Protocol):
    """Injectable process boundary used by media provider adapters."""

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult: ...


class AsyncSubprocessRunner:
    """Execute without a shell while retaining only bounded diagnostics."""

    def __init__(self, *, max_output_bytes: int = 65_536) -> None:
        if max_output_bytes < 1:
            msg = "max_output_bytes must be positive"
            raise ValueError(msg)
        self._max_output_bytes = max_output_bytes

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        command = tuple(arguments)
        if not command or any(not argument for argument in command):
            msg = "arguments must contain non-empty values"
            raise ValueError(msg)
        if timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None or process.stderr is None:  # pragma: no cover - API invariant
            raise RuntimeError("subprocess pipes were not created")
        stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError as exc:
            await self._terminate(process, stdout_task, stderr_task)
            raise CommandTimedOut(command[0]) from exc
        except asyncio.CancelledError:
            await self._terminate(process, stdout_task, stderr_task)
            raise

        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        return_code = process.returncode
        if return_code is None:  # pragma: no cover - wait() API invariant
            raise RuntimeError("subprocess return code was not set")
        return CommandResult(command, return_code, stdout, stderr)

    async def _read_bounded(self, stream: asyncio.StreamReader) -> str:
        retained = bytearray()
        while chunk := await stream.read(65_536):
            remaining = self._max_output_bytes - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
        return retained.decode("utf-8", errors="replace")

    @staticmethod
    async def _terminate(
        process: asyncio.subprocess.Process,
        stdout_task: asyncio.Task[str],
        stderr_task: asyncio.Task[str],
    ) -> None:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
