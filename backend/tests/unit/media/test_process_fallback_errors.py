"""Failure and cancellation races at the Windows process boundary used by visual runtimes."""

import asyncio
import subprocess
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandTimedOut


async def unavailable(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("deadline_failure", [True, False])
async def test_fallback_reaps_on_communication_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, running: bool, deadline_failure: bool
) -> None:
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    error = (
        subprocess.TimeoutExpired("fake", 1) if deadline_failure else RuntimeError("communication")
    )
    proc = Mock()
    proc.communicate.side_effect = [error, (b"", b"")]
    proc.poll.return_value = None if running else 1
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=proc))
    with pytest.raises(CommandTimedOut if deadline_failure else RuntimeError):
        await AsyncSubprocessRunner().run(("fake",), cwd=tmp_path, timeout_seconds=1)
    assert proc.kill.call_count == int(running)
    assert proc.communicate.call_count == 2


@pytest.mark.asyncio
async def test_fallback_cancellation_before_child_is_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    started, release = Event(), Event()
    proc = Mock(returncode=0)
    proc.communicate.return_value = (b"", b"")

    def start(*_args: object, **_kwargs: object) -> Mock:
        started.set()
        assert release.wait(5)
        return proc

    monkeypatch.setattr(subprocess, "Popen", start)
    task = asyncio.create_task(
        AsyncSubprocessRunner().run(("fake",), cwd=tmp_path, timeout_seconds=1)
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
