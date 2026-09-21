"""ASR fallback for tracks where no lyrics could be found anywhere.

Less reliable than forced alignment -- singing is hard to transcribe, and a
misheard word is embedded as-is -- so the pipeline always marks these results
for review rather than trusting them.
"""

from __future__ import annotations

from pathlib import Path

from ..device import resolve as resolve_device
from ..models import LyricLine, Lyrics, Source, Word
from ..text import clean_lyric_line

# Line-breaking limits, chosen so a line fits a phone screen and stays on
# screen long enough to read.
MAX_WORDS_PER_LINE = 9
MAX_SECONDS_PER_LINE = 6.0
SENTENCE_END = (".", "!", "?", ",", ";", ":")


class AsrTranscriber:
    """Wraps faster-whisper, emitting word-level timestamps."""

    def __init__(self, model_size: str = "large-v3", device: str = "cuda", language: str | None = "en") -> None:
        self.model_size = model_size
        self.device, self.device_note = resolve_device(device)
        self.language = language
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "faster-whisper is required for ASR: pip install '.[align]'"
            ) from exc
        compute_type = "float16" if self.device == "cuda" else "int8"
        self._model = WhisperModel(self.model_size, device=self.device, compute_type=compute_type)

    def transcribe(self, audio_path: Path) -> Lyrics:
        self._load()
        segments, _ = self._model.transcribe(
            str(audio_path),
            language=self.language,
            word_timestamps=True,
            vad_filter=True,
        )

        words: list[Word] = []
        for segment in segments:
            for word in segment.words or []:
                text = word.word.strip()
                if text:
                    words.append(Word(text, float(word.start), float(word.end)))

        return Lyrics(lines=group_into_lines(words), source=Source.ASR)


def group_into_lines(words: list[Word]) -> list[LyricLine]:
    """Chunk a flat word stream into readable lines.

    Breaks on punctuation first, then on a word count or duration cap, so the
    lines follow the phrasing of the singing rather than arbitrary cuts.
    """
    lines: list[LyricLine] = []
    current: list[Word] = []

    def flush() -> None:
        if not current:
            return
        text = clean_lyric_line(" ".join(w.text for w in current))
        if text:
            lines.append(LyricLine(text=text, start=current[0].start, end=current[-1].end,
                                   words=list(current)))
        current.clear()

    for word in words:
        current.append(word)
        spans_too_long = word.end - current[0].start >= MAX_SECONDS_PER_LINE
        if word.text.endswith(SENTENCE_END) or len(current) >= MAX_WORDS_PER_LINE or spans_too_long:
            flush()
    flush()
    return lines
