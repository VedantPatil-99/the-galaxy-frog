"""Explicit live Auth check; proof input and session tokens never reach logs."""

import argparse
import asyncio
import getpass
import html
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx

from galaxy_frog.config import Settings

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "tmp" / "workspace-auth" / "verification.json"


class SmokeFailure(Exception):
    """Only static, human-safe messages are carried to the terminal."""


def read_proof(terminal_input: bool) -> str:
    if os.name != "nt" or terminal_input:
        if not sys.stdin.isatty():
            raise SmokeFailure("Run verification in a local terminal for hidden proof input.")
        return getpass.getpass(
            "Paste the email code, confirmation link, or returned browser address (hidden): "
        )
    # A native password field supports normal Ctrl+V without echoing the proof to shell history.
    command = r"""Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Galaxy Frog - verify login'
$form.ClientSize = New-Object System.Drawing.Size(640, 205)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$label = New-Object System.Windows.Forms.Label
$label.Text = 'Paste the browser address after opening the email link, or the original link/code.'
$label.SetBounds(20, 20, 600, 42)
$box = New-Object System.Windows.Forms.TextBox
$box.UseSystemPasswordChar = $true
$box.MaxLength = 16384
$box.SetBounds(20, 72, 600, 26)
$status = New-Object System.Windows.Forms.Label
$status.Text = 'Click the field and press Ctrl+V. Pasted text stays hidden.'
$status.SetBounds(20, 111, 600, 25)
$box.Add_TextChanged({ if ($box.TextLength -gt 0) { $status.Text = 'Input received. Click Verify.' } })
$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'Verify'
$ok.SetBounds(420, 155, 95, 30)
$ok.Add_Click({ if ($box.TextLength -gt 0) { $form.DialogResult = 'OK'; $form.Close() } })
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'
$cancel.DialogResult = 'Cancel'
$cancel.SetBounds(525, 155, 95, 30)
$form.AcceptButton = $ok
$form.CancelButton = $cancel
$form.Controls.AddRange(@($label, $box, $status, $ok, $cancel))
$form.Add_Shown({ [void]$box.Focus() })
$result = $form.ShowDialog()
if ($result -eq 'OK') { [Console]::Out.Write($box.Text); exit 0 }
exit 2
"""
    print(
        "Opening a private paste window. Input received there will not appear in the terminal.",
        flush=True,
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SmokeFailure("Login input was cancelled or the paste window could not open.")
    return result.stdout.strip()


def returned_session_token(proof: str) -> str | None:
    parsed = urlsplit(proof.strip())
    if not parsed.fragment:
        return None
    if (
        len(proof) > 16384
        or parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port != 3000
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SmokeFailure(
            "Use the local browser address from this sign-in, not another site's session."
        )
    fields = parse_qs(parsed.fragment)
    if fields.get("error_code") == ["otp_expired"]:
        raise SmokeFailure(
            "That one-time link is expired or already used. A new email needs your approval."
        )
    tokens = fields.get("access_token", [])
    if len(tokens) != 1 or not tokens[0] or len(tokens[0]) > 8192:
        raise SmokeFailure(
            "This address does not contain a signed-in session. Use the original email link."
        )
    # Untrusted until FastAPI verifies it with the configured Supabase authority below.
    return tokens[0]


def verification_payload(proof: str, email: str, origin: str) -> dict[str, str]:
    proof = html.unescape(proof.strip())
    if re.fullmatch(r"\d{6,10}", proof):
        return {"email": email, "token": proof, "type": "email"}
    parsed = urlsplit(proof)
    authority = urlsplit(origin)
    if (
        len(proof) > 8192
        or parsed.scheme != "https"
        or parsed.netloc != authority.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/auth/v1/verify"
        or parsed.fragment
    ):
        raise SmokeFailure(
            "Use the code or the original confirmation link from this project's email."
        )
    query = parse_qs(parsed.query)
    tokens = query.get("token_hash", query.get("token", []))
    kinds = query.get("type", [])
    if (
        len(tokens) != 1
        or not tokens[0]
        or len(kinds) != 1
        or kinds[0] not in {"email", "signup", "magiclink"}
    ):
        raise SmokeFailure("The confirmation link is not a supported email sign-in link.")
    return {"token_hash": tokens[0], "type": kinds[0]}


def configuration(email: str | None) -> tuple[Settings, str]:
    settings = Settings()
    if settings.supabase_url is None or settings.supabase_publishable_key is None:
        raise SmokeFailure(
            "Configure the Supabase URL and publishable key in the local .env first."
        )
    choices = tuple(str(item).casefold() for item in settings.workspace_creator_emails)
    selected = email.strip().casefold() if email else choices[0] if len(choices) == 1 else ""
    if not selected or selected not in choices:
        raise SmokeFailure("Select one email from the configured workspace creator allowlist.")
    return settings, selected


async def send_email(client: httpx.AsyncClient, origin: str, key: str, email: str) -> None:
    response = await client.post(
        f"{origin}/auth/v1/otp",
        headers={"apikey": key},
        json={"email": email, "create_user": True},
    )
    if response.status_code not in (200, 202):
        raise SmokeFailure(
            f"Sign-in email was not accepted (HTTP {response.status_code}). "
            "Check the project's allowed email recipients and email rate limit; do not resend automatically."
        )


async def exchange_proof(
    client: httpx.AsyncClient, origin: str, key: str, email: str, proof: str
) -> str:
    existing_token = returned_session_token(proof)
    if existing_token is not None:
        return existing_token
    payload = verification_payload(proof, email, origin)
    response = await client.post(f"{origin}/auth/v1/verify", headers={"apikey": key}, json=payload)
    if response.status_code != 200:
        try:
            error: object = response.json()
        except ValueError:
            error = None
        if isinstance(error, dict) and any(
            cast(dict[str, object], error).get(field) == "otp_expired"
            for field in ("code", "error_code")
        ):
            raise SmokeFailure(
                "That one-time link is expired or already used. Use its completed browser address, or request a fresh email."
            )
        raise SmokeFailure(
            f"Email confirmation failed (HTTP {response.status_code}); no response body was printed."
        )
    try:
        body = response.json()
    except ValueError:
        raise SmokeFailure("The Auth service returned an invalid verification response.") from None
    token = cast(dict[str, object], body).get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise SmokeFailure("The Auth service did not return a usable access token.")
    return token


async def verify_workspace(settings: Settings, email: str, token: str) -> dict[str, bool]:
    from galaxy_frog.api.app import create_app

    if settings.database_url is None or urlsplit(
        settings.database_url.get_secret_value()
    ).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SmokeFailure(
            "Use the existing local development database for this check, not a hosted database."
        )
    # Private mode applies only to this in-process check; normal local development stays unchanged.
    application = create_app(settings.model_copy(update={"workspace_auth_enabled": True}))
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://workspace-test",
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        session = await client.get("/v1/workspace/session", headers=headers)
        if session.status_code != 200:
            raise SmokeFailure(f"Workspace session check failed (HTTP {session.status_code}).")
        body = session.json()
        if body.get("email", "").casefold() != email or body.get("role") != "creator":
            raise SmokeFailure("The verified session did not match the selected creator.")
        notebooks = await client.get("/v1/workspace/notebooks?limit=1", headers=headers)
        unauthenticated = await client.get("/v1/workspace/session")
        legacy = await client.get("/v1/providers/capabilities", headers=headers)
        checks = {
            "verified_creator_session": True,
            "private_notebook_access": notebooks.status_code == 200,
            "anonymous_access_denied": unauthenticated.status_code == 401,
            "legacy_access_denied": legacy.status_code == 403,
            "private_response_not_cached": session.headers.get("cache-control") == "no-store",
        }
        if not all(checks.values()):
            raise SmokeFailure("A workspace authorization or privacy check failed.")
        return checks


async def check_local_database(settings: Settings) -> None:
    from sqlalchemy import text

    from galaxy_frog.db.engine import create_database_engine

    if settings.database_url is None or urlsplit(
        settings.database_url.get_secret_value()
    ).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SmokeFailure("Configure the existing local development database before this check.")
    engine = create_database_engine(settings)
    try:
        async with engine.connect() as connection:
            ready = await connection.scalar(
                text(
                    "SELECT to_regclass('workspace_accounts') IS NOT NULL "
                    "AND to_regclass('workspace_notebooks') IS NOT NULL"
                )
            )
            if not ready:
                raise SmokeFailure(
                    "Apply the existing local workspace migration before requesting an email."
                )
    finally:
        await engine.dispose()


def save_receipt(status: str, **checks: bool) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(
        json.dumps(
            {"status": status, "checked_at": datetime.now(UTC).isoformat(), **checks},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


async def run(
    send: bool,
    email: str | None,
    *,
    terminal_input: bool = False,
    fresh_email_approved: bool = False,
) -> None:
    settings, selected = configuration(email)
    await check_local_database(settings)
    assert settings.supabase_url is not None and settings.supabase_publishable_key is not None
    origin, key = (
        settings.supabase_url,
        settings.supabase_publishable_key.get_secret_value(),
    )
    if send and RECEIPT.exists() and not fresh_email_approved:
        raise SmokeFailure(
            "A test email has already been requested. Verify it; do not send a second automatically."
        )
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        if send:
            if RECEIPT.exists():
                # Preserve the previous attempt; this opt-in requires separate user consent.
                previous = RECEIPT.with_name(
                    f"verification.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
                )
                previous.write_bytes(RECEIPT.read_bytes())
            # Record before dispatch so an uncertain network outcome cannot trigger an automatic resend.
            save_receipt("email_dispatch_started")
            await send_email(client, origin, key, selected)
            save_receipt("email_request_accepted")
            print(
                "Sign-in email request accepted. Check your inbox and copy the confirmation link address without opening it."
            )
            return
        proof = read_proof(terminal_input)
        token = await exchange_proof(client, origin, key, selected, proof)
        checks = await verify_workspace(settings, selected, token)
        response = await client.post(
            f"{origin}/auth/v1/logout?scope=local",
            headers={"apikey": key, "Authorization": f"Bearer {token}"},
        )
        checks["test_session_logged_out"] = response.status_code in (200, 204)
        save_receipt(
            "verified" if all(checks.values()) else "verified_logout_unconfirmed",
            **checks,
        )
        print(
            json.dumps(
                {
                    "status": "verified" if all(checks.values()) else "verified_logout_unconfirmed",
                    **checks,
                }
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--send-code", action="store_true", help="Explicitly request one sign-in email."
    )
    modes.add_argument(
        "--verify",
        action="store_true",
        help="Enter the proof locally without echoing it.",
    )
    parser.add_argument("--email", help="Email already present in the creator allowlist.")
    parser.add_argument(
        "--terminal-input", action="store_true", help="Use the original hidden console prompt."
    )
    parser.add_argument(
        "--fresh-email-approved",
        action="store_true",
        help="Request one more email only after separate explicit approval; preserve the old receipt.",
    )
    args = parser.parse_args()
    if args.fresh_email_approved and not args.send_code:
        parser.error("--fresh-email-approved requires --send-code")
    try:
        asyncio.run(
            run(
                args.send_code,
                args.email,
                terminal_input=args.terminal_input,
                fresh_email_approved=args.fresh_email_approved,
            )
        )
    except SmokeFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print(
            "Auth verification could not finish. No secret details were printed; do not resend automatically.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
