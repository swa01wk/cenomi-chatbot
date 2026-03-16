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
        "casual_dining",
        "fine_dining",
        "cafe",
        "dessert",
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
    default_shortlist_size: int = Field(default=3, ge=1, le=10)
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
# 9. Response Policy (AI Findr-style behavior controls)
# ---------------------------------------------------------------------------


class ResponsePolicy(BaseModel):
    """
    Governs the high-level response philosophy — how the concierge
    presents information, handles context, and avoids common
    chatbot anti-patterns.

    The ``style_family`` selector activates a named behavior preset.
    Boolean flags fine-tune individual behaviors within that preset.
    Shortlist bounds are policy-level caps that override response_shape
    when the policy is more restrictive.
    """

    style_family: Literal[
        "ai_findr",
        "traditional_concierge",
        "minimal",
        "custom",
    ] = Field(
        default="ai_findr",
        description=(
            "Named behavior preset. 'ai_findr' = curated, conversational, "
            "decision-helping. 'traditional_concierge' = formal, thorough. "
            "'minimal' = bare essentials. 'custom' = rely entirely on flags."
        ),
    )

    always_acknowledge_scene: bool = Field(
        default=True,
        description=(
            "When True the first response in a thread briefly mirrors the "
            "visitor's expressed situation (e.g. 'Since you're here with kids…') "
            "before presenting recommendations."
        ),
    )
    prefer_short_contextual_intro: bool = Field(
        default=True,
        description=(
            "Lead with a one-sentence contextual intro instead of a generic "
            "greeting or preamble.  Keeps the top of the response useful."
        ),
    )

    prefer_curated_shortlist: bool = Field(
        default=True,
        description=(
            "When True the response favors a curated shortlist of best-fit "
            "options over a long directory-style dump."
        ),
    )
    default_shortlist_size: int = Field(
        default=3, ge=1, le=10,
        description="Policy-level default number of items in a shortlist.",
    )
    max_shortlist_size: int = Field(
        default=5, ge=1, le=15,
        description="Hard cap — even if context suggests more, never exceed this.",
    )

    prefer_one_line_reasons: bool = Field(
        default=True,
        description=(
            "Each shortlist item gets at most one line of 'why this fits you' "
            "reasoning, not a paragraph."
        ),
    )
    show_location_only_when_useful: bool = Field(
        default=True,
        description=(
            "Include floor/zone only when it helps the visitor decide or act — "
            "e.g. when comparing proximity, not as boilerplate on every item."
        ),
    )
    deprioritize_floor_zone_details: bool = Field(
        default=True,
        description=(
            "Push floor/zone info to the end of the item line or omit "
            "unless the visitor asked for directions."
        ),
    )

    prefer_followup_narrowing_question: bool = Field(
        default=True,
        description=(
            "End the response with a narrowing question that helps the visitor "
            "decide (e.g. 'Looking for something under 300 SAR?') rather than "
            "an open-ended 'anything else?'."
        ),
    )

    avoid_brochure_tone: bool = Field(
        default=True,
        description=(
            "Suppress corporate-speak like 'wide array of', 'boasts', "
            "'plethora of options'.  Use natural conversational language."
        ),
    )
    avoid_repeating_full_mall_name: bool = Field(
        default=True,
        description=(
            "After the first mention, refer to the mall as 'here' or 'the mall' "
            "instead of repeating the full branded name."
        ),
    )

    prefer_decision_help_over_description: bool = Field(
        default=True,
        description=(
            "When describing an entity, emphasize what helps the visitor choose "
            "(price positioning, audience fit, vibe) over marketing copy."
        ),
    )

    preserve_thread_continuity: bool = Field(
        default=True,
        description=(
            "Responses reference what was discussed earlier in the thread "
            "when it would feel unnatural not to."
        ),
    )
    preserve_topic_on_short_followups: bool = Field(
        default=True,
        description=(
            "Short follow-ups like 'affordable?' or 'something quieter' refine "
            "the active thread instead of starting a new topic."
        ),
    )

    prefer_contextual_grouping: bool = Field(
        default=True,
        description=(
            "Group shortlist items by visitor-relevant dimension (e.g. by vibe, "
            "by budget tier) rather than alphabetically or by entity type."
        ),
    )
    prefer_price_range_when_exact_price_missing: bool = Field(
        default=True,
        description=(
            "If exact pricing is unavailable, provide a rough price range or "
            "tier ('mid-range', '~200 SAR') instead of asking the user or "
            "staying silent about price."
        ),
    )


RESPONSE_POLICY_MUTABILITY: dict[str, Mutability] = {
    "style_family": Mutability.ADMIN_ONLY,
    "always_acknowledge_scene": Mutability.TENANT,
    "prefer_short_contextual_intro": Mutability.TENANT,
    "prefer_curated_shortlist": Mutability.TENANT,
    "default_shortlist_size": Mutability.SESSION,
    "max_shortlist_size": Mutability.ADMIN_ONLY,
    "prefer_one_line_reasons": Mutability.TENANT,
    "show_location_only_when_useful": Mutability.TENANT,
    "deprioritize_floor_zone_details": Mutability.TENANT,
    "prefer_followup_narrowing_question": Mutability.TENANT,
    "avoid_brochure_tone": Mutability.ADMIN_ONLY,
    "avoid_repeating_full_mall_name": Mutability.TENANT,
    "prefer_decision_help_over_description": Mutability.TENANT,
    "preserve_thread_continuity": Mutability.SESSION,
    "preserve_topic_on_short_followups": Mutability.SESSION,
    "prefer_contextual_grouping": Mutability.TENANT,
    "prefer_price_range_when_exact_price_missing": Mutability.TENANT,
}


# ---------------------------------------------------------------------------
# 10. Strategy Behavior Weights (AI Findr scoring signals)
# ---------------------------------------------------------------------------


class StrategyBehaviorWeights(BaseModel):
    """
    Scoring weights used across strategy selection, response generation,
    and candidate ranking to enforce AI Findr-style quality.

    Positive weights *boost* desirable behaviors; negative-signed
    ``*_penalty`` fields *penalize* undesirable patterns.  The runtime
    multiplies these into the scoring formula at each decision point.

    All weights are 0.0–1.0.  Higher = stronger influence.
    """

    # --- positive reward signals ---
    continuity_preservation_weight: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description=(
            "How much to reward a strategy/response that maintains "
            "thread continuity with the prior turn."
        ),
    )
    shortlist_quality_weight: float = Field(
        default=0.80, ge=0.0, le=1.0,
        description=(
            "Reward strategies that produce a tight, curated shortlist "
            "over those that dump many results."
        ),
    )
    scene_acknowledgment_weight: float = Field(
        default=0.75, ge=0.0, le=1.0,
        description=(
            "Reward responses that reflect the visitor's stated scene "
            "(companions, occasion, budget) in the opening line."
        ),
    )
    followup_usefulness_weight: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description=(
            "Reward follow-up questions that narrow the decision space "
            "rather than asking open-ended 'anything else?'."
        ),
    )

    # --- penalty signals (higher = harsher penalty) ---
    overdescription_penalty: float = Field(
        default=0.65, ge=0.0, le=1.0,
        description=(
            "Penalize responses that include unnecessary marketing copy "
            "or entity descriptions beyond what helps the visitor decide."
        ),
    )
    clarification_penalty: float = Field(
        default=0.60, ge=0.0, le=1.0,
        description=(
            "Penalize strategies that require asking the visitor "
            "a clarification question before providing value."
        ),
    )
    wrong_thread_penalty: float = Field(
        default=0.80, ge=0.0, le=1.0,
        description=(
            "Penalize responses that ignore the active topic thread "
            "and address an unrelated domain."
        ),
    )
    brochure_tone_penalty: float = Field(
        default=0.70, ge=0.0, le=1.0,
        description=(
            "Penalize responses whose language resembles corporate "
            "marketing copy rather than natural conversation."
        ),
    )


STRATEGY_BEHAVIOR_MUTABILITY: dict[str, Mutability] = {
    "continuity_preservation_weight": Mutability.TENANT,
    "shortlist_quality_weight": Mutability.TENANT,
    "scene_acknowledgment_weight": Mutability.TENANT,
    "followup_usefulness_weight": Mutability.TENANT,
    "overdescription_penalty": Mutability.TENANT,
    "clarification_penalty": Mutability.TENANT,
    "wrong_thread_penalty": Mutability.ADMIN_ONLY,
    "brochure_tone_penalty": Mutability.ADMIN_ONLY,
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

    mall_id: str = "cenomi_mall_01"
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
    response_policy: ResponsePolicy = Field(default_factory=ResponsePolicy)
    strategy_behavior_weights: StrategyBehaviorWeights = Field(
        default_factory=StrategyBehaviorWeights,
    )

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
            "response_policy": RESPONSE_POLICY_MUTABILITY,
            "strategy_behavior_weights": STRATEGY_BEHAVIOR_MUTABILITY,
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
