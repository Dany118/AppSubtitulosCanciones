"""Forced alignment: we already know the words, we only need the times.

This is the accurate path. Because the transcript is given, the model never
has to guess what is being sung -- it only decides where each word sits. That
is why the pipeline works hard to find real lyrics before falling back to ASR.

Uses torchaudio's MMS_FA pipeline, which is a CTC aligner giving word-level
spans. Heavy imports are deferred so the CLI starts instantly without torch.
"""

from __future__ import annotations

import numpy as np

from ..device import resolve as resolve_device
from ..models import Word
from ..text import normalize_for_alignment


class AlignmentError(RuntimeError):
    pass


class ForcedAligner:
    """Aligns a known word sequence against audio, producing per-word spans."""

    # MMS_FA is a 16 kHz pipeline; callers decode to this rate up front so no
    # resampling is needed and no model has to be loaded to find out.
    SAMPLE_RATE = 16_000

    def __init__(self, device: str = "cuda") -> None:
        self.device, self.device_note = resolve_device(device)
        self._model = None
        self._tokenizer = None
        self._aligner = None
        self._sample_rate = self.SAMPLE_RATE

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: F401
            import torchaudio
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise AlignmentError(
                "torch/torchaudio are required for alignment: pip install '.[align]'"
            ) from exc

        bundle = torchaudio.pipelines.MMS_FA
        self._sample_rate = bundle.sample_rate
        self._model = bundle.get_model().to(self.device)
        self._tokenizer = bundle.get_tokenizer()
        self._aligner = bundle.get_aligner()

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def align(self, samples: np.ndarray, words: list[str], sample_rate: int = SAMPLE_RATE) -> list[Word]:
        """Return a :class:`Word` per input word, in the same order.

        Words that normalise to nothing (standalone punctuation) are dropped,
        so the caller must not assume a one-to-one index mapping; the returned
        words keep their original spelling for display.
        """
        import torch

        self._load()
        if sample_rate != self._sample_rate:
            raise AlignmentError(
                f"expected {self._sample_rate} Hz audio, got {sample_rate} Hz"
            )

        # Keep the display spelling next to the reduced form the model needs.
        pairs = [(word, normalize_for_alignment(word)) for word in words]
        pairs = [(display, token) for display, token in pairs if token]
        if not pairs:
            raise AlignmentError("no alignable words in the transcript")

        waveform = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).unsqueeze(0)
        tokens = self._tokenizer([token for _, token in pairs])

        emission, spans = self._emit(waveform, tokens)
        ratio = waveform.size(1) / emission.size(1) / self._sample_rate

        aligned: list[Word] = []
        for (display, _), span in zip(pairs, spans):
            start = span[0].start * ratio
            end = span[-1].end * ratio
            aligned.append(Word(display, float(start), float(max(end, start))))
        return aligned

    def _emit(self, waveform, tokens):
        """Run the model, retrying on CPU if the GPU runs out of memory.

        A full-length song is a single long sequence; on a smaller card that
        can exceed VRAM, and falling back is much better than failing a batch.
        """
        import torch

        try:
            with torch.inference_mode():
                emission, _ = self._model(waveform.to(self.device))
                return emission.cpu(), self._aligner(emission[0].cpu(), tokens)
        except torch.cuda.OutOfMemoryError:  # pragma: no cover - hardware dependent
            torch.cuda.empty_cache()
            self._model = self._model.to("cpu")
            self.device = "cpu"
            with torch.inference_mode():
                emission, _ = self._model(waveform)
                return emission, self._aligner(emission[0], tokens)
