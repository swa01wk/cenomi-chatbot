"""
Short query expander — enriches terse queries for better retrieval.

Visitors often type single-word or two-word queries like "gift", "coffee",
or "kids".  These are perfectly clear in a mall context but too sparse for
topic-block and entity matching.

``expand_short_query`` maps these short inputs to richer retrieval phrases
while the original wording is preserved for response tone.

When scene context is provided, short follow-up queries are resolved
against the active conversation frame.  For example, "food?" after
a conversation about a child becomes "food for child" rather than
a generic "food and dining options in the mall".

Usage:
    from llm.prompts.query_expander import expand_short_query

    result = expand_short_query("coffee")
    result.expanded   # "coffee shops in the mall"

    result = expand_short_query("food", scene_context={
        "target_person": "child",
        "companions": ["son"],
        "audience": ["kid_friendly"],
    })
    result.expanded   # "kid-friendly food for child in the mall"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_WORD_COUNT = 3


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    """Outcome of a short-query expansion attempt."""

    original: str
    expanded: str
    was_expanded: bool


_EXPANSION_MAP: dict[str, str] = {
    # ── Shopping ──────────────────────────────────────────────────────
    "gift": "gift ideas and gift shops in the mall",
    "gifts": "gift ideas and gift shops in the mall",
    "present": "gift ideas and gift shops in the mall",
    "shopping": "shopping options and popular stores in the mall",
    "shop": "shopping options and popular stores in the mall",
    "shops": "shopping options and popular stores in the mall",
    "buy": "things to buy and shopping stores in the mall",
    "fashion": "fashion and clothing stores in the mall",
    "clothes": "clothing and fashion stores in the mall",
    "shoes": "shoe stores in the mall",
    "jewelry": "jewelry stores in the mall",
    "perfume": "perfume and fragrance stores in the mall",
    "watch": "watch and accessories stores in the mall",
    "watches": "watch and accessories stores in the mall",
    "bag": "bags and luggage stores in the mall",
    "bags": "bags and luggage stores in the mall",
    "electronics": "electronics and gadget stores in the mall",

    # ── Dining ────────────────────────────────────────────────────────
    "coffee": "coffee shops and cafes in the mall",
    "cafe": "coffee shops and cafes in the mall",
    "food": "food and dining options in the mall",
    "eat": "places to eat and restaurants in the mall",
    "hungry": "places to eat and restaurants in the mall",
    "lunch": "lunch restaurants and quick bites in the mall",
    "dinner": "dinner restaurants in the mall",
    "breakfast": "breakfast spots and cafes in the mall",
    "snack": "snack bars and quick bites in the mall",
    "dessert": "dessert shops and sweet treats in the mall",
    "sweets": "dessert shops and sweet treats in the mall",
    "ice cream": "ice cream and dessert spots in the mall",
    "tea": "tea and coffee shops in the mall",
    "juice": "juice bars and healthy drink spots in the mall",
    "pizza": "pizza restaurants in the mall",
    "burger": "burger restaurants in the mall",
    "sushi": "sushi and Japanese restaurants in the mall",

    # ── People / occasions ────────────────────────────────────────────
    "kids": "family and kids activities in the mall",
    "children": "family and kids activities in the mall",
    "family": "family-friendly activities and dining in the mall",
    "baby": "baby stores and family facilities in the mall",
    "date": "romantic places or experiences in the mall",
    "romantic": "romantic dining and experiences in the mall",
    "anniversary": "romantic dining and gift ideas for anniversary in the mall",
    "birthday": "birthday gift ideas and celebration spots in the mall",
    "friends": "things to do with friends in the mall",

    # ── Entertainment ─────────────────────────────────────────────────
    "movie": "movie showtimes and cinema in the mall",
    "movies": "movie showtimes and cinema in the mall",
    "cinema": "cinema and movie showtimes in the mall",
    "fun": "fun activities and entertainment in the mall",
    "play": "play areas and entertainment in the mall",
    "games": "gaming and entertainment options in the mall",
    "bored": "things to do and entertainment in the mall",
    "entertainment": "entertainment options and activities in the mall",

    # ── Services / navigation ─────────────────────────────────────────
    "pray": "prayer room location in the mall",
    "prayer": "prayer room location in the mall",
    "park": "parking information and location in the mall",
    "parking": "parking information and rates in the mall",
    "valet": "valet parking service in the mall",
    "atm": "ATM and banking services in the mall",
    "wifi": "WiFi and connectivity in the mall",
    "bathroom": "restroom and bathroom locations in the mall",
    "restroom": "restroom and bathroom locations in the mall",
    "lounge": "lounge and rest areas in the mall",
    "exchange": "currency exchange services in the mall",
    "help": "information desk and assistance in the mall",
    "info": "information desk and mall directory",
    "lost": "lost and found and information desk in the mall",

    # ── Exploration ───────────────────────────────────────────────────
    "explore": "things to explore and do in the mall",
    "what": "what to do and see in the mall",
    "suggest": "suggestions for things to do in the mall",
    "recommend": "recommendations for the mall",
    "best": "best things to do and see in the mall",
    "popular": "most popular spots in the mall",
    "new": "new stores and recent openings in the mall",
    "offers": "current offers and promotions in the mall",
    "deals": "current deals and promotions in the mall",
    "sale": "current sales and promotions in the mall",
    "events": "current events happening in the mall",
}

# Phrases that represent disengagement, vagueness, or social filler — they
# must NEVER be scene-expanded to domain queries.  "Nothing much" after a
# greeting must NOT become "quick bite options (quick_visit) in the mall".
_EXPANSION_BLOCKLIST: frozenset[str] = frozenset({
    "nothing much", "not much", "nothing", "nothing really",
    "nothing in particular", "nothing specific",
    "nevermind", "never mind", "nvm", "forget it", "forget about it",
    "fine", "ok fine", "okay fine", "ok", "okay", "alright",
    "whatever", "doesn't matter", "doesnt matter", "it doesn't matter",
    "not interested", "no thanks", "no thank you",
    "just looking", "just browsing",
    "no idea", "i don't know", "i dont know", "not sure",
    "good", "great", "cool", "sure",
    "bye", "goodbye", "see you", "see ya", "thanks", "thank you",
})

# Domain words in expanded queries — if any of these appear in an expansion
# while that domain is excluded, the expansion is suppressed.
_DOMAIN_EXPANSION_WORDS: dict[str, frozenset[str]] = {
    "dining": frozenset({
        "food", "dining", "restaurant", "eat", "bite", "meal", "lunch",
        "dinner", "breakfast", "snack", "cafe", "coffee", "drink",
    }),
    "shopping": frozenset({"shopping", "shop", "store", "stores", "buy"}),
    "entertainment": frozenset({"cinema", "movie", "film", "entertainment"}),
}


# ─────────────────────────────────────────────────────────────────────────────
# Arabic keyword → English retrieval phrase map
#
# Arabic one-word or two-word queries are translated to English retrieval
# phrases so they match the English-language vector corpus and canonical data.
# These are checked BEFORE the English single-word map so Arabic queries
# don't fall through to the "append 'in the mall'" fallback with Arabic text.
# ─────────────────────────────────────────────────────────────────────────────

_ARABIC_EXPANSION_MAP: dict[str, str] = {
    # ── Shopping ──────────────────────────────────────────────────────
    "هدية": "gift ideas and gift shops in the mall",
    "هدايا": "gift ideas and gift shops in the mall",
    "تسوق": "shopping options and popular stores in the mall",
    "متجر": "shopping options and popular stores in the mall",
    "متاجر": "shopping options and popular stores in the mall",
    "ملابس": "clothing and fashion stores in the mall",
    "موضة": "fashion and clothing stores in the mall",
    "أحذية": "shoe stores in the mall",
    "مجوهرات": "jewelry stores in the mall",
    "عطر": "perfume and fragrance stores in the mall",
    "عطور": "perfume and fragrance stores in the mall",
    "ساعة": "watch and accessories stores in the mall",
    "ساعات": "watch and accessories stores in the mall",
    "حقيبة": "bags and luggage stores in the mall",
    "حقائب": "bags and luggage stores in the mall",
    "إلكترونيات": "electronics and gadget stores in the mall",

    # ── Dining ────────────────────────────────────────────────────────
    "قهوة": "coffee shops and cafes in the mall",
    "كافيه": "coffee shops and cafes in the mall",
    "مطعم": "food and dining options in the mall",
    "مطاعم": "food and dining options in the mall",
    "أكل": "places to eat and restaurants in the mall",
    "طعام": "food and dining options in the mall",
    "جائع": "places to eat and restaurants in the mall",
    "غداء": "lunch restaurants and quick bites in the mall",
    "عشاء": "dinner restaurants in the mall",
    "فطور": "breakfast spots and cafes in the mall",
    "حلويات": "dessert shops and sweet treats in the mall",
    "آيس كريم": "ice cream and dessert spots in the mall",
    "شاي": "tea and coffee shops in the mall",
    "عصير": "juice bars and healthy drink spots in the mall",
    "بيتزا": "pizza restaurants in the mall",
    "برغر": "burger restaurants in the mall",
    "سوشي": "sushi and Japanese restaurants in the mall",
    "وجبة سريعة": "fast food and quick bite options in the mall",

    # ── People / occasions ────────────────────────────────────────────
    "أطفال": "family and kids activities in the mall",
    "عائلة": "family-friendly activities and dining in the mall",
    "رضيع": "baby stores and family facilities in the mall",
    "رومانسي": "romantic dining and experiences in the mall",
    "ذكرى سنوية": "romantic dining and gift ideas for anniversary in the mall",
    "عيد ميلاد": "birthday gift ideas and celebration spots in the mall",
    "أصدقاء": "things to do with friends in the mall",

    # ── Entertainment ─────────────────────────────────────────────────
    "فيلم": "movie showtimes and cinema in the mall",
    "أفلام": "movie showtimes and cinema in the mall",
    "سينما": "cinema and movie showtimes in the mall",
    "ترفيه": "entertainment options and activities in the mall",
    "ألعاب": "gaming and entertainment options in the mall",
    "ممل": "things to do and entertainment in the mall",

    # ── Services / navigation ─────────────────────────────────────────
    "صلاة": "prayer room location in the mall",
    "مصلى": "prayer room location in the mall",
    "موقف": "parking information and location in the mall",
    "مواقف": "parking information and rates in the mall",
    "صراف": "ATM and banking services in the mall",
    "صراف آلي": "ATM and banking services in the mall",
    "واي فاي": "WiFi and connectivity in the mall",
    "دورة مياه": "restroom and bathroom locations in the mall",
    "صرافة": "currency exchange services in the mall",
    "مساعدة": "information desk and assistance in the mall",
    "معلومات": "information desk and mall directory",

    # ── Exploration ───────────────────────────────────────────────────
    "استكشاف": "things to explore and do in the mall",
    "اقتراح": "suggestions for things to do in the mall",
    "الأفضل": "best things to do and see in the mall",
    "عروض": "current offers and promotions in the mall",
    "خصومات": "current deals and promotions in the mall",
    "تخفيضات": "current sales and promotions in the mall",
    "فعاليات": "current events happening in the mall",
}

_ARABIC_MULTI_WORD_EXPANSIONS: dict[str, str] = {
    "آيس كريم": "ice cream and dessert spots in the mall",
    "صراف آلي": "ATM and banking services in the mall",
    "دورة مياه": "restroom and bathroom locations in the mall",
    "وجبة سريعة": "fast food and quick bite options in the mall",
    "غداء سريع": "quick lunch and fast casual dining in the mall",
    "وجبة خفيفة": "light meals and healthy quick bites in the mall",
    "ماذا بعد": "next activity or dining option in the mall",
    "ثم ماذا": "next activity or dining option in the mall",
    "بعد ذلك": "next activity or dining option continuing the visit",
    "ماذا الآن": "next step in the mall visit",
    "شيء حلو": "dessert and sweet treat options in the mall",
    "شيء خفيف": "light meals and quick bites in the mall",
    "شيء رومانسي": "romantic dining or gift ideas for a couple",
    "شيء مناسب": "affordable and budget-friendly options in the mall",
    "بعد التسوق": "dining or dessert options after shopping in the mall",
    "بعد الغداء": "dessert or coffee options after lunch in the mall",
    "بعد العشاء": "dessert or coffee options after dinner in the mall",
    "بعد الفيلم": "dining options after the movie in the mall",
    "قبل الفيلم": "quick bite or snack options before the movie in the mall",
}

# Arabic disengagement / filler phrases — never expanded.
_ARABIC_EXPANSION_BLOCKLIST: frozenset[str] = frozenset({
    "لا شيء", "لا يهم", "بخير", "تمام", "حسناً", "مرحبا", "شكراً", "وداعاً",
})

_MULTI_WORD_EXPANSIONS: dict[str, str] = {
    "ice cream": "ice cream and dessert spots in the mall",
    "kids zone": "kids play zone and family activities in the mall",
    "play area": "kids play area and family entertainment in the mall",
    "quick bite": "quick bite restaurants and fast food in the mall",
    "quick lunch": "quick lunch and fast casual dining in the mall",
    "light lunch": "light lunch and healthy quick bites in the mall",
    "light food": "light meals and healthy quick bites in the mall",
    "fine dining": "fine dining and upscale restaurants in the mall",
    "fast food": "fast food and quick bite options in the mall",
    "date night": "romantic date night dining and activities in the mall",
    "something fun": "fun activities and entertainment in the mall",
    "what's new": "new stores and recent openings in the mall",
    "gift ideas": "gift ideas and gift shops in the mall",
    "after that": "next activity or dining option continuing the visit",
    "what next": "next step in the mall visit",
    "and then": "next activity or dining option in the mall",
    "then what": "next activity after the current one in the mall",
    "what else": "other options related to the ongoing visit",
    "for her": "gift or shopping recommendation for girlfriend or wife",
    "for him": "gift or shopping recommendation for boyfriend or husband",
    "something romantic": "romantic dining or gift ideas for a couple",
    "something affordable": "affordable and budget-friendly options in the mall",
    "something light": "light meals and quick bites in the mall",
    "something sweet": "dessert and sweet treat options in the mall",
    "after shopping": "dining or dessert options after shopping in the mall",
    "after lunch": "dessert or coffee options after lunch in the mall",
    "after dinner": "dessert or coffee options after dinner in the mall",
    "after movie": "dining options after the movie in the mall",
    "before movie": "quick bite or snack options before the movie in the mall",
}


def expand_short_query(
    query: str,
    scene_context: dict[str, Any] | None = None,
) -> ExpansionResult:
    """
    Expand a short (<=3 word) user query into a richer retrieval phrase.

    When ``scene_context`` is provided and the query is short, the
    expansion is resolved against the active conversation frame
    (target_person, companions, audience, occasion, goal, visit_plan,
    completed_steps, visit_constraints, active_topic) so that follow-up
    queries like "food?", "after that?", "something light?" carry forward
    the visitor's full context.

    Returns an ``ExpansionResult`` with the original text, the expanded
    form, and whether expansion was applied.  Queries longer than
    ``MAX_WORD_COUNT`` words pass through unchanged.
    """
    cleaned = query.strip()
    if not cleaned:
        return ExpansionResult(original=cleaned, expanded=cleaned, was_expanded=False)

    lower = cleaned.lower()
    lower_stripped_check = lower.rstrip("?!.،، ")

    # Blocklist: disengagement / filler phrases must never be expanded.
    if lower_stripped_check in _EXPANSION_BLOCKLIST or lower_stripped_check in _ARABIC_EXPANSION_BLOCKLIST:
        return ExpansionResult(original=cleaned, expanded=cleaned, was_expanded=False)

    # ── Arabic multi-word exact matches ───────────────────────────────────
    if lower_stripped_check in _ARABIC_MULTI_WORD_EXPANSIONS:
        expanded = _ARABIC_MULTI_WORD_EXPANSIONS[lower_stripped_check]
        logger.debug("Short query expanded (arabic-multi): %r → %r", cleaned, expanded)
        return ExpansionResult(original=cleaned, expanded=expanded, was_expanded=True)

    # ── Arabic single-word exact matches ──────────────────────────────────
    if lower_stripped_check in _ARABIC_EXPANSION_MAP:
        expanded = _ARABIC_EXPANSION_MAP[lower_stripped_check]
        logger.debug("Short query expanded (arabic): %r → %r", cleaned, expanded)
        return ExpansionResult(original=cleaned, expanded=expanded, was_expanded=True)

    words = lower.split()

    # Sequential queries should always use scene expansion regardless of length
    sequential_triggers = {
        "after that", "what next", "and then", "then what",
        "what else", "next",
    }
    lower_stripped = lower.rstrip("?!., ")
    is_sequential = lower_stripped in sequential_triggers

    if len(words) > MAX_WORD_COUNT and not is_sequential:
        return ExpansionResult(original=cleaned, expanded=cleaned, was_expanded=False)

    # Scene-aware expansion for short follow-ups and sequential queries
    if scene_context and (len(words) <= 3 or is_sequential):
        scene_expanded = _expand_with_scene(lower, scene_context)
        if scene_expanded:
            # Suppress expansion if it would re-introduce an excluded domain.
            excluded_domains: list[str] = scene_context.get("excluded_domains") or []
            if excluded_domains:
                expanded_lower = scene_expanded.lower()
                blocked = any(
                    any(w in expanded_lower for w in _DOMAIN_EXPANSION_WORDS.get(dom, frozenset()))
                    for dom in excluded_domains
                )
                if blocked:
                    logger.debug(
                        "Scene expansion suppressed (excluded domain): %r → %r skipped",
                        cleaned, scene_expanded,
                    )
                    scene_expanded = None

        if scene_expanded:
            logger.debug(
                "Short query expanded (scene): %r → %r", cleaned, scene_expanded,
            )
            return ExpansionResult(
                original=cleaned, expanded=scene_expanded, was_expanded=True,
            )

    # Multi-word exact matches first
    if lower in _MULTI_WORD_EXPANSIONS:
        expanded = _MULTI_WORD_EXPANSIONS[lower]
        logger.debug("Short query expanded (multi): %r → %r", cleaned, expanded)
        return ExpansionResult(original=cleaned, expanded=expanded, was_expanded=True)

    # Single-word exact match
    if len(words) == 1 and lower in _EXPANSION_MAP:
        expanded = _EXPANSION_MAP[lower]
        logger.debug("Short query expanded (single): %r → %r", cleaned, expanded)
        return ExpansionResult(original=cleaned, expanded=expanded, was_expanded=True)

    # Try matching the head word for 2-3 word phrases not in the multi-word map
    if len(words) >= 2:
        head = words[0]
        if head in _EXPANSION_MAP:
            expanded = f"{cleaned} in the mall"
            logger.debug("Short query expanded (head): %r → %r", cleaned, expanded)
            return ExpansionResult(original=cleaned, expanded=expanded, was_expanded=True)

    # Fallback for very short unknown queries: append "in the mall"
    if len(words) <= 2 and not _looks_like_sentence(lower):
        expanded = f"{cleaned} in the mall"
        logger.debug("Short query expanded (fallback): %r → %r", cleaned, expanded)
        return ExpansionResult(original=cleaned, expanded=expanded, was_expanded=True)

    return ExpansionResult(original=cleaned, expanded=cleaned, was_expanded=False)


def _looks_like_sentence(text: str) -> bool:
    """Heuristic: if it contains a verb-like pattern, treat it as a full sentence."""
    sentence_patterns = (
        r"\b(where|how|what|when|can|do|does|is|are|i want|i need|show me|tell me)\b"
    )
    return bool(re.search(sentence_patterns, text))


# ═══════════════════════════════════════════════════════════════════════════
# Scene-aware expansion (ellipsis resolver)
# ═══════════════════════════════════════════════════════════════════════════

_PERSON_QUALIFIERS: dict[str, str] = {
    "child": "kid-friendly",
    "son": "kid-friendly",
    "daughter": "kid-friendly",
    "kids": "kid-friendly",
    "girlfriend": "romantic",
    "boyfriend": "romantic",
    "wife": "couple-friendly",
    "husband": "couple-friendly",
    "parent": "classic",
    "self": "",
}

_PERSON_LABEL: dict[str, str] = {
    "child": "child",
    "son": "child",
    "daughter": "child",
    "kids": "kids",
    "girlfriend": "girlfriend",
    "boyfriend": "boyfriend",
    "wife": "partner",
    "husband": "partner",
    "parent": "parents",
    "self": "",
}


def _expand_with_scene(
    query_lower: str,
    ctx: dict[str, Any],
) -> str | None:
    """
    Combine a short query with scene context to resolve ellipsis.

    Returns an enriched query string, or None if no scene signals apply.

    Examples:
        query="food", target_person="child"  → "kid-friendly food for child in the mall"
        query="perfume", target_person="girlfriend"  → "perfume for girlfriend in the mall"
        query="dessert", companions=["kids"]  → "kid-friendly dessert in the mall"
        query="after that", active_topic="shopping"  → "dining after shopping in the mall"
        query="coffee", visit_constraints=["quick"]  → "quick coffee in the mall"
    """
    target = ctx.get("target_person", "")
    companions = ctx.get("companions", [])
    audience = ctx.get("audience", [])
    occasion = ctx.get("occasion", "")
    goal = ctx.get("goal", "")
    previous_need = ctx.get("previous_need", "")
    active_topic = ctx.get("active_topic", "")
    visit_plan = ctx.get("visit_plan", [])
    completed_steps = ctx.get("completed_steps", [])
    visit_constraints = ctx.get("visit_constraints", [])

    stripped = query_lower.rstrip("?!.,").strip()
    if not stripped:
        return None

    # ── Sequential query resolution ───────────────────────────────────
    sequential_triggers = {
        "after that", "what next", "and then", "then what",
        "what else", "next", "after this",
    }
    if stripped in sequential_triggers:
        last_step = (
            completed_steps[-1] if completed_steps
            else active_topic
            if active_topic
            else ""
        )
        next_step = ""
        if visit_plan and completed_steps:
            remaining = [s for s in visit_plan if s not in completed_steps]
            if remaining:
                next_step = remaining[0]

        if next_step:
            expanded = f"{next_step} options in the mall"
        elif last_step:
            # Guess logical next step
            _next_step_map = {
                "shopping": "dining or coffee after shopping",
                "entertainment": "dining after entertainment",
                "dining": "dessert or coffee after dining",
                "coffee": "dessert or snack options",
                "exploration": "dining or shopping options",
            }
            expanded = _next_step_map.get(last_step, f"next activity after {last_step}") + " in the mall"
        else:
            expanded = "next activity or dining option in the mall"
        return expanded

    # ── Constraint-aware expansion ────────────────────────────────────
    if visit_constraints and stripped in (
        "food", "eat", "lunch", "dinner", "meal", "bite", "snack",
    ):
        if "quick" in visit_constraints or "light" in visit_constraints:
            constraint_word = "quick" if "quick" in visit_constraints else "light"
            qualifier_str = f"{constraint_word} "
            if target:
                person_qual = _PERSON_QUALIFIERS.get(target, "")
                person_lbl = _PERSON_LABEL.get(target, target)
                label_str = f" for {person_lbl}" if person_lbl else ""
                if person_qual:
                    return f"{constraint_word} {person_qual} {stripped}{label_str} in the mall"
                return f"{constraint_word} {stripped}{label_str} in the mall"
            return f"{constraint_word} {stripped} options in the mall"

    if not (target or companions or audience or occasion or visit_constraints):
        return None

    parts: list[str] = []

    # Add qualifier from target person (e.g. "kid-friendly")
    qualifier = ""
    person_label = ""
    if target:
        qualifier = _PERSON_QUALIFIERS.get(target, "")
        person_label = _PERSON_LABEL.get(target, target)
    elif companions:
        child_companions = {"son", "daughter", "kids"}
        partner_companions = {"girlfriend", "boyfriend", "wife", "husband"}
        if child_companions & set(companions):
            qualifier = "kid-friendly"
            person_label = "child"
        elif partner_companions & set(companions):
            qualifier = "romantic"
            person_label = companions[-1]
    elif "kid_friendly" in audience or "family_friendly" in audience:
        qualifier = "kid-friendly"
        person_label = "family"

    # Visit constraint prefix
    if visit_constraints and not qualifier:
        if "quick" in visit_constraints:
            qualifier = "quick"
        elif "light" in visit_constraints:
            qualifier = "light"
        elif "affordable" in visit_constraints:
            qualifier = "affordable"

    if qualifier:
        parts.append(qualifier)

    parts.append(stripped)

    if person_label:
        parts.append(f"for {person_label}")

    # Add occasion qualifier if relevant
    if occasion and occasion not in ("casual",):
        parts.append(f"({occasion})")

    parts.append("in the mall")

    expanded = " ".join(parts)
    return expanded
