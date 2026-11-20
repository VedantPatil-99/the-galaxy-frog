"""The operator command is preview by default and rejects hosted DB destinations."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.config import Settings
from galaxy_frog.db.workspace_sources import LegacySourceAssignmentError
from galaxy_frog.domain.workspace import NotebookNotFound
from galaxy_frog.entrypoints import workspace_assign_legacy as command


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_url",
    [
        None,
        "postgresql+asyncpg://database.example/test",
        "postgresql+asyncpg://localhost/test?host=database.example",
    ],
)
async def test_reject_remote_or_missing_database(database_url: str | None) -> None:
    settings = Settings.model_validate({"database_url": database_url})
    with pytest.raises(LegacySourceAssignmentError, match="local application PostgreSQL"):
        await command.assign(settings, uuid4(), uuid4(), [uuid4()], apply=True)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (LegacySourceAssignmentError("Invalid selection"), "Invalid selection"),
        (NotebookNotFound(), "does not exist for the selected owner"),
        (SQLAlchemyError("private diagnostic"), "Check the local database"),
    ],
)
def test_cli_reports_safe_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    message: str,
) -> None:
    operation = AsyncMock(side_effect=failure)
    monkeypatch.setattr(command, "assign", operation)
    argv = ["--owner-id", str(uuid4()), "--notebook-id", str(uuid4()), "--video-id", str(uuid4())]
    assert command.main(argv) == 1
    assert operation.call_args.kwargs["apply"] is False
    stderr = capsys.readouterr().err
    assert message in stderr
    assert "private diagnostic" not in stderr


@pytest.mark.parametrize(("extra", "printed"), [([], "Would assign"), (["--apply"], "Assigned")])
def test_cli_requires_apply_to_commit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    printed: str,
) -> None:
    operation = AsyncMock(return_value=1)
    monkeypatch.setattr(command, "assign", operation)
    argv = ["--owner-id", str(uuid4()), "--notebook-id", str(uuid4()), "--video-id", str(uuid4())]
    assert command.main(argv + extra) == 0
    assert operation.call_args.kwargs["apply"] is bool(extra)
    assert f"{printed} 1 video(s)." in capsys.readouterr().out
