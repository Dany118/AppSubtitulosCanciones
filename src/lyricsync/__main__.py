"""Entry point for ``python -m lyricsync`` and for the packaged executable."""

from __future__ import annotations

import sys

# Absolute, not relative: PyInstaller runs this file as a top-level script
# with no parent package, while `python -m lyricsync` imports it as one.
from lyricsync.cli import app


# Passing these must still reach the CLI itself rather than the gui command.
_PASSTHROUGH = {"--help", "-h", "--version"}


def main() -> None:
    """Run the CLI, defaulting to the graphical app.

    Double-clicking the executable passes no arguments, and printing a help
    screen into a window that closes is useless -- so open the app instead.
    The same applies to options given without a command (``--port 9000``),
    which would otherwise be a usage error.
    """
    if len(sys.argv) == 1:
        sys.argv.append("gui")
    elif sys.argv[1].startswith("-") and sys.argv[1] not in _PASSTHROUGH:
        sys.argv.insert(1, "gui")
    app()


if __name__ == "__main__":
    main()
