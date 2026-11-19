"""Real PostgreSQL proof of owner isolation and additive workspace constraints."""

import os
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.workspace import WorkspaceAccountRow, WorkspaceNotebookRow, WorkspaceRepository
from galaxy_frog.domain.workspace import NotebookDraft, NotebookNotFound

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires migrated PostgreSQL"
)


@pytest_asyncio.fixture
async def workspace() -> AsyncGenerator[tuple[WorkspaceRepository, UUID, UUID]]:
    engine = create_database_engine(Settings())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, outsider = uuid4(), uuid4()
    try:
        async with sessions() as session:
            repository = WorkspaceRepository(session)
            await repository.register_account(owner)
            await repository.register_account(outsider)
            await session.commit()
            try:
                yield repository, owner, outsider
            finally:
                await session.rollback()
                await session.execute(
                    delete(WorkspaceNotebookRow).where(
                        WorkspaceNotebookRow.owner_id.in_([owner, outsider])
                    )
                )
                await session.execute(
                    delete(WorkspaceAccountRow).where(WorkspaceAccountRow.id.in_([owner, outsider]))
                )
                await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_accounts_are_idempotent_and_receive_no_existing_notebooks(
    workspace: tuple[WorkspaceRepository, UUID, UUID],
) -> None:
    repository, owner, outsider = workspace
    first = await repository.register_account(owner)
    second = await repository.register_account(owner)
    assert first == second
    await repository.create_notebook(owner, NotebookDraft(title="Private evidence"))
    await repository.session.commit()
    assert await repository.list_notebooks(outsider) == ()


@pytest.mark.asyncio
async def test_another_account_cannot_list_read_or_rename_a_notebook(
    workspace: tuple[WorkspaceRepository, UUID, UUID],
) -> None:
    repository, owner, outsider = workspace
    notebook = await repository.create_notebook(owner, NotebookDraft(title="Private evidence"))
    await repository.session.commit()
    assert [item.notebook_id for item in await repository.list_notebooks(owner)] == [
        notebook.notebook_id
    ]
    assert await repository.list_notebooks(outsider) == ()
    for inaccessible_id in (notebook.notebook_id, uuid4()):
        with pytest.raises(NotebookNotFound):
            await repository.get_notebook(outsider, inaccessible_id)
        with pytest.raises(NotebookNotFound):
            await repository.rename_notebook(
                outsider, inaccessible_id, NotebookDraft(title="Attempted takeover")
            )
    unchanged = await repository.get_notebook(owner, notebook.notebook_id)
    assert unchanged.title == "Private evidence"
    renamed = await repository.rename_notebook(
        owner, notebook.notebook_id, NotebookDraft(title="  Evidence collection  ")
    )
    assert renamed.owner_id == owner and renamed.title == "Evidence collection"
    assert renamed.created_at == notebook.created_at
    assert renamed.updated_at >= notebook.updated_at


@pytest.mark.asyncio
async def test_notebooks_persist_across_sessions_and_writes_can_be_rolled_back(
    workspace: tuple[WorkspaceRepository, UUID, UUID],
) -> None:
    repository, owner, _outsider = workspace
    notebook = await repository.create_notebook(owner, NotebookDraft(title="Saved"))
    await repository.session.commit()
    async with AsyncSession(bind=repository.session.bind) as another:
        loaded = await WorkspaceRepository(another).get_notebook(owner, notebook.notebook_id)
        assert loaded == notebook
    await repository.rename_notebook(owner, notebook.notebook_id, NotebookDraft(title="Unsaved"))
    await repository.session.rollback()
    assert (await repository.get_notebook(owner, notebook.notebook_id)).title == "Saved"


@pytest.mark.asyncio
async def test_listing_is_bounded_and_has_deterministic_tie_order(
    workspace: tuple[WorkspaceRepository, UUID, UUID],
) -> None:
    repository, owner, _outsider = workspace
    first = await repository.create_notebook(owner, NotebookDraft(title="First"))
    second = await repository.create_notebook(owner, NotebookDraft(title="Second"))
    await repository.session.execute(
        text("UPDATE workspace_notebooks SET updated_at = :stamp WHERE owner_id = :owner"),
        {"stamp": first.updated_at, "owner": owner},
    )
    await repository.session.commit()
    expected = sorted([first.notebook_id, second.notebook_id], reverse=True)
    assert [item.notebook_id for item in await repository.list_notebooks(owner)] == expected
    assert [
        item.notebook_id for item in await repository.list_notebooks(owner, limit=1)
    ] == expected[:1]


@pytest.mark.asyncio
async def test_database_rejects_unregistered_owners_and_invalid_titles(
    workspace: tuple[WorkspaceRepository, UUID, UUID],
) -> None:
    repository, owner, _outsider = workspace
    with pytest.raises(IntegrityError):
        await repository.create_notebook(uuid4(), NotebookDraft(title="No owner"))
    await repository.session.rollback()
    notebook = await repository.create_notebook(owner, NotebookDraft(title="Valid"))
    await repository.session.commit()
    with pytest.raises(IntegrityError):
        await repository.session.execute(
            text("UPDATE workspace_notebooks SET title = '   ' WHERE id = :id"),
            {"id": notebook.notebook_id},
        )
    await repository.session.rollback()
    with pytest.raises(IntegrityError):
        await repository.session.execute(
            delete(WorkspaceAccountRow).where(WorkspaceAccountRow.id == owner)
        )
    await repository.session.rollback()


@pytest.mark.asyncio
async def test_workspace_tables_enable_rls_without_browser_access_policies(
    workspace: tuple[WorkspaceRepository, UUID, UUID],
) -> None:
    repository, _owner, _outsider = workspace
    result = await repository.session.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE oid IN ('workspace_accounts'::regclass, 'workspace_notebooks'::regclass)"
        )
    )
    assert dict(result.tuples().all()) == {
        "workspace_accounts": True,
        "workspace_notebooks": True,
    }
    policies = await repository.session.execute(
        text(
            "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema() "
            "AND tablename IN ('workspace_accounts', 'workspace_notebooks')"
        )
    )
    assert policies.scalar_one() == 0
    assert (await repository.session.execute(select(WorkspaceAccountRow.id))).first() is not None
