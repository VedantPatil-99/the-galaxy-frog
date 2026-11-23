"""Supabase Auth user verification using a bounded, injected HTTP client."""

from base64 import urlsafe_b64decode
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from pydantic import AwareDatetime, BaseModel, EmailStr, StrictInt

from galaxy_frog.application.workspace_auth import IdentityVerificationError
from galaxy_frog.domain.workspace import VerifiedWorkspaceIdentity


class _SupabaseUser(BaseModel):
    id: UUID
    email: EmailStr
    aud: Literal["authenticated"]
    role: Literal["authenticated"]
    email_confirmed_at: AwareDatetime
    is_anonymous: Literal[False] = False


class _VerifiedTokenExpiry(BaseModel):
    exp: StrictInt


def _verified_token_expiry(access_token: str) -> datetime | None:
    try:
        _, payload, _ = access_token.split(".")
        claims = _VerifiedTokenExpiry.model_validate_json(
            urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        return datetime.fromtimestamp(claims.exp, UTC)
    except ValueError, OverflowError, OSError:
        return None


class SupabaseIdentityVerifier:
    def __init__(self, client: httpx.AsyncClient, *, url: str, publishable_key: str) -> None:
        self.client = client
        self.endpoint = f"{url}/auth/v1/user"
        self.publishable_key = publishable_key

    async def verify(self, access_token: str) -> VerifiedWorkspaceIdentity:
        body = bytearray()
        try:
            async with self.client.stream(
                "GET",
                self.endpoint,
                headers={"Authorization": f"Bearer {access_token}", "apikey": self.publishable_key},
                follow_redirects=False,
            ) as response:
                if response.status_code in (401, 403):
                    raise IdentityVerificationError("invalid")
                if response.status_code != 200:
                    raise IdentityVerificationError("unavailable")
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 65_536:
                        raise IdentityVerificationError("unavailable")
                    body.extend(chunk)
        except httpx.HTTPError:
            raise IdentityVerificationError("unavailable") from None
        try:
            user = _SupabaseUser.model_validate_json(body)
        except ValueError:
            raise IdentityVerificationError("invalid") from None
        return VerifiedWorkspaceIdentity(
            account_id=user.id,
            email=user.email.casefold(),
            token_expires_at=_verified_token_expiry(access_token),
        )
