"""LRCLIB (https://lrclib.net) — a free, key-less, community lyrics database.

It is the fastest path to a correct result: when it already holds a synced
LRC for a track, there is nothing to compute and nothing to guess.
"""

from __future__ import annotations

import httpx

from ..lrc import parse_lrc, parse_plain
from ..models import Lyrics, Source, TrackMeta

BASE_URL = "https://lrclib.net"
USER_AGENT = "lyricsync/0.2.0 (https://github.com/Dany118/AppSubtitulosCanciones)"

# LRCLIB matches on duration; anything further apart is a different recording.
DURATION_TOLERANCE = 3.0


class LrclibProvider:
    name = "lrclib"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def fetch(self, meta: TrackMeta) -> Lyrics | None:
        if not meta.is_queryable:
            return None
        try:
            record = self._exact(meta) or self._search(meta)
        except httpx.HTTPError:
            return None
        return _to_lyrics(record) if record else None

    def _exact(self, meta: TrackMeta) -> dict | None:
        """The /api/get endpoint, which needs an exact artist+title+duration."""
        params = {"artist_name": meta.artist, "track_name": meta.title}
        if meta.album:
            params["album_name"] = meta.album
        if meta.duration:
            params["duration"] = int(round(meta.duration))

        response = self._get_client().get(f"{self._base_url}/api/get", params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        record = response.json()
        return record if _usable(record) else None

    def _search(self, meta: TrackMeta) -> dict | None:
        """Fall back to /api/search and pick the best duration match."""
        response = self._get_client().get(
            f"{self._base_url}/api/search",
            params={"artist_name": meta.artist, "track_name": meta.title},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()

        results = [r for r in response.json() if _usable(r)]
        if not results:
            return None

        if meta.duration:
            results = [
                r
                for r in results
                if r.get("duration") is None
                or abs(float(r["duration"]) - meta.duration) <= DURATION_TOLERANCE
            ]
            if not results:
                return None

        def rank(record: dict) -> tuple[int, float]:
            has_synced = 0 if record.get("syncedLyrics") else 1
            if meta.duration and record.get("duration") is not None:
                gap = abs(float(record["duration"]) - meta.duration)
            else:
                gap = DURATION_TOLERANCE
            return (has_synced, gap)

        return min(results, key=rank)


def _usable(record: dict | None) -> bool:
    if not record:
        return False
    return bool(record.get("instrumental") or record.get("syncedLyrics") or record.get("plainLyrics"))


def _to_lyrics(record: dict) -> Lyrics | None:
    title = record.get("trackName")
    artist = record.get("artistName")
    album = record.get("albumName")

    if record.get("instrumental"):
        return Lyrics(lines=[], source=Source.LRCLIB_SYNCED, title=title, artist=artist,
                      album=album, instrumental=True)

    synced = record.get("syncedLyrics")
    if synced and synced.strip():
        lyrics = parse_lrc(synced)
        lyrics.source = Source.LRCLIB_SYNCED
    else:
        plain = record.get("plainLyrics")
        if not plain or not plain.strip():
            return None
        lyrics = parse_plain(plain)
        lyrics.source = Source.LRCLIB_PLAIN

    lyrics.title, lyrics.artist, lyrics.album = title, artist, album
    return lyrics if lyrics.lines else None
