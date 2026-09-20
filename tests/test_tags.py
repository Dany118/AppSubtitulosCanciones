from __future__ import annotations

from pathlib import Path

from mutagen.id3 import ID3, SYLT, TALB, TIT2, TPE1, TXXX, USLT

from lyricsync import tags
from conftest import build_mp3_bytes


def _tag(path: Path, **fields: str) -> None:
    frames = ID3()
    if "title" in fields:
        frames.add(TIT2(encoding=3, text=[fields["title"]]))
    if "artist" in fields:
        frames.add(TPE1(encoding=3, text=[fields["artist"]]))
    if "album" in fields:
        frames.add(TALB(encoding=3, text=[fields["album"]]))
    frames.save(path)


def test_read_meta(mp3_file: Path) -> None:
    _tag(mp3_file, title="Placeholder Title", artist="Placeholder Artist", album="Placeholder Album")
    meta = tags.read_meta(mp3_file)
    assert meta.title == "Placeholder Title"
    assert meta.artist == "Placeholder Artist"
    assert meta.album == "Placeholder Album"
    assert meta.duration and meta.duration > 0
    assert meta.is_queryable


def test_write_strips_every_old_lyric_frame(mp3_file: Path, sample_lrc: str) -> None:
    frames = ID3()
    frames.add(USLT(encoding=3, lang="eng", desc="", text="stale plain lyrics"))
    frames.add(USLT(encoding=3, lang="spa", desc="other", text="stale second copy"))
    frames.add(SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=[("stale", 10)]))
    frames.add(TXXX(encoding=3, desc="LYRICS", text=["stale txxx lyrics"]))
    frames.add(TXXX(encoding=3, desc="COMMENT", text=["keep me"]))
    frames.save(mp3_file)

    removed = tags.write_lyrics(mp3_file, sample_lrc, marker="v1")

    assert len(removed) == 4
    after = ID3(mp3_file)
    uslt = [k for k in after if k.startswith("USLT")]
    assert len(uslt) == 1, "exactly one lyrics frame must survive"
    assert str(after[uslt[0]].text) == sample_lrc
    assert after["TXXX:COMMENT"].text == ["keep me"], "unrelated frames are preserved"


def test_write_preserves_audio_and_other_tags(mp3_file: Path, sample_lrc: str) -> None:
    original_audio = mp3_file.read_bytes()
    _tag(mp3_file, title="Placeholder Title", artist="Placeholder Artist")

    tags.write_lyrics(mp3_file, sample_lrc)

    meta = tags.read_meta(mp3_file)
    assert meta.title == "Placeholder Title"
    assert original_audio in mp3_file.read_bytes(), "audio frames must be untouched"


def test_write_is_idempotent(mp3_file: Path, sample_lrc: str) -> None:
    tags.write_lyrics(mp3_file, sample_lrc)
    tags.write_lyrics(mp3_file, sample_lrc)
    after = ID3(mp3_file)
    assert len([k for k in after if k.startswith("USLT")]) == 1


def test_sylt_and_marker_written(mp3_file: Path, sample_lrc: str) -> None:
    tags.write_lyrics(
        mp3_file,
        sample_lrc,
        sylt_pairs=[("alpha bravo charlie", 500), ("delta echo", 1200)],
        marker="lyricsync/0.2.0",
    )
    after = ID3(mp3_file)
    assert any(k.startswith("SYLT") for k in after)
    assert tags.read_marker(mp3_file) == "lyricsync/0.2.0"


def test_roundtrip_reads_back_synced_text(mp3_file: Path, sample_lrc: str) -> None:
    tags.write_lyrics(mp3_file, sample_lrc)
    text = tags.read_embedded_lyrics(mp3_file)
    assert text == sample_lrc
    assert tags.is_synced_text(text)
    assert not tags.is_synced_text("just a plain line\nanother line")


def test_clear_lyrics(mp3_file: Path, sample_lrc: str) -> None:
    tags.write_lyrics(mp3_file, sample_lrc)
    removed = tags.clear_lyrics(mp3_file)
    assert removed
    assert tags.read_embedded_lyrics(mp3_file) is None


def test_backup_is_created(mp3_file: Path, sample_lrc: str) -> None:
    tags.write_lyrics(mp3_file, sample_lrc, backup=True)
    assert mp3_file.with_suffix(".mp3.bak").exists()


def test_strip_lyrics3_v2(tmp_path: Path) -> None:
    audio = build_mp3_bytes(20)
    body = b"LYRICSBEGIN" + b"IND00000000" + b"LYR00005stale"
    block = body + f"{len(body):06d}".encode() + b"LYRICS200"
    id3v1 = b"TAG" + b"\x00" * 125
    path = tmp_path / "legacy.mp3"
    path.write_bytes(audio + block + id3v1)

    assert tags.strip_lyrics3(path) is True
    data = path.read_bytes()
    assert b"LYRICSBEGIN" not in data
    assert data == audio + id3v1, "audio and ID3v1 must be preserved exactly"


def test_strip_lyrics3_v1_without_id3v1(tmp_path: Path) -> None:
    audio = build_mp3_bytes(20)
    block = b"LYRICSBEGIN" + b"stale legacy lyrics" + b"LYRICSEND"
    path = tmp_path / "legacy1.mp3"
    path.write_bytes(audio + block)

    assert tags.strip_lyrics3(path) is True
    assert path.read_bytes() == audio


def test_strip_lyrics3_noop_on_clean_file(mp3_file: Path) -> None:
    before = mp3_file.read_bytes()
    assert tags.strip_lyrics3(mp3_file) is False
    assert mp3_file.read_bytes() == before
