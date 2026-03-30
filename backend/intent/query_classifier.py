"""
Query preprocessing utilities — normalization, brand correction, unsupported detection.

All classification is handled by the LLM in interpret_turn.py.
This module provides pre-processing helpers that run before the LLM call.

Public API
──────────
  normalize_query(query) → str
  normalize_query_with_pattern(query) → (normalized_str, pattern_label)
  maybe_correct_brand(query) → (brand_name | None, confidence)
  is_likely_unsupported(query) → bool
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 0. Query normalization — semantic equivalence table
# ═══════════════════════════════════════════════════════════════════════════

# Maps semantically equivalent query variants to a single canonical form.
# Applied BEFORE classification so all variants produce the same intent path.
# Key: lowercase normalized query substring or full phrase
# Value: canonical form that the classifier handles well

_NORMALIZATION_TABLE: list[tuple[re.Pattern[str], str]] = [
    # Movie lookups — factual variants → canonical "what movies are showing"
    # NOTE: "show me movies" intentionally excluded — it reads as a recommendation
    # request ("show me options"), not a raw listing. It should not be normalized
    # to the factual "what movies are showing" form.
    (re.compile(
        r"^(movies?\??|what\s+movies?\s*(do\s+we\s+have|are\s+there|are\s+showing|"
        r"can\s+i\s+watch|can\s+i\s+see|are\s+on|are\s+available)?|which\s+movies?|"
        r"movie\s+list|list\s+of\s+movies?|movies?\s+available|all\s+movies?|"
        r"now\s+showing|what'?s?\s+(?:playing|on)|what\s+is\s+playing|"
        r"what\s+can\s+i\s+(?:watch|see)(\s+here)?|"
        r"what\s+films?\s+(are\s+(there|showing|on)|do\s+you\s+have)|"
        r"any\s+(?:good\s+)?movies?)\??$", re.I),
     "what movies are showing"),

    # Mall overview — all variants → canonical "tell me about the mall"
    (re.compile(
        r"^(tell\s+me\s+about\s+(the|this)\s+mall|more\s+about\s+(the|this)\s+mall|"
        r"mall\s+info(?:rmation)?|mall\s+overview|overview\s+of\s+(the\s+)?mall|"
        r"about\s+(the|this)\s+mall|about\s+this\s+place|"
        r"what'?s?\s+in\s+(this|the)\s+mall|what\s+does\s+this\s+mall\s+(have|offer)|"
        r"what\s+(is\s+this\s+place|can\s+i\s+find\s+here|is\s+(here|in\s+this\s+mall)))\??$",
        re.I),
     "tell me about the mall"),

    # Brand lookup — all variants → canonical "is [brand] here"
    # Handled by brand-specific regex — these map generic forms only:
    (re.compile(r"^(is\s+(it|that)\s+here|do\s+you\s+have\s+it|is\s+this\s+available\s+here)\??$", re.I),
     "do you have it here"),

    # Dining lookup — "where can i eat" variants
    (re.compile(
        r"^(where\s+can\s+(i|we)\s+eat|places?\s+to\s+eat|"
        r"food\s+options?|where\s+to\s+eat|what\s+to\s+eat|"
        r"i\s+(want\s+to\s+eat|am\s+hungry|'m\s+hungry)|"
        r"looking\s+for\s+(?:some\s+)?food|"
        r"where\s+should\s+(i|we)\s+eat|any\s+(?:good\s+)?restaurants?)\??$", re.I),
     "where can i eat"),

    # Offer/deal queries — all variants → canonical "what offers are available"
    (re.compile(
        r"^(any\s+offers?|what\s+offers?\s*(are\s+(there|available|on|going\s+on))?|"
        r"any\s+deals?|what\s+deals?\s*(are\s+(there|available))?|"
        r"any\s+discounts?|any\s+sales?|any\s+promotions?|"
        r"what\s+(?:promotions?|discounts?|sales?)\s*(are\s+(there|available|on))?|"
        r"what(?:'s|\s+is)\s+on\s+(?:offer|sale))\??$", re.I),
     "what offers are available"),

    # Quick bite variants
    (re.compile(
        r"^(something\s+quick|quick\s+food|quick\s+bite|grab\s+(a\s+)?bite|"
        r"fast\s+food|something\s+fast\s+to\s+eat|light\s+bite|light\s+snack)\??$", re.I),
     "quick bite"),

    # Shopping variants
    (re.compile(
        r"^(show\s+me\s+stores?|what\s+stores?\s+(are\s+here|do\s+you\s+have)|"
        r"shopping\s+options?|where\s+can\s+i\s+shop|"
        r"what\s+shops?\s*(are\s+here|do\s+you\s+have)?)\??$", re.I),
     "show me stores"),

    # Opening hours variants
    (re.compile(
        r"^(what\s+(are\s+(your|the)\s+)?hours?|"
        r"opening\s+hours?|when\s+(are\s+you|do\s+you)\s+open|"
        r"what\s+time\s+do\s+you\s+open|when\s+does\s+(the\s+)?mall\s+open|"
        r"what\s+time\s+does\s+(?:the\s+)?mall\s+close|closing\s+time)\??$", re.I),
     "what are the opening hours"),

    # What can i do here variants
    (re.compile(
        r"^(what\s+can\s+(i|we)\s+do\s+(here)?|things?\s+to\s+do|"
        r"what\s+(to\s+do|is\s+there\s+to\s+do)|what\s+do\s+you\s+have|"
        r"anything\s+(?:fun|interesting|to\s+do)\s+here\??)\??$", re.I),
     "what can i do here"),
]


def normalize_query(query: str) -> str:
    """
    Normalize semantically equivalent queries to a canonical form.

    Reduces phrasing sensitivity so that "show me movies" and
    "what movies do we have" produce the same downstream behavior.

    Returns the normalized form if a match is found, otherwise the original.
    """
    stripped = query.strip()
    for pattern, canonical in _NORMALIZATION_TABLE:
        if pattern.match(stripped):
            logger.debug(
                "Query normalized: %r → %r", stripped, canonical
            )
            return canonical
    return stripped


def normalize_query_with_pattern(query: str) -> tuple[str, str]:
    """
    Normalize a query AND return the canonical_query_pattern name.

    Returns:
        (normalized_query, canonical_query_pattern)
        Where canonical_query_pattern is a label like "movie_lookup",
        "mall_overview", "offer_query", etc., or "" if not normalized.
    """
    # Map canonical forms → pattern labels used in debug output
    _CANONICAL_PATTERN_LABELS: dict[str, str] = {
        "what movies are showing":    "movie_lookup",
        "tell me about the mall":     "mall_overview",
        "do you have it here":        "brand_availability",
        "where can i eat":            "dining_lookup",
        "what offers are available":  "offer_query",
        "quick bite":                 "quick_dining",
        "show me stores":             "shopping_lookup",
        "what are the opening hours": "hours_lookup",
        "what can i do here":         "activity_discovery",
    }
    stripped = query.strip()
    for pattern, canonical in _NORMALIZATION_TABLE:
        if pattern.match(stripped):
            label = _CANONICAL_PATTERN_LABELS.get(canonical, "")
            logger.debug(
                "Query normalized: %r → %r (pattern=%s)", stripped, canonical, label
            )
            return canonical, label
    return stripped, ""

_BRAND_FUZZY_MAP: dict[str, str] = {
    "nkie":      "Nike",
    "niki":      "Nike",
    "niike":     "Nike",
    "nuke":      "Nike",
    "zaara":     "Zara",
    "zarra":     "Zara",
    "zaraa":     "Zara",
    "h&m":       "H&M",
    "hm":        "H&M",
    "starbuks":  "Starbucks",
    "starbacks": "Starbucks",
    "starbeck":  "Starbucks",
    "macdonald": "McDonald's",
    "mcdonalds": "McDonald's",
    "zara":      "Zara",
    "addidas":   "Adidas",
    "adiddas":   "Adidas",
    "adidass":   "Adidas",
}

# Tokens that indicate the user is searching for a brand
_BRAND_SEARCH_SIGNALS = frozenset({
    "is", "do", "does", "have", "here", "where", "find",
    "available", "carry", "stock", "sell",
})


def maybe_correct_brand(query: str) -> tuple[str | None, float]:
    """
    Check if a query looks like a misspelled brand name.

    Returns:
        (corrected_brand_name, confidence) or (None, 0.0) if not a brand misspelling.

    The confidence reflects how certain we are this is the intended brand.
    Callers should use this as a *hint* — always ground-check before asserting presence.
    """
    lower = query.strip().lower().rstrip("?! ")

    # Strip leading question prefixes
    for prefix in (
        "is ", "do you have ", "do you carry ", "where is ", "find ",
        "can i find ", "can you find ", "are you stocking ",
    ):
        if lower.startswith(prefix):
            lower = lower[len(prefix):]
            break

    # Strip trailing location / filler words
    _TRAILING_FILLERS = frozenset({
        "here", "there", "available", "nearby", "at the mall",
        "in the mall", "in this mall", "in here",
    })
    tokens = lower.split()
    while tokens and tokens[-1] in _TRAILING_FILLERS:
        tokens = tokens[:-1]
    lower = " ".join(tokens)

    if lower in _BRAND_FUZZY_MAP:
        return _BRAND_FUZZY_MAP[lower], 0.75

    # Single-token query that looks like a brand attempt
    tokens = lower.split()
    if len(tokens) == 1 and len(lower) >= 3:
        candidate = tokens[0]
        if candidate in _BRAND_FUZZY_MAP:
            return _BRAND_FUZZY_MAP[candidate], 0.75

    return None, 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 3c. Unsupported / random input detector
# ═══════════════════════════════════════════════════════════════════════════

def is_likely_unsupported(query: str) -> bool:
    """
    Fast pre-flight that returns True ONLY for trivially empty or
    single-character inputs that are not recognisable keywords.

    All other gibberish / off-topic detection is now handled by the LLM
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

