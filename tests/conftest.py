"""Shared fixtures.

The tests build a synthetic MP3 from raw MPEG frame headers so the suite has
no external tooling dependency (no ffmpeg, no sample files in the repo).
All lyric text in the tests is invented placeholder text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# MPEG-1 Layer III, 128 kbps, 44.1 kHz, joint stereo.
_FRAME_HEADER = b"\xff\xfb\x90\x64"
_FRAME_SIZE = 144 * 128000 // 44100  # 417 bytes


def build_mp3_bytes(frames: int = 80) -> bytes:
    """A structurally valid, silent MP3 of roughly ``frames * 26 ms``."""
    frame = _FRAME_HEADER + b"\x00" * (_FRAME_SIZE - len(_FRAME_HEADER))
    return frame * frames


@pytest.fixture
def mp3_file(tmp_path: Path) -> Path:
    path = tmp_path / "track.mp3"
    path.write_bytes(build_mp3_bytes())
    return path


@pytest.fixture
def sample_lrc() -> str:
    return (
        "[ti:Placeholder Title]\n"
        "[ar:Placeholder Artist]\n"
        "[00:00.50]alpha bravo charlie\n"
        "[00:01.20]delta echo\n"
        "[00:01.90]foxtrot golf hotel\n"
    )
