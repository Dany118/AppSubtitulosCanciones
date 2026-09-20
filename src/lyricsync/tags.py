"""Reading, stripping and writing lyrics in MP3 tags.

Everything here writes through a temporary copy and finishes with
``os.replace``, so an interrupted batch can never leave a half-written file.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, SYLT, TXXX, USLT
from mutagen.mp3 import MP3

from .models import TrackMeta

# TXXX descriptions used by other taggers to stash lyrics.
_LYRIC_TXXX_DESCS = {"lyrics", "unsyncedlyrics", "syncedlyrics", "unsynced lyrics", "synced lyrics", "lyricist_lyrics"}
# Our own marker, so a batch re-run can tell what it already processed.
MARKER_DESC = "LYRICSYNC"

_TIMESTAMPED_RE = re.compile(r"^\s*\[\d{1,4}:\d{1,2}(?:[.:]\d{1,3})?\]", re.M)


def read_meta(path: Path) -> TrackMeta:
    """Read artist/title/album/duration without needing ffmpeg."""
    audio = MP3(path)
    duration = float(audio.info.length) if audio.info else None
    tags = audio.tags

    def first(frame_id: str) -> str | None:
        if not tags or frame_id not in tags:
            return None
        value = tags[frame_id].text
        text = str(value[0]).strip() if value else ""
        return text or None

    return TrackMeta(
        path=path,
        title=first("TIT2"),
        artist=first("TPE1") or first("TPE2"),
        album=first("TALB"),
        duration=duration,
    )


def read_embedded_lyrics(path: Path) -> str | None:
    """Return the first non-empty USLT/TXXX lyrics payload, if any."""
    try:
        tags = ID3(path)
    except (ID3NoHeaderError, Exception):  # noqa: BLE001 - unreadable tag is "none"
        return None

    # Returned verbatim: callers compare it against what they wrote, and
    # trailing whitespace is part of the stored payload.
    for key in tags:
        if key.startswith("USLT"):
            text = str(tags[key].text)
            if text.strip():
                return text
    for key in tags:
        if key.startswith("TXXX:") and key.split(":", 1)[1].lower() in _LYRIC_TXXX_DESCS:
            text = "\n".join(str(v) for v in tags[key].text)
            if text.strip():
                return text
    return None


def is_synced_text(text: str | None) -> bool:
    """True when the text carries LRC timestamps rather than being plain."""
    return bool(text) and bool(_TIMESTAMPED_RE.search(text))


def describe_lyric_frames(path: Path) -> list[str]:
    """List the lyrics-bearing frame keys currently on the file."""
    try:
        tags = ID3(path)
    except Exception:  # noqa: BLE001
        return []
    return sorted(_lyric_frame_keys(tags))


def _lyric_frame_keys(tags: ID3) -> list[str]:
    keys = []
    for key in list(tags.keys()):
        if key.startswith(("USLT", "SYLT")):
            keys.append(key)
        elif key.startswith("TXXX:"):
            desc = key.split(":", 1)[1].lower()
            if desc in _LYRIC_TXXX_DESCS or desc == MARKER_DESC.lower():
                keys.append(key)
    return keys


def _strip_frames(tags: ID3) -> list[str]:
    removed = _lyric_frame_keys(tags)
    for key in removed:
        del tags[key]
    return removed


def strip_lyrics3(path: Path) -> bool:
    """Remove a legacy Lyrics3 v1/v2 block from the end of the file.

    These predate ID3v2 and sit between the audio and any ID3v1 tag. mutagen
    ignores them, but some Android players still read them and would show the
    stale lyrics instead of ours. Returns True if a block was removed.
    """
    size = path.stat().st_size
    if size < 32:
        return False

    with path.open("r+b") as handle:
        # An ID3v1 tag, if present, is the last 128 bytes and must be preserved.
        handle.seek(max(0, size - 128))
        trailer = handle.read(128)
        id3v1 = trailer[:3] == b"TAG" and len(trailer) == 128
        end = size - 128 if id3v1 else size

        if end < 20:
            return False
        handle.seek(end - 9)
        marker = handle.read(9)

        if marker == b"LYRICS200":
            handle.seek(end - 15)
            raw_size = handle.read(6)
            if not raw_size.isdigit():
                return False
            block = int(raw_size)
            start = end - 15 - block
            if start < 0:
                return False
            handle.seek(start)
            if handle.read(11) != b"LYRICSBEGIN":
                return False
        elif marker[-9:] == b"LYRICSEND":
            # v1 has no size field; it is capped at 5100 bytes by the spec.
            window = min(5100, end)
            handle.seek(end - window)
            data = handle.read(window)
            index = data.find(b"LYRICSBEGIN")
            if index < 0:
                return False
            start = end - window + index
        else:
            return False

        tail = trailer if id3v1 else b""
        handle.seek(start)
        handle.write(tail)
        handle.truncate(start + len(tail))
    return True


def write_lyrics(
    path: Path,
    lrc_text: str,
    *,
    sylt_pairs: list[tuple[str, int]] | None = None,
    plain_fallback: str | None = None,
    lang: str = "eng",
    id3_version: int = 3,
    backup: bool = False,
    marker: str | None = None,
) -> list[str]:
    """Strip old lyrics and embed ``lrc_text``, atomically.

    Returns the frame keys that were removed. ``lrc_text`` goes into USLT
    because that is the frame Android players actually read; SYLT is written
    alongside for the few that prefer it.
    """
    # v2.3 has no UTF-8 encoding; UTF-16 is the portable choice there.
    encoding = 3 if id3_version == 4 else 1

    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".lyricsync-", suffix=".mp3")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(path, tmp_path)
        strip_lyrics3(tmp_path)

        try:
            tags = ID3(tmp_path)
        except ID3NoHeaderError:
            tags = ID3()

        removed = _strip_frames(tags)

        tags.add(USLT(encoding=encoding, lang=lang, desc="", text=lrc_text))
        if sylt_pairs:
            tags.add(
                SYLT(
                    encoding=encoding,
                    lang=lang,
                    format=2,  # timestamps are absolute milliseconds
                    type=1,  # content type: lyrics
                    desc="",
                    text=sylt_pairs,
                )
            )
        if plain_fallback:
            tags.add(TXXX(encoding=encoding, desc="LYRICS", text=[plain_fallback]))
        if marker:
            tags.add(TXXX(encoding=encoding, desc=MARKER_DESC, text=[marker]))

        tags.save(tmp_path, v2_version=id3_version)

        if backup:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        os.replace(tmp_path, path)
        return removed
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def clear_lyrics(path: Path, *, backup: bool = False) -> list[str]:
    """Remove every lyrics frame without writing a replacement."""
    tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".lyricsync-", suffix=".mp3")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(path, tmp_path)
        strip_lyrics3(tmp_path)
        try:
            tags = ID3(tmp_path)
        except ID3NoHeaderError:
            return []
        removed = _strip_frames(tags)
        tags.save(tmp_path)
        if backup:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        os.replace(tmp_path, path)
        return removed
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def read_marker(path: Path) -> str | None:
    """Read the lyricsync marker written on a previous run, if present."""
    try:
        tags = ID3(path)
    except Exception:  # noqa: BLE001
        return None
    key = f"TXXX:{MARKER_DESC}"
    if key in tags:
        return str(tags[key].text[0])
    return None
