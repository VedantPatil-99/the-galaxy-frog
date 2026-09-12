"""Provider interfaces for acquiring and normalizing bounded audio."""

from pathlib import Path
from typing import Protocol

from galaxy_frog.application.media.models import DownloadedAudio, NormalizedAudio
from galaxy_frog.domain.media import AudioAcquisitionLimits, AudioAcquisitionRequest


class AudioDownloader(Protocol):
    """Retrieve source-native audio into an isolated attempt workspace."""

    async def download(
        self,
        request: AudioAcquisitionRequest,
        workspace: Path,
        limits: AudioAcquisitionLimits,
    ) -> DownloadedAudio: ...


class AudioNormalizer(Protocol):
    """Inspect and normalize downloaded audio for later transcription."""

    async def normalize(
        self,
        source: DownloadedAudio,
        output_path: Path,
        limits: AudioAcquisitionLimits,
    ) -> NormalizedAudio: ...
