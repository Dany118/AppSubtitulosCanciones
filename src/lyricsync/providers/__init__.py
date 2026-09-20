"""Sources of lyrics, tried in order by the pipeline."""

from .base import LyricsProvider
from .local import EmbeddedProvider, LocalFileProvider
from .lrclib import LrclibProvider

__all__ = ["LyricsProvider", "LrclibProvider", "LocalFileProvider", "EmbeddedProvider"]
