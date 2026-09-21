"""Tests for the local review server.

A real server is started on an ephemeral port, so these cover the actual HTTP
behaviour -- including Range handling, which is what lets the browser seek
inside the MP3.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from mutagen.id3 import ID3, TIT2, TPE1

from lyricsync.gui.server import AppServer
from lyricsync.gui.state import AppConfig, apply_edits, load_track
from lyricsync.models import LyricLine, Lyrics, Source, Word
from lyricsync.tags import read_embedded_lyrics
from conftest import build_mp3_bytes

SHORT_LRC = "[00:00.50]alpha bravo\n[00:01.20]charlie delta\n"


@pytest.fixture
def track(tmp_path: Path) -> Path:
    path = tmp_path / "track.mp3"
    path.write_bytes(build_mp3_bytes(80))
    frames = ID3()
    frames.add(TIT2(encoding=3, text=["Placeholder Title"]))
    frames.add(TPE1(encoding=3, text=["Placeholder Artist"]))
    frames.save(path)
    path.with_suffix(".lrc").write_text(SHORT_LRC, encoding="utf-8")
    return path


@pytest.fixture
def client(track: Path) -> Iterator[httpx.Client]:
    server = AppServer(("127.0.0.1", 0), [track], AppConfig())
    # A short poll interval keeps per-test teardown from costing half a second.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=10.0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_index_serves_the_page(client: httpx.Client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "lyricsync" in response.text


def test_library_lists_tracks(client: httpx.Client) -> None:
    payload = client.get("/api/library").json()
    assert len(payload["tracks"]) == 1
    entry = payload["tracks"][0]
    assert entry["name"] == "track.mp3"
    assert entry["title"] == "Placeholder Title"
    assert entry["hasLyrics"] and entry["lines"] == 2


def test_track_detail(client: httpx.Client) -> None:
    data = client.get("/api/track/0").json()
    assert data["artist"] == "Placeholder Artist"
    assert [line["start"] for line in data["lines"]] == [0.5, 1.2]


def test_unknown_track_is_404(client: httpx.Client) -> None:
    assert client.get("/api/track/99").status_code == 404
    assert client.get("/api/track/abc").status_code == 404
    assert client.get("/api/audio/99").status_code == 404


def test_unknown_route_is_404(client: httpx.Client) -> None:
    assert client.get("/api/nope").status_code == 404
    assert client.post("/api/track/0/delete").status_code == 404


def test_audio_full_request(client: httpx.Client, track: Path) -> None:
    response = client.get("/api/audio/0")
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert len(response.content) == track.stat().st_size


def test_audio_range_returns_partial_content(client: httpx.Client, track: Path) -> None:
    size = track.stat().st_size
    response = client.get("/api/audio/0", headers={"Range": "bytes=0-99"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-99/{size}"
    assert len(response.content) == 100
    assert response.content == track.read_bytes()[:100]


def test_open_ended_range_returns_the_remainder(client: httpx.Client, track: Path) -> None:
    size = track.stat().st_size
    response = client.get("/api/audio/0", headers={"Range": "bytes=100-"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-{size - 1}/{size}"
    assert response.content == track.read_bytes()[100:]


def test_suffix_range_returns_the_tail(client: httpx.Client, track: Path) -> None:
    response = client.get("/api/audio/0", headers={"Range": "bytes=-50"})
    assert response.status_code == 206
    assert response.content == track.read_bytes()[-50:]


def test_range_past_the_end_is_rejected(client: httpx.Client, track: Path) -> None:
    size = track.stat().st_size
    response = client.get("/api/audio/0", headers={"Range": f"bytes={size + 10}-"})
    assert response.status_code == 416


def test_malformed_range_is_rejected(client: httpx.Client) -> None:
    assert client.get("/api/audio/0", headers={"Range": "chapters=1-2"}).status_code == 416


def test_range_end_past_eof_is_clamped(client: httpx.Client, track: Path) -> None:
    size = track.stat().st_size
    response = client.get("/api/audio/0", headers={"Range": f"bytes=0-{size + 500}"})
    assert response.status_code == 206
    assert len(response.content) == size


def test_save_writes_new_timings_to_the_file(client: httpx.Client, track: Path) -> None:
    response = client.post("/api/track/0/save", json={"starts": [2.0, 3.0]})
    assert response.status_code == 200
    assert response.json()["saved"] is True

    embedded = read_embedded_lyrics(track)
    assert embedded is not None
    assert "[00:02.00]alpha bravo" in embedded
    assert "[00:03.00]charlie delta" in embedded
    assert "[00:00.50]" not in embedded, "old timings must be gone"

    sidecar = track.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[00:02.00]alpha bravo" in sidecar


def test_saved_timings_are_served_back(client: httpx.Client) -> None:
    client.post("/api/track/0/save", json={"starts": [2.0, 3.0]})
    data = client.get("/api/track/0").json()
    assert [line["start"] for line in data["lines"]] == [2.0, 3.0]


def test_save_rejects_a_wrong_length_list(client: httpx.Client) -> None:
    response = client.post("/api/track/0/save", json={"starts": [1.0]})
    assert response.status_code == 400
    assert "expected 2" in response.json()["error"]


def test_save_rejects_non_numeric_values(client: httpx.Client) -> None:
    response = client.post("/api/track/0/save", json={"starts": ["soon", "later"]})
    assert response.status_code == 400


def test_save_rejects_a_missing_starts_field(client: httpx.Client) -> None:
    assert client.post("/api/track/0/save", json={}).status_code == 400


def test_save_rejects_a_non_json_body(client: httpx.Client) -> None:
    response = client.post(
        "/api/track/0/save", content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


# ------------------------------------------------------------------ state

def test_load_track_prefers_the_sidecar(track: Path) -> None:
    meta, lyrics = load_track(track)
    assert meta.title == "Placeholder Title"
    assert lyrics is not None and lyrics.source is Source.LOCAL_LRC


def test_load_track_without_lyrics(tmp_path: Path) -> None:
    path = tmp_path / "bare.mp3"
    path.write_bytes(build_mp3_bytes())
    _, lyrics = load_track(path)
    assert lyrics is None


def test_apply_edits_shifts_words_with_their_line() -> None:
    lyrics = Lyrics(
        [LyricLine("alpha bravo", 10.0, 11.0,
                   [Word("alpha", 10.0, 10.5), Word("bravo", 10.5, 11.0)])],
        Source.LOCAL_LRC,
    )
    edited = apply_edits(lyrics, [9.0])
    line = edited.lines[0]

    assert (line.start, line.end) == (9.0, 10.0)
    assert [(w.start, w.end) for w in line.words] == [(9.0, 9.5), (9.5, 10.0)]
    assert lyrics.lines[0].start == 10.0, "the input must not be mutated"


def test_apply_edits_never_produces_negative_times() -> None:
    lyrics = Lyrics([LyricLine("alpha", 0.2, 0.5, [Word("alpha", 0.2, 0.5)])], Source.LOCAL_LRC)
    line = apply_edits(lyrics, [0.0]).lines[0]
    assert line.start == 0.0 and line.words[0].start == 0.0


def test_apply_edits_resorts_after_a_reorder() -> None:
    lyrics = Lyrics([LyricLine("alpha", 1.0), LyricLine("bravo", 2.0)], Source.LOCAL_LRC)
    edited = apply_edits(lyrics, [5.0, 2.0])
    assert [line.text for line in edited.lines] == ["bravo", "alpha"]


def test_apply_edits_rejects_a_length_mismatch() -> None:
    lyrics = Lyrics([LyricLine("alpha", 1.0)], Source.LOCAL_LRC)
    with pytest.raises(ValueError, match="expected 1"):
        apply_edits(lyrics, [1.0, 2.0])


# ------------------------------------------------------------------ library

def test_library_can_be_pointed_at_another_folder(client: httpx.Client, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.mp3").write_bytes(build_mp3_bytes(20))
    (other / "b.mp3").write_bytes(build_mp3_bytes(20))

    payload = client.post("/api/library", json={"path": str(other)}).json()
    assert [t["name"] for t in payload["tracks"]] == ["a.mp3", "b.mp3"]
    assert payload["folder"] == str(other)


def test_library_rejects_a_missing_folder(client: httpx.Client, tmp_path: Path) -> None:
    response = client.post("/api/library", json={"path": str(tmp_path / "nope")})
    assert response.status_code == 400
    assert "no such folder" in response.json()["error"]


def test_library_requires_a_path(client: httpx.Client) -> None:
    assert client.post("/api/library", json={}).status_code == 400


def test_switching_folder_reindexes_audio(client: httpx.Client, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "only.mp3").write_bytes(build_mp3_bytes(20))

    client.post("/api/library", json={"path": str(other)})
    assert client.get("/api/audio/0").status_code == 200
    assert client.get("/api/audio/1").status_code == 404, "stale indices must not resolve"


# ------------------------------------------------------------------ inspect

def test_inspect_reports_tags(client: httpx.Client) -> None:
    client.post("/api/track/0/save", json={"starts": [0.5, 1.2]})
    data = client.get("/api/inspect/0").json()

    assert data["name"] == "track.mp3"
    assert any(f.startswith("USLT") for f in data["frames"])
    assert data["marker"] is not None
    assert data["sidecar"] is True
    assert "[00:00.50]alpha bravo" in data["embedded"]


def test_inspect_unknown_track(client: httpx.Client) -> None:
    assert client.get("/api/inspect/9").status_code == 404


# ------------------------------------------------------------------- export

def test_export_writes_a_plain_sidecar(client: httpx.Client, track: Path) -> None:
    response = client.post("/api/export", json={"index": 0, "enhanced": False})
    assert response.status_code == 200
    assert response.json()["lines"] == 2
    assert "[00:00.50]alpha bravo" in track.with_suffix(".lrc").read_text(encoding="utf-8")


def test_export_enhanced_needs_word_timings(client: httpx.Client) -> None:
    response = client.post("/api/export", json={"index": 0, "enhanced": True})
    assert response.status_code == 400
    assert "word-level" in response.json()["error"]


def test_export_rejects_an_unknown_track(client: httpx.Client) -> None:
    assert client.post("/api/export", json={"index": 7}).status_code == 400


# ---------------------------------------------------------------------- jobs

def wait_for_job(client: httpx.Client, job_id: str, timeout: float = 15.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] != "running":
            return data
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def test_strip_job_removes_lyrics(client: httpx.Client, track: Path) -> None:
    client.post("/api/track/0/save", json={"starts": [0.5, 1.2]})

    started = client.post("/api/jobs/strip", json={"indices": [0]})
    assert started.status_code == 202

    job = wait_for_job(client, started.json()["id"])
    assert job["status"] == "finished"
    assert job["done"] == 1
    assert read_embedded_lyrics(track) is None


def test_strip_job_can_delete_the_sidecar(client: httpx.Client, track: Path) -> None:
    started = client.post("/api/jobs/strip", json={"indices": [0], "options": {"sidecar": True}})
    wait_for_job(client, started.json()["id"])
    assert not track.with_suffix(".lrc").exists()


def test_sync_job_runs_offline(client: httpx.Client, track: Path) -> None:
    track.with_suffix(".lrc").write_text(SHORT_LRC, encoding="utf-8")
    started = client.post("/api/jobs/sync", json={
        "indices": [0],
        "options": {"offline": True, "demucs": False, "force": True},
    })
    assert started.status_code == 202

    job = wait_for_job(client, started.json()["id"])
    assert job["status"] == "finished"
    assert job["events"][0]["status"] == "ok"
    assert read_embedded_lyrics(track) is not None


def test_job_defaults_to_the_whole_library(client: httpx.Client) -> None:
    started = client.post("/api/jobs/strip", json={})
    job = wait_for_job(client, started.json()["id"])
    assert job["total"] == 1


def test_job_rejects_bad_indices(client: httpx.Client) -> None:
    assert client.post("/api/jobs/strip", json={"indices": [5]}).status_code == 400
    assert client.post("/api/jobs/strip", json={"indices": "all"}).status_code == 400


def test_job_rejects_an_empty_selection(client: httpx.Client) -> None:
    assert client.post("/api/jobs/strip", json={"indices": []}).status_code == 400


def test_unknown_job_kind_is_404(client: httpx.Client) -> None:
    assert client.post("/api/jobs/frobnicate", json={}).status_code == 404


def test_unknown_job_id_is_404(client: httpx.Client) -> None:
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_cancel_reports_whether_it_took_effect(client: httpx.Client) -> None:
    assert client.post("/api/jobs/deadbeef/cancel").json()["cancelled"] is False


# ------------------------------------------------------------------ history

def test_history_is_empty_without_a_cache(client: httpx.Client) -> None:
    assert client.get("/api/history").json()["runs"] == []


# ------------------------------------------------------------------- assets

def test_static_assets_are_served_with_correct_types(client: httpx.Client) -> None:
    for asset, expected in (("/app.css", "text/css"), ("/app.js", "text/javascript")):
        response = client.get(asset)
        assert response.status_code == 200, asset
        assert expected in response.headers["content-type"]


# --------------------------------------------------------- review state

def test_library_reports_no_status_without_a_cache(client: httpx.Client) -> None:
    entry = client.get("/api/library").json()["tracks"][0]
    assert entry["lastStatus"] is None
    assert entry["lastMessage"] == ""


def test_library_surfaces_the_last_run_status(track: Path, tmp_path: Path) -> None:
    """A flagged run must be visible in the listing, not only in the history."""
    from lyricsync.store import Store

    cache = tmp_path / "cache.db"
    Store(cache).log_run(track, "review", "lrclib-synced", "first line drifts")

    server = AppServer(("127.0.0.1", 0), [track], AppConfig(), cache)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}") as c:
            entry = c.get("/api/library").json()["tracks"][0]
        assert entry["lastStatus"] == "review"
        assert entry["lastMessage"] == "first line drifts"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sync_job_records_warnings_as_the_run_note(track: Path, tmp_path: Path) -> None:
    """A review result carries warnings, not a message; the log must say why."""
    from lyricsync.store import Store

    cache = tmp_path / "cache.db"
    # Lyrics that run past the end of the ~2 s fixture: written, but flagged.
    track.with_suffix(".lrc").write_text("[00:00.50]alpha bravo\n[00:04.00]charlie\n", encoding="utf-8")

    server = AppServer(("127.0.0.1", 0), [track], AppConfig(), cache)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=15.0) as c:
            started = c.post("/api/jobs/sync", json={
                "indices": [0], "options": {"offline": True, "demucs": False, "force": True},
            })
            job = wait_for_job(c, started.json()["id"])
            assert job["events"][0]["status"] == "review"

            entry = c.get("/api/library").json()["tracks"][0]
            assert entry["lastStatus"] == "review"
            assert entry["lastMessage"], "the reason must be recorded, not blank"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
