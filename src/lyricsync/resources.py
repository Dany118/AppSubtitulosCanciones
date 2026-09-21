"""Locating bundled files, whether running from source or from a frozen build.

PyInstaller unpacks data files into a temporary directory and points
``sys._MEIPASS`` at it, so a path built from ``__file__`` is not reliable
inside a one-file executable.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than the source tree."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_dir(*parts: str) -> Path:
    """Resolve a directory shipped with the package.

    ``parts`` is the path relative to the package root, e.g.
    ``resource_dir("gui", "static")``.
    """
    if is_frozen():
        return Path(sys._MEIPASS) / "lyricsync" / Path(*parts)
    return Path(__file__).parent / Path(*parts)
