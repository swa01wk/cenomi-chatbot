"""
Language detection utility — lightweight Arabic/English detection.

Uses Unicode range checking (U+0600–U+06FF) with no external dependencies.
Falls back to "en" for mixed or unknown scripts.
"""

from __future__ import annotations

from typing import Literal

LANGUAGE = Literal["ar", "en"]

# Arabic Unicode block: U+0600–U+06FF
_ARABIC_RANGE_START = 0x0600
_ARABIC_RANGE_END = 0x06FF

# Minimum fraction of characters that must be Arabic for the text to be
# classified as Arabic.  This threshold filters out English messages that
# happen to include a single Arabic character (e.g. a name or transliteration).
_ARABIC_THRESHOLD = 0.25


def detect_language(text: str) -> LANGUAGE:
    """
    Detect whether the given text is Arabic or English.

    Counts the fraction of alphabetic/script characters that fall within the
    Arabic Unicode block (U+0600–U+06FF).  If the fraction exceeds
    ``_ARABIC_THRESHOLD``, the text is classified as Arabic; otherwise English.

    Parameters
    ----------
    text:
        The raw user message to classify.

    Returns
    -------
    "ar" if the message appears to be primarily Arabic, "en" otherwise.
    """
    if not text or not text.strip():
        return "en"

    # Collect non-whitespace, non-digit characters that are either Latin alpha or Arabic script.
    script_chars = [c for c in text if _is_arabic(c) or (c.isalpha() and not c.isdigit())]
    if not script_chars:
        return "en"

    arabic_count = sum(1 for c in script_chars if _is_arabic(c))
    ratio = arabic_count / len(script_chars)
    return "ar" if ratio >= _ARABIC_THRESHOLD else "en"


def _is_arabic(char: str) -> bool:
    """Return True if the character is in the Arabic Unicode block."""
    cp = ord(char)
    return _ARABIC_RANGE_START <= cp <= _ARABIC_RANGE_END
