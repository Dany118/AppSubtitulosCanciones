"""Local web reviewer for checking and correcting lyric timings by ear."""

from .server import ReviewServer, serve

__all__ = ["ReviewServer", "serve"]
