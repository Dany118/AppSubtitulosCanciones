"""Choosing where torch work runs.

Asking for CUDA on a build that has none fails at model-load time with an
unhelpful message, and the CPU-only wheel is what pip installs by default on
Windows. Rather than fail, the requested device is checked once and quietly
downgraded, with the caller told what actually happened.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def cuda_available() -> bool:
    """True when torch is installed and was built with a usable CUDA device."""
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - a broken driver must not crash the run
        return False


def resolve(requested: str) -> tuple[str, str | None]:
    """Return the device to use and a note when it is not the one asked for.

    ``("cpu", "...")`` means the run continues, just slower -- which is far
    better than refusing to translate at all.
    """
    requested = (requested or "cpu").strip().lower()

    if requested.startswith("cuda") and not cuda_available():
        return "cpu", (
            "CUDA is not available (torch is the CPU-only build or no GPU was "
            "found); running on the CPU instead, which is slower"
        )
    return requested, None
