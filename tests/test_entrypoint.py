"""The packaged executable's argument handling.

Double-clicking passes no arguments at all, so the entry point has to decide
what that means rather than printing a usage error into a window that closes.
"""

from __future__ import annotations

import sys

import pytest

from lyricsync.__main__ import main


@pytest.fixture
def argv(monkeypatch):
    """Capture what the CLI would be invoked with, without running it."""
    captured = {}

    def fake_app() -> None:
        captured["argv"] = list(sys.argv)

    monkeypatch.setattr("lyricsync.__main__.app", fake_app)
    return captured


def run(monkeypatch, argv, args: list[str]) -> list[str]:
    monkeypatch.setattr(sys, "argv", ["lyricsync", *args])
    main()
    return argv["argv"][1:]


def test_no_arguments_opens_the_gui(monkeypatch, argv) -> None:
    assert run(monkeypatch, argv, []) == ["gui"]


def test_bare_options_open_the_gui(monkeypatch, argv) -> None:
    assert run(monkeypatch, argv, ["--port", "9000"]) == ["gui", "--port", "9000"]


def test_help_still_reaches_the_cli(monkeypatch, argv) -> None:
    assert run(monkeypatch, argv, ["--help"]) == ["--help"]
    assert run(monkeypatch, argv, ["-h"]) == ["-h"]


def test_an_explicit_command_is_untouched(monkeypatch, argv) -> None:
    assert run(monkeypatch, argv, ["sync", "song.mp3"]) == ["sync", "song.mp3"]
    assert run(monkeypatch, argv, ["gui", "~/Music"]) == ["gui", "~/Music"]


def test_a_path_argument_is_not_mistaken_for_an_option(monkeypatch, argv) -> None:
    assert run(monkeypatch, argv, ["strip", "-y", "song.mp3"]) == ["strip", "-y", "song.mp3"]
