"""Track state for the reviewer: loading, editing and saving timings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..lrc import format_lrc, parse_lrc, to_sylt
from ..models import LyricLine, Lyrics, Source, TrackMeta, Word
from ..tags import is_synced_text, read_embedded_lyrics, read_meta, write_lyrics
from ..validate import validate_timing


@dataclass
class ReviewConfig:
    """Write settings, mirroring the pipeline's so both agree on output."""

    id3_version: int = 3
    write_sidecar: bool = True
    enhanced_sidecar: bool = False
    write_sylt: bool = True
    decimals: int = 2
    backup: bool = False


def load_track(path: Path) -> tuple[TrackMeta, Lyrics | None]:
    """Read a track's metadata and its current synced lyrics.

    The sidecar wins over the embedded copy: if a previous review session
    wrote one, that is the more recent edit.
    """
    meta = read_meta(path)

    sidecar = path.with_suffix(".lrc")
    if sidecar.exists():
        text = sidecar.read_text(encoding="utf-8", errors="replace")
        if is_synced_text(text):
            lyrics = parse_lrc(text)
            lyrics.source = Source.LOCAL_LRC
            _restore_words(path, lyrics)
            return meta, (lyrics if lyrics.lines else None)

    embedded = read_embedded_lyrics(path)
    if embedded and is_synced_text(embedded):
        lyrics = parse_lrc(embedded)
        lyrics.source = Source.EMBEDDED
        _restore_words(path, lyrics)
        return meta, (lyrics if lyrics.lines else None)

    return meta, None


def _restore_words(path: Path, lyrics: Lyrics) -> None:
    """Re-attach word timings from the sidecar JSON, matching lines by text.

    Word timings are expensive to compute, so an edit in the reviewer should
    carry them along rather than throw them away. Matching on text keeps the
    pairing correct even when line order or count has changed.
    """
    words_path = path.with_suffix(".words.json")
    if not words_path.exists():
        return
    try:
        payload = json.loads(words_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    by_text: dict[str, list[list]] = {}
    for entry in payload.get("lines", []):
        if entry.get("words"):
            by_text.setdefault(entry["text"], []).append(entry["words"])

    for line in lyrics.lines:
        bucket = by_text.get(line.text)
        if bucket:
            line.words = [Word(text, start, end) for text, start, end in bucket.pop(0)]


def apply_edits(lyrics: Lyrics, starts: list[float | None]) -> Lyrics:
    """Return a copy of ``lyrics`` with new line start times.

    Each line's words are shifted by the same delta as the line, so word-level
    timing survives both a global offset and a single re-tapped line.
    """
    if len(starts) != len(lyrics.lines):
        raise ValueError(f"expected {len(lyrics.lines)} start times, got {len(starts)}")

    edited: list[LyricLine] = []
    for line, new_start in zip(lyrics.lines, starts):
        if new_start is None or line.start is None:
            edited.append(LyricLine(line.text, line.start, line.end, list(line.words)))
            continue
        delta = new_start - line.start
        edited.append(
            LyricLine(
                text=line.text,
                start=max(0.0, new_start),
                end=(line.end + delta) if line.end is not None else None,
                words=[Word(w.text, max(0.0, w.start + delta), max(0.0, w.end + delta))
                       for w in line.words],
            )
        )

    edited.sort(key=lambda ln: ln.start if ln.start is not None else 0.0)
    return Lyrics(
        lines=edited,
        source=lyrics.source,
        title=lyrics.title,
        artist=lyrics.artist,
        album=lyrics.album,
    )


def render(lyrics: Lyrics, meta: TrackMeta, config: ReviewConfig) -> str:
    from .. import __version__

    metadata = {
        "ti": lyrics.title or meta.title or "",
        "ar": lyrics.artist or meta.artist or "",
        "al": lyrics.album or meta.album or "",
        "tool": f"lyricsync {__version__}",
    }
    return format_lrc(
        lyrics,
        decimals=config.decimals,
        metadata={k: v for k, v in metadata.items() if v},
    )


def save_track(path: Path, lyrics: Lyrics, config: ReviewConfig) -> dict:
    """Write edited timings back to the MP3 and its sidecars.

    Validation runs but never blocks: in the reviewer the user is listening to
    the track, so their judgement outranks the heuristics. Warnings are
    returned for display.
    """
    from .. import __version__

    meta = read_meta(path)
    report = validate_timing(lyrics, duration=meta.duration)
    lrc_text = render(lyrics, meta, config)

    write_lyrics(
        path,
        lrc_text,
        sylt_pairs=to_sylt(lyrics) if config.write_sylt else None,
        id3_version=config.id3_version,
        backup=config.backup,
        marker=f"lyricsync/{__version__}/reviewed",
    )

    if config.write_sidecar:
        body = (
            format_lrc(lyrics, enhanced=True, decimals=config.decimals)
            if config.enhanced_sidecar and lyrics.word_level
            else lrc_text
        )
        path.with_suffix(".lrc").write_text(body, encoding="utf-8")

    if lyrics.word_level:
        payload = {
            "source": lyrics.source.value,
            "tool": f"lyricsync/{__version__}",
            "lines": [
                {
                    "text": line.text,
                    "start": round(line.start, 3) if line.start is not None else None,
                    "end": round(line.end, 3) if line.end is not None else None,
                    "words": [[w.text, round(w.start, 3), round(w.end, 3)] for w in line.words],
                }
                for line in lyrics.lines
            ],
        }
        path.with_suffix(".words.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    return {"errors": report.errors, "warnings": report.warnings}
