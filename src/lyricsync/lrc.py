"""Reading and writing the LRC format.

Standard LRC is line-level::

    [00:12.34]a line of text

"Enhanced" (A2) LRC adds per-word tags, which Poweramp and Salt Player render
as karaoke-style highlighting::

    [00:12.34]<00:12.34>a <00:12.61>line

BlackPlayer EX and most other Android players only understand the standard
form, so that stays the default output.
"""

from __future__ import annotations

import re

from .models import LyricLine, Lyrics, Source, Word
from .text import clean_lyric_line, split_words

_TIME_RE = re.compile(r"\[(\d{1,4}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_WORD_TIME_RE = re.compile(r"<(\d{1,4}):(\d{1,2}(?:[.:]\d{1,3})?)>")
_META_RE = re.compile(r"^\[([a-zA-Z]{2,10}):(.*)\]\s*$")

# Metadata keys we recognise; anything else in the header is passed through.
_KNOWN_META = {"ti", "ar", "al", "au", "by", "length", "offset", "re", "ve", "tool",
               # Written by us so a bilingual file can be read back apart
               # again: the target language, and the inline separator used.
               "tr", "trsep"}


def _parse_stamp(minutes: str, seconds: str) -> float:
    seconds = seconds.replace(":", ".")
    return int(minutes) * 60 + float(seconds)


def _format_stamp(value: float, decimals: int = 2) -> str:
    """Format seconds as ``mm:ss.xx``, truncating so a line never shows late."""
    value = max(0.0, value)
    scale = 10**decimals
    total = int(value * scale)  # truncate, do not round up
    minutes, rem = divmod(total, 60 * scale)
    whole, frac = divmod(rem, scale)
    return f"{minutes:02d}:{whole:02d}.{frac:0{decimals}d}"


def parse_lrc(content: str) -> Lyrics:
    """Parse LRC text into :class:`Lyrics`, including per-word tags if present.

    Lines carrying several timestamps (a repeated chorus) are expanded into one
    :class:`LyricLine` per timestamp, and the result is sorted by time.
    """
    lines: list[LyricLine] = []
    meta: dict[str, str] = {}
    offset = 0.0

    for raw in content.splitlines():
        raw = raw.rstrip()
        if not raw:
            continue

        stamps = list(_TIME_RE.finditer(raw))
        if not stamps:
            match = _META_RE.match(raw)
            if match and match.group(1).lower() in _KNOWN_META:
                key, value = match.group(1).lower(), match.group(2).strip()
                meta[key] = value
                if key == "offset":
                    try:
                        # LRC offset is in ms and positive means "shift earlier".
                        offset = -int(value) / 1000.0
                    except ValueError:
                        pass
            continue

        # Timestamps are only a prefix; text starts after the last leading one.
        prefix_end = 0
        starts: list[float] = []
        for stamp in stamps:
            if stamp.start() != prefix_end:
                break
            starts.append(_parse_stamp(stamp.group(1), stamp.group(2)))
            prefix_end = stamp.end()
        if not starts:
            continue

        body = raw[prefix_end:]
        words, text = _parse_word_tags(body)
        text = clean_lyric_line(text)
        for start in starts:
            shifted = [Word(w.text, w.start + offset, w.end + offset) for w in words]
            lines.append(
                LyricLine(
                    text=text,
                    start=max(0.0, start + offset),
                    end=shifted[-1].end if shifted else None,
                    words=shifted,
                )
            )

    lines.sort(key=lambda ln: (ln.start if ln.start is not None else 0.0))
    if "tr" in meta:
        _unmerge_translations(lines, meta.get("trsep"))
    _fill_line_ends(lines)
    return Lyrics(
        lines=lines,
        source=Source.LOCAL_LRC,
        title=meta.get("ti"),
        artist=meta.get("ar"),
        album=meta.get("al"),
    )


def _parse_word_tags(body: str) -> tuple[list[Word], str]:
    """Extract ``<mm:ss.xx>`` word tags, returning the words and the clean text."""
    tags = list(_WORD_TIME_RE.finditer(body))
    if not tags:
        return [], body

    words: list[Word] = []
    for index, tag in enumerate(tags):
        start = _parse_stamp(tag.group(1), tag.group(2))
        text_end = tags[index + 1].start() if index + 1 < len(tags) else len(body)
        chunk = body[tag.end() : text_end].strip()
        if not chunk:
            continue
        end = (
            _parse_stamp(tags[index + 1].group(1), tags[index + 1].group(2))
            if index + 1 < len(tags)
            else start
        )
        words.append(Word(chunk, start, max(end, start)))

    return words, _WORD_TIME_RE.sub("", body)


def _unmerge_translations(lines: list[LyricLine], separator: str | None) -> None:
    """Recover the original text and its translation from a file we wrote.

    Rendering is lossy on its own -- once the two languages are in the file
    there is nothing marking which is which -- so this only runs for files
    carrying our ``[tr:]`` header, which also records the layout used.
    Without it, re-processing a translated file would treat the merged line
    as the original and translate it again, compounding on every run.
    """
    if separator:
        # Inline: the original was written first, so the first separator is
        # the boundary.
        for line in lines:
            if separator in line.text:
                original, _, translation = line.text.partition(separator)
                line.text = original.strip()
                line.translation = translation.strip() or None
        return

    # Stacked: the translation is the following line at the same timestamp.
    merged: list[LyricLine] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        nxt = lines[index + 1] if index + 1 < len(lines) else None
        if nxt is not None and nxt.start == line.start and line.translation is None:
            line.translation = nxt.text
            index += 2
        else:
            index += 1
        merged.append(line)
    lines[:] = merged


def _fill_line_ends(lines: list[LyricLine]) -> None:
    """Give every line an end time, defaulting to the next line's start."""
    for index, line in enumerate(lines):
        if line.end is not None or line.start is None:
            continue
        nxt = lines[index + 1] if index + 1 < len(lines) else None
        line.end = nxt.start if nxt and nxt.start is not None else line.start


def read_tool_tag(path) -> str | None:
    """Read the ``[tool:...]`` header from an LRC file, if it has one.

    This is how a sidecar-only run knows it already processed a track: no tag
    is written into the MP3 in that mode, so the marker lives in the .lrc.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for _ in range(12):  # the header is at the top or not there
                line = handle.readline()
                if not line:
                    break
                match = _META_RE.match(line.rstrip())
                if match and match.group(1).lower() == "tool":
                    return match.group(2).strip() or None
    except OSError:
        return None
    return None


def parse_plain(content: str) -> Lyrics:
    """Parse untimed lyrics, one line per line, keeping blank-line structure."""
    lines = [LyricLine(clean_lyric_line(raw)) for raw in content.splitlines()]
    while lines and lines[-1].is_blank:
        lines.pop()
    return Lyrics(lines=lines, source=Source.LOCAL_TXT)


# How a translated line is laid out. LRC has no native second-language
# field, so both of these are conventions rather than standards:
#
#   "inline"  -> one line holding both, joined by a separator. Every player
#                shows it, because it is just a line of text.
#   "stacked" -> two lines sharing a timestamp. Reads better on a phone, but
#                relies on the player showing consecutive same-time lines.
#   "off"     -> original only.
BILINGUAL_STYLES = ("off", "inline", "stacked")
DEFAULT_SEPARATOR = " / "


def format_lrc(
    lyrics: Lyrics,
    *,
    enhanced: bool = False,
    decimals: int = 2,
    lead_in: float = 0.0,
    metadata: dict[str, str] | None = None,
    bilingual: str = "off",
    separator: str = DEFAULT_SEPARATOR,
) -> str:
    """Render :class:`Lyrics` as LRC text.

    ``lead_in`` shifts every line earlier by that many seconds so the reader
    sees a line just before it is sung. A line is never pulled back past the
    previous line's start, so the ordering always holds.

    ``bilingual`` selects how a line's translation is written; see
    :data:`BILINGUAL_STYLES`. Lines without a translation are unaffected, so
    a partially translated set still renders correctly.
    """
    if bilingual not in BILINGUAL_STYLES:
        raise ValueError(f"unknown bilingual style: {bilingual!r}")
    out: list[str] = []
    for key in ("ti", "ar", "al", "length", "tool"):
        value = (metadata or {}).get(key)
        if value:
            out.append(f"[{key}:{value}]")

    # Record the layout so the file can be read back apart again.
    if bilingual != "off" and lyrics.translated:
        out.append(f"[tr:{(metadata or {}).get('tr', 'translated')}]")
        if bilingual == "inline":
            out.append(f"[trsep:{separator}]")

    timed = [ln for ln in lyrics.lines if ln.start is not None]
    previous = 0.0
    for line in timed:
        start = max(previous, (line.start or 0.0) - lead_in)
        stamp = _format_stamp(start, decimals)
        translation = line.translation if bilingual != "off" else None

        if enhanced and line.words:
            body = " ".join(
                f"<{_format_stamp(max(start, w.start - lead_in), decimals)}>{w.text}"
                for w in line.words
            )
            # Word tags describe the sung words, so a translation can only be
            # appended after them, never interleaved.
            if translation and bilingual == "inline":
                body += f"{separator}{translation}"
            out.append(f"[{stamp}]{body}")
        elif translation and bilingual == "inline":
            out.append(f"[{stamp}]{line.text}{separator}{translation}")
        else:
            out.append(f"[{stamp}]{line.text}")

        if translation and bilingual == "stacked":
            out.append(f"[{stamp}]{translation}")

        previous = start

    return "\n".join(out) + "\n"


def to_sylt(lyrics: Lyrics) -> list[tuple[str, int]]:
    """Build the ``(text, milliseconds)`` pairs an ID3 SYLT frame expects."""
    return [
        (line.text, int(max(0.0, line.start or 0.0) * 1000))
        for line in lyrics.lines
        if line.start is not None
    ]


def attach_words(lyrics: Lyrics, words: list[Word]) -> None:
    """Distribute a flat aligned word stream back onto the lyric lines.

    The aligner is fed the lines' words in order, so this walks the same order
    and hands each line exactly as many words as it contributed.
    """
    cursor = 0
    for line in lyrics.lines:
        expected = len(split_words(line.text))
        if not expected:
            continue
        chunk = words[cursor : cursor + expected]
        cursor += expected
        if not chunk:
            continue
        line.words = chunk
        line.start = chunk[0].start
        line.end = chunk[-1].end
