"""Provider tests.

LRCLIB is exercised through a mock transport so the suite is deterministic
and makes no network calls.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lyricsync.models import Source, TrackMeta
from lyricsync.providers import EmbeddedProvider, LocalFileProvider, LrclibProvider
from lyricsync.tags import write_lyrics

SYNCED = "[00:10.00]alpha bravo\n[00:20.00]charlie delta\n"
PLAIN = "alpha bravo\ncharlie delta\n"


def record(**overrides: object) -> dict:
    base = {
        "trackName": "Placeholder Title",
        "artistName": "Placeholder Artist",
        "albumName": "Placeholder Album",
        "duration": 200,
        "instrumental": False,
        "plainLyrics": PLAIN,
        "syncedLyrics": SYNCED,
    }
    base.update(overrides)
    return base


def provider_for(handler) -> LrclibProvider:
    return LrclibProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def meta(tmp_path: Path) -> TrackMeta:
    return TrackMeta(
        path=tmp_path / "track.mp3",
        title="Placeholder Title",
        artist="Placeholder Artist",
        duration=200.0,
    )


def test_exact_hit_returns_synced(meta: TrackMeta) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/get"
        return httpx.Response(200, json=record())

    lyrics = provider_for(handler).fetch(meta)
    assert lyrics is not None
    assert lyrics.source is Source.LRCLIB_SYNCED
    assert [line.start for line in lyrics.lines] == [10.0, 20.0]


def test_plain_only_record_is_marked_plain(meta: TrackMeta) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=record(syncedLyrics=None))

    lyrics = provider_for(handler).fetch(meta)
    assert lyrics is not None
    assert lyrics.source is Source.LRCLIB_PLAIN
    assert not lyrics.synced


def test_instrumental_is_flagged_not_dropped(meta: TrackMeta) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=record(instrumental=True, syncedLyrics=None, plainLyrics=None))

    lyrics = provider_for(handler).fetch(meta)
    assert lyrics is not None and lyrics.instrumental


def test_falls_back_to_search_on_404(meta: TrackMeta) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/get":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=[record()])

    lyrics = provider_for(handler).fetch(meta)
    assert seen == ["/api/get", "/api/search"]
    assert lyrics is not None and lyrics.synced


def test_search_rejects_candidates_with_wrong_duration(meta: TrackMeta) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/get":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=[record(duration=320)])

    assert provider_for(handler).fetch(meta) is None, "a 2-minute mismatch is a different recording"


def test_search_prefers_synced_then_closest_duration(meta: TrackMeta) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/get":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=[
            record(duration=201, syncedLyrics=None, albumName="plain-close"),
            record(duration=202, albumName="synced-further"),
        ])

    lyrics = provider_for(handler).fetch(meta)
    assert lyrics is not None and lyrics.album == "synced-further"


def test_network_errors_are_swallowed(meta: TrackMeta) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert provider_for(handler).fetch(meta) is None


def test_untagged_file_is_not_queried(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("should not query without artist and title")

    assert provider_for(handler).fetch(TrackMeta(path=tmp_path / "x.mp3")) is None


def test_local_lrc_sidecar_is_picked_up(mp3_file: Path) -> None:
    mp3_file.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    lyrics = LocalFileProvider().fetch(TrackMeta(path=mp3_file, title="t", artist="a"))
    assert lyrics is not None and lyrics.source is Source.LOCAL_LRC and lyrics.synced


def test_local_txt_is_plain(mp3_file: Path) -> None:
    mp3_file.with_suffix(".txt").write_text(PLAIN, encoding="utf-8")
    lyrics = LocalFileProvider().fetch(TrackMeta(path=mp3_file, title="t", artist="a"))
    assert lyrics is not None and lyrics.source is Source.LOCAL_TXT and not lyrics.synced


def test_unsynced_sidecar_lrc_is_ignored(mp3_file: Path) -> None:
    mp3_file.with_suffix(".lrc").write_text(PLAIN, encoding="utf-8")
    assert LocalFileProvider().fetch(TrackMeta(path=mp3_file, title="t", artist="a")) is None


def test_embedded_provider_reads_back_what_was_written(mp3_file: Path) -> None:
    write_lyrics(mp3_file, SYNCED)
    lyrics = EmbeddedProvider().fetch(TrackMeta(path=mp3_file, title="t", artist="a"))
    assert lyrics is not None and lyrics.synced and lyrics.source is Source.EMBEDDED
