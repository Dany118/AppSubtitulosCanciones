"""Offline machine translation with a Marian model.

Helsinki-NLP's opus-mt models are small (a few hundred MB), run comfortably on
a GPU or CPU, and need no API key or network once downloaded. Each line is
translated on its own so the result stays one-to-one with the timestamps.
"""

from __future__ import annotations

from ..device import resolve as resolve_device

# Marian publishes one model per language pair.
MODEL_TEMPLATE = "Helsinki-NLP/opus-mt-{source}-{target}"
BATCH_SIZE = 16
MAX_TOKENS = 256


class TranslationError(RuntimeError):
    pass


class MarianTranslator:
    """Translates lyric lines locally, in batches."""

    name = "marian"

    def __init__(
        self,
        target_language: str = "es",
        source_language: str = "en",
        device: str = "cuda",
        model_name: str | None = None,
    ) -> None:
        self.target_language = target_language
        self.source_language = source_language
        self.device, self.device_note = resolve_device(device)
        self.model_name = model_name or MODEL_TEMPLATE.format(
            source=source_language, target=target_language
        )
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch  # noqa: F401
            from transformers import MarianMTModel, MarianTokenizer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise TranslationError(
                "transformers and torch are required for translation: "
                "pip install -r requirements-translate.txt"
            ) from exc

        try:
            self._tokenizer = MarianTokenizer.from_pretrained(self.model_name)
            self._model = MarianMTModel.from_pretrained(self.model_name).to(self.device)
        except Exception as exc:  # noqa: BLE001 - download or load failure
            raise TranslationError(f"could not load {self.model_name}: {exc}") from exc
        self._model.eval()

    def translate(self, lines: list[str]) -> list[str]:
        import torch

        if not lines:
            return []
        self._load()

        # Blank lines would waste a slot in the batch and can confuse the
        # model, so only real text is sent and the result is reassembled.
        indexed = [(i, text) for i, text in enumerate(lines) if text.strip()]
        output = [""] * len(lines)

        for start in range(0, len(indexed), BATCH_SIZE):
            chunk = indexed[start : start + BATCH_SIZE]
            batch = self._tokenizer(
                [text for _, text in chunk],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=MAX_TOKENS,
            ).to(self.device)

            with torch.inference_mode():
                generated = self._model.generate(**batch, max_new_tokens=MAX_TOKENS, num_beams=4)

            decoded = self._tokenizer.batch_decode(generated, skip_special_tokens=True)
            for (index, _), text in zip(chunk, decoded):
                output[index] = text.strip()

        return output
