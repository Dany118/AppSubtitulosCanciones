"""Device selection.

pip installs a CPU-only torch by default on Windows, and asking that build
for CUDA fails at model-load time with a message that does not explain
itself. These cover the downgrade that keeps the run going instead.
"""

from __future__ import annotations

import pytest

from lyricsync import device


@pytest.fixture(autouse=True)
def clear_probe_cache():
    device.cuda_available.cache_clear()
    yield
    device.cuda_available.cache_clear()


def test_cuda_request_falls_back_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(device, "cuda_available", lambda: False)
    resolved, note = device.resolve("cuda")

    assert resolved == "cpu"
    assert note and "CUDA is not available" in note


def test_cuda_request_is_honoured_when_available(monkeypatch) -> None:
    monkeypatch.setattr(device, "cuda_available", lambda: True)
    assert device.resolve("cuda") == ("cuda", None)


def test_indexed_cuda_device_is_handled(monkeypatch) -> None:
    monkeypatch.setattr(device, "cuda_available", lambda: True)
    assert device.resolve("cuda:1") == ("cuda:1", None)

    monkeypatch.setattr(device, "cuda_available", lambda: False)
    assert device.resolve("cuda:1")[0] == "cpu"


def test_cpu_request_is_left_alone() -> None:
    assert device.resolve("cpu") == ("cpu", None)


def test_input_is_normalised() -> None:
    assert device.resolve("  CPU  ") == ("cpu", None)
    assert device.resolve("") == ("cpu", None)


def test_missing_torch_reports_no_cuda(monkeypatch) -> None:
    """The probe must answer rather than raise when torch is not installed."""
    import builtins

    real_import = builtins.__import__

    def no_torch(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_torch)
    assert device.cuda_available() is False


def test_a_broken_driver_does_not_crash_the_run(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    class BrokenCuda:
        @staticmethod
        def is_available():
            raise RuntimeError("driver mismatch")

    class FakeTorch:
        cuda = BrokenCuda

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            return FakeTorch
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert device.cuda_available() is False


def test_components_resolve_their_device(monkeypatch) -> None:
    """Every torch-backed stage must go through the same check."""
    monkeypatch.setattr(device, "cuda_available", lambda: False)

    from lyricsync.align.asr import AsrTranscriber
    from lyricsync.align.forced import ForcedAligner
    from lyricsync.translate.local import MarianTranslator

    for component in (MarianTranslator(device="cuda"), ForcedAligner(device="cuda"),
                      AsrTranscriber(device="cuda")):
        assert component.device == "cpu"
        assert component.device_note


def test_pipeline_reports_a_downgrade_once(monkeypatch) -> None:
    """A batch must not repeat the same note on every track."""
    monkeypatch.setattr(device, "cuda_available", lambda: False)

    from lyricsync.pipeline import Config, Pipeline
    from lyricsync.translate.local import MarianTranslator

    pipeline = Pipeline(Config(device="cuda"))
    translator = MarianTranslator(device="cuda")

    assert pipeline._device_note(translator)
    assert pipeline._device_note(translator) == []


def test_doctor_reports_a_cpu_only_torch(monkeypatch) -> None:
    """The diagnostic must name the fix, not just the symptom."""
    from typer.testing import CliRunner

    from lyricsync.cli import app

    monkeypatch.setattr(device, "cuda_available", lambda: False)
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "PyTorch" in result.output
    assert "requirements-gpu.txt" in result.output


def test_doctor_runs_without_torch(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__
    blocked = {"torch", "torchaudio", "transformers", "sentencepiece",
               "demucs", "faster_whisper"}

    def no_ml(name, *args, **kwargs):
        if name in blocked:
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_ml)

    from typer.testing import CliRunner

    from lyricsync.cli import app

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "requirements-translate.txt" in result.output
