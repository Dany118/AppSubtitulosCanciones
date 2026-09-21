"""End-to-end tests of the synced-lyrics path.

These run the real pipeline -- read tags, pick a provider, validate, embed --
without needing ffmpeg, torch or the network. The alignment and ASR branches
need the optional ML extra and are not covered here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2, TPE1

from lyricsync.models import LyricLine, Lyrics, Source, Status
from lyricsync.pipeline import Config, Pipeline, find_tracks
from lyricsync.lrc import read_tool_tag
from lyricsync.tags import read_embedded_lyrics, read_marker
from lyricsync.validate import validate_timing
from conftest import build_mp3_bytes

# Timings that fit inside the ~2 s synthetic fixture.
SHORT_LRC = "[00:00.50]alpha bravo\n[00:01.20]charlie delta\n"


def tagged_mp3(path: Path, frames: int = 80) -> Path:
    path.write_bytes(build_mp3_bytes(frames))
    id3 = ID3()
    id3.add(TIT2(encoding=3, text=["Placeholder Title"]))
    id3.add(TPE1(encoding=3, text=["Placeholder Artist"]))
    id3.save(path)
    return path


@pytest.fixture
def offline_config() -> Config:
    """No network, no ML: exercises the synced-LRC path only."""
    return Config(use_network=False, separate_vocals=False, realign_on_drift=False)


@pytest.fixture
def embedding_config(offline_config: Config) -> Config:
    """Same, but writing into the MP3 as well as the sidecar."""
    offline_config.sidecar_only = False
    return offline_config


@pytest.fixture
def track(tmp_path: Path) -> Path:
    path = tagged_mp3(tmp_path / "track.mp3")
    path.with_suffix(".lrc").write_text(SHORT_LRC, encoding="utf-8")
    return path


def test_writes_the_sidecar_without_touching_the_mp3(track: Path, offline_config: Config) -> None:
    """The default mode writes only the .lrc, which is what players prefer."""
    before = track.read_bytes()
    result = Pipeline(offline_config).process(track)

    assert result.status is Status.OK, result.message
    assert result.line_count == 2

    sidecar = track.with_suffix(".lrc").read_text(encoding="utf-8")
    assert "[00:00.50]alpha bravo" in sidecar
    assert "[ti:Placeholder Title]" in sidecar, "metadata header should be written"
    assert track.read_bytes() == before, "the MP3 must not be rewritten"
    assert read_embedded_lyrics(track) is None


def test_embeds_into_the_mp3_when_asked(track: Path, embedding_config: Config) -> None:
    result = Pipeline(embedding_config).process(track)

    assert result.status is Status.OK, result.message
    embedded = read_embedded_lyrics(track)
    assert embedded is not None
    assert "[00:00.50]alpha bravo" in embedded
    assert "[ti:Placeholder Title]" in embedded


def test_embedded_marker_makes_a_second_run_a_no_op(track: Path, embedding_config: Config) -> None:
    Pipeline(embedding_config).process(track)
    assert read_marker(track) is not None

    again = Pipeline(embedding_config).process(track)
    assert again.status is Status.SKIPPED


def test_sidecar_only_run_is_still_idempotent(track: Path, offline_config: Config) -> None:
    """With nothing written to the MP3, the .lrc header carries the marker."""
    Pipeline(offline_config).process(track)
    assert read_marker(track) is None, "nothing should have been written to the MP3"
    assert read_tool_tag(track.with_suffix(".lrc")) is not None

    again = Pipeline(offline_config).process(track)
    assert again.status is Status.SKIPPED


def test_force_reprocesses_a_marked_file(track: Path, offline_config: Config) -> None:
    Pipeline(offline_config).process(track)
    offline_config.force = True
    assert Pipeline(offline_config).process(track).status is Status.OK


def test_dry_run_writes_nothing(track: Path, offline_config: Config) -> None:
    original = track.with_suffix(".lrc").read_text(encoding="utf-8")
    offline_config.dry_run = True
    result = Pipeline(offline_config).process(track)

    assert result.status is Status.OK
    assert read_embedded_lyrics(track) is None
    assert read_marker(track) is None
    assert track.with_suffix(".lrc").read_text(encoding="utf-8") == original


def test_untagged_file_fails_with_a_clear_message(tmp_path: Path, offline_config: Config) -> None:
    path = tmp_path / "untagged.mp3"
    path.write_bytes(build_mp3_bytes())

    result = Pipeline(offline_config).process(path)
    assert result.status is Status.FAILED
    assert "artist/title" in result.message


def test_no_lyrics_anywhere_fails_rather_than_guessing(tmp_path: Path, offline_config: Config) -> None:
    result = Pipeline(offline_config).process(tagged_mp3(tmp_path / "bare.mp3"))
    assert result.status is Status.FAILED
    assert "no lyrics found" in result.message


def test_lyrics_running_past_the_track_are_rejected(tmp_path: Path, offline_config: Config) -> None:
    path = tagged_mp3(tmp_path / "mismatch.mp3")
    path.with_suffix(".lrc").write_text("[00:10.00]alpha\n[03:20.00]bravo\n", encoding="utf-8")

    result = Pipeline(offline_config).process(path)
    assert result.status is Status.FAILED
    assert "after the track ends" in result.message
    assert read_embedded_lyrics(path) is None, "bad timings must never be embedded"


def test_out_of_order_lyrics_are_rejected() -> None:
    """parse_lrc sorts on read, so this guards the aligner's output path."""
    jumbled = Lyrics([LyricLine("alpha", 5.0), LyricLine("bravo", 1.0)], Source.LOCAL_LRC)
    report = validate_timing(jumbled, duration=10.0)
    assert not report.ok
    assert "chronological" in report.errors[0]


def test_sidecar_is_rewritten_in_canonical_form(track: Path, offline_config: Config) -> None:
    Pipeline(offline_config).process(track)
    sidecar = track.with_suffix(".lrc").read_text(encoding="utf-8")
    assert sidecar.startswith("[ti:Placeholder Title]")
    assert "[00:00.50]alpha bravo" in sidecar


def test_no_sidecar_when_disabled(tmp_path: Path, embedding_config: Config) -> None:
    path = tagged_mp3(tmp_path / "nosidecar.mp3")
    embedding_config.write_sidecar = False
    # Provide lyrics through the embedded frame so no .lrc file is involved.
    from lyricsync.tags import write_lyrics

    write_lyrics(path, SHORT_LRC)
    embedding_config.force = True

    assert Pipeline(embedding_config).process(path).status is Status.OK
    assert not path.with_suffix(".lrc").exists()


def test_find_tracks_walks_a_directory(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    tagged_mp3(tmp_path / "a.mp3")
    tagged_mp3(tmp_path / "nested" / "b.mp3")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    assert [p.name for p in find_tracks(tmp_path)] == ["a.mp3", "b.mp3"]
    assert [p.name for p in find_tracks(tmp_path, recursive=False)] == ["a.mp3"]


def test_find_tracks_on_a_single_file(tmp_path: Path) -> None:
    path = tagged_mp3(tmp_path / "one.mp3")
    assert find_tracks(path) == [path]
    assert find_tracks(tmp_path / "notes.txt") == []
