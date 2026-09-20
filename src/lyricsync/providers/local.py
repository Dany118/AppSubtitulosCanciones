"""Lyrics that are already on disk or already in the file.

These cover the common real case: you have the words in a text file and only
need the timings computed.
"""

from __future__ import annotations

from pathlib import Path

from ..lrc import parse_lrc, parse_plain
from ..models import Lyrics, Source, TrackMeta
from ..tags import is_synced_text, read_embedded_lyrics


def _read_text(path: Path) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
        return text if text.strip() else None
    return None


class LocalFileProvider:
    """Picks up a sibling ``<track>.lrc`` or ``<track>.txt``."""

    name = "local-file"

    def fetch(self, meta: TrackMeta) -> Lyrics | None:
        lrc_path = meta.path.with_suffix(".lrc")
        if lrc_path.exists():
            text = _read_text(lrc_path)
            if text and is_synced_text(text):
                lyrics = parse_lrc(text)
                lyrics.source = Source.LOCAL_LRC
                if lyrics.lines:
                    return lyrics

        for suffix in (".txt", ".lyrics"):
            txt_path = meta.path.with_suffix(suffix)
            if txt_path.exists():
                text = _read_text(txt_path)
                if text:
                    lyrics = parse_plain(text)
                    lyrics.source = Source.LOCAL_TXT
                    if lyrics.lines:
                        return lyrics
        return None


class EmbeddedProvider:
    """Reuses lyrics already embedded in the file.

    Useful when a track has correct words but no timings: the words are kept
    and only the alignment stage runs.
    """

    name = "embedded"

    def fetch(self, meta: TrackMeta) -> Lyrics | None:
        text = read_embedded_lyrics(meta.path)
        if not text or not text.strip():
            return None
        lyrics = parse_lrc(text) if is_synced_text(text) else parse_plain(text)
        lyrics.source = Source.EMBEDDED
        return lyrics if lyrics.lines else None
