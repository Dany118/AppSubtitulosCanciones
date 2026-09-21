"""The contract every translator implements."""

from __future__ import annotations

from typing import Protocol


class Translator(Protocol):
    """Translates lyric lines, preserving their order and count.

    The mapping must stay one-to-one: each input line gets exactly one output
    line, because the result is paired back against timestamps. A line that
    cannot be translated comes back as an empty string rather than being
    dropped.
    """

    name: str
    target_language: str

    def translate(self, lines: list[str]) -> list[str]: ...
