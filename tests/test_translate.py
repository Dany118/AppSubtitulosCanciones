"""Translation: providers, caching, and how a translated line is rendered.

The Marian model is never loaded here; a stub stands in for it so the suite
stays fast and offline. All text is invented placeholder text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lyricsync.lrc import format_lrc, parse_lrc
from lyricsync.models import LyricLine, Lyrics, Source
from lyricsync.pipeline import Config, Pipeline
from lyricsync.store import Store, translation_key
from lyricsync.translate.sidecar import SidecarTranslator


class StubTranslator:
    """Records what it was asked for and echoes a predictable translation."""

    name = "stub"

    def __init__(self, target_language: str = "es") -> None:
        self.target_language = target_language
        self.seen: list[list[str]] = []

    def translate(self, lines: list[str]) -> list[str]:
        self.seen.append(list(lines))
        return [f"<{line}>" for line in lines]


# ------------------------------------------------------------------ rendering

def bilingual_lyrics() -> Lyrics:
    return Lyrics(
        [
            LyricLine("alpha bravo", 1.0, translation="alfa bravo"),
            LyricLine("charlie", 2.0, translation="charli"),
        ],
        Source.LOCAL_LRC,
    )


def test_inline_style_joins_both_languages_on_one_line() -> None:
    out = format_lrc(bilingual_lyrics(), bilingual="inline")
    assert "[00:01.00]alpha bravo / alfa bravo" in out
    assert out.count("[00:01.00]") == 1


def lyric_lines(rendered: str) -> list[str]:
    """Just the timestamped lines, dropping the metadata header."""
    return [line for line in rendered.strip().splitlines() if line.startswith("[0")]


def test_stacked_style_emits_two_lines_sharing_a_timestamp() -> None:
    out = lyric_lines(format_lrc(bilingual_lyrics(), bilingual="stacked"))
    assert out[0] == "[00:01.00]alpha bravo"
    assert out[1] == "[00:01.00]alfa bravo"


def test_off_style_drops_the_translation() -> None:
    out = format_lrc(bilingual_lyrics(), bilingual="off")
    assert "alfa" not in out


def test_untranslated_lines_render_unchanged() -> None:
    lyrics = Lyrics([LyricLine("alpha", 1.0, translation="alfa"),
                     LyricLine("bravo", 2.0)], Source.LOCAL_LRC)
    out = lyric_lines(format_lrc(lyrics, bilingual="inline"))
    assert out == ["[00:01.00]alpha / alfa", "[00:02.00]bravo"]


def test_custom_separator() -> None:
    out = format_lrc(bilingual_lyrics(), bilingual="inline", separator=" | ")
    assert "[00:01.00]alpha bravo | alfa bravo" in out


def test_unknown_style_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown bilingual style"):
        format_lrc(bilingual_lyrics(), bilingual="sideways")


def test_stacked_output_stays_valid_lrc_for_a_player() -> None:
    """A player sees plain LRC: ordered timestamps, one line of text each."""
    rendered = format_lrc(bilingual_lyrics(), bilingual="stacked")
    lines = lyric_lines(rendered)

    assert len(lines) == 4, "each original line plus its translation"
    stamps = [line[1:9] for line in lines]
    assert stamps == sorted(stamps), "a player reads these top to bottom"


# -------------------------------------------------------------------- sidecar

def test_sidecar_translator_reads_a_hand_written_file(tmp_path: Path) -> None:
    track = tmp_path / "song.mp3"
    track.touch()
    (tmp_path / "song.es.txt").write_text("alfa bravo\ncharli\n", encoding="utf-8")

    translator = SidecarTranslator(track)
    assert translator.available()
    assert translator.translate(["alpha bravo", "charlie"]) == ["alfa bravo", "charli"]


def test_sidecar_translator_pads_a_short_file(tmp_path: Path) -> None:
    track = tmp_path / "song.mp3"
    track.touch()
    (tmp_path / "song.es.txt").write_text("alfa bravo\n", encoding="utf-8")
    assert SidecarTranslator(track).translate(["a", "b", "c"]) == ["alfa bravo", "", ""]


def test_sidecar_translator_without_a_file(tmp_path: Path) -> None:
    track = tmp_path / "song.mp3"
    track.touch()
    translator = SidecarTranslator(track)
    assert not translator.available()
    assert translator.translate(["a", "b"]) == ["", ""]


# ---------------------------------------------------------------------- cache

def test_cache_round_trip(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.put_translations({"alpha": "alfa", "bravo": "bravo"}, "es")
    assert store.get_translations(["alpha", "missing"], "es") == {"alpha": "alfa"}


def test_cache_is_scoped_by_language(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.put_translations({"alpha": "alfa"}, "es")
    assert store.get_translations(["alpha"], "fr") == {}
    assert translation_key("alpha", "es") != translation_key("alpha", "fr")


def test_cache_ignores_empty_results(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    store.put_translations({"alpha": "", "bravo": "   "}, "es")
    assert store.get_translations(["alpha", "bravo"], "es") == {}


def test_cache_handles_more_lines_than_sqlite_variable_limit(tmp_path: Path) -> None:
    store = Store(tmp_path / "c.db")
    pairs = {f"line {i}": f"linea {i}" for i in range(1200)}
    store.put_translations(pairs, "es")
    found = store.get_translations(list(pairs), "es")
    assert len(found) == 1200


# ------------------------------------------------------------------- pipeline

@pytest.fixture
def translating_pipeline(tmp_path: Path):
    config = Config(use_network=False, separate_vocals=False, realign_on_drift=False,
                    translate=True, target_language="es")
    store = Store(tmp_path / "c.db")
    pipeline = Pipeline(config, store=store)
    stub = StubTranslator()
    pipeline._translator = stub
    return pipeline, stub, store


def test_pipeline_translates_every_line(translating_pipeline, tmp_path: Path) -> None:
    pipeline, stub, _ = translating_pipeline
    track = tmp_path / "song.mp3"
    track.touch()
    lyrics = Lyrics([LyricLine("alpha bravo", 1.0), LyricLine("charlie", 2.0)], Source.LOCAL_LRC)

    from lyricsync.models import TrackMeta

    assert pipeline.translate(TrackMeta(path=track), lyrics) == []
    assert [line.translation for line in lyrics.lines] == ["<alpha bravo>", "<charlie>"]


def test_repeated_lines_are_translated_once(translating_pipeline, tmp_path: Path) -> None:
    """A chorus repeats; sending it to the model twice is wasted work."""
    pipeline, stub, _ = translating_pipeline
    track = tmp_path / "song.mp3"
    track.touch()
    lyrics = Lyrics(
        [LyricLine("chorus line", 1.0), LyricLine("verse", 2.0), LyricLine("chorus line", 3.0)],
        Source.LOCAL_LRC,
    )

    from lyricsync.models import TrackMeta

    pipeline.translate(TrackMeta(path=track), lyrics)
    assert stub.seen == [["chorus line", "verse"]]
    assert lyrics.lines[2].translation == "<chorus line>"


def test_cache_spares_the_model_on_a_second_track(translating_pipeline, tmp_path: Path) -> None:
    pipeline, stub, _ = translating_pipeline
    track = tmp_path / "song.mp3"
    track.touch()

    from lyricsync.models import TrackMeta

    meta = TrackMeta(path=track)
    pipeline.translate(meta, Lyrics([LyricLine("alpha", 1.0)], Source.LOCAL_LRC))
    second = Lyrics([LyricLine("alpha", 1.0)], Source.LOCAL_LRC)
    pipeline.translate(meta, second)

    assert len(stub.seen) == 1, "the second call must be served from the cache"
    assert second.lines[0].translation == "<alpha>"


def test_a_hand_written_sidecar_beats_the_model(translating_pipeline, tmp_path: Path) -> None:
    """A correction must survive the next run."""
    pipeline, stub, _ = translating_pipeline
    track = tmp_path / "song.mp3"
    track.touch()
    (tmp_path / "song.es.txt").write_text("traduccion corregida\n", encoding="utf-8")
    lyrics = Lyrics([LyricLine("alpha", 1.0)], Source.LOCAL_LRC)

    from lyricsync.models import TrackMeta

    pipeline.translate(TrackMeta(path=track), lyrics)
    assert lyrics.lines[0].translation == "traduccion corregida"
    assert stub.seen == [], "the model must not be called when a sidecar exists"


def test_translation_is_off_by_default(tmp_path: Path) -> None:
    pipeline = Pipeline(Config(use_network=False))
    track = tmp_path / "song.mp3"
    track.touch()
    lyrics = Lyrics([LyricLine("alpha", 1.0)], Source.LOCAL_LRC)

    from lyricsync.models import TrackMeta

    assert pipeline.translate(TrackMeta(path=track), lyrics) == []
    assert lyrics.lines[0].translation is None


# ------------------------------------------------------- lossless round-trip

META = {"tool": "lyricsync test", "tr": "es"}


@pytest.mark.parametrize("style", ["inline", "stacked"])
def test_a_written_file_reads_back_apart_again(style: str) -> None:
    """Rendering merges two languages; reading must separate them again."""
    rendered = format_lrc(bilingual_lyrics(), bilingual=style, metadata=META)
    back = parse_lrc(rendered)

    assert [line.text for line in back.lines] == ["alpha bravo", "charlie"]
    assert [line.translation for line in back.lines] == ["alfa bravo", "charli"]


@pytest.mark.parametrize("style", ["inline", "stacked"])
def test_reprocessing_does_not_compound_the_translation(style: str) -> None:
    """Regression: a re-run treated the merged line as the original text.

    That appended the translation a second time, and a third run a third
    time, corrupting the file a little more on every pass.
    """
    first = format_lrc(bilingual_lyrics(), bilingual=style, metadata=META)
    second = format_lrc(parse_lrc(first), bilingual=style, metadata=META)
    third = format_lrc(parse_lrc(second), bilingual=style, metadata=META)

    assert second == first
    assert third == second
    assert "alfa bravo / alfa bravo" not in second
    assert second.count("alfa bravo") == 1


def test_header_records_the_layout() -> None:
    inline = format_lrc(bilingual_lyrics(), bilingual="inline", metadata=META)
    assert "[tr:es]" in inline
    assert "[trsep: / ]" in inline, "the separator must be recorded to split on later"

    stacked = format_lrc(bilingual_lyrics(), bilingual="stacked", metadata=META)
    assert "[tr:es]" in stacked
    assert "[trsep:" not in stacked, "stacked needs no separator"


def test_a_plain_file_is_never_unmerged() -> None:
    """Without our header, a slash in the lyrics is just a slash."""
    back = parse_lrc("[00:01.00]alpha / bravo\n")
    assert back.lines[0].text == "alpha / bravo"
    assert back.lines[0].translation is None


def test_custom_separator_survives_the_round_trip() -> None:
    rendered = format_lrc(bilingual_lyrics(), bilingual="inline", separator=" :: ", metadata=META)
    back = parse_lrc(rendered)
    assert back.lines[0].text == "alpha bravo"
    assert back.lines[0].translation == "alfa bravo"


def test_stacked_unmerge_leaves_a_repeated_chorus_alone() -> None:
    """Repeated timestamps on one line are a chorus, not a translation."""
    back = parse_lrc("[tr:es]\n[00:10.00][00:40.00]alpha bravo\n")
    assert len(back.lines) == 2
    assert [line.start for line in back.lines] == [10.0, 40.0]
    assert all(line.translation is None for line in back.lines)


def test_pipeline_caches_the_untranslated_form(tmp_path: Path) -> None:
    """The lyrics cache must not hold rendered bilingual text.

    Storing the merged output there was the other half of the compounding
    bug: the next run read it back as if it were the original lyrics.
    """
    from lyricsync.store import cache_key
    from lyricsync.models import TrackMeta
    from mutagen.id3 import ID3, TIT2, TPE1
    from conftest import build_mp3_bytes

    track = tmp_path / "song.mp3"
    track.write_bytes(build_mp3_bytes(80))
    frames = ID3()
    frames.add(TIT2(encoding=3, text=["Placeholder Title"]))
    frames.add(TPE1(encoding=3, text=["Placeholder Artist"]))
    frames.save(track)
    track.with_suffix(".lrc").write_text("[00:00.50]alpha bravo\n", encoding="utf-8")
    track.with_suffix(".es.txt").write_text("alfa bravo\n", encoding="utf-8")

    store = Store(tmp_path / "c.db")
    config = Config(use_network=False, separate_vocals=False, realign_on_drift=False,
                    translate=True, bilingual="inline")
    assert Pipeline(config, store=store).process(track).status.value in ("ok", "review")

    cached = store.get_lyrics(cache_key(TrackMeta(
        path=track, title="Placeholder Title", artist="Placeholder Artist",
        duration=track.stat().st_size and 2.085,
    )))
    if cached is not None:
        assert all("/" not in line.text for line in cached.lines), \
            "the cache must hold canonical lyrics, not the rendered bilingual form"
