"""The orchestrator: track in, timed lyrics out.

Strategy cascade, cheapest and most reliable first:

1. An existing synced LRC (sidecar, LRCLIB, or already embedded) -- nothing to
   compute, so nothing to get wrong.
2. Plain lyrics plus forced alignment -- the words are known, only the times
   are inferred. This is the accurate path.
3. ASR -- a last resort, always flagged for review.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .align.forced import AlignmentError, ForcedAligner
from .audio import AudioError, decode_mono, separate_vocals, vocal_onset
from .lrc import attach_words, format_lrc, to_sylt
from .models import Lyrics, ProcessResult, Source, Status, TrackMeta, Word
from .providers import EmbeddedProvider, LocalFileProvider, LrclibProvider
from .store import Store, cache_key
from .tags import read_marker, read_meta, write_lyrics
from .text import is_annotation, split_words
from .validate import validate_timing

AUDIO_SUFFIXES = {".mp3"}


@dataclass
class Config:
    """Everything tunable about a run."""

    device: str = "cuda"
    demucs_model: str = "htdemucs"
    whisper_model: str = "large-v3"
    language: str = "en"

    # Output
    id3_version: int = 3           # v2.3 is the safest for Android players
    write_sidecar: bool = True     # <track>.lrc next to the file
    enhanced_sidecar: bool = False # word-level LRC (Poweramp / Salt Player)
    write_sylt: bool = True
    write_words_json: bool = True  # keeps word timings for later re-export
    lead_in: float = 0.0           # show each line this many seconds early
    decimals: int = 2

    # Behaviour
    separate_vocals: bool = True
    allow_asr: bool = False
    realign_on_drift: bool = True  # re-time a synced LRC that fails onset check
    force: bool = False            # reprocess files already marked
    backup: bool = False
    dry_run: bool = False
    use_network: bool = True

    extra_warnings: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, config: Config, store: Store | None = None) -> None:
        self.config = config
        self.store = store
        self._aligner: ForcedAligner | None = None
        self._lrclib = LrclibProvider() if config.use_network else None
        self._local = LocalFileProvider()
        self._embedded = EmbeddedProvider()

    # ---------------------------------------------------------------- lyrics

    def _candidates(self, meta: TrackMeta) -> list[Lyrics]:
        """Collect what every provider has, in order of trust."""
        found: list[Lyrics] = []
        for provider in (self._local, self._lrclib, self._embedded):
            if provider is None:
                continue
            try:
                result = provider.fetch(meta)
            except Exception:  # noqa: BLE001 - one bad source must not stop a batch
                continue
            if result is not None:
                found.append(result)
        return found

    def acquire(self, meta: TrackMeta) -> tuple[Lyrics | None, bool]:
        """Return the best available lyrics and whether they came from cache."""
        if self.store is not None:
            cached = self.store.get_lyrics(cache_key(meta))
            if cached is not None and cached.synced:
                return cached, True

        candidates = self._candidates(meta)
        if not candidates:
            return None, False
        for lyrics in candidates:
            if lyrics.instrumental:
                return lyrics, False
        # Prefer something already timed; otherwise the best plain text.
        synced = [c for c in candidates if c.synced]
        return (synced[0] if synced else candidates[0]), False

    # ------------------------------------------------------------- alignment

    def _get_aligner(self) -> ForcedAligner:
        if self._aligner is None:
            self._aligner = ForcedAligner(device=self.config.device)
        return self._aligner

    def _alignable_lines(self, lyrics: Lyrics) -> None:
        """Drop blanks and section markers in place; they have no audio."""
        lyrics.lines = [
            line for line in lyrics.lines if not line.is_blank and not is_annotation(line.text)
        ]

    def align(self, meta: TrackMeta, lyrics: Lyrics, work_dir: Path) -> list[str]:
        """Compute word-level timings for plain lyrics. Returns warnings."""
        warnings: list[str] = []
        self._alignable_lines(lyrics)
        words = [w for line in lyrics.lines for w in split_words(line.text)]
        if not words:
            raise AlignmentError("lyrics contain no words to align")

        source_path = meta.path
        if self.config.separate_vocals:
            try:
                source_path = separate_vocals(
                    meta.path, work_dir, device=self.config.device, model=self.config.demucs_model
                )
            except AudioError as exc:
                warnings.append(f"vocal separation failed, aligning on the full mix ({exc})")
                source_path = meta.path

        aligner = self._get_aligner()
        samples = decode_mono(source_path, ForcedAligner.SAMPLE_RATE)
        aligned: list[Word] = aligner.align(samples, words, sample_rate=ForcedAligner.SAMPLE_RATE)
        attach_words(lyrics, aligned)
        return warnings

    def _onset(self, meta: TrackMeta, work_dir: Path) -> float | None:
        """Detect when singing starts, for the timing plausibility check."""
        try:
            path = meta.path
            if self.config.separate_vocals:
                path = separate_vocals(
                    meta.path, work_dir, device=self.config.device, model=self.config.demucs_model
                )
            return vocal_onset(decode_mono(path))
        except (AudioError, OSError):
            return None

    # --------------------------------------------------------------- writing

    def render(self, lyrics: Lyrics, meta: TrackMeta) -> str:
        metadata = {
            "ti": lyrics.title or meta.title or "",
            "ar": lyrics.artist or meta.artist or "",
            "al": lyrics.album or meta.album or "",
            "tool": f"lyricsync {__version__}",
        }
        return format_lrc(
            lyrics,
            decimals=self.config.decimals,
            lead_in=self.config.lead_in,
            metadata={k: v for k, v in metadata.items() if v},
        )

    def write(self, meta: TrackMeta, lyrics: Lyrics, lrc_text: str) -> None:
        write_lyrics(
            meta.path,
            lrc_text,
            sylt_pairs=to_sylt(lyrics) if self.config.write_sylt else None,
            plain_fallback=None,
            id3_version=self.config.id3_version,
            backup=self.config.backup,
            marker=f"lyricsync/{__version__}/{lyrics.source.value}",
        )

        if self.config.write_sidecar:
            sidecar = meta.path.with_suffix(".lrc")
            body = (
                format_lrc(lyrics, enhanced=True, decimals=self.config.decimals,
                           lead_in=self.config.lead_in)
                if self.config.enhanced_sidecar and lyrics.word_level
                else lrc_text
            )
            sidecar.write_text(body, encoding="utf-8")

        if self.config.write_words_json and lyrics.word_level:
            payload = {
                "source": lyrics.source.value,
                "tool": f"lyricsync/{__version__}",
                "lines": [
                    {
                        "text": line.text,
                        "start": round(line.start, 3) if line.start is not None else None,
                        "end": round(line.end, 3) if line.end is not None else None,
                        "words": [[w.text, round(w.start, 3), round(w.end, 3)] for w in line.words],
                    }
                    for line in lyrics.lines
                ],
            }
            meta.path.with_suffix(".words.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )

    # --------------------------------------------------------------- process

    def process(self, path: Path) -> ProcessResult:
        try:
            meta = read_meta(path)
        except Exception as exc:  # noqa: BLE001
            return ProcessResult(path, Status.FAILED, message=f"unreadable MP3: {exc}")

        if not self.config.force:
            marker = read_marker(path)
            if marker:
                return ProcessResult(path, Status.SKIPPED, message=f"already processed ({marker})")

        if not meta.is_queryable:
            return ProcessResult(
                path, Status.FAILED,
                message="missing artist/title tags, cannot look up lyrics",
            )

        lyrics, cached = self.acquire(meta)
        if lyrics is None:
            if not self.config.allow_asr:
                return ProcessResult(path, Status.FAILED, message="no lyrics found (use --asr to transcribe)")
            return self._process_asr(meta)

        if lyrics.instrumental:
            return ProcessResult(path, Status.SKIPPED, source=lyrics.source, message="instrumental track")

        warnings: list[str] = []
        work_dir = Path(tempfile.mkdtemp(prefix="lyricsync-"))
        try:
            if not lyrics.synced:
                try:
                    warnings += self.align(meta, lyrics, work_dir)
                except (AlignmentError, AudioError) as exc:
                    return ProcessResult(path, Status.FAILED, source=lyrics.source,
                                         message=f"alignment failed: {exc}")

            onset = None
            if self.config.realign_on_drift and lyrics.synced and not cached:
                onset = self._onset(meta, work_dir)

            report = validate_timing(lyrics, duration=meta.duration, onset=onset)
            if not report.ok:
                return ProcessResult(path, Status.FAILED, source=lyrics.source,
                                     message="; ".join(report.errors), warnings=report.warnings)
            warnings += report.warnings

            lrc_text = self.render(lyrics, meta)
            if not self.config.dry_run:
                self.write(meta, lyrics, lrc_text)
                if self.store is not None:
                    self.store.put_lyrics(cache_key(meta), lyrics, lrc_text)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        status = Status.REVIEW if warnings else Status.OK
        return ProcessResult(
            path, status, source=lyrics.source,
            message="dry run" if self.config.dry_run else "",
            warnings=warnings,
            line_count=len(lyrics.timed_lines()),
            word_level=lyrics.word_level,
        )

    def _process_asr(self, meta: TrackMeta) -> ProcessResult:
        from .align.asr import AsrTranscriber

        work_dir = Path(tempfile.mkdtemp(prefix="lyricsync-asr-"))
        try:
            source_path = meta.path
            if self.config.separate_vocals:
                try:
                    source_path = separate_vocals(
                        meta.path, work_dir, device=self.config.device, model=self.config.demucs_model
                    )
                except AudioError:
                    source_path = meta.path

            transcriber = AsrTranscriber(
                model_size=self.config.whisper_model,
                device=self.config.device,
                language=self.config.language,
            )
            lyrics = transcriber.transcribe(source_path)
        except Exception as exc:  # noqa: BLE001
            return ProcessResult(meta.path, Status.FAILED, message=f"transcription failed: {exc}")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        report = validate_timing(lyrics, duration=meta.duration)
        if not report.ok:
            return ProcessResult(meta.path, Status.FAILED, source=Source.ASR,
                                 message="; ".join(report.errors))

        lrc_text = self.render(lyrics, meta)
        if not self.config.dry_run:
            self.write(meta, lyrics, lrc_text)

        # Machine-transcribed words are never trusted without a listen.
        return ProcessResult(
            meta.path, Status.REVIEW, source=Source.ASR,
            warnings=["transcribed by ASR, wording needs checking", *report.warnings],
            line_count=len(lyrics.timed_lines()), word_level=lyrics.word_level,
        )


def find_tracks(target: Path, recursive: bool = True) -> list[Path]:
    """Collect MP3s from a file or directory, in a stable order."""
    if target.is_file():
        return [target] if target.suffix.lower() in AUDIO_SUFFIXES else []
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in target.glob(pattern) if p.suffix.lower() in AUDIO_SUFFIXES)
