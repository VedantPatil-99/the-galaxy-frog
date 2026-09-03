"""Video source adapter implementations."""

from galaxy_frog.adapters.video_sources.local_file import LocalFileSource
from galaxy_frog.adapters.video_sources.youtube import YouTubeSource

__all__ = ["LocalFileSource", "YouTubeSource"]
