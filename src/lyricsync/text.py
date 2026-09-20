"""Text normalisation.

Two very different jobs live here:

* :func:`normalize_for_alignment` produces the reduced, romanised token an
  acoustic model can actually match against. It is lossy on purpose.
* :func:`clean_lyric_line` tidies text that will be *displayed*, so the user
  reads the words as written.
"""

from __future__ import annotations

import re
import unicodedata

# Lines that are annotations rather than sung words. Alignment would try to
# find audio for them and drag the surrounding timings off.
_ANNOTATION_RE = re.compile(r"^\s*[\[(](chorus|verse|bridge|intro|outro|refrain|hook|pre-chorus|instrumental|solo)\b[^\])]*[\])]\s*$", re.I)
_BRACKET_NOTE_RE = re.compile(r"[\[(](?:x\d+|\d+x)[\])]", re.I)
_WS_RE = re.compile(r"\s+")
_ALIGN_KEEP_RE = re.compile(r"[^a-z']+")


def clean_lyric_line(text: str) -> str:
    """Normalise whitespace and strip repeat markers, keeping the words intact."""
    text = text.replace(" ", " ")
    text = _BRACKET_NOTE_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def is_annotation(text: str) -> bool:
    """True for section markers like ``[Chorus]`` that should not be aligned."""
    return bool(_ANNOTATION_RE.match(text))


def normalize_for_alignment(word: str) -> str:
    """Reduce a display word to the lowercase a-z/apostrophe form MMS_FA expects.

    Returns an empty string when nothing alignable is left (pure punctuation),
    and the caller is expected to drop that token.
    """
    word = unicodedata.normalize("NFKD", word)
    word = "".join(ch for ch in word if not unicodedata.combining(ch))
    word = word.lower()
    # Curly quotes must survive as apostrophes or "don't" splits into two tokens.
    word = word.replace("’", "'").replace("ʼ", "'")
    word = _ALIGN_KEEP_RE.sub("", word)
    return word.strip("'")


def split_words(text: str) -> list[str]:
    """Split a display line into words, preserving the original spelling."""
    return [w for w in _WS_RE.split(text.strip()) if w]
