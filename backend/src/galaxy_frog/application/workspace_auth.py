"""Identity verification port independent of HTTP frameworks and provider SDKs."""

from typing import Literal, Protocol

from galaxy_frog.domain.workspace import VerifiedWorkspaceIdentity


class IdentityVerificationError(Exception):
    """Safe classification without tokens, upstream bodies, or provider exception text."""

    def __init__(self, reason: Literal["invalid", "unavailable"]) -> None:
        super().__init__(reason)
        self.reason = reason


class WorkspaceIdentityVerifier(Protocol):
    async def verify(self, access_token: str) -> VerifiedWorkspaceIdentity:
        """Verify a bearer token against the configured identity authority."""
        ...
