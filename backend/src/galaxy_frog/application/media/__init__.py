"""Provider-neutral application boundaries for local media work."""

from galaxy_frog.application.media.models import DownloadedAudio, NormalizedAudio
from galaxy_frog.application.media.ports import AudioDownloader, AudioNormalizer

__all__ = ["AudioDownloader", "AudioNormalizer", "DownloadedAudio", "NormalizedAudio"]
