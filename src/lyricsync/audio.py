"""Audio decoding and vocal isolation.

Isolating the vocal stem before alignment is the single biggest accuracy win
in the whole pipeline: with the instrumental mixed in, the acoustic model
drifts by a few hundred milliseconds; on a clean vocal it lands within tens.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


class AudioError(RuntimeError):
    """Raised when an external audio tool is missing or fails."""


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def decode_mono(path: Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any audio file to a mono float32 array at ``sample_rate``."""
    if not have_ffmpeg():
        raise AudioError("ffmpeg not found on PATH; install it to use alignment")

    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "1", "-ar", str(sample_rate), "-",
    ]
    process = subprocess.run(command, capture_output=True, check=False)
    if process.returncode != 0:
        raise AudioError(f"ffmpeg failed on {path.name}: {process.stderr.decode(errors='replace')[:300]}")
    return np.frombuffer(process.stdout, dtype=np.float32).copy()


def separate_vocals(path: Path, work_dir: Path, *, device: str = "cuda", model: str = "htdemucs") -> Path:
    """Run Demucs and return the path to the isolated vocal stem.

    Uses the CLI rather than the Python API because the CLI surface has been
    stable across Demucs versions while the API has not.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-n", model,
        "--device", device,
        "-o", str(work_dir),
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, check=False)
    if process.returncode != 0:
        raise AudioError(f"demucs failed on {path.name}: {process.stderr.decode(errors='replace')[-400:]}")

    stem = work_dir / model / path.stem / "vocals.wav"
    if not stem.exists():
        matches = list(work_dir.rglob("vocals.wav"))
        if not matches:
            raise AudioError(f"demucs produced no vocal stem for {path.name}")
        stem = matches[0]
    return stem


def frame_rms(samples: np.ndarray, sample_rate: int, window: float = 0.05) -> tuple[np.ndarray, float]:
    """Return per-frame RMS energy and the frame duration in seconds."""
    size = max(1, int(sample_rate * window))
    usable = len(samples) - (len(samples) % size)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32), window
    frames = samples[:usable].reshape(-1, size)
    return np.sqrt(np.mean(np.square(frames), axis=1)), size / sample_rate


def vocal_onset(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    *,
    threshold: float = 0.08,
    sustain: float = 0.15,
) -> float | None:
    """Estimate when singing first starts, in seconds.

    ``threshold`` is a fraction of the track's peak frame energy, and the
    level must hold for ``sustain`` seconds so a single percussive bleed does
    not register as a vocal. Returns None when nothing crosses the threshold.
    """
    rms, frame_seconds = frame_rms(samples, sample_rate)
    if rms.size == 0:
        return None

    peak = float(rms.max())
    if peak <= 0:
        return None

    needed = max(1, int(sustain / frame_seconds))
    loud = rms >= peak * threshold

    run = 0
    for index, is_loud in enumerate(loud):
        if is_loud:
            run += 1
            if run >= needed:
                return (index - run + 1) * frame_seconds
        else:
            run = 0
    return None
