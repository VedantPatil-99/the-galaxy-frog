"""Application-facing audio acquisition protocol."""

from typing import Protocol, runtime_checkable

from galaxy_frog.domain.media.models import AcquiredAudio, AudioAcquisitionRequest


@runtime_checkable
class AudioAcquirer(Protocol):
    """Acquire and later clean one bounded, provenance-bearing audio artifact."""

    async def acquire(self, request: AudioAcquisitionRequest) -> AcquiredAudio: ...

    async def cleanup(self, artifact: AcquiredAudio) -> bool:
        """Delete configured temporary media and report whether it was removed."""
        ...
