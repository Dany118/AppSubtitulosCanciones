"""Core data structures shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Source(str, Enum):
    """Where a set of lyrics came from. Recorded in the sidecar and the cache."""

    LRCLIB_SYNCED = "lrclib-synced"
    LRCLIB_PLAIN = "lrclib-plain"
    LOCAL_LRC = "local-lrc"
    LOCAL_TXT = "local-txt"
    EMBEDDED = "embedded"
    ASR = "asr"


@dataclass(slots=True)
class Word:
    """A single word with its start/end offset in seconds from the track start."""

    text: str
    start: float
    end: float


@dataclass(slots=True)
class LyricLine:
    """One displayed line.

    ``start`` is None for unsynced lyrics. ``words`` is populated only by the
    forced aligner and the ASR stage; the LRC writers degrade gracefully
    without it.
    """

    text: str
    start: float | None = None
    end: float | None = None
    words: list[Word] = field(default_factory=list)

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


@dataclass(slots=True)
class Lyrics:
    """A full set of lyrics plus the provenance we need for caching and review."""

    lines: list[LyricLine]
    source: Source
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    # Providers flag purely instrumental tracks so the pipeline skips them
    # instead of trying to align silence against an empty transcript.
    instrumental: bool = False

    @property
    def synced(self) -> bool:
        return any(line.start is not None for line in self.lines)

    @property
    def word_level(self) -> bool:
        return any(line.words for line in self.lines)

    @property
    def plain_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def timed_lines(self) -> list[LyricLine]:
        return [line for line in self.lines if line.start is not None]


@dataclass(slots=True)
class TrackMeta:
    """Tag metadata read off the file, used to query lyrics providers."""

    path: Path
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration: float | None = None

    @property
    def query_label(self) -> str:
        return f"{self.artist or '?'} - {self.title or self.path.stem}"

    @property
    def is_queryable(self) -> bool:
        return bool(self.title and self.artist)


class Status(str, Enum):
    OK = "ok"
    REVIEW = "review"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True)
class ProcessResult:
    """What happened to one file. The batch report is a list of these."""

    path: Path
    status: Status
    source: Source | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    line_count: int = 0
    word_level: bool = False
