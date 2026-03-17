"""
Concierge playbook engine — deterministic itinerary planning.

Provides pre-built, step-by-step concierge plans for common visitor
scenarios.  Each playbook is a sequence of steps with category and
tenant suggestions that the response composer turns into natural
language.

This engine is fully deterministic: no LLM calls, no randomness.
Selection is based on keyword overlap between user intent signals
and playbook tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PlaybookStep:
    """A single step in a concierge itinerary."""

    description: str
    suggested_categories: list[str] = field(default_factory=list)
    suggested_tenants: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Playbook:
    """
    A named concierge plan that maps visitor intent to a concrete
    sequence of mall activities.
    """

    name: str
    intent_tags: list[str] = field(default_factory=list)
    experience_tags: list[str] = field(default_factory=list)
    steps: list[PlaybookStep] = field(default_factory=list)


@dataclass(frozen=True)
class PlaybookPlan:
    """
    The structured plan returned by ``select_playbook``.

    Contains the matched playbook, the match score, and resolved
    tenant details from the mall context for each step.
    """

    playbook: Playbook
    score: float
    resolved_steps: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Built-in playbooks
# ═══════════════════════════════════════════════════════════════════════════


PLAYBOOKS: list[Playbook] = [
    # ── 1. Girlfriend Gift ────────────────────────────────────────────
    Playbook(
        name="girlfriend_gift_playbook",
        intent_tags=[
            "gift", "girlfriend", "romantic_gift", "present",
            "surprise", "her", "partner",
        ],
        experience_tags=[
            "romantic", "premium", "special_occasion", "gift_friendly",
        ],
        steps=[
            PlaybookStep(
                description="Browse jewelry",
                suggested_categories=["Jewelry"],
                suggested_tenants=["L'azurde", "Pandora"],
            ),
            PlaybookStep(
                description="Check perfumes and beauty",
                suggested_categories=["Beauty"],
                suggested_tenants=["Sephora", "Bath & Body Works"],
            ),
            PlaybookStep(
                description="Add a dessert or coffee stop",
                suggested_categories=["Dessert", "Cafe"],
                suggested_tenants=["Baskin-Robbins", "Starbucks"],
            ),
        ],
    ),

    # ── 2. Family Visit ───────────────────────────────────────────────
    Playbook(
        name="family_visit_playbook",
        intent_tags=[
            "family", "kids", "children", "family_outing",
            "family_visit", "family_plan", "kid_friendly",
        ],
        experience_tags=[
            "family_friendly", "kid_friendly", "relaxed",
            "child_activity",
        ],
        steps=[
            PlaybookStep(
                description="Start with kids essentials shopping",
                suggested_categories=["Kids"],
                suggested_tenants=["Mothercare"],
            ),
            PlaybookStep(
                description="Family-friendly dining with kids menu",
                suggested_categories=["Casual Dining", "Fast Casual"],
                suggested_tenants=[
                    "Shake Shack", "The Cheesecake Factory", "Al Romansiah",
                ],
            ),
            PlaybookStep(
                description="Entertainment or activity for the kids",
                suggested_categories=["Cinema", "Entertainment"],
                suggested_tenants=["VOX Cinemas"],
            ),
            PlaybookStep(
                description="Wrap up with a treat",
                suggested_categories=["Dessert"],
                suggested_tenants=["Baskin-Robbins"],
            ),
        ],
    ),

    # ── 3. Quick Lunch ────────────────────────────────────────────────
    Playbook(
        name="quick_lunch_playbook",
        intent_tags=[
            "quick_lunch", "hungry", "quick_bite", "fast_lunch",
            "lunch", "eat_quickly", "food",
        ],
        experience_tags=[
            "quick_bite", "fast_casual", "efficient",
        ],
        steps=[
            PlaybookStep(
                description="Grab a fast-casual meal",
                suggested_categories=["Fast Casual", "Burgers"],
                suggested_tenants=["Shake Shack"],
            ),
            PlaybookStep(
                description="Or sit down for a quick casual bite",
                suggested_categories=["Casual Dining"],
                suggested_tenants=[
                    "Al Romansiah", "The Cheesecake Factory",
                ],
            ),
            PlaybookStep(
                description="Coffee to go",
                suggested_categories=["Cafe"],
                suggested_tenants=["Starbucks"],
            ),
        ],
    ),

    # ── 4. Dessert After Movie ────────────────────────────────────────
    Playbook(
        name="dessert_after_movie",
        intent_tags=[
            "movie", "cinema", "dessert", "after_movie",
            "movie_night", "dinner_and_movie", "movie_food",
        ],
        experience_tags=[
            "after_movie", "dessert_spot", "near_cinema",
            "entertainment",
        ],
        steps=[
            PlaybookStep(
                description="Catch a movie at the cinema",
                suggested_categories=["Cinema"],
                suggested_tenants=["VOX Cinemas"],
            ),
            PlaybookStep(
                description="Grab dessert afterward",
                suggested_categories=["Dessert"],
                suggested_tenants=["Baskin-Robbins"],
            ),
            PlaybookStep(
                description="Or wind down with coffee and cheesecake",
                suggested_categories=["Cafe", "Casual Dining"],
                suggested_tenants=[
                    "Starbucks", "The Cheesecake Factory",
                ],
            ),
        ],
    ),

    # ── 5. Solo Browsing ──────────────────────────────────────────────
    Playbook(
        name="solo_browsing",
        intent_tags=[
            "solo", "browsing", "explore", "alone", "personal",
            "solo_visit", "window_shopping", "me_time",
        ],
        experience_tags=[
            "solo_friendly", "solo_browsing", "relaxed_visit",
            "self_treat", "trendy",
        ],
        steps=[
            PlaybookStep(
                description="Start with a coffee to settle in",
                suggested_categories=["Cafe"],
                suggested_tenants=["Starbucks"],
            ),
            PlaybookStep(
                description="Browse fashion and lifestyle stores",
                suggested_categories=["Fashion", "Sportswear", "Lifestyle"],
                suggested_tenants=["Zara", "H&M", "Nike"],
            ),
            PlaybookStep(
                description="Check out electronics or home decor",
                suggested_categories=["Electronics", "Home"],
                suggested_tenants=["Apple Store", "Pottery Barn"],
            ),
            PlaybookStep(
                description="Reward yourself with a treat",
                suggested_categories=["Dessert", "Cafe"],
                suggested_tenants=["Baskin-Robbins", "Starbucks"],
            ),
        ],
    ),

    # ── 6. Kids Day Out ───────────────────────────────────────────────
    Playbook(
        name="kids_day_out",
        intent_tags=[
            "kids_day_out", "kids_outing", "kids_activity",
            "children_fun", "play", "kids_entertainment",
        ],
        experience_tags=[
            "kid_friendly", "child_activity", "family_entertainment",
            "playful",
        ],
        steps=[
            PlaybookStep(
                description="Head to the entertainment zone for kids activities",
                suggested_categories=["Entertainment"],
                suggested_tenants=["VOX Cinemas"],
            ),
            PlaybookStep(
                description="Kid-friendly lunch",
                suggested_categories=["Fast Casual", "Burgers"],
                suggested_tenants=["Shake Shack"],
            ),
            PlaybookStep(
                description="Pick up something fun for the kids",
                suggested_categories=["Kids"],
                suggested_tenants=["Mothercare"],
            ),
            PlaybookStep(
                description="Ice cream to close the day",
                suggested_categories=["Dessert"],
                suggested_tenants=["Baskin-Robbins"],
            ),
        ],
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# Selection engine
# ═══════════════════════════════════════════════════════════════════════════


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip, split on whitespace and common delimiters."""
    cleaned = text.lower().replace("/", " ").replace(",", " ").replace("_", " ")
    return {tok.strip() for tok in cleaned.split() if tok.strip()}


_MIN_PREFIX = 4


def _shared_prefix_len(a: str, b: str) -> int:
    """Return the length of the common prefix between two strings."""
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def _fuzzy_token_hit(tag_tokens: set[str], input_tokens: set[str]) -> bool:
    """
    Return True if any tag token matches an input token via exact
    equality or a shared prefix of at least ``_MIN_PREFIX`` characters.

    Handles morphological variants like browse/browsing, gift/gifts,
    explore/exploring, etc.
    """
    for tt in tag_tokens:
        for it in input_tokens:
            if tt == it:
                return True
            if _shared_prefix_len(tt, it) >= _MIN_PREFIX:
                return True
    return False


def _score_playbook(
    playbook: Playbook,
    intent_tokens: set[str],
    context_tokens: set[str],
) -> float:
    """
    Score a playbook against intent and context tokens.

    Intent-tag matches are weighted higher (1.0 each) than
    experience-tag matches (0.5 each).  The score is normalized
    by total available tags so no single playbook is advantaged
    by having more tags.
    """
    intent_hits = sum(
        1 for tag in playbook.intent_tags
        if _fuzzy_token_hit(_tokenize(tag), intent_tokens)
    )
    exp_hits = sum(
        1 for tag in playbook.experience_tags
        if _fuzzy_token_hit(_tokenize(tag), context_tokens)
    )

    raw = intent_hits * 1.0 + exp_hits * 0.5
    total_tags = len(playbook.intent_tags) + len(playbook.experience_tags)
    if total_tags == 0:
        return 0.0

    return raw / total_tags


def _resolve_tenants_for_step(
    step: PlaybookStep,
    mall_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Enrich a step with matching entities from the mall context.

    Looks through canonical entity lists (stores, dining, cinemas,
    services) and returns matching entities for the step's suggested
    categories and tenants.
    """
    matched_entities: list[dict[str, Any]] = []
    entity_lists = ["stores", "dining", "cinemas", "services"]

    tenant_names_lower = {t.lower() for t in step.suggested_tenants}
    category_names_lower = {c.lower() for c in step.suggested_categories}

    for list_key in entity_lists:
        for entity in mall_context.get(list_key, []):
            name = (entity.get("name") or "").lower()
            category = (entity.get("category") or "").lower()
            cuisine = (entity.get("cuisine_type") or "").lower()
            dining_style = (entity.get("dining_style") or "").lower()
            subcategory = (entity.get("subcategory") or "").lower()

            name_match = name in tenant_names_lower
            cat_match = (
                category in category_names_lower
                or cuisine in category_names_lower
                or dining_style in category_names_lower
                or subcategory in category_names_lower
            )

            if name_match or cat_match:
                matched_entities.append({
                    "entity_id": entity.get("entity_id", ""),
                    "name": entity.get("name", ""),
                    "category": entity.get("category", ""),
                    "location": entity.get("location", {}),
                })

    return {
        "description": step.description,
        "suggested_categories": step.suggested_categories,
        "suggested_tenants": step.suggested_tenants,
        "matched_entities": matched_entities,
    }


def select_playbook(
    user_intent: str,
    mall_context: dict[str, Any],
    *,
    playbooks: list[Playbook] | None = None,
    threshold: float = 0.1,
) -> PlaybookPlan | None:
    """
    Select the best-matching playbook for a user intent.

    This function is **deterministic** — no LLM, no randomness.

    Parameters
    ----------
    user_intent:
        Free-text or structured intent string from the user
        (e.g. ``"gift for girlfriend"`` or ``"shopping/gift_recommendation"``).
    mall_context:
        The canonical mall data dict (stores, dining, cinemas, etc.).
    playbooks:
        Optional override list.  Defaults to the built-in ``PLAYBOOKS``.
    threshold:
        Minimum normalized score to accept a match.

    Returns
    -------
    PlaybookPlan | None
        A structured plan with resolved entities per step, or ``None``
        if no playbook clears the threshold.
    """
    active_playbooks = playbooks if playbooks is not None else PLAYBOOKS

    intent_tokens = _tokenize(user_intent)
    context_tokens = intent_tokens  # experience tags also match against the full input

    scored: list[tuple[Playbook, float]] = []
    for pb in active_playbooks:
        score = _score_playbook(pb, intent_tokens, context_tokens)
        if score >= threshold:
            scored.append((pb, score))

    if not scored:
        return None

    scored.sort(key=lambda pair: pair[1], reverse=True)
    best_playbook, best_score = scored[0]

    resolved_steps = [
        _resolve_tenants_for_step(step, mall_context)
        for step in best_playbook.steps
    ]

    return PlaybookPlan(
        playbook=best_playbook,
        score=round(best_score, 4),
        resolved_steps=resolved_steps,
    )
