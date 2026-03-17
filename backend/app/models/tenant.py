"""
Canonical tenant entity models.

Defines normalized, structured representations for all entity types
within a mall: stores, dining outlets, cinemas, service points,
plus temporal content entities (movies, events, offers).

Each entity is schema-validated, independent of upstream API formats,
and ready for semantic enrichment.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.mall import ContactInfo, LocationRef, OperatingSchedule


# ---------------------------------------------------------------------------
# Retail entities
# ---------------------------------------------------------------------------


class Store(BaseModel):
    """A retail store within the mall."""

    entity_id: str
    entity_type: Literal["store"] = "store"
    name: str
    name_ar: str = ""
    brand: str = ""
    category: str
    subcategory: str = ""
    description: str = ""
    location: LocationRef
    operating_hours: OperatingSchedule = Field(default_factory=OperatingSchedule)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    price_range: Literal["budget", "mid_range", "premium", "luxury"] = "mid_range"
    target_audience: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    accepts_loyalty: bool = False
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dining entities
# ---------------------------------------------------------------------------


class DiningOutlet(BaseModel):
    """A restaurant, cafe, or food outlet within the mall."""

    entity_id: str
    entity_type: Literal["dining"] = "dining"
    name: str
    name_ar: str = ""
    brand: str = ""
    cuisine_type: str = ""
    dining_style: Literal[
        "quick_service",
        "fast_casual",
        "fast_food",
        "casual_dining",
        "fine_dining",
        "cafe",
        "dessert",
        "dessert_cafe",
        "food_court_counter",
    ] = "casual_dining"
    description: str = ""
    location: LocationRef
    operating_hours: OperatingSchedule = Field(default_factory=OperatingSchedule)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    price_range: Literal["budget", "mid_range", "premium", "luxury"] = "mid_range"
    has_kids_menu: bool = False
    has_outdoor_seating: bool = False
    has_private_dining: bool = False
    halal_certified: bool = True
    average_meal_time_minutes: int = 30
    reservations_accepted: bool = False
    delivery_available: bool = False
    target_audience: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    accepts_loyalty: bool = False
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Entertainment entities
# ---------------------------------------------------------------------------


class ScreenInfo(BaseModel):
    """A cinema screen or auditorium."""

    screen_id: str
    name: str
    format: str
    capacity: int = 0


class Cinema(BaseModel):
    """A cinema venue within the mall."""

    entity_id: str
    entity_type: Literal["cinema"] = "cinema"
    name: str
    name_ar: str = ""
    brand: str = ""
    description: str = ""
    location: LocationRef
    operating_hours: OperatingSchedule = Field(default_factory=OperatingSchedule)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    screens: list[ScreenInfo] = Field(default_factory=list)
    formats_available: list[str] = Field(default_factory=list)
    has_vip_lounge: bool = False
    snack_bar: bool = True
    price_range: Literal["budget", "mid_range", "premium", "luxury"] = "mid_range"
    accepts_loyalty: bool = False
    tags: list[str] = Field(default_factory=list)


class Movie(BaseModel):
    """A currently showing or upcoming movie."""

    entity_id: str
    entity_type: Literal["movie"] = "movie"
    title: str
    title_ar: str = ""
    genre: list[str] = Field(default_factory=list)
    rating: str = ""
    duration_minutes: int = 0
    language: str = "English"
    subtitles: str = ""
    synopsis: str = ""
    cinema_entity_id: str = ""
    formats_available: list[str] = Field(default_factory=list)
    showtimes: list[str] = Field(default_factory=list)
    is_new_release: bool = False
    audience_fit: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Service entities
# ---------------------------------------------------------------------------


class ServicePoint(BaseModel):
    """A mall service (valet, information desk, currency exchange, etc.)."""

    entity_id: str
    entity_type: Literal["service"] = "service"
    name: str
    name_ar: str = ""
    service_category: str
    description: str = ""
    location: LocationRef
    operating_hours: OperatingSchedule = Field(default_factory=OperatingSchedule)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    is_free: bool = True
    pricing_notes: str = ""
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Temporal content entities
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """A mall event or engagement activity."""

    entity_id: str
    entity_type: Literal["event"] = "event"
    title: str
    title_ar: str = ""
    event_type: str = ""
    description: str = ""
    location: LocationRef | None = None
    start_date: str = ""
    end_date: str = ""
    recurring: bool = False
    schedule_notes: str = ""
    target_audience: list[str] = Field(default_factory=list)
    is_free: bool = True
    registration_required: bool = False
    tags: list[str] = Field(default_factory=list)


class Offer(BaseModel):
    """A promotion, discount, or special offer."""

    entity_id: str
    entity_type: Literal["offer"] = "offer"
    title: str
    title_ar: str = ""
    offer_type: str = ""
    description: str = ""
    tenant_entity_ids: list[str] = Field(default_factory=list)
    discount_value: str = ""
    minimum_spend: str = ""
    valid_from: str = ""
    valid_until: str = ""
    terms: str = ""
    loyalty_exclusive: bool = False
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-tenant commercial tuning (individual store/outlet boosting)
# ---------------------------------------------------------------------------


class TenantParams(BaseModel):
    """Tunable parameters that influence concierge presentation of a tenant."""

    tenant_id: str
    visibility_weight: float = Field(default=1.0, ge=0.0, le=5.0)
    recommendation_boost: float = Field(default=0.0, ge=-1.0, le=1.0)
    preferred_scenarios: list[str] = Field(default_factory=list)
    tone_hints: list[str] = Field(default_factory=list)
    suppressed: bool = False
    custom: dict = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Mall-level tenant configuration system
#
# Controls chatbot behavior at the mall (tenant) level without touching
# core engine primitives.  Eight parameter groups govern tone, strategy
# selection, retrieval policy, ranking, context composition, response
# shape, clarification policy, and session adaptation.
#
# Mutability tiers:
#   static       — baked into config, changed only on deploy
#   admin_only   — changeable by mall admin via dashboard
#   tenant       — auto-tunable by feedback loop at mall level
#   session      — adjustable within a single user session
# ═══════════════════════════════════════════════════════════════════════════


class Mutability(str, Enum):
    """How freely a parameter can be changed at runtime."""

    STATIC = "static"
    ADMIN_ONLY = "admin_only"
    TENANT = "tenant"
    SESSION = "session"


# ---------------------------------------------------------------------------
# 1. Tone Profile
# ---------------------------------------------------------------------------


class ToneProfile(BaseModel):
    """
    Controls the linguistic personality of the concierge.

    All values 0.0–1.0 where 0.0 is "none / minimal" and 1.0 is "maximum".
    """

    warmth: float = Field(default=0.75, ge=0.0, le=1.0)
    directness: float = Field(default=0.80, ge=0.0, le=1.0)
    formality: float = Field(default=0.35, ge=0.0, le=1.0)
    emoji_level: float = Field(default=0.15, ge=0.0, le=1.0)
    concierge_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    promotional_intensity: float = Field(default=0.30, ge=0.0, le=1.0)
    mall_branding_prominence: float = Field(default=0.40, ge=0.0, le=1.0)
    practicalness_vs_exploratory: float = Field(
        default=0.65, ge=0.0, le=1.0,
        description="0.0 = purely exploratory/inspirational, 1.0 = purely practical/direct",
    )


TONE_MUTABILITY: dict[str, Mutability] = {
    "warmth": Mutability.TENANT,
    "directness": Mutability.TENANT,
    "formality": Mutability.TENANT,
    "emoji_level": Mutability.TENANT,
    "concierge_confidence": Mutability.ADMIN_ONLY,
    "promotional_intensity": Mutability.ADMIN_ONLY,
    "mall_branding_prominence": Mutability.ADMIN_ONLY,
    "practicalness_vs_exploratory": Mutability.SESSION,
}


# ---------------------------------------------------------------------------
# 2. Clarification Policy
# ---------------------------------------------------------------------------


class ClarificationPolicy(BaseModel):
    """
    Governs when the concierge asks follow-up questions vs. just answering.

    Higher ambiguity_tolerance + higher answer_first_bias = AI-Findr behavior
    (answer first, infer boldly, avoid interrogating the user).
    """

    ambiguity_tolerance: float = Field(
        default=0.80, ge=0.0, le=1.0,
        description="How much ambiguity to absorb before asking for clarification",
    )
    answer_first_bias: float = Field(
        default=0.90, ge=0.0, le=1.0,
        description="Preference for providing an answer before asking follow-ups",
    )
    max_followups_before_answer: int = Field(
        default=1, ge=0, le=5,
        description="Hard cap on clarification turns before the bot must answer",
    )
    correction_sensitivity: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description="How aggressively to detect and react to user corrections",
    )
    refinement_bias: float = Field(
        default=0.60, ge=0.0, le=1.0,
        description="Preference for refining the current answer over restarting",
    )
    assumption_aggressiveness: float = Field(
        default=0.75, ge=0.0, le=1.0,
        description="Willingness to fill in gaps with reasonable assumptions",
    )


CLARIFICATION_MUTABILITY: dict[str, Mutability] = {
    "ambiguity_tolerance": Mutability.TENANT,
    "answer_first_bias": Mutability.TENANT,
    "max_followups_before_answer": Mutability.ADMIN_ONLY,
    "correction_sensitivity": Mutability.TENANT,
    "refinement_bias": Mutability.SESSION,
    "assumption_aggressiveness": Mutability.TENANT,
}


# ---------------------------------------------------------------------------
# 3. Strategy Weights
# ---------------------------------------------------------------------------


class StrategyWeights(BaseModel):
    """
    Relative weights for response strategy selection.

    The router uses these to bias strategy choice when multiple strategies
    could fit.  Values are relative — the runtime normalizes them.
    """

    direct_fact: float = Field(default=1.0, ge=0.0, le=5.0)
    shortlist_recommendation: float = Field(default=1.2, ge=0.0, le=5.0)
    mini_itinerary: float = Field(default=0.8, ge=0.0, le=5.0)
    gift_formula: float = Field(default=0.9, ge=0.0, le=5.0)
    family_plan: float = Field(default=1.0, ge=0.0, le=5.0)
    solo_plan: float = Field(default=0.7, ge=0.0, le=5.0)
    movie_plus_food: float = Field(default=0.8, ge=0.0, le=5.0)
    budget_plan: float = Field(default=0.8, ge=0.0, le=5.0)
    fallback_guided_response: float = Field(default=0.5, ge=0.0, le=5.0)


STRATEGY_MUTABILITY: dict[str, Mutability] = {
    "direct_fact": Mutability.STATIC,
    "shortlist_recommendation": Mutability.TENANT,
    "mini_itinerary": Mutability.TENANT,
    "gift_formula": Mutability.TENANT,
    "family_plan": Mutability.TENANT,
    "solo_plan": Mutability.TENANT,
    "movie_plus_food": Mutability.TENANT,
    "budget_plan": Mutability.TENANT,
    "fallback_guided_response": Mutability.STATIC,
}


# ---------------------------------------------------------------------------
# 4. Response Shape Defaults
# ---------------------------------------------------------------------------


class ResponseShapeDefaults(BaseModel):
    """
    Controls the structural shape of generated responses.
    """

    default_response_length: Literal["brief", "moderate", "detailed"] = "moderate"
    default_shortlist_size: int = Field(default=8, ge=1, le=20)
    default_itinerary_steps: int = Field(default=3, ge=2, le=8)
    location_detail_level: Literal["floor_only", "floor_zone", "full_directions"] = "floor_zone"
    suggest_next_step: bool = True
    paragraph_vs_bullets: Literal["paragraph", "bullets", "mixed"] = "mixed"
    recommendation_density: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description="0.0 = sparse (more explanation), 1.0 = dense (more entity mentions)",
    )
    exact_name_usage_bias: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description="How often to use real store/outlet names vs. generic references",
    )


RESPONSE_SHAPE_MUTABILITY: dict[str, Mutability] = {
    "default_response_length": Mutability.SESSION,
    "default_shortlist_size": Mutability.SESSION,
    "default_itinerary_steps": Mutability.SESSION,
    "location_detail_level": Mutability.TENANT,
    "suggest_next_step": Mutability.SESSION,
    "paragraph_vs_bullets": Mutability.SESSION,
    "recommendation_density": Mutability.TENANT,
    "exact_name_usage_bias": Mutability.ADMIN_ONLY,
}


# ---------------------------------------------------------------------------
# 5. Context Composition Weights
# ---------------------------------------------------------------------------


class ContextCompositionWeights(BaseModel):
    """
    Relative priority weights for context-building decisions.

    The context assembler uses these to decide which context blocks
    to inject, how much space to allocate to each, and which signals
    to amplify.
    """

    prioritize_playbooks: float = Field(default=0.80, ge=0.0, le=1.0)
    prioritize_semantic_tags: float = Field(default=0.70, ge=0.0, le=1.0)
    prioritize_current_topic: float = Field(default=0.85, ge=0.0, le=1.0)
    prioritize_current_area: float = Field(default=0.60, ge=0.0, le=1.0)
    prioritize_current_mall: float = Field(default=1.00, ge=0.0, le=1.0)
    prioritize_family_signals: float = Field(default=0.75, ge=0.0, le=1.0)
    prioritize_kid_signals: float = Field(default=0.70, ge=0.0, le=1.0)
    prioritize_budget_signals: float = Field(default=0.65, ge=0.0, le=1.0)
    prioritize_romantic_signals: float = Field(default=0.60, ge=0.0, le=1.0)
    prioritize_exact_entity_examples: float = Field(default=0.75, ge=0.0, le=1.0)


CONTEXT_MUTABILITY: dict[str, Mutability] = {
    "prioritize_playbooks": Mutability.ADMIN_ONLY,
    "prioritize_semantic_tags": Mutability.ADMIN_ONLY,
    "prioritize_current_topic": Mutability.TENANT,
    "prioritize_current_area": Mutability.SESSION,
    "prioritize_current_mall": Mutability.STATIC,
    "prioritize_family_signals": Mutability.TENANT,
    "prioritize_kid_signals": Mutability.TENANT,
    "prioritize_budget_signals": Mutability.TENANT,
    "prioritize_romantic_signals": Mutability.TENANT,
    "prioritize_exact_entity_examples": Mutability.ADMIN_ONLY,
}


# ---------------------------------------------------------------------------
# 6. Retrieval Policy
# ---------------------------------------------------------------------------


class RetrievalPolicy(BaseModel):
    """
    Determines when the pipeline must do an exact lookup vs. relying on
    pre-loaded context and LLM knowledge.

    `require_exact_*` flags force retrieval for factual safety.
    `avoid_retrieval_*` flags let the LLM answer from context alone.
    """

    require_exact_lookup_for_store_timing: bool = True
    require_exact_lookup_for_live_offers: bool = True
    require_exact_lookup_for_events: bool = True
    require_exact_lookup_for_loyalty: bool = True
    avoid_retrieval_for_general_recommendations: bool = True
    avoid_retrieval_for_vague_queries: bool = True


RETRIEVAL_MUTABILITY: dict[str, Mutability] = {
    "require_exact_lookup_for_store_timing": Mutability.ADMIN_ONLY,
    "require_exact_lookup_for_live_offers": Mutability.ADMIN_ONLY,
    "require_exact_lookup_for_events": Mutability.ADMIN_ONLY,
    "require_exact_lookup_for_loyalty": Mutability.ADMIN_ONLY,
    "avoid_retrieval_for_general_recommendations": Mutability.TENANT,
    "avoid_retrieval_for_vague_queries": Mutability.TENANT,
}


# ---------------------------------------------------------------------------
# 7. Ranking Biases
# ---------------------------------------------------------------------------


class RankingBiases(BaseModel):
    """
    Soft biases applied during entity ranking.

    All values 0.0–1.0.  These multiply into the scoring formula
    alongside playbook biases and semantic tag matches.
    """

    favor_budget: float = Field(default=0.50, ge=0.0, le=1.0)
    favor_premium: float = Field(default=0.50, ge=0.0, le=1.0)
    favor_family_friendly: float = Field(default=0.60, ge=0.0, le=1.0)
    favor_kid_friendly: float = Field(default=0.55, ge=0.0, le=1.0)
    favor_couple_friendly: float = Field(default=0.50, ge=0.0, le=1.0)
    favor_proximity: float = Field(default=0.40, ge=0.0, le=1.0)
    favor_anchor_entities: float = Field(default=0.55, ge=0.0, le=1.0)
    favor_diversity_vs_confidence: float = Field(
        default=0.45, ge=0.0, le=1.0,
        description="0.0 = always pick safest, 1.0 = maximize variety in shortlists",
    )


RANKING_MUTABILITY: dict[str, Mutability] = {
    "favor_budget": Mutability.SESSION,
    "favor_premium": Mutability.SESSION,
    "favor_family_friendly": Mutability.SESSION,
    "favor_kid_friendly": Mutability.SESSION,
    "favor_couple_friendly": Mutability.SESSION,
    "favor_proximity": Mutability.SESSION,
    "favor_anchor_entities": Mutability.TENANT,
    "favor_diversity_vs_confidence": Mutability.TENANT,
}


# ---------------------------------------------------------------------------
# 8. Session Adaptation Biases
# ---------------------------------------------------------------------------


class SessionAdaptationBiases(BaseModel):
    """
    Controls how strongly the pipeline preserves session context
    across turns and how aggressively it reinterprets short follow-ups.
    """

    preserve_companions_strongly: float = Field(default=0.85, ge=0.0, le=1.0)
    preserve_budget_strongly: float = Field(default=0.80, ge=0.0, le=1.0)
    preserve_current_area_strongly: float = Field(default=0.60, ge=0.0, le=1.0)
    preserve_active_topic_strongly: float = Field(default=0.75, ge=0.0, le=1.0)
    reinterpret_short_followups_aggressively: float = Field(default=0.80, ge=0.0, le=1.0)
    correction_overrides_previous_scope: float = Field(default=0.90, ge=0.0, le=1.0)


SESSION_ADAPTATION_MUTABILITY: dict[str, Mutability] = {
    "preserve_companions_strongly": Mutability.TENANT,
    "preserve_budget_strongly": Mutability.TENANT,
    "preserve_current_area_strongly": Mutability.SESSION,
    "preserve_active_topic_strongly": Mutability.SESSION,
    "reinterpret_short_followups_aggressively": Mutability.TENANT,
    "correction_overrides_previous_scope": Mutability.ADMIN_ONLY,
}


# ---------------------------------------------------------------------------
# Composed tenant configuration
# ---------------------------------------------------------------------------


class TenantConfig(BaseModel):
    """
    Complete behavioral configuration for a single mall tenant.

    This is the top-level object that the runtime loads and consumes.
    Every field has production-ready defaults — a mall can run with
    zero overrides and still behave like AI Findr.
    """

    mall_id: str = "al_nakheel_plaza_28"
    config_version: str = "1.0.0"
    description: str = ""

    tone: ToneProfile = Field(default_factory=ToneProfile)
    clarification: ClarificationPolicy = Field(default_factory=ClarificationPolicy)
    strategy_weights: StrategyWeights = Field(default_factory=StrategyWeights)
    response_shape: ResponseShapeDefaults = Field(default_factory=ResponseShapeDefaults)
    context_weights: ContextCompositionWeights = Field(default_factory=ContextCompositionWeights)
    retrieval: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    ranking: RankingBiases = Field(default_factory=RankingBiases)
    session_adaptation: SessionAdaptationBiases = Field(default_factory=SessionAdaptationBiases)

    # Per-entity commercial overrides (keyed by entity_id)
    entity_params: dict[str, TenantParams] = Field(default_factory=dict)

    def get_mutability_map(self) -> dict[str, dict[str, Mutability]]:
        """Returns the full mutability map for all parameter groups."""
        return {
            "tone": TONE_MUTABILITY,
            "clarification": CLARIFICATION_MUTABILITY,
            "strategy_weights": STRATEGY_MUTABILITY,
            "response_shape": RESPONSE_SHAPE_MUTABILITY,
            "context_weights": CONTEXT_MUTABILITY,
            "retrieval": RETRIEVAL_MUTABILITY,
            "ranking": RANKING_MUTABILITY,
            "session_adaptation": SESSION_ADAPTATION_MUTABILITY,
        }

    def get_tunable_params(self, tier: Mutability) -> dict[str, list[str]]:
        """List parameters tunable at or below a given mutability tier."""
        hierarchy = [Mutability.SESSION, Mutability.TENANT, Mutability.ADMIN_ONLY, Mutability.STATIC]
        allowed = set(hierarchy[: hierarchy.index(tier) + 1])
        result: dict[str, list[str]] = {}
        for group, mapping in self.get_mutability_map().items():
            keys = [k for k, v in mapping.items() if v in allowed]
            if keys:
                result[group] = keys
        return result

    def param_value(self, group: str, key: str) -> Any:
        """Read a single parameter value by group name and key."""
        section = getattr(self, group, None)
        if section is None:
            raise KeyError(f"Unknown parameter group: {group}")
        return getattr(section, key)
