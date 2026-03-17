"""
Hybrid intent classifier — rule-based fast path with LLM fallback.

For short queries (< 3 words) we resolve intent from a static lookup
table, avoiding an LLM round-trip entirely.  For longer queries we
apply keyword pattern rules first; only when those produce low
confidence do we delegate to the LLM.

Public API
──────────
  classify_query(query, history_len, scene_context) → ClassifiedIntent
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 0. Query normalization — semantic equivalence table
# ═══════════════════════════════════════════════════════════════════════════

# Maps semantically equivalent query variants to a single canonical form.
# Applied BEFORE classification so all variants produce the same intent path.
# Key: lowercase normalized query substring or full phrase
# Value: canonical form that the classifier handles well

_NORMALIZATION_TABLE: list[tuple[re.Pattern[str], str]] = [
    # Movie lookups — all variants → canonical "what movies are showing"
    (re.compile(
        r"^(show\s+me\s+movies?|movies?\??|what\s+movies?\s*(do\s+we\s+have|are\s+there|are\s+showing|"
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


# ═══════════════════════════════════════════════════════════════════════════
# Intent taxonomy
# ═══════════════════════════════════════════════════════════════════════════


class IntentClass(str, Enum):
    TENANT_LOOKUP = "tenant_lookup"
    SHOPPING = "shopping"
    DINING = "dining"
    EXPERIENCE = "experience"
    ENTERTAINMENT = "entertainment"
    MALL_NAVIGATION = "mall_navigation"
    FACILITY_QUERY = "facility_query"
    MALL_INFO = "mall_info"
    SMALL_TALK = "small_talk"
    CROSS_MALL = "cross_mall"


@dataclass
class ClassifiedIntent:
    """Lightweight result from the hybrid classifier."""

    intent_class: IntentClass
    sub_tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "rule"  # "rule" | "keyword" | "llm"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Short-query lookup table  (query word-count < 3)
# ═══════════════════════════════════════════════════════════════════════════

SHORT_QUERY_INTENTS: dict[str, tuple[IntentClass, list[str]]] = {
    # ── Shopping ──────────────────────────────────────────────────────
    "gift":         (IntentClass.SHOPPING, ["gift", "shopping"]),
    "gifts":        (IntentClass.SHOPPING, ["gift", "shopping"]),
    "shop":         (IntentClass.SHOPPING, ["general"]),
    "shopping":     (IntentClass.SHOPPING, ["general"]),
    "clothes":      (IntentClass.SHOPPING, ["fashion"]),
    "clothing":     (IntentClass.SHOPPING, ["fashion"]),
    "fashion":      (IntentClass.SHOPPING, ["fashion"]),
    "outfits":      (IntentClass.SHOPPING, ["fashion"]),
    "outfit":       (IntentClass.SHOPPING, ["fashion"]),
    "jewelry":      (IntentClass.SHOPPING, ["jewelry"]),
    "jewellery":    (IntentClass.SHOPPING, ["jewelry"]),
    "accessories":  (IntentClass.SHOPPING, ["accessories"]),
    "perfume":      (IntentClass.SHOPPING, ["perfume"]),
    "fragrance":    (IntentClass.SHOPPING, ["perfume"]),
    "shoes":        (IntentClass.SHOPPING, ["shoes"]),
    "bags":         (IntentClass.SHOPPING, ["bags"]),
    "handbags":     (IntentClass.SHOPPING, ["bags"]),
    "offers":       (IntentClass.SHOPPING, ["offers"]),
    "offer":        (IntentClass.SHOPPING, ["offers"]),
    "deals":        (IntentClass.SHOPPING, ["offers"]),
    "deal":         (IntentClass.SHOPPING, ["offers"]),
    "sale":         (IntentClass.SHOPPING, ["offers"]),
    "sales":        (IntentClass.SHOPPING, ["offers"]),
    "discount":     (IntentClass.SHOPPING, ["offers"]),
    "discounts":    (IntentClass.SHOPPING, ["offers"]),
    "promotions":   (IntentClass.SHOPPING, ["offers"]),
    "promotion":    (IntentClass.SHOPPING, ["offers"]),
    "promo":        (IntentClass.SHOPPING, ["offers"]),

    # ── Dining ────────────────────────────────────────────────────────
    "hungry":      (IntentClass.DINING, ["food", "quick_bite"]),
    "food":        (IntentClass.DINING, ["food"]),
    "eat":         (IntentClass.DINING, ["food"]),
    "eating":      (IntentClass.DINING, ["food"]),
    "meal":        (IntentClass.DINING, ["food"]),
    "meals":       (IntentClass.DINING, ["food"]),
    "lunch":       (IntentClass.DINING, ["food"]),
    "dinner":      (IntentClass.DINING, ["food"]),
    "breakfast":   (IntentClass.DINING, ["food"]),
    "snack":       (IntentClass.DINING, ["quick_bite"]),
    "restaurant":  (IntentClass.DINING, ["food"]),
    "restaurants": (IntentClass.DINING, ["food"]),
    "dining":      (IntentClass.DINING, ["food"]),
    "coffee":      (IntentClass.DINING, ["cafe"]),
    "cafe":        (IntentClass.DINING, ["cafe"]),
    "cafes":       (IntentClass.DINING, ["cafe"]),
    "dessert":     (IntentClass.DINING, ["dessert"]),
    "desserts":    (IntentClass.DINING, ["dessert"]),
    "date":        (IntentClass.DINING, ["romantic"]),
    "sushi":       (IntentClass.DINING, ["food"]),
    "pizza":       (IntentClass.DINING, ["food"]),
    "burger":      (IntentClass.DINING, ["food"]),
    "ice cream":   (IntentClass.DINING, ["dessert"]),
    "tea":         (IntentClass.DINING, ["cafe"]),

    # ── Experience / Exploration ──────────────────────────────────────
    "kids":       (IntentClass.EXPERIENCE, ["family", "kids"]),
    "children":   (IntentClass.EXPERIENCE, ["family", "kids"]),
    "family":     (IntentClass.EXPERIENCE, ["family"]),
    "fun":        (IntentClass.EXPERIENCE, ["activity"]),
    "bored":      (IntentClass.EXPERIENCE, ["activity"]),
    "explore":    (IntentClass.EXPERIENCE, ["exploration"]),
    "play":       (IntentClass.EXPERIENCE, ["kids", "activity"]),

    # ── Entertainment ─────────────────────────────────────────────────
    "movie":         (IntentClass.ENTERTAINMENT, ["movie"]),
    "movies":        (IntentClass.ENTERTAINMENT, ["movie"]),
    "cinema":        (IntentClass.ENTERTAINMENT, ["movie"]),
    "cinemas":       (IntentClass.ENTERTAINMENT, ["movie"]),
    "film":          (IntentClass.ENTERTAINMENT, ["movie"]),
    "films":         (IntentClass.ENTERTAINMENT, ["movie"]),
    "showtime":      (IntentClass.ENTERTAINMENT, ["movie"]),
    "showtimes":     (IntentClass.ENTERTAINMENT, ["movie"]),
    "now showing":   (IntentClass.ENTERTAINMENT, ["movie"]),
    "arcade":        (IntentClass.ENTERTAINMENT, ["arcade"]),
    "bowling":       (IntentClass.ENTERTAINMENT, ["arcade"]),
    "gaming":        (IntentClass.ENTERTAINMENT, ["arcade"]),

    # ── Navigation ────────────────────────────────────────────────────
    "where":      (IntentClass.MALL_NAVIGATION, ["location"]),
    "directions": (IntentClass.MALL_NAVIGATION, ["directions"]),
    "floor":      (IntentClass.MALL_NAVIGATION, ["floor_info"]),
    "map":        (IntentClass.MALL_NAVIGATION, ["map"]),
    "gate":       (IntentClass.MALL_NAVIGATION, ["entrance"]),
    "entrance":   (IntentClass.MALL_NAVIGATION, ["entrance"]),
    "exit":       (IntentClass.MALL_NAVIGATION, ["exit"]),

    # ── Facility / Services ───────────────────────────────────────────
    "parking":    (IntentClass.FACILITY_QUERY, ["parking"]),
    "park":       (IntentClass.FACILITY_QUERY, ["parking"]),
    "valet":      (IntentClass.FACILITY_QUERY, ["parking"]),
    "wifi":       (IntentClass.FACILITY_QUERY, ["wifi"]),
    "pray":       (IntentClass.FACILITY_QUERY, ["prayer_room"]),
    "prayer":     (IntentClass.FACILITY_QUERY, ["prayer_room"]),
    "bathroom":   (IntentClass.FACILITY_QUERY, ["restroom"]),
    "restroom":   (IntentClass.FACILITY_QUERY, ["restroom"]),
    "atm":        (IntentClass.FACILITY_QUERY, ["atm"]),
    "lounge":     (IntentClass.FACILITY_QUERY, ["lounge"]),
    "stroller":   (IntentClass.FACILITY_QUERY, ["stroller"]),
    "wheelchair":  (IntentClass.FACILITY_QUERY, ["accessibility"]),
    "lost":       (IntentClass.FACILITY_QUERY, ["lost_and_found"]),
    "hours":      (IntentClass.FACILITY_QUERY, ["store_hours"]),

    # ── Mall info ────────────────────────────────────────────────────
    "mall":       (IntentClass.MALL_INFO, ["overview"]),
    "overview":   (IntentClass.MALL_INFO, ["overview"]),

    # ── Small talk / greetings ────────────────────────────────────────
    "hi":         (IntentClass.SMALL_TALK, ["greeting"]),
    "hello":      (IntentClass.SMALL_TALK, ["greeting"]),
    "hey":        (IntentClass.SMALL_TALK, ["greeting"]),
    "yo":         (IntentClass.SMALL_TALK, ["greeting"]),
    "thanks":     (IntentClass.SMALL_TALK, ["thanks"]),
    "thank you":  (IntentClass.SMALL_TALK, ["thanks"]),
    "bye":        (IntentClass.SMALL_TALK, ["farewell"]),
    "goodbye":    (IntentClass.SMALL_TALK, ["farewell"]),
    "ok":         (IntentClass.SMALL_TALK, ["acknowledgement"]),
    "okay":       (IntentClass.SMALL_TALK, ["acknowledgement"]),
    "sure":       (IntentClass.SMALL_TALK, ["acknowledgement"]),
    "nice":       (IntentClass.SMALL_TALK, ["acknowledgement"]),
}


# ═══════════════════════════════════════════════════════════════════════════
# 2. Keyword pattern rules  (longer queries, ordered by priority)
# ═══════════════════════════════════════════════════════════════════════════

_KeywordRule = tuple[re.Pattern[str], IntentClass, list[str], float]

_KEYWORD_RULES: list[_KeywordRule] = [
    # ── Cross-mall brand search (highest priority — must fire before everything else) ──
    (re.compile(
        r"\b(which (?:of (?:your|the) |cenomi )?malls? (?:has|have|carries|carry|offer[s]?)"
        r"|do(?:es)? (?:any|other) (?:of (?:your|the) |cenomi )?malls?"
        r"|(?:is|are) .{1,40}(?:in|at|available (?:in|at)) (?:both|all|other) (?:malls?|cenomi)"
        r"|(?:also|too) (?:have|carry|offer|available) .{0,30}(?:in|at) (?:other|the other) mall"
        r"|(?:in|at) (?:both|all|other) (?:your )?malls?"
        r"|across (?:all |your |the )?malls?"
        r"|(?:at|in) any (?:cenomi |other )?mall"
        r"|(?:other|the other) (?:cenomi )?malls? (?:also|too)?\s*(?:have|has|carry|offer)"
        r"|does (?:the other|any other|mall of arabia|al nakheel) (?:also )?have"
        r"|also (?:available|found|in|at) (?:other|the other|any) malls?"
        r"|is (?:this|it|that) (?:brand|store|shop) (?:also |too )?(?:in|at|available)"
        r")\b", re.I),
     IntentClass.CROSS_MALL, ["cross_mall_search"], 0.97),

    # ── Mall info (high priority — must precede tenant/shopping/experience) ──
    (re.compile(
        r"\b(tell me about (?:the |this )?mall"
        r"|what (?:is|does) this (?:mall|place) (?:like|have)"
        r"|what can i find here"
        r"|give me an overview"
        r"|overview of (?:the |this )?mall)\b", re.I),
     IntentClass.MALL_INFO, ["overview"], 0.93),
    (re.compile(
        r"\b(family[- ]?friendly"
        r"|good for (?:families|kids|children)"
        r"|come (?:here )?with (?:my )?kids"
        r"|(?:is it|is this mall) (?:suitable |okay |ok )?for (?:families|kids|children))\b", re.I),
     IntentClass.MALL_INFO, ["family_friendliness"], 0.93),
    (re.compile(
        r"\b(what time does the mall open"
        r"|(?:the )?mall(?:'?s)? (?:opening )?hours"
        r"|when (?:does|is) the mall open"
        r"|what are the (?:mall )?opening hours)\b", re.I),
     IntentClass.MALL_INFO, ["opening_hours"], 0.93),
    (re.compile(
        r"\b(what(?:'s| is) available (?:at|in) (?:the |this )?mall"
        r"|what(?:'s| is) (?:in|at) (?:the |this )?mall"
        r"|what (?:shops|stores|restaurants) (?:are|does) (?:the |this )?mall have)\b", re.I),
     IntentClass.MALL_INFO, ["what_is_available"], 0.92),
    (re.compile(
        r"\b(mall facilities|facilities (?:at|in) (?:the |this )?mall"
        r"|what facilities)\b", re.I),
     IntentClass.MALL_INFO, ["facilities_summary"], 0.92),

    # ── Tenant / store-specific look-ups ──────────────────────────────
    (re.compile(r"\b(store hours?|opening hours?|when (?:does|do) .+ open)\b", re.I),
     IntentClass.TENANT_LOOKUP, ["store_hours"], 0.90),
    (re.compile(r"\b(is .+ open|closing time)\b", re.I),
     IntentClass.TENANT_LOOKUP, ["store_hours"], 0.85),

    # ── Navigation ────────────────────────────────────────────────────
    (re.compile(r"\b(where is|how (?:do i|to) (?:get|go|find)|take me to|locate)\b", re.I),
     IntentClass.MALL_NAVIGATION, ["location"], 0.90),
    (re.compile(r"\b(which floor|what floor)\b", re.I),
     IntentClass.MALL_NAVIGATION, ["floor_info"], 0.90),
    (re.compile(r"\b(nearest|closest)\b", re.I),
     IntentClass.MALL_NAVIGATION, ["nearest"], 0.80),

    # ── Facility / services ───────────────────────────────────────────
    (re.compile(r"\b(parking|valet|car park)\b", re.I),
     IntentClass.FACILITY_QUERY, ["parking"], 0.90),
    (re.compile(r"\b(prayer room|mosque|musalla)\b", re.I),
     IntentClass.FACILITY_QUERY, ["prayer_room"], 0.90),
    (re.compile(r"\b(restroom|bathroom|toilet|wc)\b", re.I),
     IntentClass.FACILITY_QUERY, ["restroom"], 0.90),
    (re.compile(r"\b(atm|cash|withdraw)\b", re.I),
     IntentClass.FACILITY_QUERY, ["atm"], 0.85),
    (re.compile(r"\b(wi-?fi|internet)\b", re.I),
     IntentClass.FACILITY_QUERY, ["wifi"], 0.90),
    (re.compile(r"\b(stroller|baby care|nursing)\b", re.I),
     IntentClass.FACILITY_QUERY, ["family_services"], 0.85),
    (re.compile(r"\b(wheelchair|accessibility|disabled|special needs)\b", re.I),
     IntentClass.FACILITY_QUERY, ["accessibility"], 0.85),
    (re.compile(r"\b(lost and found|lost item)\b", re.I),
     IntentClass.FACILITY_QUERY, ["lost_and_found"], 0.90),
    (re.compile(r"\b(lounge|quiet area|seating)\b", re.I),
     IntentClass.FACILITY_QUERY, ["lounge"], 0.80),

    # ── Dining ────────────────────────────────────────────────────────
    (re.compile(r"\b(romantic|date night|candlelight|anniversary)\b", re.I),
     IntentClass.DINING, ["romantic"], 0.90),
    (re.compile(r"\b(quick bite|fast food|grab (?:a |some )?(?:bite|food))\b", re.I),
     IntentClass.DINING, ["quick_bite"], 0.90),
    (re.compile(r"\b(family (?:restaurant|dining|friendly))\b", re.I),
     IntentClass.DINING, ["family_dining"], 0.85),
    (re.compile(r"\b(coffee|cafe|latte|cappuccino|espresso)\b", re.I),
     IntentClass.DINING, ["cafe"], 0.85),
    (re.compile(r"\b(dessert|cake|sweet|pastry|chocolate)\b", re.I),
     IntentClass.DINING, ["dessert"], 0.85),
    (re.compile(r"\b(eat|food|restaurant|hungry|lunch|dinner|breakfast|cuisine|dine|dining)\b", re.I),
     IntentClass.DINING, ["food"], 0.80),

    # ── Shopping ──────────────────────────────────────────────────────
    (re.compile(r"\b(offers?|deals?|discounts?|promotions?|promos?|sales?)\b", re.I),
     IntentClass.SHOPPING, ["offers"], 0.90),
    (re.compile(r"\b(gift|present|souvenir)\b", re.I),
     IntentClass.SHOPPING, ["gift"], 0.85),
    (re.compile(r"\b(fashion|clothes|clothing|wear|outfit)\b", re.I),
     IntentClass.SHOPPING, ["fashion"], 0.85),
    (re.compile(r"\b(jewelry|jewellery|watch|watches|diamond)\b", re.I),
     IntentClass.SHOPPING, ["jewelry"], 0.85),
    (re.compile(r"\b(perfume|fragrance|oud)\b", re.I),
     IntentClass.SHOPPING, ["perfume"], 0.85),
    (re.compile(r"\b(shop|buy|purchase|store|brand|mall)\b", re.I),
     IntentClass.SHOPPING, ["general"], 0.75),

    # ── Entertainment ─────────────────────────────────────────────────
    (re.compile(r"\b(movies?|cinema|film|showtimes?|imax|now\s+showing|what\s+movies?)\b", re.I),
     IntentClass.ENTERTAINMENT, ["movie"], 0.90),
    (re.compile(r"\b(arcade|bowling|karting|trampoline|laser tag)\b", re.I),
     IntentClass.ENTERTAINMENT, ["arcade"], 0.85),
    (re.compile(r"\b(event|concert|show|exhibition|festival)\b", re.I),
     IntentClass.ENTERTAINMENT, ["event"], 0.80),

    # ── Experience / exploration ──────────────────────────────────────
    (re.compile(r"\b(kids?|child|children|play area|kids zone)\b", re.I),
     IntentClass.EXPERIENCE, ["family", "kids"], 0.85),
    (re.compile(r"\b(what can i do|what('s| is) here|things to do|suggest|recommend)\b", re.I),
     IntentClass.EXPERIENCE, ["exploration"], 0.80),
    (re.compile(r"\b(bored|explore|first time|show me around)\b", re.I),
     IntentClass.EXPERIENCE, ["exploration"], 0.80),
    (re.compile(r"\b(fun|activity|activities|something to do)\b", re.I),
     IntentClass.EXPERIENCE, ["activity"], 0.75),

    # ── Small talk (low priority) ─────────────────────────────────────
    (re.compile(r"^(hi|hello|hey|yo|howdy|hiya|greetings)\b", re.I),
     IntentClass.SMALL_TALK, ["greeting"], 0.95),
    (re.compile(r"\b(thanks?|thank you|cheers)\b", re.I),
     IntentClass.SMALL_TALK, ["thanks"], 0.90),
    (re.compile(r"\b(bye|goodbye|see you|later)\b", re.I),
     IntentClass.SMALL_TALK, ["farewell"], 0.90),
]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Intent → graph-state mapping
# ═══════════════════════════════════════════════════════════════════════════

INTENT_TO_DOMAIN: dict[IntentClass, str] = {
    IntentClass.TENANT_LOOKUP:    "services",
    IntentClass.SHOPPING:         "shopping",
    IntentClass.DINING:           "dining",
    IntentClass.EXPERIENCE:       "exploration",
    IntentClass.ENTERTAINMENT:    "entertainment",
    IntentClass.MALL_NAVIGATION:  "navigation",
    IntentClass.FACILITY_QUERY:   "services",
    IntentClass.MALL_INFO:        "mall_info",
    IntentClass.SMALL_TALK:       "general",
    IntentClass.CROSS_MALL:       "cross_mall",
}

_SUB_TAG_TO_SUB_INTENT: dict[str, str] = {
    "gift":            "gift_recommendation",
    "shopping":        "general_shopping",
    "fashion":         "fashion_shopping",
    "general":         "general_shopping",
    "offers":          "offer_details",
    "jewelry":         "jewelry_shopping",
    "perfume":         "perfume_shopping",
    "shoes":           "general_shopping",
    "bags":            "general_shopping",
    "food":            "general_dining",
    "quick_bite":      "quick_bite",
    "romantic":        "romantic_dining",
    "family_dining":   "family_dining",
    "cafe":            "cafe_recommendation",
    "dessert":         "dessert_recommendation",
    "movie":           "movie_showtime",
    "arcade":          "general_entertainment",
    "event":           "event_schedule",
    "exploration":     "open_exploration",
    "activity":        "activity_suggestion",
    "family":          "family_dining",
    "kids":            "activity_suggestion",
    "location":        "location_query",
    "floor_info":      "location_query",
    "nearest":         "location_query",
    "directions":      "location_query",
    "entrance":        "location_query",
    "exit":            "location_query",
    "map":             "location_query",
    "parking":         "parking_info",
    "prayer_room":     "prayer_room",
    "restroom":        "service_info",
    "atm":             "service_info",
    "wifi":            "service_info",
    "lounge":          "service_info",
    "family_services": "service_info",
    "accessibility":   "service_info",
    "lost_and_found":  "service_info",
    "store_hours":     "store_hours",
    "overview":             "overview",
    "facilities_summary":   "facilities_summary",
    "opening_hours":        "opening_hours",
    "family_friendliness":  "family_friendliness",
    "what_is_available":    "what_is_available",
    "greeting":          "general_inquiry",
    "thanks":            "general_inquiry",
    "farewell":          "general_inquiry",
    "acknowledgement":   "general_inquiry",
    "cross_mall_search": "cross_mall_search",
}


def map_to_graph_intent(
    result: ClassifiedIntent,
) -> tuple[str, str]:
    """Convert a ClassifiedIntent to (domain, sub_intent) for the graph state."""
    domain = INTENT_TO_DOMAIN.get(result.intent_class, "general")

    sub_intent = "general_inquiry"
    for tag in result.sub_tags:
        if tag in _SUB_TAG_TO_SUB_INTENT:
            sub_intent = _SUB_TAG_TO_SUB_INTENT[tag]
            break

    return domain, sub_intent


# ═══════════════════════════════════════════════════════════════════════════
# 3b. Known brand / misspelling correction table
# ═══════════════════════════════════════════════════════════════════════════

# Maps likely misspellings or partial brand names to canonical grounded names.
# These are only used as a HINT when the classifier confidence is low —
# never used to hallucinate presence of a brand.

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

# Minimum character diversity ratio for a query to have semantic content
_MIN_UNIQUE_CHAR_RATIO = 0.4
# Minimum word length average for a real query
_MIN_AVG_WORD_LEN = 2.5


def is_likely_unsupported(query: str) -> bool:
    """
    Return True when the query appears to be random, gibberish, or entirely
    outside the supported domain (mall concierge context).

    Conservative — only fires when we're quite confident the input has no
    semantic content. Legitimate short queries ("food?", "hi") should NOT
    trigger this.
    """
    stripped = query.strip()
    if not stripped:
        return True

    # Very short (1-2 chars) and not a known greeting / keyword
    if len(stripped) <= 2:
        return stripped.lower() not in {"hi", "ok", "yo", "go"}

    tokens = stripped.split()

    # All-consonant or near-all-consonant single token = probably random
    if len(tokens) == 1:
        word = tokens[0].lower().rstrip("?!.,")
        vowels = sum(1 for c in word if c in "aeiou")
        if len(word) >= 4 and vowels == 0:
            return True  # e.g. "sdkfj", "rtks"

        # Very low vowel ratio — e.g. "asdf" (1 vowel in 4 chars = 0.25)
        if len(word) >= 4:
            vowel_ratio = vowels / len(word)
            if vowel_ratio < 0.2:
                return True  # "asdf", "qwrt"

        # High ratio of repeated chars
        if len(set(word)) < max(2, len(word) // 3):
            return True  # e.g. "aaaaaa", "zzzzz"

        # Sequential keyboard characters (home-row mashing)
        _KEYBOARD_SEQUENCES = frozenset({
            "asdf", "qwer", "zxcv", "asdfg", "qwerty", "zxcvb",
            "asdff", "qwerr", "hjkl", "uiop",
        })
        if word in _KEYBOARD_SEQUENCES:
            return True

    # Low character diversity across the whole query
    chars = [c for c in stripped.lower() if c.isalpha()]
    if len(chars) >= 6:
        unique_ratio = len(set(chars)) / len(chars)
        if unique_ratio < _MIN_UNIQUE_CHAR_RATIO:
            return True

    return False


# ═══════════════════════════════════════════════════════════════════════════
# 4. Public entry point
# ═══════════════════════════════════════════════════════════════════════════

#: Confidence threshold — at or above this value we skip the LLM.
RULE_CONFIDENCE_THRESHOLD = 0.75


def classify_query(
    query: str,
    *,
    history_len: int = 0,
    scene_context: dict[str, Any] | None = None,
) -> ClassifiedIntent:
    """
    Classify *query* using rules first; return immediately when confidence
    is high enough.  Callers that want LLM fallback should check
    ``result.confidence < RULE_CONFIDENCE_THRESHOLD``.

    Parameters
    ----------
    query:
        The user's (normalized) message text.
    history_len:
        Number of messages already in the conversation.
    scene_context:
        Optional dict with keys like ``active_topic``, ``companions``,
        ``target_person``, ``audience``.
    """
    tokens = query.strip().split()

    # ── A. Very short queries → static lookup ─────────────────────────
    if len(tokens) < 3:
        result = _match_short_query(query, tokens)
        if result is not None:
            # Apply scene-aware refinement for short queries in
            # an active conversation (the ellipsis resolver)
            if scene_context and history_len >= 1:
                result = _refine_with_scene(result, scene_context)
            return result

    # ── B. Pattern/keyword rules ──────────────────────────────────────
    result = _match_keyword_rules(query)
    if result is not None:
        return result

    # ── C. No rule matched — return low-confidence fallback ───────────
    return ClassifiedIntent(
        intent_class=IntentClass.SMALL_TALK,
        sub_tags=["unknown"],
        confidence=0.0,
        source="rule",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════


def _match_short_query(
    raw: str, tokens: list[str],
) -> ClassifiedIntent | None:
    """
    Try the SHORT_QUERY_INTENTS table.

    Attempts full-phrase match first (e.g. "ice cream", "thank you"),
    then falls back to single-token matches.
    """
    lower = raw.strip().lower()

    # Full-phrase hit
    if lower in SHORT_QUERY_INTENTS:
        cls, tags = SHORT_QUERY_INTENTS[lower]
        return ClassifiedIntent(
            intent_class=cls,
            sub_tags=list(tags),
            confidence=0.95,
            source="rule",
        )

    # Per-token hit (first match wins)
    for tok in tokens:
        tok_lower = tok.lower().rstrip("?!.,")
        if tok_lower in SHORT_QUERY_INTENTS:
            cls, tags = SHORT_QUERY_INTENTS[tok_lower]
            return ClassifiedIntent(
                intent_class=cls,
                sub_tags=list(tags),
                confidence=0.90,
                source="rule",
            )

    return None


def _match_keyword_rules(query: str) -> ClassifiedIntent | None:
    """Scan *query* against compiled regex rules, return highest-confidence hit."""
    best: ClassifiedIntent | None = None
    best_conf = 0.0

    for pattern, intent_cls, tags, conf in _KEYWORD_RULES:
        if pattern.search(query):
            if conf > best_conf:
                best = ClassifiedIntent(
                    intent_class=intent_cls,
                    sub_tags=list(tags),
                    confidence=conf,
                    source="keyword",
                )
                best_conf = conf

    return best


# ═══════════════════════════════════════════════════════════════════════════
# 5. Scene-aware refinement  (ellipsis resolver)
# ═══════════════════════════════════════════════════════════════════════════

_CHILD_SIGNALS = frozenset({"son", "daughter", "kids", "children", "child"})
_PARTNER_SIGNALS = frozenset({"girlfriend", "boyfriend", "wife", "husband"})
_CHILD_AUDIENCE = frozenset({"kid_friendly", "family_friendly"})
_COUPLE_AUDIENCE = frozenset({"couple_friendly"})


def _refine_with_scene(
    result: ClassifiedIntent,
    ctx: dict[str, Any],
) -> ClassifiedIntent:
    """
    Refine a short-query classification using the active conversation frame.

    If the scene indicates a target person (child, girlfriend, etc.) or
    audience tags, this adjusts the sub_tags so downstream nodes (intent
    mapping, retrieval, response) resolve the query in context.

    Examples:
        - "food" + target_person=child  →  sub_tags become ["food", "family_dining"]
        - "perfume" + target_person=girlfriend  →  sub_tags become ["perfume", "gift"]
        - "dessert" + audience=[kid_friendly]  →  sub_tags become ["dessert", "family"]
    """
    target = ctx.get("target_person", "")
    companions = set(ctx.get("companions", []))
    audience = set(ctx.get("audience", []))

    is_child_context = (
        target in ("child", "son", "daughter", "kids")
        or bool(companions & _CHILD_SIGNALS)
        or bool(audience & _CHILD_AUDIENCE)
    )
    is_couple_context = (
        target in ("girlfriend", "boyfriend", "wife", "husband")
        or bool(companions & _PARTNER_SIGNALS)
        or bool(audience & _COUPLE_AUDIENCE)
    )

    tags = list(result.sub_tags)
    modified = False

    if is_child_context:
        if result.intent_class == IntentClass.DINING:
            if "family_dining" not in tags:
                tags.append("family_dining")
                modified = True
            if "family" not in tags:
                tags.append("family")
                modified = True
        elif result.intent_class == IntentClass.SHOPPING:
            if "kids" not in tags:
                tags.append("kids")
                modified = True
        elif result.intent_class == IntentClass.ENTERTAINMENT:
            if "kids" not in tags:
                tags.append("kids")
                modified = True
        elif result.intent_class == IntentClass.EXPERIENCE:
            if "kids" not in tags:
                tags.append("kids")
                modified = True

    elif is_couple_context:
        if result.intent_class == IntentClass.DINING:
            if "romantic" not in tags:
                tags.append("romantic")
                modified = True
        elif result.intent_class == IntentClass.SHOPPING:
            if "gift" not in tags:
                tags.append("gift")
                modified = True
        elif result.intent_class == IntentClass.ENTERTAINMENT:
            if "date_spot" not in tags:
                tags.append("date_spot")
                modified = True

    if not modified:
        return result

    return ClassifiedIntent(
        intent_class=result.intent_class,
        sub_tags=tags,
        confidence=result.confidence,
        source=f"{result.source}+scene",
    )
