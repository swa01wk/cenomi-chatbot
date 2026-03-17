"""
Intent parser — canonical example mappings and validation helpers.

Provides:
  EXAMPLE_MAPPINGS   – dict mapping example queries → (domain, sub_intent)
  MALL_INFO_EXAMPLES – subset focused on the mall_info domain
  validate_intent()  – check a (domain, sub_intent) pair is recognised
  parse_intent()     – classify + map a raw query, returning (domain, sub_intent, confidence)
"""

from __future__ import annotations

from intent.query_classifier import (
    ClassifiedIntent,
    classify_query,
    map_to_graph_intent,
)

# ═══════════════════════════════════════════════════════════════════════════
# Canonical example query → (domain, sub_intent) mappings
# ═══════════════════════════════════════════════════════════════════════════

MALL_INFO_EXAMPLES: dict[str, tuple[str, str]] = {
    # ── overview ──────────────────────────────────────────────────────
    "tell me about the mall":          ("mall_info", "overview"),
    "what is this mall like":          ("mall_info", "overview"),
    "what can i find here":            ("mall_info", "overview"),
    "give me an overview of the mall": ("mall_info", "overview"),
    "what does this mall have":        ("mall_info", "overview"),

    # ── family_friendliness ───────────────────────────────────────────
    "is this mall family friendly":    ("mall_info", "family_friendliness"),
    "can i come here with kids":       ("mall_info", "family_friendliness"),
    "is it good for families":         ("mall_info", "family_friendliness"),

    # ── opening_hours ─────────────────────────────────────────────────
    "what time does the mall open":    ("mall_info", "opening_hours"),
    "what are the opening hours":      ("mall_info", "opening_hours"),

    # ── facilities_summary ────────────────────────────────────────────
    "what facilities does the mall have": ("mall_info", "facilities_summary"),
    "mall facilities":                    ("mall_info", "facilities_summary"),

    # ── what_is_available ─────────────────────────────────────────────
    "what shops are in this mall":     ("mall_info", "what_is_available"),
    "what is available at the mall":   ("mall_info", "what_is_available"),
}

EXAMPLE_MAPPINGS: dict[str, tuple[str, str]] = {
    **MALL_INFO_EXAMPLES,

    # ── dining (should NOT capture mall-level queries) ────────────────
    "where can i eat":                 ("dining", "general_dining"),
    "i want a romantic dinner":        ("dining", "romantic_dining"),
    "quick bite before movie":         ("dining", "quick_bite"),
    "coffee near me":                  ("dining", "cafe_recommendation"),

    # ── shopping ──────────────────────────────────────────────────────
    "i want to buy a gift":            ("shopping", "gift_recommendation"),
    "any fashion stores":              ("shopping", "fashion_shopping"),

    # ── entertainment ─────────────────────────────────────────────────
    "what movies are showing":         ("entertainment", "movie_showtime"),
    "is there an arcade":              ("entertainment", "general_entertainment"),

    # ── exploration ───────────────────────────────────────────────────
    "what can i do here":              ("exploration", "open_exploration"),
    "i'm bored":                       ("exploration", "activity_suggestion"),
    "first time at this mall":         ("exploration", "first_visit_guide"),

    # ── services / navigation ─────────────────────────────────────────
    "where is the parking":            ("services", "parking_info"),
    "where is zara":                   ("navigation", "location_query"),

    # ── general (small talk) ──────────────────────────────────────────
    "hello":                           ("general", "general_inquiry"),
    "thanks":                          ("general", "general_inquiry"),
}


# ═══════════════════════════════════════════════════════════════════════════
# Valid intent taxonomy
# ═══════════════════════════════════════════════════════════════════════════

VALID_DOMAINS = {
    "dining", "shopping", "entertainment", "services", "navigation",
    "exploration", "mall_info", "general",
}

VALID_SUB_INTENTS_BY_DOMAIN: dict[str, set[str]] = {
    "mall_info": {
        "overview", "facilities_summary", "opening_hours",
        "family_friendliness", "what_is_available",
    },
    "dining": {
        "general_dining", "romantic_dining", "quick_bite",
        "family_dining", "cafe_recommendation", "dessert_recommendation",
    },
    "shopping": {
        "general_shopping", "gift_recommendation", "fashion_shopping",
    },
    "entertainment": {
        "general_entertainment", "movie_showtime",
    },
    "services": {
        "store_hours", "parking_info", "service_info", "prayer_room",
    },
    "navigation": {
        "location_query",
    },
    "exploration": {
        "open_exploration", "activity_suggestion", "first_visit_guide",
    },
    "general": {
        "general_inquiry",
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Public helpers
# ═══════════════════════════════════════════════════════════════════════════


def validate_intent(domain: str, sub_intent: str) -> bool:
    """Return *True* if the (domain, sub_intent) pair is recognised."""
    allowed = VALID_SUB_INTENTS_BY_DOMAIN.get(domain)
    if allowed is None:
        return False
    return sub_intent in allowed


def parse_intent(
    query: str,
    *,
    history_len: int = 0,
) -> tuple[str, str, float]:
    """
    Classify *query* and return ``(domain, sub_intent, confidence)``.

    Thin convenience wrapper around :func:`classify_query` +
    :func:`map_to_graph_intent`.
    """
    result: ClassifiedIntent = classify_query(
        query, history_len=history_len,
    )
    domain, sub_intent = map_to_graph_intent(result)
    return domain, sub_intent, result.confidence
