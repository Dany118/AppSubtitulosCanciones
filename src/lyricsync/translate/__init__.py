"""Turning each lyric line into the reader's own language."""

from .base import Translator
from .local import MarianTranslator
from .sidecar import SidecarTranslator

__all__ = ["Translator", "MarianTranslator", "SidecarTranslator"]
