"""Synthetic Auth plus real PostgreSQL proves private workspace HTTP isolation."""

import asyncio
import os
from base64 import urlsafe_b64encode
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
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
from galaxy_frog.db import workspace_page_ocr
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.workspace import WorkspaceAccountRow, WorkspaceNotebookRow
from galaxy_frog.db.workspace_documents import WorkspaceDocumentRepository, WorkspaceDocumentRow
from galaxy_frog.db.workspace_page_ocr import WorkspacePageOCRRow
from galaxy_frog.db.workspace_text import WorkspaceTextSourceRow
from galaxy_frog.domain.workspace import NotebookNotFound
from galaxy_frog.domain.workspace_documents import (
    DocumentNotFound,
    DocumentStorageUnavailable,
    ParsedDocument,
    StoredDocumentSection,
)
from galaxy_frog.domain.workspace_ocr import (
    PageOCRFailed,
    PageOCROutcomeUnknown,
    PageOCRResult,
    WorkspacePageOCRProvider,
)
from galaxy_frog.domain.workspace_run import WorkspaceTurnSubmit

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1", reason="requires migrated PostgreSQL"
)

_fixture_expiry = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
_fixture_claims = urlsafe_b64encode(f'{{"exp":{_fixture_expiry}}}'.encode()).decode().rstrip("=")
_DURABLE_BEARER = f"eyJhbGciOiJIUzI1NiJ9.{_fixture_claims}.fixture"
_DURABLE_OWNER = {"authorization": f"Bearer {_DURABLE_BEARER}"}


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
        if token == _DURABLE_BEARER:
            token = "owner-token"
        account = owners.get("owner-token" if token == "former-owner-token" else token)
        if account is None:
            return httpx.Response(401, json={"message": "Invalid session"})
        email = {
            "owner-token": "owner@example.com",
            "other-token": "other@example.com",
            "former-owner-token": "former@example.com",
        }[token]
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
                    delete(WorkspacePageOCRRow).where(
                        WorkspacePageOCRRow.version_id.in_(
                            select(WorkspaceDocumentRow.version_id).where(
                                WorkspaceDocumentRow.notebook_id.in_(notebook_ids)
                            )
                        )
                    )
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
async def test_chat_submission_saves_a_queued_turn_and_clears_its_draft(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    client, _ = workspace_http
    owner = _DURABLE_OWNER
    path, body = await _grounded_chat(client, owner)

    wrong_block = WorkspaceTurnSubmit.model_validate(body).passage.model_dump(mode="json")
    wrong_block["block_id"] = str(uuid4())
    for payload, status, code in (
        (
            {**body, "question": "What happens after the glacier moves?"},
            422,
            "WORKSPACE_TEMPORAL_SCOPE_REQUIRED",
        ),
        ({**body, "passage": wrong_block}, 409, "WORKSPACE_EVIDENCE_SELECTION_STALE"),
    ):
        denied = await client.post(f"{path}/turns", headers=owner, json=payload)
        assert (denied.status_code, denied.json()["error"]["code"]) == (status, code)
    assert (
        await client.post(f"{path}/turns", headers=owner, json={**body, "question": " "})
    ).status_code == 422
    assert (
        await client.post(
            f"{path}/turns", headers={"authorization": "Bearer owner-token"}, json=body
        )
    ).status_code == 401
    accepted = await client.post(f"{path}/turns", headers=owner, json=body)
    assert accepted.status_code == 202, accepted.text
    turn = accepted.json()
    assert (turn["sequence"], turn["question"], turn["run"]["status"]) == (
        1,
        "What moves?",
        "queued",
    )
    assert turn["passage"] == body["passage"]
    assert turn["run"]["settings"]["provider"] == "ollama"
    assert accepted.headers["cache-control"] == "no-store"
    reopened = await client.get(f"{path}/turns", headers=owner)
    assert reopened.json()["turns"] == [turn]
    assert (await client.get(f"{path}/turns?before=1", headers=owner)).json() == {
        "turns": [],
        "next_before": None,
    }
    assert (await client.get(path, headers=owner)).json()["draft"] == {"text": "", "revision": 2}


async def _grounded_chat(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> tuple[str, dict[str, object]]:
    notebook = await client.post(
        "/v1/workspace/notebooks", headers=owner, json={"title": "Glacier notebook"}
    )
    path = f"/v1/workspace/notebooks/{notebook.json()['notebook_id']}"
    source = (
        await client.post(
            f"{path}/text-sources",
            headers=owner,
            json={"title": "Field notes", "format": "plain", "text": "Ice flows downhill."},
        )
    ).json()
    chat_id = str(uuid4())
    assert (
        await client.post(
            f"{path}/chats", headers=owner, json={"chat_id": chat_id, "title": "Glaciers"}
        )
    ).status_code == 201
    chat = f"{path}/chats/{chat_id}"
    assert (
        await client.put(
            f"{chat}/draft", headers=owner, json={"text": "What moves?", "revision": 0}
        )
    ).json() == {"text": "What moves?", "revision": 1}
    return chat, {
        "submission_key": str(uuid4()),
        "question": "What moves?",
        "draft_revision": 1,
        "passage": {
            "source_id": source["source_id"],
            "version_id": source["version_id"],
            "block_id": source["blocks"][0]["block_id"],
        },
    }


@pytest.mark.asyncio
async def test_chat_submission_replays_after_a_dropped_response_without_clearing_a_newer_draft(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    client, _ = workspace_http
    owner = _DURABLE_OWNER
    other = {"authorization": "Bearer other-token"}
    path, body = await _grounded_chat(client, owner)
    first, duplicate = await asyncio.gather(
        *(client.post(f"{path}/turns", headers=owner, json=body) for _ in range(2))
    )
    assert first.status_code == duplicate.status_code == 202
    assert first.json() == duplicate.json()
    draft = {"text": "A later question", "revision": 2}
    assert (await client.put(f"{path}/draft", headers=owner, json=draft)).status_code == 200
    replay = await client.post(f"{path}/turns", headers=owner, json=body)
    assert replay.json() == first.json()
    assert (await client.get(path, headers=owner)).json()["draft"] == {**draft, "revision": 3}
    for payload, error in (
        ({**body, "question": "Different question"}, "WORKSPACE_SUBMISSION_CONFLICT"),
        (
            {**body, "question": "What happens after the glacier moves?"},
            "WORKSPACE_SUBMISSION_CONFLICT",
        ),
        ({**body, "submission_key": str(uuid4())}, "WORKSPACE_DRAFT_CONFLICT"),
        (
            {
                **body,
                "submission_key": str(uuid4()),
                "question": draft["text"],
                "draft_revision": 3,
            },
            "WORKSPACE_RUN_ACTIVE",
        ),
    ):
        denied = await client.post(f"{path}/turns", headers=owner, json=payload)
        assert denied.status_code == 409 and denied.json()["error"]["code"] == error
    assert (await client.post(f"{path}/turns", headers=other, json=body)).status_code == 404
    assert (await client.get(f"{path}/turns", headers=other)).status_code == 404
    assert (await client.get(f"{path}/turns", headers=owner)).json()["turns"] == [first.json()]
    another, _ = await _grounded_chat(client, owner)
    crossed = f"{another.rsplit('/', 1)[0]}/{path.rsplit('/', 1)[-1]}/turns"
    assert (await client.get(crossed, headers=owner)).status_code == 404
    assert (await client.post(crossed, headers=owner, json=body)).status_code == 404


@pytest.mark.asyncio
async def test_chat_worker_saves_one_validated_answer_while_the_provider_is_outside_the_transaction(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    from galaxy_frog.adapters.generation.ollama import OllamaGenerationProvider
    from galaxy_frog.entrypoints.workspace_chat_worker import WorkspaceChatWorker

    client, _ = workspace_http
    owner = _DURABLE_OWNER
    path, body = await _grounded_chat(client, owner)
    queued = (await client.post(f"{path}/turns", headers=owner, json=body)).json()
    entered, release = asyncio.Event(), asyncio.Event()
    settings = auth_settings(
        database_url=Settings().database_url, workspace_creator_emails=["owner@example.com"]
    )
    answer = '{"blocks":[{"text":"Ice moves downhill.","citations":[{"reference":"E1","quote":"Ice flows downhill."}]}]}'

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        assert b"Ice flows downhill." in request.content
        entered.set()
        await release.wait()
        return httpx.Response(
            200, json={"model": settings.generation_model, "done": True, "response": answer}
        )

    async def installed(_origin: str) -> dict[str, str]:
        return {settings.generation_model: "test-model-digest"}

    engine = create_database_engine(settings)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            provider = OllamaGenerationProvider(
                base_url=settings.ollama_base_url, model=settings.generation_model, client=transport
            )
            worker = WorkspaceChatWorker(settings, async_sessionmaker(engine), provider, installed)
            execution = asyncio.create_task(worker.run_once())
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                running = await client.get(f"{path}/turns", headers=owner)
                assert running.json()["turns"][0]["run"]["status"] == "running"
                assert not await worker.run_once()
                newer_draft = await client.put(
                    f"{path}/draft", headers=owner, json={"text": "Next question", "revision": 2}
                )
                assert newer_draft.status_code == 200
            finally:
                release.set()
                await execution
    finally:
        await engine.dispose()

    saved = (await client.get(f"{path}/turns", headers=owner)).json()["turns"][0]
    assert saved["turn_id"] == queued["turn_id"]
    assert saved["run"]["run_id"] == queued["run"]["run_id"]
    assert saved["run"]["status"] == "completed"
    assert saved["run"]["answer"]["blocks"][0]["citations"] == [
        {"reference": "E1", "quote": "Ice flows downhill."}
    ]
    evidence = saved["run"]["evidence"]
    assert (evidence["start_char"], evidence["end_char"], evidence["excerpt"]) == (
        0,
        19,
        "Ice flows downhill.",
    )
    assert evidence["source"]["version_id"] == saved["passage"]["version_id"]
    assert evidence["block_id"] == saved["passage"]["block_id"]
    assert (await client.get(path, headers=owner)).json()["draft"] == {
        "text": "Next question",
        "revision": 3,
    }
    erased_test_source = create_database_engine(settings)
    try:
        async with async_sessionmaker(erased_test_source).begin() as session:
            await session.execute(
                delete(WorkspaceTextSourceRow).where(
                    WorkspaceTextSourceRow.id == UUID(saved["passage"]["source_id"])
                )
            )
    finally:
        await erased_test_source.dispose()
    withdrawn = (await client.get(f"{path}/turns", headers=owner)).json()["turns"][0]["run"]
    assert (withdrawn["status"], withdrawn["failure"]) == ("failed", "evidence_unavailable")
    assert withdrawn["answer"] is None and withdrawn["evidence"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "status", "failure"),
    [
        ("unavailable", "failed", "model_unavailable"),
        ("revoked", "failed", "access_changed"),
        ("missing_auth", "failed", "access_changed"),
        ("missing", "failed", "evidence_unavailable"),
        ("forged", "failed", "answer_unverified"),
        ("lost", "uncertain", "dispatch_unknown"),
        ("revision_changed", "failed", "model_unavailable"),
    ],
)
async def test_chat_worker_surfaces_fail_closed_outcomes_without_replaying(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
    scenario: str,
    status: str,
    failure: str,
) -> None:
    from galaxy_frog.adapters.generation.ollama import OllamaGenerationProvider
    from galaxy_frog.entrypoints.workspace_chat_worker import WorkspaceChatWorker

    client, _ = workspace_http
    owner = _DURABLE_OWNER
    path, body = await _grounded_chat(client, owner)
    accepted = await client.post(f"{path}/turns", headers=owner, json=body)
    assert accepted.status_code == 202
    pin = accepted.json()["passage"]
    settings = auth_settings(
        database_url=Settings().database_url,
        supabase_url=None if scenario == "missing_auth" else AUTH_URL,
        workspace_creator_emails=["other@example.com"]
        if scenario == "revoked"
        else ["owner@example.com"],
    )
    engine = create_database_engine(settings)
    dispatches = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        if scenario == "lost":
            raise httpx.ReadTimeout(
                "Private transport diagnostics should never surface.", request=request
            )
        quote = "Ice flows downhill." if scenario == "revision_changed" else "forged quote"
        return httpx.Response(
            200,
            json={
                "model": settings.generation_model,
                "done": True,
                "response": f'{{"blocks":[{{"text":"Ice moves down.","citations":[{{"reference":"E1","quote":"{quote}"}}]}}]}}',
            },
        )

    async def installed(_origin: str) -> dict[str, str]:
        if scenario == "unavailable":
            return {}
        digest = "different" if scenario == "revision_changed" and dispatches else "fixture-digest"
        return {settings.generation_model: digest}

    try:
        sessions = async_sessionmaker(engine)
        if scenario == "missing":
            async with sessions.begin() as session:
                await session.execute(
                    delete(WorkspaceTextSourceRow).where(
                        WorkspaceTextSourceRow.id == UUID(pin["source_id"])
                    )
                )
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            provider = OllamaGenerationProvider(
                base_url=settings.ollama_base_url, model=settings.generation_model, client=transport
            )
            worker = WorkspaceChatWorker(settings, sessions, provider, installed)
            assert await worker.run_once()
            assert not await worker.run_once()
    finally:
        await engine.dispose()

    reopened = await client.get(f"{path}/turns", headers=owner)
    outcome = reopened.json()["turns"][0]["run"]
    assert (outcome["status"], outcome["failure"]) == (status, failure)
    assert outcome["answer"] is None and outcome["evidence"] is None
    assert "Private transport diagnostics" not in reopened.text
    assert dispatches == int(scenario in {"lost", "forged", "revision_changed"})
    replay = await client.post(f"{path}/turns", headers=owner, json=body)
    assert replay.json()["run"] == outcome


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
async def test_workspace_account_binding_rejects_a_switched_session_before_writing(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    client, owners = workspace_http
    path = "/v1/workspace/notebooks"
    old_tab = {
        "authorization": "Bearer other-token",
        "x-workspace-account": str(owners["owner-token"]),
    }
    assert (await client.get(path, headers=old_tab)).status_code == 409
    rejected = await client.post(path, headers=old_tab, json={"title": "Stale optimistic notebook"})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "WORKSPACE_SESSION_CHANGED"
    assert rejected.headers["cache-control"] == "no-store"
    new_tab = {**old_tab, "x-workspace-account": str(owners["other-token"])}
    assert (await client.get(path, headers=new_tab)).json() == {"notebooks": []}
    created = await client.post(path, headers=new_tab, json={"title": "Current notebook"})
    assert created.status_code == 201
    assert created.json()["owner_id"] == str(owners["other-token"])


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
async def test_pinned_pasted_passage_survives_a_new_copy_and_rejects_an_invalid_session(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
) -> None:
    client, _ = workspace_http
    owner = {"authorization": "Bearer owner-token"}
    notebook = await client.post(
        "/v1/workspace/notebooks", headers=owner, json={"title": "Version evidence"}
    )
    assert notebook.status_code == 201
    path = f"/v1/workspace/notebooks/{notebook.json()['notebook_id']}/text-sources"
    original = await client.post(
        path,
        headers=owner,
        json={"title": "Field notes", "format": "markdown", "text": "# Ice\r\n\r\nIce flows."},
    )
    assert original.status_code == 200
    source = original.json()
    passage = source["blocks"][1]
    assert (passage["start_char"], passage["end_char"], passage["text"]) == (7, 17, "Ice flows.")
    pin = f"{path}/{source['source_id']}/versions/{source['version_id']}"

    changed = await client.post(
        path,
        headers=owner,
        json={"title": "Field notes", "format": "markdown", "text": "# Ice\n\nIce melts."},
    )
    assert changed.status_code == 200
    new_source = changed.json()
    assert new_source["source_id"] != source["source_id"]
    assert new_source["version_id"] != source["version_id"]
    assert new_source["blocks"][1]["block_id"] != passage["block_id"]

    reopened = await client.get(pin, headers=owner)
    assert reopened.status_code == 200
    assert reopened.json()["blocks"][1] == passage
    assert reopened.json()["text"][7:17] == "Ice flows."
    assert reopened.headers["cache-control"] == "no-store"

    wrong_version = f"{path}/{source['source_id']}/versions/{new_source['version_id']}"
    assert (await client.get(wrong_version, headers=owner)).status_code == 404
    assert (
        await client.get(pin, headers={"authorization": "Bearer other-token"})
    ).status_code == 404
    denied = await client.get(pin, headers={"authorization": "Bearer invalidated-token"})
    assert denied.status_code == 401
    assert "Ice flows." not in denied.text
    outside_allowlist = await client.get(
        pin, headers={"authorization": "Bearer former-owner-token"}
    )
    assert outside_allowlist.status_code == 403
    assert outside_allowlist.json()["error"]["code"] == "WORKSPACE_INVITATION_REQUIRED"
    assert "Ice flows." not in outside_allowlist.text

    # Simulate loss of this test's exact pin. The workspace has no source-erasure route.
    engine = create_database_engine(Settings())
    try:
        async with async_sessionmaker(engine).begin() as session:
            await session.execute(
                delete(WorkspaceTextSourceRow).where(
                    WorkspaceTextSourceRow.id == UUID(source["source_id"]),
                    WorkspaceTextSourceRow.version_id == UUID(source["version_id"]),
                    WorkspaceTextSourceRow.notebook_id == UUID(notebook.json()["notebook_id"]),
                )
            )
    finally:
        await engine.dispose()
    missing = await client.get(pin, headers=owner)
    assert missing.status_code == 404
    assert "Ice flows." not in missing.text
    new_path = f"{path}/{new_source['source_id']}/versions/{new_source['version_id']}"
    assert (await client.get(new_path, headers=owner)).json()["blocks"][1]["text"] == "Ice melts."


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


_OWNER = {"authorization": "Bearer owner-token"}
_OTHER = {"authorization": "Bearer other-token"}
_CONSENT = {"allow_external_page_image": True}
_OCR_PNG = b"\x89PNG\r\n\x1a\nscanned-page"


@pytest_asyncio.fixture
async def pdf_ocr_url(workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]]) -> str:
    client, _ = workspace_http
    result = await client.post(
        "/v1/workspace/notebooks", headers=_OWNER, json={"title": "OCR test"}
    )
    assert result.status_code == 201
    root = f"/v1/workspace/notebooks/{result.json()['notebook_id']}/documents"
    uploaded = await client.post(
        root,
        params={"filename": "scan.pdf"},
        headers=_OWNER | {"content-type": "application/octet-stream"},
        content=sample_pdf(),
    )
    assert uploaded.status_code == 200
    source = uploaded.json()
    return f"{root}/{source['source_id']}/versions/{source['version_id']}/pages/2/ocr"


@pytest.mark.asyncio
async def test_ocr_requests_require_consent_blank_pdf_page_and_owner(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
    pdf_ocr_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = workspace_http
    assert (await client.get(pdf_ocr_url, headers=_OWNER)).json()["status"] == "unavailable"
    off = await client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT)
    assert off.status_code == 200 and off.json()["status"] == "unavailable"
    provider = AsyncMock(spec=WorkspacePageOCRProvider)
    monkeypatch.setattr(workspace, "_page_ocr_provider", Mock(return_value=provider))
    read = await client.get(pdf_ocr_url, headers=_OWNER)
    assert read.json()["status"] == "ready"
    assert read.headers["cache-control"] == "no-store"
    for invalid in (
        {},
        {"allow_external_page_image": False},
        {"allow_external_page_image": 1},
        {"allow_external_page_image": "true"},
    ):
        assert (await client.post(pdf_ocr_url, headers=_OWNER, json=invalid)).status_code == 422
    assert (await client.get(pdf_ocr_url, headers=_OTHER)).status_code == 404
    assert (await client.post(pdf_ocr_url, headers=_OTHER, json=_CONSENT)).status_code == 404
    assert (await client.post(pdf_ocr_url, json=_CONSENT)).status_code == 401
    first_page = pdf_ocr_url.replace("/pages/2/", "/pages/1/")
    assert (await client.get(first_page, headers=_OWNER)).json()["status"] == "not_needed"
    assert (await client.post(first_page, headers=_OWNER, json=_CONSENT)).json()[
        "status"
    ] == "not_needed"

    root = pdf_ocr_url.split("/documents/", 1)[0] + "/documents"
    uploaded = await client.post(
        root,
        params={"filename": "notes.txt"},
        headers=_OWNER | {"content-type": "application/octet-stream"},
        content=b"notes",
    )
    source = uploaded.json()
    non_pdf = f"{root}/{source['source_id']}/versions/{source['version_id']}/pages/1/ocr"
    assert (await client.get(non_pdf, headers=_OWNER)).status_code == 404

    with monkeypatch.context() as patch:
        patch.setattr(
            WorkspaceDocumentRepository, "pdf_digest", AsyncMock(side_effect=DocumentNotFound)
        )
        assert (await client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT)).status_code == 404
    with monkeypatch.context() as patch:
        patch.setattr(
            WorkspaceDocumentProcessor, "preview", AsyncMock(side_effect=DocumentProcessingTimeout)
        )
        assert (await client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT)).status_code == 504
    provider.extract.assert_not_called()
    assert (await client.get(pdf_ocr_url, headers=_OWNER)).json()["status"] == "ready"


@pytest.mark.asyncio
async def test_concurrent_ocr_approvals_send_one_page_and_reopen_saved_evidence(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
    pdf_ocr_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = workspace_http
    both_rendering = asyncio.Event()
    job_recorded = asyncio.Event()
    finish = asyncio.Event()
    participants = 0

    async def render(_: Path, page_number: int) -> bytes:
        nonlocal participants
        assert page_number == 2
        participants += 1
        if participants == 2:
            both_rendering.set()
        await both_rendering.wait()
        return _OCR_PNG

    async def recognize(
        png: bytes, *, record_job: Callable[[str], Awaitable[None]]
    ) -> PageOCRResult:
        assert png == _OCR_PNG
        await record_job("versioned-job")
        job_recorded.set()
        await finish.wait()
        return PageOCRResult("Glacier evidence.\r\n\r\nSecond line.")

    provider = AsyncMock(spec=WorkspacePageOCRProvider)
    provider.extract.side_effect = recognize
    monkeypatch.setattr(workspace, "_page_ocr_provider", Mock(return_value=provider))
    monkeypatch.setattr(WorkspaceDocumentProcessor, "preview", AsyncMock(side_effect=render))
    requests = [
        asyncio.create_task(client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT))
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(job_recorded.wait(), timeout=5)
        completed, _ = await asyncio.wait(requests, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert len(completed) == 1
        assert completed.pop().result().json()["status"] == "running"
        with monkeypatch.context() as patch:
            patch.setattr(workspace_page_ocr, "OCR_LEDGER_TIMEOUT_SECONDS", 0)
            assert (await client.get(pdf_ocr_url, headers=_OWNER)).json()["status"] == "unknown"
    finally:
        finish.set()
        await asyncio.gather(*requests)

    receipt = (await client.get(pdf_ocr_url, headers=_OWNER)).json()
    assert receipt["status"] == "succeeded"
    assert receipt["page_number"] == 2 and receipt["provider"] == "llamaparse"
    assert receipt["approved_at"] and receipt["completed_at"]
    assert receipt["text"] == "Glacier evidence.\n\nSecond line."
    assert [block["text"] for block in receipt["blocks"]] == ["Glacier evidence.", "Second line."]
    assert (await client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT)).json() == receipt
    assert "provider_job_id" not in receipt
    assert (await client.get(pdf_ocr_url, headers=_OTHER)).status_code == 404
    original = pdf_ocr_url.replace("/pages/2/ocr", "/sections/2")
    assert (await client.get(original, headers=_OWNER)).json()["text"] == ""
    provider.extract.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "status"),
    [
        ("", "no_text"),
        ("bad\0text", "failed"),
        (PageOCRFailed, "failed"),
        (PageOCROutcomeUnknown, "unknown"),
    ],
)
async def test_ocr_terminal_results_are_reopened_without_resending(
    workspace_http: tuple[httpx.AsyncClient, dict[str, UUID]],
    pdf_ocr_url: str,
    monkeypatch: pytest.MonkeyPatch,
    result: str | type[Exception],
    status: str,
) -> None:
    client, _ = workspace_http
    provider = AsyncMock(spec=WorkspacePageOCRProvider)
    if isinstance(result, str):
        provider.extract.return_value = PageOCRResult(result)
    else:
        provider.extract.side_effect = result
    monkeypatch.setattr(workspace, "_page_ocr_provider", Mock(return_value=provider))
    monkeypatch.setattr(WorkspaceDocumentProcessor, "preview", AsyncMock(return_value=_OCR_PNG))
    first = await client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT)
    assert first.status_code == 200 and first.json()["status"] == status
    assert first.json()["blocks"] == []
    assert (await client.post(pdf_ocr_url, headers=_OWNER, json=_CONSENT)).json() == first.json()
    provider.extract.assert_awaited_once()
