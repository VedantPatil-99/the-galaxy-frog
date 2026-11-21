"""Synthetic Auth plus real PostgreSQL proves private workspace HTTP isolation."""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from tests.test_workspace_auth import AUTH_URL, KEY, auth_settings
from tests.test_workspace_documents import sample_pdf

from galaxy_frog.adapters.workspace_auth import SupabaseIdentityVerifier
from galaxy_frog.adapters.workspace_documents import (
    DocumentProcessingTimeout,
    DocumentProcessingUnavailable,
    WorkspaceDocumentProcessor,
)
from galaxy_frog.api.app import create_app
from galaxy_frog.api.routes import workspace
from galaxy_frog.api.workspace_auth import get_workspace_identity_verifier
from galaxy_frog.config import Settings
from galaxy_frog.db.workspace import WorkspaceAccountRow, WorkspaceNotebookRow
from galaxy_frog.db.workspace_documents import WorkspaceDocumentRepository, WorkspaceDocumentRow
from galaxy_frog.db.workspace_text import WorkspaceTextSourceRow
from galaxy_frog.domain.workspace import NotebookNotFound
from galaxy_frog.domain.workspace_documents import (
    DocumentStorageUnavailable,
    ParsedDocument,
    StoredDocumentSection,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires migrated PostgreSQL"
)


@pytest_asyncio.fixture
async def workspace_http(
    tmp_path: Path,
) -> AsyncGenerator[tuple[httpx.AsyncClient, dict[str, UUID]]]:
    owners = {"owner-token": uuid4(), "other-token": uuid4()}
    application = create_app(
        auth_settings(
            database_url=Settings().database_url,
            workspace_creator_emails=["owner@example.com", "other@example.com"],
            workspace_document_root=tmp_path / "document-store",
        )
    )

    def authenticated_user(request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        account = owners[token]
        email = "owner@example.com" if token == "owner-token" else "other@example.com"
        return httpx.Response(
            200,
            json={
                "id": str(account),
                "email": email,
                "aud": "authenticated",
                "role": "authenticated",
                "email_confirmed_at": "2026-11-19T00:00:00Z",
                "is_anonymous": False,
            },
        )

    async def verifier() -> AsyncGenerator[SupabaseIdentityVerifier]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(authenticated_user)) as auth:
            yield SupabaseIdentityVerifier(auth, url=AUTH_URL, publishable_key=KEY)

    application.dependency_overrides[get_workspace_identity_verifier] = verifier
    async with application.router.lifespan_context(application):
        engine: AsyncEngine = application.state.database_engine
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://test"
            ) as client:
                yield client, owners
        finally:
            async with async_sessionmaker(engine)() as session:
                notebook_ids = select(WorkspaceNotebookRow.id).where(
                    WorkspaceNotebookRow.owner_id.in_(owners.values())
                )
                await session.execute(
                    delete(WorkspaceDocumentRow).where(
                        WorkspaceDocumentRow.notebook_id.in_(notebook_ids)
                    )
                )
                await session.execute(
                    delete(WorkspaceTextSourceRow).where(
                        WorkspaceTextSourceRow.notebook_id.in_(notebook_ids)
                    )
                )
                await session.execute(
                    delete(WorkspaceNotebookRow).where(
                        WorkspaceNotebookRow.owner_id.in_(owners.values())
                    )
                )
                await session.execute(
                    delete(WorkspaceAccountRow).where(WorkspaceAccountRow.id.in_(owners.values()))
                )
                await session.commit()


@pytest.mark.asyncio
async def test_verified_http_users_cannot_access_or_overwrite_another_owners_notebook(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    client, owners = workspace_http
    own = {"authorization": "Bearer owner-token"}
    other = {"authorization": "Bearer other-token"}
    result = await client.post(
        "/v1/workspace/notebooks", headers=own, json={"title": "Owner's evidence"}
    )
    assert result.status_code == 201
    notebook = result.json()
    assert notebook["owner_id"] == str(owners["owner-token"])
    path = f"/v1/workspace/notebooks/{notebook['notebook_id']}"
    assert (await client.get("/v1/workspace/session", headers=other)).status_code == 200
    assert (await client.get("/v1/workspace/notebooks", headers=other)).json() == {"notebooks": []}
    for identifier in (notebook["notebook_id"], str(uuid4())):
        private_path = f"/v1/workspace/notebooks/{identifier}"
        assert (await client.get(private_path, headers=other)).status_code == 404
        response = await client.patch(private_path, headers=other, json={"title": "Takeover"})
        assert response.status_code == 404
    assert (await client.get(path, headers=own)).json()["title"] == "Owner's evidence"
    response = await client.patch(path, headers=own, json={"title": "Renamed evidence"})
    assert response.status_code == 200 and response.json()["title"] == "Renamed evidence"
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get(path)).status_code == 401
    assert (await client.get(f"/v1/videos/{uuid4()}", headers=own)).status_code == 403


@pytest.mark.asyncio
async def test_pasted_text_is_idempotent_versioned_and_owner_scoped(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    client, _ = workspace_http
    own = {"authorization": "Bearer owner-token"}
    other = {"authorization": "Bearer other-token"}
    notebook = await client.post(
        "/v1/workspace/notebooks", headers=own, json={"title": "Field notes"}
    )
    assert notebook.status_code == 201
    notebook_id = notebook.json()["notebook_id"]
    path = f"/v1/workspace/notebooks/{notebook_id}/text-sources"
    draft = {
        "title": "Glacier notes",
        "format": "markdown",
        "text": "# Ice\r\n\r\nA glacier flows.",
    }
    first, repeat = await asyncio.gather(
        client.post(path, headers=own, json=draft), client.post(path, headers=own, json=draft)
    )
    assert first.status_code == repeat.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    version = first.json()
    assert repeat.json() == version
    assert version["text"] == "# Ice\n\nA glacier flows."
    assert [block["text"] for block in version["blocks"]] == ["# Ice", "A glacier flows."]
    version_path = f"{path}/{version['source_id']}/versions/{version['version_id']}"
    assert (await client.get(version_path, headers=own)).json() == version

    different = await client.post(path, headers=own, json={**draft, "title": "Another note"})
    assert different.status_code == 200
    assert different.json()["source_id"] != version["source_id"]
    first_page = (await client.get(f"{path}?limit=1", headers=own)).json()
    assert len(first_page["sources"]) == 1
    assert "text" not in first_page["sources"][0]
    second_page = (
        await client.get(f"{path}?limit=1&after={first_page['next_after']}", headers=own)
    ).json()
    assert len(second_page["sources"]) == 1 and second_page["next_after"] is None
    assert first_page["sources"][0]["source_id"] != second_page["sources"][0]["source_id"]

    assert (await client.get(path, headers=other)).status_code == 404
    assert (await client.post(path, headers=other, json=draft)).status_code == 404
    assert (await client.get(version_path, headers=other)).status_code == 404
    assert (await client.get(version_path)).status_code == 401

    malformed = await client.post(path, headers=own, json={**draft, "text": " \n "})
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "WORKSPACE_TEXT_REJECTED"
    assert (await client.post(path, headers=own, json={"title": "Incomplete"})).status_code == 422
    unsupported = await client.post(path, headers=own, content="plain body")
    assert unsupported.status_code == 415
    oversized = await client.post(
        path, headers={**own, "content-type": "application/json"}, content=b" " * (512 * 1024 + 1)
    )
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_private_upload_reopens_versioned_pdf_sections_and_images(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = workspace_http
    own = {"authorization": "Bearer owner-token", "content-type": "application/octet-stream"}
    other = {"authorization": "Bearer other-token"}
    notebook = await client.post(
        "/v1/workspace/notebooks",
        headers={"authorization": own["authorization"]},
        json={"title": "Document test"},
    )
    assert notebook.status_code == 201
    notebook_id = notebook.json()["notebook_id"]
    path = f"/v1/workspace/notebooks/{notebook_id}/documents"
    data = sample_pdf()
    first, repeat = await asyncio.gather(
        client.post(path, params={"filename": "Field notes.pdf"}, headers=own, content=data),
        client.post(path, params={"filename": "Field notes.pdf"}, headers=own, content=data),
    )
    assert first.status_code == repeat.status_code == 200, (first.text, repeat.text)
    source = first.json()
    assert source == repeat.json()
    assert source["section_count"] == 2 and source["pages_without_text"] == 1
    files = list((tmp_path / "document-store" / notebook_id).iterdir())
    assert len(files) == 1 and files[0].read_bytes() == data
    assert (await client.get(path, headers=own)).json()["sources"] == [source]

    root = f"{path}/{source['source_id']}/versions/{source['version_id']}"
    section = (await client.get(f"{root}/sections/1", headers=own)).json()
    assert section["page_number"] == 1 and section["blocks"]
    assert "Water flows downhill." in section["blocks"][0]["text"]
    assert (await client.get(f"{root}/sections/1", headers=own)).json() == section
    blank = (await client.get(f"{root}/sections/2", headers=own)).json()
    assert blank["page_number"] == 2 and blank["text"] == "" and blank["blocks"] == []

    image_path = f"{root}/pages/1/image"
    preview = await client.get(image_path, headers=own)
    assert preview.status_code == 200, preview.text[:200]
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["cache-control"] == "no-store"
    assert preview.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert (await client.get(image_path, headers=other)).status_code == 404
    assert (await client.get(path, headers=other)).status_code == 404
    assert (
        await client.post(path, params={"filename": "copy.pdf"}, headers=other, content=data)
    ).status_code == 404
    assert (await client.get(f"{root}/sections/1", headers=other)).status_code == 404
    assert (await client.get(image_path)).status_code == 401
    assert (await client.get(f"{root}/pages/3/image", headers=own)).status_code == 404

    rejected = await client.post(
        path, params={"filename": "wrong.pdf"}, headers=own, content=b"just text"
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "WORKSPACE_DOCUMENT_REJECTED"
    oversized = await client.post(
        path, params={"filename": "large.pdf"}, headers=own, content=b"x" * (8 * 1024 * 1024 + 1)
    )
    assert oversized.status_code == 413
    assert (
        await client.post(path, params={"filename": "notes.bin"}, headers=own, content=b"unknown")
    ).status_code == 422
    assert (
        await client.post(
            path,
            params={"filename": "notes.txt"},
            headers={"authorization": own["authorization"]},
            json={"text": "wrong wire format"},
        )
    ).status_code == 415

    markdown = await client.post(
        path, params={"filename": "Notes.md"}, headers=own, content=b"# Stream notes\n\nCold water."
    )
    assert markdown.status_code == 200
    text = markdown.json()
    assert text["format"] == "markdown" and text["pages_without_text"] is None
    version = f"{path}/{text['source_id']}/versions/{text['version_id']}"
    saved = (await client.get(f"{version}/sections/1", headers=own)).json()
    assert saved["page_number"] is None
    assert [block["text"] for block in saved["blocks"]] == ["# Stream notes", "Cold water."]
    assert (await client.get(f"{version}/pages/1/image", headers=own)).status_code == 404
    first_page = (await client.get(f"{path}?limit=1", headers=own)).json()
    second_page = (
        await client.get(f"{path}?limit=1&after={first_page['next_after']}", headers=own)
    ).json()
    assert len(first_page["sources"]) == len(second_page["sources"]) == 1
    assert second_page["next_after"] is None
    assert first_page["sources"][0]["source_id"] != second_page["sources"][0]["source_id"]

    with monkeypatch.context() as patch:
        patch.setattr(
            workspace, "document_original", AsyncMock(side_effect=DocumentStorageUnavailable)
        )
        rejected_preview = await client.get(image_path, headers=own)
        assert rejected_preview.status_code == 503
        assert rejected_preview.json()["error"]["code"] == "WORKSPACE_DOCUMENT_STORAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_document_import_errors_are_typed_and_do_not_create_sources(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = workspace_http
    identity = {"authorization": "Bearer owner-token"}
    notebook = await client.post(
        "/v1/workspace/notebooks", headers=identity, json={"title": "Errors"}
    )
    path = f"/v1/workspace/notebooks/{notebook.json()['notebook_id']}/documents"
    headers = identity | {"content-type": "application/octet-stream"}

    for failure, status, code in (
        (DocumentProcessingTimeout, 504, "WORKSPACE_DOCUMENT_TIMEOUT"),
        (DocumentProcessingUnavailable, 503, "WORKSPACE_DOCUMENT_PROCESSOR_UNAVAILABLE"),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(WorkspaceDocumentProcessor, "extract", AsyncMock(side_effect=failure))
            response = await client.post(
                path, params={"filename": "notes.txt"}, headers=headers, content=b"text"
            )
            assert response.status_code == status and response.json()["error"]["code"] == code

    parsed = ParsedDocument(sections=(StoredDocumentSection(text="text", spans=((0, 4),)),))
    with monkeypatch.context() as patch:
        patch.setattr(WorkspaceDocumentProcessor, "extract", AsyncMock(return_value=parsed))
        patch.setattr(WorkspaceDocumentRepository, "save", AsyncMock(side_effect=NotebookNotFound))
        disappeared = await client.post(
            path, params={"filename": "notes.txt"}, headers=headers, content=b"text"
        )
        assert disappeared.status_code == 404
    assert (await client.get(path, headers=identity)).json() == {"sources": [], "next_after": None}
