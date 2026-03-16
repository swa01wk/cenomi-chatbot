"""
Semantic mall intelligence layer.

Enriches canonical entities with recommendation signals, audience fit,
vibe descriptors, scenario-specific priority scores, shortlist-ready
concierge reasons, topic continuity helpers, and price expectation
metadata.

This layer transforms raw entity data into concierge-ready intelligence
that powers contextual, AI Findr-style recommendations with:
- curated shortlist explanations per audience/scenario
- topic continuity across multi-turn conversations
- non-live pricing guidance
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tag taxonomy — the controlled vocabulary for semantic enrichment
# ---------------------------------------------------------------------------

AUDIENCE_TAGS = [
    "kid_friendly",
    "family_friendly",
    "couple_friendly",
    "teen_friendly",
    "solo_friendly",
    "elderly_friendly",
    "group_friendly",
]

VIBE_TAGS = [
    "romantic",
    "casual",
    "upscale",
    "trendy",
    "cozy",
    "energetic",
    "quiet",
    "playful",
    "elegant",
    "modern",
]

PRICE_TAGS = ["budget", "mid_range", "premium", "luxury"]

DINING_TAGS = [
    "quick_bite",
    "sit_down_dining",
    "dessert_spot",
    "coffee_spot",
    "breakfast_spot",
    "late_night_dining",
]

OCCASION_TAGS = [
    "before_movie",
    "after_movie",
    "date_spot",
    "family_outing",
    "solo_browsing",
    "special_occasion",
    "practical_shopping",
    "quick_visit",
    "relaxed_visit",
    "celebration",
]

GIFT_TAGS = [
    "gift_friendly",
    "girlfriend_gift",
    "wife_gift",
    "husband_gift",
    "child_gift",
    "parent_gift",
    "friend_gift",
    "corporate_gift",
    "self_treat",
]

LOCATION_TAGS = [
    "near_cinema",
    "near_food_court",
    "near_main_entrance",
    "near_parking",
    "near_prayer_room",
    "near_kids_zone",
    "entertainment_anchor",
]

ACTIVITY_TAGS = [
    "child_activity",
    "family_entertainment",
    "interactive_experience",
    "workshop",
]

ALL_SEMANTIC_TAGS: list[str] = (
    AUDIENCE_TAGS
    + VIBE_TAGS
    + PRICE_TAGS
    + DINING_TAGS
    + OCCASION_TAGS
    + GIFT_TAGS
    + LOCATION_TAGS
    + ACTIVITY_TAGS
)


# ---------------------------------------------------------------------------
# Shortlist-ready concierge reasons (Part 1)
#
# One-line explanations the runtime can inject directly into responses.
# Each key targets a specific audience, scenario, or response shape.
# ---------------------------------------------------------------------------


class ConciergeReasons(BaseModel):
    """
    Pre-written one-liner reasons the LLM can use verbatim or paraphrase
    when explaining why an entity appears in a shortlist.

    Keys are intentionally scenario-flavored so the runtime can pick the
    right reason based on the active playbook and detected audience.
    """

    short_reason: str = ""
    kid_reason: str = ""
    budget_reason: str = ""
    couple_reason: str = ""
    family_reason: str = ""
    quick_reason: str = ""
    movie_reason: str = ""
    gift_reason: str = ""
    when_best_used: str = ""
    avoid_when: str = ""
    followup_hint: str = ""


# ---------------------------------------------------------------------------
# Topic continuity helpers (Part 3)
#
# Metadata that helps the runtime preserve topic context across turns,
# detect refinements vs. topic switches, and generate relevant follow-ups.
# ---------------------------------------------------------------------------


class TopicContinuity(BaseModel):
    """
    Continuity metadata for an entity or playbook.

    The runtime uses this to:
    - detect whether a follow-up refines the current topic or switches
    - preserve the right domain/subdomain across turns
    - suggest narrowing follow-up questions
    """

    topic_domain: str = ""
    topic_subdomain: str = ""
    continuity_keywords: list[str] = Field(default_factory=list)
    refinement_keywords: list[str] = Field(default_factory=list)
    likely_followups: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Price expectation metadata (Part 4)
#
# Non-live pricing guidance so the concierge can set expectations
# without promising exact numbers.
# ---------------------------------------------------------------------------


class PriceExpectation(BaseModel):
    """
    Non-live price metadata for stores and dining entities.

    Enables the concierge to answer "how much does X cost?" without
    live pricing feeds by providing positioning, confidence, and
    scripted response hints.
    """

    price_confidence_mode: Literal["exact", "range_only", "unknown"] = "unknown"
    rough_price_positioning: str = ""
    price_expectation_notes: str = ""
    price_question_response_hint: str = ""


# ---------------------------------------------------------------------------
# Semantic profile — enrichment for a single entity
# ---------------------------------------------------------------------------


class SemanticProfile(BaseModel):
    """
    Semantic enrichment attached to a single canonical entity.

    Carries recommendation signals, audience fit, vibe descriptors,
    scenario-specific priority scores, shortlist-ready concierge reasons,
    topic continuity helpers, and price expectation metadata used by the
    concierge to generate contextual, AI Findr-style responses.
    """

    entity_id: str
    entity_type: str

    # Core semantic tags (drawn from taxonomy above)
    semantic_tags: list[str] = Field(default_factory=list)

    # Structured recommendation signals
    audience_fit: list[str] = Field(default_factory=list)
    vibe: list[str] = Field(default_factory=list)
    price_band: str = "mid_range"

    # Contextual fit
    outing_fit: list[str] = Field(default_factory=list)
    gift_fit: list[str] = Field(default_factory=list)
    meal_fit: list[str] = Field(default_factory=list)

    # Recommendation guidance
    when_to_recommend: list[str] = Field(default_factory=list)
    avoid_if: list[str] = Field(default_factory=list)

    # Scenario-specific priority scores (playbook_id or scenario -> 0.0-1.0)
    priority_scores: dict[str, float] = Field(default_factory=dict)

    # Free-text guidance for LLM reasoning
    concierge_notes: str = ""

    # --- Part 1: shortlist-ready concierge reasons ---
    concierge_reasons: ConciergeReasons = Field(default_factory=ConciergeReasons)
    response_fit_tags: list[str] = Field(
        default_factory=list,
        description="Tags indicating which response shapes this entity fits: "
        "e.g. shortlist, itinerary, comparison, standalone",
    )

    # --- Part 3: topic continuity ---
    topic_continuity: TopicContinuity = Field(default_factory=TopicContinuity)

    # --- Part 4: price expectation ---
    price_expectation: PriceExpectation = Field(default_factory=PriceExpectation)


# ---------------------------------------------------------------------------
# Enrichment rules — automatic tagging logic
# ---------------------------------------------------------------------------


class EnrichmentRule(BaseModel):
    """
    Maps entity attributes to semantic tags.

    Used by the enrichment pipeline to auto-tag entities based on
    their canonical properties (category, subcategory, price_range,
    location, features, etc.).
    """

    rule_id: str
    description: str = ""
    match_conditions: dict[str, list[str]] = Field(default_factory=dict)
    apply_tags: list[str] = Field(default_factory=list)
    apply_audience: list[str] = Field(default_factory=list)
    apply_vibe: list[str] = Field(default_factory=list)
    apply_outing_fit: list[str] = Field(default_factory=list)
    apply_gift_fit: list[str] = Field(default_factory=list)
    apply_meal_fit: list[str] = Field(default_factory=list)
    priority_boost: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


class SemanticMallIntelligence(BaseModel):
    """Complete semantic intelligence layer for one mall."""

    mall_id: str
    profiles: list[SemanticProfile] = Field(default_factory=list)
    enrichment_rules: list[EnrichmentRule] = Field(default_factory=list)
    tag_taxonomy_version: str = "1.0"
