"""Local media acquisition adapters."""

from galaxy_frog.adapters.media.ffmpeg import FfmpegAudioNormalizer
from galaxy_frog.adapters.media.local import LocalAudioAcquirer
from galaxy_frog.adapters.media.process import AsyncSubprocessRunner
from galaxy_frog.adapters.media.workspace import IsolatedMediaWorkspace
from galaxy_frog.adapters.media.yt_dlp import YtDlpAudioDownloader

__all__ = [
    "AsyncSubprocessRunner",
    "FfmpegAudioNormalizer",
    "IsolatedMediaWorkspace",
    "LocalAudioAcquirer",
    "YtDlpAudioDownloader",
]
