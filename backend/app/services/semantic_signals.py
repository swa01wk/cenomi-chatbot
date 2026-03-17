"""
Semantic Signal Extractor — maps user text + scene context to semantic tags.

This module provides the query-side semantic intelligence layer.
Given a user message and the current scene, it produces a list of semantic
tags that describe the intent, constraints, and context of the visit.

These signals are used by:
  - resolve_playbooks: to pick the right playbook
  - rank_and_dedupe: to score entity suitability
  - compose_context: to filter/boost relevant entities
"""

from __future__ import annotations

from app.models.state import SceneMemory


# ── Phrase-to-tag mappings ───────────────────────────────────────────────────

# Ordered longest-first so more specific phrases take precedence
_PHRASE_TAG_MAP: list[tuple[str, list[str]]] = [
    # Child/family signals
    ("with my kid", ["kid_friendly", "family_friendly", "parent_friendly", "family_visit"]),
    ("with my kids", ["kid_friendly", "family_friendly", "parent_friendly", "family_visit"]),
    ("with my child", ["kid_friendly", "family_friendly", "parent_friendly", "family_visit"]),
    ("with my son", ["kid_friendly", "family_friendly", "parent_friendly", "family_visit"]),
    ("with my daughter", ["kid_friendly", "family_friendly", "parent_friendly", "family_visit"]),
    ("yr old", ["kid_friendly", "family_friendly", "parent_friendly", "child_present"]),
    ("year old", ["kid_friendly", "family_friendly", "parent_friendly", "child_present"]),
    ("toddler", ["kid_friendly", "family_friendly", "stroller_friendly", "child_present"]),
    ("baby", ["kid_friendly", "family_friendly", "stroller_friendly", "child_present"]),
    ("infant", ["kid_friendly", "family_friendly", "stroller_friendly", "child_present"]),
    # Romantic / couple signals
    ("with my girlfriend", ["couple_friendly", "romantic", "gift_friendly"]),
    ("with my boyfriend", ["couple_friendly", "romantic", "gift_friendly"]),
    ("with my wife", ["couple_friendly", "romantic", "gift_friendly"]),
    ("with my husband", ["couple_friendly", "romantic", "gift_friendly"]),
    ("date night", ["romantic", "couple_friendly", "special_occasion"]),
    ("anniversary", ["romantic", "couple_friendly", "special_occasion", "gift_friendly"]),
    ("romantic", ["romantic", "couple_friendly"]),
    # Gift signals
    ("quick gift", ["gift_friendly", "quick_stop"]),
    ("last minute gift", ["gift_friendly", "quick_stop"]),
    ("looking for a gift", ["gift_friendly"]),
    ("buy a gift", ["gift_friendly", "shopping_mission"]),
    ("gift for", ["gift_friendly"]),
    ("present for", ["gift_friendly"]),
    # Movie / cinema signals
    ("before the movie", ["before_movie", "time_sensitive", "near_cinema"]),
    ("before movie", ["before_movie", "time_sensitive", "near_cinema"]),
    ("after the movie", ["after_movie", "near_cinema", "casual"]),
    ("after movie", ["after_movie", "near_cinema", "casual"]),
    ("going to cinema", ["near_cinema", "before_movie"]),
    ("watching a movie", ["near_cinema"]),
    ("catching a movie", ["near_cinema"]),
    # Quick / fast signals
    ("something quick", ["quick_stop", "low_commitment"]),
    ("something quicker", ["quick_stop", "low_commitment"]),
    ("not much time", ["quick_stop", "time_sensitive"]),
    ("in a hurry", ["quick_stop", "time_sensitive"]),
    ("quick stop", ["quick_stop", "low_commitment"]),
    ("quick snack", ["quick_stop", "quick_bite"]),
    ("quick bite", ["quick_stop", "quick_bite"]),
    # Budget / price signals
    ("not expensive", ["budget_sensitive", "value_shopping"]),
    ("not too expensive", ["budget_sensitive", "value_shopping"]),
    ("not too pricey", ["budget_sensitive", "value_shopping"]),
    ("more affordable", ["budget_sensitive", "value_shopping"]),
    ("something affordable", ["budget_sensitive", "value_shopping"]),
    ("budget friendly", ["budget_sensitive", "value_shopping"]),
    ("cheap", ["budget_sensitive", "value_shopping"]),
    ("affordable", ["budget_sensitive"]),
    # Proximity signals
    ("closer to cinema", ["near_cinema"]),
    ("closer to the cinema", ["near_cinema"]),
    ("near the cinema", ["near_cinema"]),
    ("near cinema", ["near_cinema"]),
    ("close to cinema", ["near_cinema"]),
    # Shopping intent
    ("want to do shopping", ["shopping_mission", "practical_shopping"]),
    ("want to shop", ["shopping_mission", "practical_shopping"]),
    ("do some shopping", ["shopping_mission", "practical_shopping"]),
    ("go shopping", ["shopping_mission", "practical_shopping"]),
    ("shopping", ["shopping_mission"]),
    # Solo signals
    ("by myself", ["solo_friendly"]),
    ("alone", ["solo_friendly"]),
    ("on my own", ["solo_friendly"]),
    # Browsing / casual
    ("just browsing", ["easy_browse", "low_commitment", "casual"]),
    ("just looking", ["easy_browse", "low_commitment", "casual"]),
    ("explore", ["easy_browse", "casual"]),
    # Self-care
    ("treat myself", ["self_care", "reward_stop"]),
    ("pamper", ["self_care"]),
    ("spa", ["self_care"]),
    ("beauty", ["self_care"]),
    # Food-specific
    ("grab a bite", ["quick_bite", "quick_stop"]),
    ("grab lunch", ["quick_bite"]),
    ("grab food", ["quick_bite"]),
    ("hungry", ["dining"]),
    ("snack", ["quick_bite", "quick_stop"]),
    ("dessert", ["dessert_spot"]),
    ("coffee", ["coffee_spot"]),
]

# Scene-field to tag mappings (based on scene state rather than message text)
_SCENE_COMPANION_TAG_MAP: dict[str, list[str]] = {
    "child": ["kid_friendly", "family_friendly", "parent_friendly", "child_relief_anchor"],
    "kids": ["kid_friendly", "family_friendly", "parent_friendly"],
    "son": ["kid_friendly", "family_friendly", "parent_friendly"],
    "daughter": ["kid_friendly", "family_friendly", "parent_friendly"],
    "family": ["family_friendly", "family_visit"],
    "girlfriend": ["couple_friendly", "romantic"],
    "boyfriend": ["couple_friendly", "romantic"],
    "wife": ["couple_friendly", "romantic"],
    "husband": ["couple_friendly", "romantic"],
    "solo": ["solo_friendly"],
    "friends": ["group_friendly", "casual"],
}

_SCENE_OCCASION_TAG_MAP: dict[str, list[str]] = {
    "before_movie": ["before_movie", "time_sensitive", "near_cinema", "quick_stop"],
    "after_movie": ["after_movie", "near_cinema", "casual", "reward_stop"],
    "birthday": ["special_occasion", "gift_friendly", "celebration"],
    "anniversary": ["special_occasion", "romantic", "gift_friendly"],
    "date": ["romantic", "couple_friendly", "date_spot"],
    "casual": ["casual", "easy_browse"],
    "quick_visit": ["quick_stop", "low_commitment", "time_sensitive"],
}

_SCENE_CONSTRAINT_TAG_MAP: dict[str, list[str]] = {
    "quick": ["quick_stop", "time_sensitive"],
    "quick_stop_preferred": ["quick_stop", "time_sensitive", "low_commitment"],
    "time_sensitive": ["time_sensitive", "quick_stop"],
    "near_cinema_preferred": ["near_cinema"],
    "kid_friendly_required": ["kid_friendly", "family_friendly"],
    "budget_sensitive": ["budget_sensitive", "value_shopping"],
    "light": ["quick_bite"],
    "healthy": ["healthy"],
    "affordable": ["budget_sensitive"],
}

_SCENE_BUDGET_TAG_MAP: dict[str, list[str]] = {
    "budget": ["budget_sensitive", "value_shopping"],
    "mid_range": ["mid_range"],
    "premium": ["premium"],
    "luxury": ["premium", "luxury"],
}

_INTENT_DOMAIN_TAG_MAP: dict[str, list[str]] = {
    "shopping": ["shopping_mission"],
    "dining": ["dining"],
    "entertainment": ["entertainment"],
    "services": ["services"],
}

_INTENT_SUBINTENT_TAG_MAP: dict[str, list[str]] = {
    "gift_recommendation": ["gift_friendly", "shopping_mission"],
    "romantic_dining": ["romantic", "couple_friendly", "special_occasion"],
    "family_dining": ["family_friendly", "kid_friendly"],
    "quick_bite": ["quick_stop", "quick_bite"],
    "cafe_recommendation": ["coffee_spot"],
    "dessert_recommendation": ["dessert_spot"],
    "movie_showtime": ["near_cinema", "entertainment"],
    "fashion_shopping": ["practical_shopping", "fashion_forward"],
    "perfume_shopping": ["gift_friendly"],
    "activity_suggestion": ["entertainment"],
    "first_visit_guide": ["easy_browse"],
}


def extract_semantic_signals(
    msg: str,
    scene: SceneMemory,
    intent_domain: str = "",
    intent_sub_intent: str = "",
) -> list[str]:
    """
    Extract semantic tags from a user message + scene context.

    Returns a deduplicated, ordered list of semantic tags that describe:
    - Who the visitor is (family, couple, solo)
    - What they want (shopping, dining, entertainment)
    - How they want it (quick, affordable, near cinema)
    - Why they're here (gift, occasion, casual)

    These tags are used by the playbook resolver and ranking pipeline.
    """
    tags: list[str] = []
    msg_lower = msg.lower()

    # ── 1. Text phrase matching ──────────────────────────────────────
    for phrase, phrase_tags in _PHRASE_TAG_MAP:
        if phrase in msg_lower:
            for tag in phrase_tags:
                if tag not in tags:
                    tags.append(tag)

    # ── 2. Scene companion signals ───────────────────────────────────
    for companion in scene.companions:
        companion_tags = _SCENE_COMPANION_TAG_MAP.get(companion, [])
        for tag in companion_tags:
            if tag not in tags:
                tags.append(tag)

    # Also check companion_details for child type
    for detail in scene.companion_details:
        if detail.get("type") == "child":
            for tag in _SCENE_COMPANION_TAG_MAP.get("child", []):
                if tag not in tags:
                    tags.append(tag)

    # ── 3. Scene occasion signals ────────────────────────────────────
    if scene.occasion:
        occasion_tags = _SCENE_OCCASION_TAG_MAP.get(scene.occasion, [])
        for tag in occasion_tags:
            if tag not in tags:
                tags.append(tag)

    # ── 4. Scene constraint signals ──────────────────────────────────
    for constraint in scene.visit_constraints:
        constraint_tags = _SCENE_CONSTRAINT_TAG_MAP.get(constraint, [])
        for tag in constraint_tags:
            if tag not in tags:
                tags.append(tag)

    # ── 5. Budget signals ────────────────────────────────────────────
    if scene.budget:
        budget_tags = _SCENE_BUDGET_TAG_MAP.get(scene.budget, [])
        for tag in budget_tags:
            if tag not in tags:
                tags.append(tag)

    # ── 6. Audience signals ──────────────────────────────────────────
    for aud_tag in scene.audience:
        if aud_tag not in tags:
            tags.append(aud_tag)

    # ── 7. Intent domain/sub-intent signals ─────────────────────────
    if intent_domain:
        for tag in _INTENT_DOMAIN_TAG_MAP.get(intent_domain, []):
            if tag not in tags:
                tags.append(tag)
    if intent_sub_intent:
        for tag in _INTENT_SUBINTENT_TAG_MAP.get(intent_sub_intent, []):
            if tag not in tags:
                tags.append(tag)

    return tags


def build_semantic_match_explanations(
    signals: list[str],
    scene: SceneMemory,
    msg: str,
) -> list[str]:
    """
    Produce human-readable explanations of why each major signal was inferred.
    Used for debug output.
    """
    explanations: list[str] = []
    msg_lower = msg.lower()

    if "kid_friendly" in signals:
        if any(d.get("type") == "child" for d in scene.companion_details):
            age_info = ", ".join(
                str(d.get("age", "?"))
                for d in scene.companion_details if d.get("type") == "child"
            )
            explanations.append(f"kid_friendly: child companion detected (age {age_info})")
        elif "child" in scene.companions:
            explanations.append("kid_friendly: child companion mentioned")
        elif "yr old" in msg_lower or "year old" in msg_lower:
            explanations.append(f"kid_friendly: age phrase detected in message")

    if "before_movie" in signals:
        explanations.append("before_movie: visitor mentioned going to the cinema first")

    if "budget_sensitive" in signals:
        if any(p in msg_lower for p in ("not expensive", "affordable", "budget", "cheap")):
            explanations.append("budget_sensitive: price constraint phrase detected")

    if "near_cinema" in signals:
        explanations.append("near_cinema: visitor wants options close to the cinema")

    if "quick_stop" in signals:
        explanations.append("quick_stop: visitor indicated time constraint or quick visit")

    if "shopping_mission" in signals:
        explanations.append("shopping_mission: visitor has explicit shopping intent")

    if "romantic" in signals:
        explanations.append("romantic: couple companion or occasion detected")

    if "gift_friendly" in signals:
        if "gift" in msg_lower or "present" in msg_lower:
            explanations.append("gift_friendly: visitor explicitly mentioned a gift")
        else:
            explanations.append("gift_friendly: inferred from partner companion context")

    return explanations
