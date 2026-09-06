"""Provider-independent audio acquisition contracts and provenance values."""

from galaxy_frog.domain.media.models import (
    AcquiredAudio,
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
    AudioAcquisitionRequest,
    AudioFallbackReason,
)
from galaxy_frog.domain.media.ports import AudioAcquirer

__all__ = [
    "AcquiredAudio",
    "AudioAcquirer",
    "AudioAcquisitionError",
    "AudioAcquisitionErrorCode",
    "AudioAcquisitionLimits",
    "AudioAcquisitionRequest",
    "AudioFallbackReason",
]
