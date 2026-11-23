"""The live-check helper pins proof origins, never retries email, and redacts failures."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

SCRIPT = run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "workspace_auth_smoke.py"))
payload: Callable[[str, str, str], dict[str, str]] = SCRIPT["verification_payload"]
send: Callable[[httpx.AsyncClient, str, str, str], Awaitable[None]] = SCRIPT["send_email"]
exchange: Callable[[httpx.AsyncClient, str, str, str, str], Awaitable[str]] = SCRIPT[
    "exchange_proof"
]
Failure: type[Exception] = SCRIPT["SmokeFailure"]
returned_token: Callable[[str], str | None] = SCRIPT["returned_session_token"]
ORIGIN = "https://project.supabase.co"
EMAIL = "owner@example.com"
SECRET = "synthetic-private-token-never-print"


def test_accepts_code_and_pinned_confirmation_link_without_following_redirect() -> None:
    assert payload("123456", EMAIL, ORIGIN) == {"email": EMAIL, "token": "123456", "type": "email"}
    assert payload(
        f"{ORIGIN}/auth/v1/verify?token=hash&amp;type=signup&redirect_to=https://elsewhere.test",
        EMAIL,
        ORIGIN,
    ) == {"token_hash": "hash", "type": "signup"}
    assert (
        payload(f"{ORIGIN}/auth/v1/verify?token_hash=hash&type=email", EMAIL, ORIGIN)["type"]
        == "email"
    )


@pytest.mark.parametrize(
    "proof",
    [
        "https://different.supabase.co/auth/v1/verify?token=hash&type=email",
        "http://project.supabase.co/auth/v1/verify?token=hash&type=email",
        "https://user:password@project.supabase.co/auth/v1/verify?token=hash&type=email",
        f"{ORIGIN}/auth/v1/verify?token=hash&type=recovery",
        f"{ORIGIN}/auth/v1/verify?token=a&token=b&type=email",
        f"{ORIGIN}/auth/v1/verify?token=hash",
        f"{ORIGIN}/auth/v1/verify?token=hash&type=email#fragment",
    ],
)
def test_rejects_other_projects_and_non_login_flows(proof: str) -> None:
    with pytest.raises(Failure):
        payload(proof, EMAIL, ORIGIN)


@pytest.mark.asyncio
async def test_email_is_dispatched_once_and_error_body_is_not_exposed() -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, text=SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(Failure) as failure:
            await send(client, ORIGIN, "sb_publishable_synthetic", EMAIL)
    assert len(calls) == 1 and calls[0].url.path == "/auth/v1/otp"
    assert SECRET not in str(failure.value)


@pytest.mark.asyncio
async def test_already_opened_link_uses_returned_token_without_replaying_confirmation() -> None:
    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("A completed browser session must not replay the email link.")

    address = f"http://localhost:3000/#access_token={SECRET}&token_type=bearer"
    assert returned_token(address) == SECRET
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
        assert await exchange(client, ORIGIN, "sb_publishable_synthetic", EMAIL, address) == SECRET


@pytest.mark.parametrize(
    "address",
    [
        f"https://elsewhere.test/#access_token={SECRET}",
        f"http://localhost:9999/#access_token={SECRET}",
        f"http://user:password@localhost:3000/#access_token={SECRET}",
        "http://localhost:3000/#error_code=otp_expired",
    ],
)
def test_returned_session_input_stays_local_and_reports_used_links_safely(address: str) -> None:
    with pytest.raises(Failure) as failure:
        returned_token(address)
    assert SECRET not in str(failure.value)


@pytest.mark.asyncio
async def test_expired_confirmation_has_actionable_message_without_provider_body() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                403, json={"code": 403, "error_code": "otp_expired", "message": SECRET}
            )
        )
    ) as client:
        with pytest.raises(Failure, match="expired or already used") as failure:
            await exchange(client, ORIGIN, "sb_publishable_synthetic", EMAIL, "123456")
    assert SECRET not in str(failure.value)


def test_native_input_keeps_proof_out_of_command_arguments_and_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reader = SCRIPT["read_proof"]
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=SECRET))
    monkeypatch.setitem(reader.__globals__, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(reader.__globals__, "subprocess", SimpleNamespace(run=run))
    assert reader(False) == SECRET
    command = run.call_args.args[0]
    assert SECRET not in " ".join(command)
    assert "UseSystemPasswordChar = $true" in command[-1]
    assert SECRET not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_verification_returns_token_only_in_memory_and_sanitizes_failures() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"access_token": SECRET})
        )
    ) as client:
        assert await exchange(client, ORIGIN, "sb_publishable_synthetic", EMAIL, "123456") == SECRET
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(403, text=SECRET))
    ) as client:
        with pytest.raises(Failure) as failure:
            await exchange(client, ORIGIN, "sb_publishable_synthetic", EMAIL, "123456")
    assert SECRET not in str(failure.value)


@pytest.mark.parametrize("approved", [False, True])
def test_second_email_needs_separate_approval_and_keeps_prior_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    approved: bool,
) -> None:
    run = SCRIPT["run"]
    receipt = tmp_path / "verification.json"
    previous = '{"status": "email_request_accepted"}\n'
    receipt.write_text(previous, encoding="utf-8")
    settings = SimpleNamespace(
        supabase_url=ORIGIN,
        supabase_publishable_key=SimpleNamespace(get_secret_value=lambda: SECRET),
    )
    dispatch = AsyncMock()
    monkeypatch.setitem(run.__globals__, "RECEIPT", receipt)
    monkeypatch.setitem(run.__globals__, "configuration", Mock(return_value=(settings, EMAIL)))
    monkeypatch.setitem(run.__globals__, "check_local_database", AsyncMock())
    monkeypatch.setitem(run.__globals__, "send_email", dispatch)
    client = MagicMock()
    client.__aenter__.return_value = client
    monkeypatch.setitem(
        run.__globals__, "httpx", SimpleNamespace(AsyncClient=Mock(return_value=client))
    )
    if approved:
        asyncio.run(run(True, EMAIL, fresh_email_approved=True))
        dispatch.assert_awaited_once_with(client, ORIGIN, SECRET, EMAIL)
        archives = list(tmp_path.glob("verification.*.json"))
        assert len(archives) == 1
        assert archives[0].read_text(encoding="utf-8") == previous
        assert '"status": "email_request_accepted"' in receipt.read_text(encoding="utf-8")
    else:
        with pytest.raises(Failure, match="already been requested"):
            asyncio.run(run(True, EMAIL))
        dispatch.assert_not_awaited()
        assert receipt.read_text(encoding="utf-8") == previous
        assert list(tmp_path.glob("verification.*.json")) == []
    assert SECRET not in capsys.readouterr().out
