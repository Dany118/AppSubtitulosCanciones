"""SQLite cache and run log.

Re-running a batch should not re-query the network for tracks that were
already resolved, and should leave a record of what happened to each file.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .lrc import parse_lrc
from .models import Lyrics, Source, TrackMeta

SCHEMA = """
CREATE TABLE IF NOT EXISTS lyrics_cache (
    key        TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    lrc        TEXT NOT NULL,
    word_json  TEXT,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    path    TEXT NOT NULL,
    status  TEXT NOT NULL,
    source  TEXT,
    message TEXT,
    ran_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_path_idx ON runs(path);
CREATE TABLE IF NOT EXISTS translations (
    key     TEXT PRIMARY KEY,
    lang    TEXT NOT NULL,
    source  TEXT NOT NULL,
    target  TEXT NOT NULL,
    made_at REAL NOT NULL
);
"""


def translation_key(text: str, lang: str) -> str:
    """Identify a line of text in a target language.

    Keyed on the text itself, not on the track: a chorus repeats within a song
    and across a library, and translating it once is the whole point.
    """
    return hashlib.sha1(f"{lang}|{text.strip()}".encode("utf-8")).hexdigest()


def cache_key(meta: TrackMeta) -> str:
    """Identify a recording by artist, title and rounded duration.

    Deliberately not a file hash: we rewrite the file's tags, so a hash would
    change on every run and never hit.
    """
    parts = [
        (meta.artist or "").strip().lower(),
        (meta.title or meta.path.stem).strip().lower(),
        str(int(round(meta.duration or 0))),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_lyrics(self, key: str, max_age_days: float | None = None) -> Lyrics | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM lyrics_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        if max_age_days is not None and time.time() - row["fetched_at"] > max_age_days * 86400:
            return None
        lyrics = parse_lrc(row["lrc"])
        try:
            lyrics.source = Source(row["source"])
        except ValueError:
            pass
        return lyrics if lyrics.lines else None

    def put_lyrics(self, key: str, lyrics: Lyrics, lrc_text: str) -> None:
        words = [
            [w.text, round(w.start, 3), round(w.end, 3)]
            for line in lyrics.lines
            for w in line.words
        ]
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO lyrics_cache VALUES (?, ?, ?, ?, ?)",
                (key, lyrics.source.value, lrc_text, json.dumps(words) if words else None, time.time()),
            )
            conn.commit()

    def log_run(self, path: Path, status: str, source: str | None, message: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
                (str(path), status, source, message, time.time()),
            )
            conn.commit()

    def get_translations(self, texts: list[str], lang: str) -> dict[str, str]:
        """Return the cached translation for each text that has one."""
        if not texts:
            return {}
        keys = {translation_key(text, lang): text for text in texts}
        found: dict[str, str] = {}
        with closing(self._connect()) as conn:
            # Chunked to stay under SQLite's variable limit on a long track.
            key_list = list(keys)
            for start in range(0, len(key_list), 400):
                chunk = key_list[start : start + 400]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT key, target FROM translations WHERE key IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    found[keys[row["key"]]] = row["target"]
        return found

    def put_translations(self, pairs: dict[str, str], lang: str) -> None:
        """Store translations, skipping empty results."""
        rows = [
            (translation_key(source, lang), lang, source, target, time.time())
            for source, target in pairs.items()
            if target.strip()
        ]
        if not rows:
            return
        with closing(self._connect()) as conn:
            conn.executemany("INSERT OR REPLACE INTO translations VALUES (?, ?, ?, ?, ?)", rows)
            conn.commit()

    def latest_statuses(self) -> dict[str, str]:
        """The most recent run status for every file, keyed by path.

        SQLite resolves the bare ``status`` column to the row that produced
        the MAX(), so this needs one pass rather than a correlated subquery.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT path, status, MAX(ran_at) FROM runs GROUP BY path"
            ).fetchall()
        return {row["path"]: row["status"] for row in rows}

    def latest_messages(self) -> dict[str, str]:
        """The note recorded alongside each file's most recent run."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT path, message, MAX(ran_at) FROM runs GROUP BY path"
            ).fetchall()
        return {row["path"]: row["message"] or "" for row in rows}

    def history(self, limit: int = 50) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM runs ORDER BY ran_at DESC LIMIT ?", (limit,)
            ).fetchall()
