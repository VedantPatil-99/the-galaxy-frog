"""Verified identity, invitation policy, configuration, and safe provider failures."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError

from galaxy_frog.adapters.workspace_auth import SupabaseIdentityVerifier
from galaxy_frog.api.app import create_app
from galaxy_frog.api.workspace_auth import get_workspace_identity_verifier
from galaxy_frog.application.workspace_auth import IdentityVerificationError
from galaxy_frog.config import Settings
from galaxy_frog.domain.workspace import VerifiedWorkspaceIdentity

AUTH_URL = "https://project.supabase.co"
KEY = "sb_publishable_synthetic-project-key"
TOKEN = "synthetic-user-access-token"
ACCOUNT = uuid4()


def auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": None,
        "workspace_auth_enabled": True,
        "supabase_url": AUTH_URL,
        "supabase_publishable_key": KEY,
        "workspace_creator_emails": ["owner@example.com"],
    }
    values.update(overrides)
    return Settings.model_validate(values)


def user_payload() -> dict[str, object]:
    return {
        "id": str(ACCOUNT),
        "email": "Owner@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "email_confirmed_at": "2026-11-19T00:00:00Z",
        "is_anonymous": False,
        "user_metadata": {"role": "admin", "owner_id": str(uuid4())},
    }


async def request(
    application: FastAPI, path: str, *, authorization: str | None = f"Bearer {TOKEN}"
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        return await client.get(
            path, headers={"authorization": authorization} if authorization else {}
        )


def with_verifier(application: FastAPI, verifier: Mock) -> None:
    application.dependency_overrides[get_workspace_identity_verifier] = lambda: verifier


def test_auth_settings_normalize_origin_and_keep_key_secret() -> None:
    settings = auth_settings(supabase_url=f" {AUTH_URL}/ ", supabase_publishable_key=f" {KEY} ")
    assert settings.supabase_url == AUTH_URL
    assert settings.supabase_publishable_key == SecretStr(KEY)
    assert KEY not in repr(settings)
    assert settings.workspace_auth_required is True
    assert auth_settings(workspace_auth_enabled=False).workspace_auth_required is False
    assert auth_settings(workspace_auth_enabled=False, app_env="production").workspace_auth_required


@pytest.mark.parametrize(
    "url",
    [
        "http://project.supabase.co",
        "https://name:password@auth.example.com",
        "https://auth.test/a",
        "https://auth.test?query=1",
        "https://auth.test#fragment",
        "file:///tmp/auth",
        "https:///",
        "https://auth.example.com:0",
        "https://auth.example.com:invalid",
        "https://bad host.example.com",
    ],
)
def test_auth_settings_reject_unsafe_origins(url: str) -> None:
    with pytest.raises(ValidationError):
        auth_settings(supabase_url=url)


@pytest.mark.parametrize(
    "key",
    ["", "sb_secret_private", "legacy.jwt.role", "sb_publishable_" + "x" * 4096],
    ids=["empty", "secret", "legacy", "oversized"],
)
def test_auth_settings_reject_secret_or_unbounded_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        auth_settings(supabase_publishable_key=key)


@pytest.mark.parametrize("timeout", [0, 16])
def test_auth_settings_bound_network_deadlines(timeout: int) -> None:
    with pytest.raises(ValidationError):
        auth_settings(workspace_auth_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_supabase_verifies_with_configured_authority_and_ignores_user_metadata() -> None:
    def respond(outbound: httpx.Request) -> httpx.Response:
        assert str(outbound.url) == f"{AUTH_URL}/auth/v1/user"
        assert outbound.headers["authorization"] == f"Bearer {TOKEN}"
        assert outbound.headers["apikey"] == KEY
        return httpx.Response(200, json=user_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        verifier = SupabaseIdentityVerifier(client, url=AUTH_URL, publishable_key=KEY)
        identity = await verifier.verify(TOKEN)
    assert identity == VerifiedWorkspaceIdentity(account_id=ACCOUNT, email="owner@example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "invalid"),
        (403, "invalid"),
        (429, "unavailable"),
        (500, "unavailable"),
        (404, "unavailable"),
        (302, "unavailable"),
    ],
)
async def test_supabase_failures_are_safe_and_redirects_are_not_followed(
    status: int, reason: str
) -> None:
    calls: list[str] = []

    def respond(outbound: httpx.Request) -> httpx.Response:
        calls.append(str(outbound.url))
        return httpx.Response(
            status, text=f"{KEY} {TOKEN}", headers={"location": "https://elsewhere.test"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        verifier = SupabaseIdentityVerifier(client, url=AUTH_URL, publishable_key=KEY)
        with pytest.raises(IdentityVerificationError) as failure:
            await verifier.verify(TOKEN)
    assert failure.value.reason == reason
    assert KEY not in str(failure.value) and TOKEN not in str(failure.value)
    assert calls == [f"{AUTH_URL}/auth/v1/user"]


@pytest.mark.asyncio
async def test_supabase_network_failure_is_retryable_and_does_not_leak_exception() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"private {TOKEN} {KEY}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(IdentityVerificationError, match="unavailable") as failure:
            await SupabaseIdentityVerifier(client, url=AUTH_URL, publishable_key=KEY).verify(TOKEN)
    assert TOKEN not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"email_confirmed_at": None},
        {"role": "service_role"},
        {"aud": "service_role"},
        {"is_anonymous": True},
        {"id": "invalid"},
        {"email": "invalid"},
    ],
)
async def test_supabase_rejects_unconfirmed_anonymous_or_invalid_identities(
    changes: dict[str, object],
) -> None:
    payload = user_payload() | changes
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(IdentityVerificationError, match="invalid"):
            await SupabaseIdentityVerifier(client, url=AUTH_URL, publishable_key=KEY).verify(TOKEN)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,reason",
    [(b"not-json", "invalid"), (b"x" * 65_537, "unavailable")],
    ids=["malformed", "oversized"],
)
async def test_supabase_rejects_invalid_or_oversized_responses(content: bytes, reason: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=content))
    ) as client:
        with pytest.raises(IdentityVerificationError) as failure:
            await SupabaseIdentityVerifier(client, url=AUTH_URL, publishable_key=KEY).verify(TOKEN)
    assert failure.value.reason == reason


@pytest.mark.asyncio
async def test_workspace_status_is_public_safe_and_never_cached() -> None:
    application = create_app(auth_settings())
    response = await request(application, "/v1/workspace/status", authorization=None)
    assert response.json() == {
        "mode": "workspace",
        "authentication_configured": True,
        "creator_access_configured": True,
        "legacy_resources_available": False,
    }
    assert response.headers["cache-control"] == "no-store"
    assert KEY not in response.text and "owner@example.com" not in response.text
    local = create_app(
        auth_settings(
            workspace_auth_enabled=False,
            supabase_url=None,
            supabase_publishable_key=None,
            workspace_creator_emails=[],
        )
    )
    assert (await request(local, "/v1/workspace/status")).json() == {
        "mode": "local",
        "authentication_configured": False,
        "creator_access_configured": False,
        "legacy_resources_available": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"workspace_auth_enabled": False}, "WORKSPACE_AUTH_DISABLED"),
        ({"supabase_url": None}, "WORKSPACE_AUTH_NOT_CONFIGURED"),
        ({"supabase_publishable_key": None}, "WORKSPACE_AUTH_NOT_CONFIGURED"),
    ],
)
async def test_unconfigured_workspace_cannot_expose_account_data(
    overrides: dict[str, object], code: str
) -> None:
    response = await request(create_app(auth_settings(**overrides)), "/v1/workspace/session")
    assert response.status_code == 503 and response.json()["error"]["code"] == code
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [None, "Basic abc", "Bearer", "Bearer token with spaces", "Bearer " + "x" * 8193],
    ids=["missing", "basic", "empty", "whitespace", "oversized"],
)
async def test_missing_or_malformed_bearer_never_calls_verifier(authorization: str | None) -> None:
    application = create_app(auth_settings())
    verifier = Mock(verify=AsyncMock())
    with_verifier(application, verifier)
    response = await request(application, "/v1/workspace/session", authorization=authorization)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    verifier.verify.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,status", [("invalid", 401), ("unavailable", 503)])
async def test_verification_failure_is_normalized_before_database_access(
    reason: str, status: int
) -> None:
    application = create_app(auth_settings())
    failure = IdentityVerificationError("invalid" if reason == "invalid" else "unavailable")
    with_verifier(application, Mock(verify=AsyncMock(side_effect=failure)))
    response = await request(application, "/v1/workspace/session")
    assert response.status_code == status
    assert response.json()["error"]["retryable"] == (status == 503)
    assert TOKEN not in response.text and KEY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [[], ["somebody-else@example.com"]])
async def test_verified_but_uninvited_user_cannot_reach_database(allowed: list[str]) -> None:
    application = create_app(auth_settings(workspace_creator_emails=allowed))
    identity = VerifiedWorkspaceIdentity(account_id=ACCOUNT, email="owner@example.com")
    with_verifier(application, Mock(verify=AsyncMock(return_value=identity)))
    response = await request(application, "/v1/workspace/session")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "WORKSPACE_INVITATION_REQUIRED"


@pytest.mark.asyncio
async def test_real_dependency_constructs_and_closes_request_scoped_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from galaxy_frog.api import workspace_auth

    closed: list[bool] = []

    class Client(httpx.AsyncClient):
        async def __aexit__(self, *args: object) -> None:
            await self.aclose()
            closed.append(self.is_closed)

    def make_client(*, timeout: float) -> httpx.AsyncClient:
        assert timeout == 5
        return Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=user_payload()))
        )

    # Patch the API module's httpx reference, leaving the test transport untouched.
    monkeypatch.setattr(workspace_auth, "httpx", Mock(AsyncClient=make_client))
    application = create_app(auth_settings())
    response = await request(application, "/v1/workspace/session")
    assert response.status_code == 503  # Identity passed; no database was configured.
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert closed == [True]
