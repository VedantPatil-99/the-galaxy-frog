"""Private HTTP notebook ownership, transaction behavior, and legacy route fences."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.test_workspace_auth import TOKEN, auth_settings, with_verifier

from galaxy_frog.api.app import create_app
from galaxy_frog.api.dependencies import get_workspace_repository
from galaxy_frog.db.workspace import WorkspaceRepository
from galaxy_frog.domain.workspace import (
    Notebook,
    NotebookNotFound,
    VerifiedWorkspaceIdentity,
    WorkspaceAccount,
)


def private_application() -> tuple[FastAPI, Mock, VerifiedWorkspaceIdentity, Notebook]:
    application = create_app(auth_settings())
    identity = VerifiedWorkspaceIdentity(account_id=uuid4(), email="owner@example.com")
    now = datetime.now(UTC)
    notebook = Notebook(
        notebook_id=uuid4(),
        owner_id=identity.account_id,
        title="Private Galaxy",
        created_at=now,
        updated_at=now,
    )
    repository = Mock(spec=WorkspaceRepository)
    repository.session = Mock(commit=AsyncMock())
    repository.register_account = AsyncMock(
        return_value=WorkspaceAccount(account_id=identity.account_id, created_at=now)
    )
    repository.create_notebook = AsyncMock(return_value=notebook)
    repository.get_notebook = AsyncMock(return_value=notebook)
    repository.rename_notebook = AsyncMock(return_value=notebook)
    repository.list_notebooks = AsyncMock(return_value=(notebook,))
    with_verifier(application, Mock(verify=AsyncMock(return_value=identity)))
    application.dependency_overrides[get_workspace_repository] = lambda: repository
    return application, repository, identity, notebook


async def call(
    application: FastAPI, method: str, path: str, body: dict[str, str] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        return await client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        )


@pytest.mark.asyncio
async def test_session_returns_only_verified_identity_and_persists_registration() -> None:
    app, repository, identity, _notebook = private_application()
    response = await call(app, "GET", "/v1/workspace/session")
    assert response.status_code == 200
    assert response.json()["account"]["account_id"] == str(identity.account_id)
    assert response.json()["email"] == identity.email and response.json()["role"] == "creator"
    repository.register_account.assert_awaited_once_with(identity.account_id)
    repository.session.commit.assert_awaited_once()
    assert response.headers["cache-control"] == "no-store" and TOKEN not in response.text


@pytest.mark.asyncio
async def test_create_list_get_and_rename_use_identity_owner_and_commit_mutations() -> None:
    app, repository, identity, notebook = private_application()
    response = await call(app, "POST", "/v1/workspace/notebooks", {"title": "  Created  "})
    assert response.status_code == 201 and response.json()["owner_id"] == str(identity.account_id)
    assert repository.create_notebook.await_args.args[0] == identity.account_id
    assert repository.create_notebook.await_args.args[1].title == "Created"
    response = await call(app, "GET", "/v1/workspace/notebooks?limit=3")
    assert response.status_code == 200 and len(response.json()["notebooks"]) == 1
    repository.list_notebooks.assert_awaited_once_with(identity.account_id, limit=3)
    path = f"/v1/workspace/notebooks/{notebook.notebook_id}"
    assert (await call(app, "GET", path)).status_code == 200
    repository.get_notebook.assert_awaited_once_with(identity.account_id, notebook.notebook_id)
    assert (await call(app, "PATCH", path, {"title": "Renamed"})).status_code == 200
    assert repository.rename_notebook.await_args.args[:2] == (
        identity.account_id,
        notebook.notebook_id,
    )
    assert repository.session.commit.await_count == 3


@pytest.mark.asyncio
async def test_request_body_cannot_supply_owner_or_invalid_names() -> None:
    app, repository, _identity, _notebook = private_application()
    for body in (
        {"title": "Private", "owner_id": str(uuid4())},
        {"title": "   "},
        {"title": "x" * 121},
    ):
        response = await call(app, "POST", "/v1/workspace/notebooks", body)
        assert response.status_code == 422
    repository.create_notebook.assert_not_called()
    for limit in (0, 101):
        assert (await call(app, "GET", f"/v1/workspace/notebooks?limit={limit}")).status_code == 422
    repository.list_notebooks.assert_not_called()


@pytest.mark.asyncio
async def test_missing_and_inaccessible_notebooks_return_safe_not_found() -> None:
    app, repository, _identity, notebook = private_application()
    repository.get_notebook.side_effect = NotebookNotFound
    repository.rename_notebook.side_effect = NotebookNotFound
    path = f"/v1/workspace/notebooks/{notebook.notebook_id}"
    for method in ("GET", "PATCH"):
        response = await call(app, method, path, {"title": "Rename"} if method == "PATCH" else None)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOTEBOOK_NOT_FOUND"
        assert str(notebook.notebook_id) not in response.text
    repository.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("production", [False, True])
async def test_every_legacy_method_is_locked_before_private_resources_can_be_read(
    production: bool,
) -> None:
    settings = auth_settings(
        app_env="production" if production else "test", workspace_auth_enabled=not production
    )
    application = create_app(settings)
    paths = application.openapi()["paths"]
    checked = 0
    for template, operations in paths.items():
        if not template.startswith("/v1/") or template.startswith("/v1/workspace/"):
            continue
        path = template
        for parameter in ("video_id", "job_id", "artifact_id", "run_id", "trace_id", "consent_id"):
            path = path.replace("{" + parameter + "}", str(uuid4()))
        path = (
            path.replace("{profile_id}", "local")
            .replace("{sequence}", "1")
            .replace("{operation}", "study")
        )
        for method in operations:
            response = await call(application, method.upper(), path, {})
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["error"]["code"] == "LEGACY_RESOURCES_LOCKED"
            assert response.headers["cache-control"] == "no-store"
            checked += 1
    assert checked > 0
    assert (await call(application, "GET", "/health/live")).status_code == 200
    # The internal wake-up route still requires its separate signature dependency.
    response = await call(application, "POST", "/internal/qstash/dispatch", {})
    assert (
        response.status_code == 503 and response.json()["error"]["code"] == "QSTASH_NOT_CONFIGURED"
    )


@pytest.mark.asyncio
async def test_database_outages_are_safe_and_request_scoped_session_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from galaxy_frog.api import dependencies

    app, _repository, identity, _notebook = private_application()
    app.dependency_overrides.pop(get_workspace_repository)
    app.state.database_engine = cast(AsyncEngine, Mock(spec=AsyncEngine))
    database = Mock(
        execute=AsyncMock(side_effect=OperationalError("private SQL", {}, Exception(TOKEN)))
    )
    context = Mock(
        __aenter__=AsyncMock(return_value=database), __aexit__=AsyncMock(return_value=False)
    )

    def session_factory(*_args: object, **_kwargs: object) -> Callable[[], Mock]:
        return lambda: context

    monkeypatch.setattr(dependencies, "async_sessionmaker", session_factory)
    response = await call(app, "GET", "/v1/workspace/session")
    assert identity.email not in response.text and TOKEN not in response.text
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WORKSPACE_DATABASE_UNAVAILABLE"
    context.__aexit__.assert_awaited_once()
