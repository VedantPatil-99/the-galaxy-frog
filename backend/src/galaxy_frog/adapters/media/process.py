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

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except NotImplementedError:
            return await self._run_fallback(command, cwd=cwd, timeout_seconds=timeout_seconds)

        if process.stdout is None or process.stderr is None:  # pragma: no cover - API invariant
            raise RuntimeError("subprocess pipes were not created")
        stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
        wait_task = asyncio.create_task(process.wait())
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), timeout=timeout_seconds)
        except TimeoutError as exc:
            await self._terminate(process, wait_task, stdout_task, stderr_task)
            raise CommandTimedOut(command[0]) from exc
        except asyncio.CancelledError:
            await self._terminate(process, wait_task, stdout_task, stderr_task)
            raise

        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        return_code = process.returncode
        if return_code is None:  # pragma: no cover - wait() API invariant
            raise RuntimeError("subprocess return code was not set")
        return CommandResult(command, return_code, stdout, stderr)

    async def _run_fallback(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        import subprocess
        from contextlib import suppress

        loop = asyncio.get_running_loop()
        proc_ref: list[subprocess.Popen[bytes]] = []

        def _communicate() -> tuple[int, bytes, bytes]:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc_ref.append(proc)
            try:
                stdout_data, stderr_data = proc.communicate(timeout=timeout_seconds)
                return proc.returncode, stdout_data, stderr_data
            except subprocess.TimeoutExpired as exc:
                if proc.poll() is None:
                    with suppress(OSError):
                        proc.kill()
                with suppress(OSError):
                    proc.communicate()
                raise TimeoutError(command[0]) from exc
            except Exception:
                if proc.poll() is None:
                    with suppress(OSError):
                        proc.kill()
                with suppress(OSError):
                    proc.communicate()
                raise

        try:
            return_code, stdout_bytes, stderr_bytes = await loop.run_in_executor(None, _communicate)
        except TimeoutError as exc:
            raise CommandTimedOut(command[0]) from exc
        except asyncio.CancelledError:
            if proc_ref and proc_ref[0].poll() is None:
                with suppress(OSError):
                    proc_ref[0].kill()
                with suppress(OSError):
                    await loop.run_in_executor(None, proc_ref[0].communicate)
            raise

        stdout = stdout_bytes[: self._max_output_bytes].decode("utf-8", errors="replace")
        stderr = stderr_bytes[: self._max_output_bytes].decode("utf-8", errors="replace")
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
        wait_task: asyncio.Task[int],
        stdout_task: asyncio.Task[str],
        stderr_task: asyncio.Task[str],
    ) -> None:
        if process.returncode is None:  # pragma: no branch - normal timeout/cancel state
            process.kill()
        await wait_task
        await asyncio.gather(stdout_task, stderr_task)
