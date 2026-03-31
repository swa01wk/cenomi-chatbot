"""
Query preprocessing utilities — minimal pre-flight checks only.

All intent classification, query normalization, and brand detection is now
handled by the LLM classifier in interpret_turn.py.  This module provides
only the minimal pre-flight check needed to skip trivially empty inputs.

Public API
──────────
  is_likely_unsupported(query) → bool
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def is_likely_unsupported(query: str) -> bool:
    """
    Fast pre-flight that returns True ONLY for trivially empty or
    single-character inputs that are not recognisable keywords.

    All other gibberish / off-topic detection is handled by the LLM
    classifier via the ``is_gibberish`` field in the classification prompt,
    which provides more accurate, context-aware analysis without
    false-positives on short but valid queries.
    """
    stripped = query.strip()
    if not stripped:
        return True

    # Very short (1-2 chars) and not a known greeting / keyword
    if len(stripped) <= 2:
        return stripped.lower() not in {"hi", "ok", "yo", "go"}

    return False
