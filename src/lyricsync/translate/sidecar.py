"""Translations supplied by hand, in a file next to the track.

Lets you correct a machine translation, or use any external service, without
the tool needing to know about it. The file is read line for line, so it must
line up with the lyrics.
"""

from __future__ import annotations

from pathlib import Path


class SidecarTranslator:
    """Reads ``<track>.<lang>.txt`` — for example ``song.es.txt``."""

    name = "sidecar"

    def __init__(self, track_path: Path, target_language: str = "es") -> None:
        self.target_language = target_language
        self.path = track_path.with_suffix(f".{target_language}.txt")

    def available(self) -> bool:
        return self.path.exists()

    def translate(self, lines: list[str]) -> list[str]:
        """Return one translation per input line, padding if the file is short."""
        if not self.available():
            return [""] * len(lines)

        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                content = self.path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, OSError):
                continue
        else:
            return [""] * len(lines)

        supplied = [line.strip() for line in content.splitlines() if line.strip()]
        # Padded rather than rejected: a partially written file still helps,
        # and the pipeline reports how much of the track it covered.
        return [supplied[i] if i < len(supplied) else "" for i in range(len(lines))]
