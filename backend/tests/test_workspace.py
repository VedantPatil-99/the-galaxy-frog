"""Notebook validation, mapping, failure semantics, and transaction ownership."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.workspace import WorkspaceAccountRow, WorkspaceNotebookRow, WorkspaceRepository
from galaxy_frog.domain.workspace import NotebookDraft, NotebookNotFound


def database() -> Mock:
    db = Mock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    return db


def notebook_row() -> WorkspaceNotebookRow:
    now = datetime.now(UTC)
    return WorkspaceNotebookRow(
        id=uuid4(), owner_id=uuid4(), title="Neural networks", created_at=now, updated_at=now
    )


def test_notebook_title_is_trimmed_and_immutable() -> None:
    draft = NotebookDraft(title="  Neural networks  ")
    assert draft.title == "Neural networks"
    with pytest.raises(ValidationError, match="frozen"):
        draft.title = "Changed"


@pytest.mark.parametrize("title", ["", "   ", "\t\n", "x" * 121])
def test_notebook_rejects_unusable_titles(title: str) -> None:
    with pytest.raises(ValidationError):
        NotebookDraft(title=title)


def test_notebook_rejects_owner_in_user_editable_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        NotebookDraft.model_validate({"title": "My Galaxy", "owner_id": str(uuid4())})
    assert len(NotebookDraft(title="x" * 120).title) == 120


@pytest.mark.asyncio
async def test_registration_returns_existing_account_without_committing() -> None:
    db = database()
    row = WorkspaceAccountRow(id=uuid4(), created_at=datetime.now(UTC))
    db.execute.side_effect = [Mock(), Mock(scalar_one=lambda: row)]
    account = await WorkspaceRepository(cast(AsyncSession, db)).register_account(row.id)
    assert account.account_id == row.id and account.created_at == row.created_at
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_creation_assigns_server_id_and_preserves_validated_title() -> None:
    db = database()
    owner = uuid4()
    notebook = await WorkspaceRepository(cast(AsyncSession, db)).create_notebook(
        owner, NotebookDraft(title="  Evidence notebook  ")
    )
    assert notebook.owner_id == owner
    assert notebook.title == "Evidence notebook"
    assert notebook.notebook_id != owner
    assert notebook.created_at == notebook.updated_at
    assert notebook.created_at.tzinfo is UTC
    db.flush.assert_awaited_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_listing_maps_records_and_handles_empty_accounts() -> None:
    db = database()
    row = notebook_row()
    empty: list[WorkspaceNotebookRow] = []
    db.execute.side_effect = [Mock(scalars=lambda: [row]), Mock(scalars=lambda: empty)]
    repository = WorkspaceRepository(cast(AsyncSession, db))
    records = await repository.list_notebooks(row.owner_id)
    assert len(records) == 1 and records[0].notebook_id == row.id
    assert await repository.list_notebooks(uuid4(), limit=1) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_listing_rejects_unbounded_requests(limit: int) -> None:
    db = database()
    with pytest.raises(ValueError, match="between 1 and 100"):
        await WorkspaceRepository(cast(AsyncSession, db)).list_notebooks(uuid4(), limit=limit)
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_and_rename_return_mapped_records() -> None:
    db = database()
    row = notebook_row()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: row)
    repository = WorkspaceRepository(cast(AsyncSession, db))
    loaded = await repository.get_notebook(row.owner_id, row.id)
    assert loaded.title == row.title and loaded.owner_id == row.owner_id
    row.title = "Renamed notebook"
    renamed = await repository.rename_notebook(row.owner_id, row.id, NotebookDraft(title=row.title))
    assert renamed.title == row.title and renamed.notebook_id == row.id
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_missing_or_inaccessible_notebooks_share_one_failure() -> None:
    db = database()
    db.execute.return_value = Mock(scalar_one_or_none=lambda: None)
    repository = WorkspaceRepository(cast(AsyncSession, db))
    with pytest.raises(NotebookNotFound):
        await repository.get_notebook(uuid4(), uuid4())
    with pytest.raises(NotebookNotFound):
        await repository.rename_notebook(uuid4(), uuid4(), NotebookDraft(title="Other"))
