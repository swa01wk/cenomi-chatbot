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
    Derive semantic tags from structured scene/intent state.

    Raw-text phrase matching has been removed — companions, occasions,
    constraints, and other context signals are now extracted by the LLM
    scene extractor and stored in SceneMemory.  This function maps that
    structured state to semantic tags used by the playbook resolver and
    ranking pipeline.

    Returns a deduplicated, ordered list of semantic tags.
    """
    tags: list[str] = []

    # ── 1. Scene companion signals ───────────────────────────────────
    for companion in scene.companions:
        companion_tags = _SCENE_COMPANION_TAG_MAP.get(companion, [])
        for tag in companion_tags:
            if tag not in tags:
                tags.append(tag)

    for detail in scene.companion_details:
        if detail.get("type") == "child":
            for tag in _SCENE_COMPANION_TAG_MAP.get("child", []):
                if tag not in tags:
                    tags.append(tag)

    # ── 2. Scene occasion signals ────────────────────────────────────
    if scene.occasion:
        occasion_tags = _SCENE_OCCASION_TAG_MAP.get(scene.occasion, [])
        for tag in occasion_tags:
            if tag not in tags:
                tags.append(tag)

    # ── 3. Scene constraint signals ──────────────────────────────────
    for constraint in scene.visit_constraints:
        constraint_tags = _SCENE_CONSTRAINT_TAG_MAP.get(constraint, [])
        for tag in constraint_tags:
            if tag not in tags:
                tags.append(tag)

    # ── 4. Budget signals ────────────────────────────────────────────
    if scene.budget:
        budget_tags = _SCENE_BUDGET_TAG_MAP.get(scene.budget, [])
        for tag in budget_tags:
            if tag not in tags:
                tags.append(tag)

    # ── 5. Audience signals ──────────────────────────────────────────
    for aud_tag in scene.audience:
        if aud_tag not in tags:
            tags.append(aud_tag)

    # ── 6. Intent domain/sub-intent signals ─────────────────────────
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

    if "kid_friendly" in signals:
        if any(d.get("type") == "child" for d in scene.companion_details):
            age_info = ", ".join(
                str(d.get("age", "?"))
                for d in scene.companion_details if d.get("type") == "child"
            )
            explanations.append(f"kid_friendly: child companion detected (age {age_info})")
        elif "child" in scene.companions:
            explanations.append("kid_friendly: child companion in scene")

    if "before_movie" in signals:
        explanations.append("before_movie: visitor mentioned going to the cinema first")

    if "budget_sensitive" in signals:
        explanations.append("budget_sensitive: budget constraint from scene/intent")

    if "near_cinema" in signals:
        explanations.append("near_cinema: visitor wants options close to the cinema")

    if "quick_stop" in signals:
        explanations.append("quick_stop: visitor indicated time constraint or quick visit")

    if "shopping_mission" in signals:
        explanations.append("shopping_mission: visitor has explicit shopping intent")

    if "romantic" in signals:
        explanations.append("romantic: couple companion or occasion detected")

    if "gift_friendly" in signals:
        explanations.append("gift_friendly: gift context from scene/intent")

    return explanations
