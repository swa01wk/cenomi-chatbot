"""
Semantic mall intelligence layer.

Enriches canonical entities with recommendation signals, audience fit,
vibe descriptors, and scenario-specific priority scores.

This layer transforms raw entity data into concierge-ready intelligence
that powers contextual, AI Findr-style recommendations.
"""

from __future__ import annotations

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
# Semantic profile — enrichment for a single entity
# ---------------------------------------------------------------------------


class SemanticProfile(BaseModel):
    """
    Semantic enrichment attached to a single canonical entity.

    Carries recommendation signals, audience fit, vibe descriptors,
    and scenario-specific priority scores used by the concierge
    to generate contextual, AI Findr-style responses.
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
