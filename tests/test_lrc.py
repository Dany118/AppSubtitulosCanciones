from __future__ import annotations

from lyricsync.lrc import attach_words, format_lrc, parse_lrc, parse_plain, to_sylt
from lyricsync.models import LyricLine, Lyrics, Source, Word


def test_parse_basic_timestamps() -> None:
    lyrics = parse_lrc("[00:10.00]alpha\n[01:05.50]bravo\n")
    assert [line.start for line in lyrics.lines] == [10.0, 65.5]
    assert lyrics.synced


def test_parse_reads_metadata() -> None:
    lyrics = parse_lrc("[ti:Placeholder Title]\n[ar:Placeholder Artist]\n[00:01.00]alpha\n")
    assert lyrics.title == "Placeholder Title"
    assert lyrics.artist == "Placeholder Artist"


def test_repeated_timestamps_expand_into_separate_lines() -> None:
    lyrics = parse_lrc("[00:10.00][00:40.00]alpha bravo\n")
    assert len(lyrics.lines) == 2
    assert [line.start for line in lyrics.lines] == [10.0, 40.0]
    assert all(line.text == "alpha bravo" for line in lyrics.lines)


def test_offset_tag_shifts_every_timestamp() -> None:
    lyrics = parse_lrc("[offset:500]\n[00:10.00]alpha\n")
    assert lyrics.lines[0].start == 9.5


def test_offset_never_produces_negative_times() -> None:
    lyrics = parse_lrc("[offset:5000]\n[00:01.00]alpha\n")
    assert lyrics.lines[0].start == 0.0


def test_lines_are_sorted_by_time() -> None:
    lyrics = parse_lrc("[00:30.00]charlie\n[00:10.00]alpha\n[00:20.00]bravo\n")
    assert [line.text for line in lyrics.lines] == ["alpha", "bravo", "charlie"]


def test_word_level_tags_are_parsed() -> None:
    lyrics = parse_lrc("[00:10.00]<00:10.00>alpha <00:10.50>bravo\n")
    line = lyrics.lines[0]
    assert lyrics.word_level
    assert [w.text for w in line.words] == ["alpha", "bravo"]
    assert line.words[0].end == 10.5
    assert line.text == "alpha bravo", "word tags must be stripped from display text"


def test_timestamps_truncate_so_a_line_never_shows_late() -> None:
    lyrics = Lyrics([LyricLine("alpha", 9.999)], Source.LOCAL_LRC)
    assert "[00:09.99]" in format_lrc(lyrics)


def test_format_emits_metadata_header() -> None:
    lyrics = Lyrics([LyricLine("alpha", 1.0)], Source.LOCAL_LRC)
    out = format_lrc(lyrics, metadata={"ti": "Placeholder Title", "ar": "Placeholder Artist"})
    assert out.startswith("[ti:Placeholder Title]\n[ar:Placeholder Artist]\n")


def test_lead_in_shifts_lines_earlier_without_reordering() -> None:
    lyrics = Lyrics([LyricLine("alpha", 10.0), LyricLine("bravo", 10.1)], Source.LOCAL_LRC)
    out = format_lrc(lyrics, lead_in=0.5)
    stamps = [line[1:9] for line in out.strip().splitlines()]
    assert stamps == sorted(stamps), "lead-in must not push a line before its predecessor"
    assert stamps[0] == "00:09.50"


def test_enhanced_output_includes_word_tags() -> None:
    lyrics = Lyrics(
        [LyricLine("alpha bravo", 1.0, 2.0, [Word("alpha", 1.0, 1.5), Word("bravo", 1.5, 2.0)])],
        Source.LOCAL_LRC,
    )
    out = format_lrc(lyrics, enhanced=True)
    assert "<00:01.00>alpha" in out and "<00:01.50>bravo" in out


def test_enhanced_falls_back_to_plain_when_no_words() -> None:
    lyrics = Lyrics([LyricLine("alpha bravo", 1.0)], Source.LOCAL_LRC)
    assert format_lrc(lyrics, enhanced=True).strip() == "[00:01.00]alpha bravo"


def test_roundtrip_preserves_timings() -> None:
    original = "[00:10.00]alpha bravo\n[00:20.25]charlie\n"
    reparsed = parse_lrc(format_lrc(parse_lrc(original)))
    assert [line.start for line in reparsed.lines] == [10.0, 20.25]


def test_parse_plain_has_no_timings() -> None:
    lyrics = parse_plain("alpha bravo\ncharlie delta\n")
    assert not lyrics.synced
    assert len(lyrics.lines) == 2


def test_to_sylt_uses_milliseconds() -> None:
    lyrics = Lyrics([LyricLine("alpha", 1.5), LyricLine("bravo", 2.25)], Source.LOCAL_LRC)
    assert to_sylt(lyrics) == [("alpha", 1500), ("bravo", 2250)]


def test_attach_words_distributes_by_line_word_count() -> None:
    lyrics = parse_plain("alpha bravo\ncharlie")
    attach_words(lyrics, [Word("alpha", 1.0, 1.4), Word("bravo", 1.4, 1.9), Word("charlie", 2.0, 2.6)])
    assert lyrics.lines[0].start == 1.0 and lyrics.lines[0].end == 1.9
    assert lyrics.lines[1].start == 2.0
    assert len(lyrics.lines[0].words) == 2
