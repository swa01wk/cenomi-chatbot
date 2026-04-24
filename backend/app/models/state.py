"""
LangGraph state model — the single source of truth flowing through the graph.

All nodes read from and write to this state. Nodes return partial dicts;
LangGraph merges them between steps.

Design principles:
  - Pydantic sub-models for each logical group (intent, scene, playbook, etc.)
  - Annotated list reducers for accumulating fields (messages, node_trace, warnings)
  - Every field has a sensible default — the graph runs with zero initialization
  - Single-mall scoped: mode is always "single_mall" for this iteration
"""

from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Conversation
# ═══════════════════════════════════════════════════════════════════════════


class Message(BaseModel):
    """A single conversation message in the rolling history."""

    role: Literal["user", "assistant", "system"]
    content: str
    turn_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Interpreted Intent
# ═══════════════════════════════════════════════════════════════════════════

class MessageKind(str, Enum):
    """
    Classification of the current conversational turn.

    Inherits from ``str`` so all downstream string comparisons
    (e.g. ``intent.message_kind == "acknowledgement"``) continue to work
    unchanged — no modifications needed in route_flow, update_scene_memory,
    generate_response, or any other consumer.
    """
    FRESH_REQUEST         = "fresh_request"
    REFINEMENT            = "refinement"
    CORRECTION            = "correction"
    TOPIC_SWITCH          = "topic_switch"
    FOLLOWUP              = "followup"
    CONSTRAINT_REFINEMENT = "constraint_refinement"
    CONTEXT_SETTING       = "context_setting"   # user declaring role/companions/occasion
    GREETING              = "greeting"
    SMALLTALK             = "smalltalk"
    EMOTIONAL             = "emotional"         # frustration, boredom, mood expression
    DISENGAGEMENT         = "disengagement"     # "forget it", "whatever", giving up
    CATEGORY_NEGATION     = "category_negation" # "not food", "no cinema"
    ACKNOWLEDGEMENT       = "acknowledgement"   # "okay", "wait", "hmm" — no action needed
    COMPANION_CORRECTION  = "companion_correction"  # "I don't have kids", "I'm alone"
    # Previously regex-only in smalltalk.py — now LLM-classified
    CRISIS                = "crisis"     # self-harm / suicidal language
    IDENTITY              = "identity"   # "who are you", "are you a bot", "what can you do"
    HOWRU                 = "howru"      # "how are you", "how's it going"
    THANKS                = "thanks"     # "thanks", "thank you", "shukran"
    FAREWELL              = "farewell"   # "bye", "goodbye", "see you"


# Backward-compatible type alias — existing annotations like ``message_kind: MESSAGE_KIND``
# now refer to the MessageKind enum class.
MESSAGE_KIND = MessageKind

# Smalltalk routing gate — used by is_smalltalk() and the smalltalk node.
# Any turn whose message_kind is in this set bypasses route_flow and goes
# directly to the smalltalk node.
SMALLTALK_KINDS: frozenset[MessageKind] = frozenset({
    MessageKind.GREETING,
    MessageKind.HOWRU,
    MessageKind.THANKS,
    MessageKind.FAREWELL,
    MessageKind.EMOTIONAL,
    MessageKind.IDENTITY,
    MessageKind.CRISIS,
})

FLOW_TYPE = Literal["concierge", "factual", ""]

# Fact scope identifies the kind of exact lookup being requested
FACT_SCOPE = Literal[
    "mall_fact",
    "store_lookup",
    "service_lookup",
    "cinema_lookup",
    "movie_schedule",
    "brand_availability",
    "cross_mall_availability",
    "route_hint",
    "",
]

# Factual response modes
FACT_RESPONSE_MODE = Literal[
    "quick_answer",
    "direct_lookup",
    "route_hint",
    "structured_fact_list",
    "schedule_answer",
    "cross_mall_availability",
    "",
]


class InterpretedIntent(BaseModel):
    """Result of intent interpretation for the current turn."""

    domain: str = ""
    sub_intent: str = ""
    message_kind: MessageKind = MessageKind.FRESH_REQUEST
    confidence: float = 0.0
    raw_signals: dict[str, Any] = Field(default_factory=dict)
    # Enriched signals emitted by interpret_turn for downstream use
    implicit_goal: str = ""
    scene_candidates: list[str] = Field(default_factory=list)
    semantic_candidates: list[str] = Field(default_factory=list)
    # Flow routing hints — consumed by route_flow node
    flow_type_candidate: FLOW_TYPE = ""
    fact_scope_candidate: FACT_SCOPE = ""
    fact_entity_type_candidate: str = ""
    # ── Hybrid intent bundle ─────────────────────────────────────────
    # primary_intent: the dominant, non-overridable intent
    # secondary_intents: filters/contexts that modify but don't replace primary
    # modifiers: semantic tags that bias ranking and response shaping
    primary_intent: str = ""
    secondary_intents: list[str] = Field(default_factory=list)
    modifiers: list[str] = Field(default_factory=list)
    # ── Scene correction directives (LLM-populated on companion_correction turns) ─
    # List of correction tokens applied by update_scene_memory before any other mutation.
    # Valid tokens: "all_family_context", "companion:<name>", "visit_type:<value>",
    #               "target_person", "scenario", "visit_type:solo", "audience:family"
    scene_corrections: list[str] = Field(default_factory=list)
    # ── Response mode hint from the LLM classifier ───────────────────────────
    # Consumed by response_mode_resolver as the primary signal.
    # One of: direct_factual, guided_recommendation, hybrid_plan,
    #         best_effort_shortlist, context_acknowledgement, graceful_recovery,
    #         clarification_request
    response_mode_hint: str = ""
    # ── LLM-extracted entity for factual/cross-mall queries ──────────────────
    # The LLM classifier extracts the target entity/term directly from the message
    # (e.g. "which all malls does shrimp" → "shrimp",
    #       "where is Starbucks" → "Starbucks").
    # Avoids regex-based post-processing for entity extraction.
    entity_query: str = ""
    # ── LLM-driven pipeline signals ──────────────────────────────────────────
    # retrieval_needed: LLM determines whether this turn requires a live data
    #   lookup. Consumed by decide_retrieval as the primary signal, replacing
    #   the static _SKIP_INTENTS / _EXACT_INTENTS binary.
    retrieval_needed: bool = False
    # companion_context: companions the LLM detected in THIS turn's message.
    #   Populated on any flow type (including factual), so companions aren't
    #   lost when a domain lock routes away from update_scene_memory.
    #   e.g. ["child"] for "with kid", ["wife"] for "buying for my wife"
    companion_context: list[str] = Field(default_factory=list)
    # releases_topic_lock: LLM signals that this turn genuinely moves to a
    #   different domain from the active topic_lock, even when message_kind is
    #   not "topic_switch". Consumed by update_memory to clear scene.topic_lock.
    releases_topic_lock: bool = False
    # excluded_entity_types: LLM-extracted entity types the guest implicitly or
    #   explicitly excludes via language like "besides movies", "other than cinema",
    #   "something to do besides eating". Populated by the classifier LLM; consumed
    #   by compose_context (hard filter) and generate_response (constraint instruction).
    #   e.g. ["cinema"] for "entertainment besides movies"
    excluded_entity_types: list[str] = Field(default_factory=list)
    # preferred_entity_types: LLM-extracted category of entities the guest is
    #   specifically asking about. Populated for discovery/exploration queries to
    #   bias retrieval toward the right entity types instead of generic fallbacks.
    #   e.g. ["entertainment_center", "arcade", "play_area"] for "fun activities for kids"
    preferred_entity_types: list[str] = Field(default_factory=list)
    # is_gibberish: promoted from raw_signals to a typed field so choose_strategy
    #   can differentiate unintelligible input from intelligible off-topic requests
    #   when both resolve to graceful_recovery response mode.
    is_gibberish: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# Shopping Task
# ═══════════════════════════════════════════════════════════════════════════


class ShoppingTask(BaseModel):
    """
    Structured product-shopping task — persists across turns.

    Created when the user expresses a specific shopping intent (e.g. "I want
    to buy jackets") and refined on subsequent turns ("for my 5 year old son",
    "something affordable"). Drives task-scoped retrieval in compose_context
    and ranking bias in rank_and_dedupe.

    shopping_stage progression:
      discovery → refinement → price_guidance → budget_refinement
    """

    product_type: str = ""          # e.g. "jacket", "shoes", "perfume"
    product_category: str = ""      # e.g. "outerwear", "kids_outerwear", "fragrance"
    target_person: str = ""         # e.g. "son", "daughter", "girlfriend", "self"
    target_age: int | None = None   # e.g. 5 (when shopping for a child)
    target_gender: str = ""         # "boy" | "girl" | "male" | "female"
    budget_preference: str = ""     # "affordable" | "mid_range" | "premium"
    style_preference: list[str] = Field(default_factory=list)
    use_case: str = ""              # e.g. "birthday_gift", "casual_wear"
    shopping_stage: str = ""        # discovery | refinement | price_guidance | budget_refinement


# ═══════════════════════════════════════════════════════════════════════════
# Scene Memory
# ═══════════════════════════════════════════════════════════════════════════


class SceneMemory(BaseModel):
    """
    Accumulated conversational context about the visitor's situation.

    Persists across turns and is updated incrementally by the
    update_scene_memory node.  Captures who the visitor is, what they
    need, and where they are in their mall journey.

    The ``target_person`` field resolves *who* the current query is about
    (e.g. "child", "girlfriend") so that terse follow-ups like "food?"
    are interpreted as "food for child" rather than "food in general".
    """

    visit_type: str = ""
    companions: list[str] = Field(default_factory=list)
    # Structured companion details e.g. [{"type": "child", "age": 5}]
    companion_details: list[dict[str, Any]] = Field(default_factory=list)
    occasion: str = ""
    budget: str = ""
    # Inferred pace: "fast", "moderate", "leisurely"
    pace: str = ""
    audience: list[str] = Field(default_factory=list)
    current_area: str = ""
    current_need: str = ""
    previous_need: str = ""
    active_topic: str = ""
    previous_topic: str = ""
    active_shortlist: list[str] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)
    # Conversation frame extensions
    target_person: str = ""
    goal: str = ""
    # Inferred higher-level goal e.g. "shopping while keeping child engaged"
    implicit_goal: str = ""
    topic_history: list[str] = Field(default_factory=list)

    # ── Scenario / role fields (set by update_scene_memory) ──────────
    # user_role: the role the visitor declared (e.g. "bridesmaid", "parent", "couple")
    user_role: str = ""
    # style_intent: desired vibe/aesthetic (e.g. "elegant", "romantic", "practical", "quick")
    style_intent: list[str] = Field(default_factory=list)
    # scenario: real-world situation (e.g. "wedding_related", "family_outing", "date", "quick_visit")
    scenario: str = ""

    # Time-of-day context — set from the request at session start
    # Values: "morning" | "afternoon" | "evening" | "late_night" | ""
    time_of_day: str = ""

    # Vague-input clarification tracking
    needs_clarification: bool = False
    clarification_topic: str = ""  # e.g. "gift_target", "dining_preference"

    # Factual-flow follow-up memory
    last_flow_type: str = ""
    active_fact_scope: str = ""
    last_resolved_entity: str = ""
    last_resolved_entity_type: str = ""
    # Hybrid-intent follow-up memory
    active_primary_intent: str = ""
    active_secondary_filters: list[str] = Field(default_factory=list)
    active_modifiers: list[str] = Field(default_factory=list)

    # Visit planning — tracks multi-step visit sequences across turns
    visit_plan: list[str] = Field(
        default_factory=list,
        description="Ordered planned activity sequence, e.g. ['shopping', 'coffee', 'dessert']",
    )
    completed_steps: list[str] = Field(
        default_factory=list,
        description="Activities already discussed/recommended in this conversation",
    )
    visit_constraints: list[str] = Field(
        default_factory=list,
        description="User-expressed constraints, e.g. ['quick', 'light', 'affordable']",
    )
    excluded_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Domains/categories explicitly excluded by the user (e.g. 'dining', 'cafe'). "
            "Accumulated across turns and never reset — hard-filtered in compose_context "
            "and injected as a HARD CONSTRAINT into the LLM prompt."
        ),
    )
    multi_activity_mode: bool = Field(
        default=False,
        description="True when the user has stated a multi-step visit plan",
    )
    current_plan_step: str = Field(
        default="",
        description="Which step of visit_plan is currently being addressed",
    )

    # ── Structured shopping task (persists across turns) ─────────────
    shopping_task: ShoppingTask = Field(default_factory=ShoppingTask)

    # ── Greeting streak (persists across turns via update_memory) ────────
    # Tracks how many consecutive greeting turns have happened so far in this
    # session.  Used by the smalltalk node to select a progressive response
    # (welcome → mall insight → direct ask) instead of repeating the same line.
    # Incremented by update_memory when response_debug_summary starts with
    # "smalltalk/greeting"; reset to 0 on any non-greeting turn.
    greeting_streak: int = 0

    # ── Emotional / mood state (persists across turns via update_memory) ─
    # recent_mood: the most recent emotional signal from the visitor.
    # Set when message_kind is "emotional" or "disengagement"; expires after
    # 2 subsequent non-emotional turns so it doesn't bleed into unrelated queries.
    recent_mood: str = Field(
        default="",
        description=(
            "Most recent emotional signal: 'emotional' | 'disengagement' | ''. "
            "Expires after 2 non-emotional turns."
        ),
    )
    mood_turn_index: int = Field(
        default=0,
        description="Turn index (len(state.messages)) when recent_mood was last recorded.",
    )

    # ── Scene acknowledgment tracking ─────────────────────────────────
    # True once the bot has explicitly acknowledged the visitor's companion/
    # visit-context situation.  Prevents repetitive re-acknowledgment every turn.
    # Reset to False when companions change so the new context gets acknowledged.
    scene_acknowledged: bool = False

    # ── Topic lock — prevents drift on follow-up turns ────────────────
    # topic_lock: the locked active topic (e.g. "movies", "dining", "mall_info")
    topic_lock: str = Field(
        default="",
        description="Current locked topic preventing unintended drift on follow-ups",
    )
    topic_lock_confidence: float = Field(
        default=0.0,
        description="Confidence [0–1] that topic_lock is still valid",
    )
    # last_selected_playbook: cross-turn continuity
    last_selected_playbook: str = Field(
        default="",
        description="Most recently selected playbook (persistent across turns)",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Playbook Resolution
# ═══════════════════════════════════════════════════════════════════════════


class PlaybookResolution(BaseModel):
    """Which scenario playbooks matched and which was selected."""

    matched_playbooks: list[str] = Field(default_factory=list)
    selected_playbook: str = ""
    playbook_confidence: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Context Composition
# ═══════════════════════════════════════════════════════════════════════════


class ContextComposition(BaseModel):
    """The assembled context blocks for this turn's LLM prompt."""

    selected_topic_blocks: list[str] = Field(default_factory=list)
    selected_entities: list[dict[str, Any]] = Field(default_factory=list)
    selected_semantic_signals: list[str] = Field(default_factory=list)
    ranking_notes: list[str] = Field(default_factory=list)
    # Pre-dedupe candidate count for debug purposes
    candidate_count_before_dedupe: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# Retrieval Decision
# ═══════════════════════════════════════════════════════════════════════════


class RetrievalDecision(BaseModel):
    """Whether exact retrieval is needed and the results if executed."""

    retrieval_needed: bool = False
    retrieval_reason: str = ""
    retrieval_targets: list[str] = Field(default_factory=list)
    retrieval_results: list[dict[str, Any]] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Response Planning
# ═══════════════════════════════════════════════════════════════════════════

STRATEGY_CHOICES = Literal[
    "mall_overview",
    "direct_fact",
    "exploration_overview",
    "shortlist_recommendation",
    "mini_itinerary",
    "gift_formula",
    "family_plan",
    "solo_plan",
    "movie_plus_food",
    "budget_plan",
    "fallback_guided_response",
    # Concierge-first strategies
    "guided_plan",
    "concise_shortlist",
    "route_plus_plan",
    "quick_answer",
    "route_hint",
    # Factual-flow strategies
    "structured_fact_list",
    "schedule_answer",
    "direct_lookup",
    "cross_mall_availability",
    "compare_and_recommend",
    # Hybrid factual strategies (factual + active secondary filters)
    "filtered_factual_list",   # factual list filtered by audience/genre/budget
    "factual_presence",        # brand availability check (yes/no + location)
]

# ── Response Strategy Names ───────────────────────────────────────────────────
# Human-readable response strategy labels resolved from primary_intent.
# Used for explicit routing control and debug observability.
RESPONSE_STRATEGY = Literal[
    "factual_list",           # Unfiltered factual list (movie_lookup, service_lookup)
    "filtered_factual_list",  # Factual list + active secondary filters applied
    "factual_presence",       # Brand/service availability (yes/no + location)
    "structured_overview",    # Structured mall overview
    "guided_plan",            # Concierge-guided plan (shopping, dining, gift)
    "",
]


class ResponsePlan(BaseModel):
    """How the generator should shape its response."""

    chosen_strategy: str = ""
    response_shape_hint: str = ""
    response_constraints: list[str] = Field(default_factory=list)
    # Concierge-mode fields
    answer_mode: str = ""          # "concierge_guided" | "direct_answer" | "shortlist"
    tone_mode: str = ""            # "compact_human_concierge" | "structured"
    itinerary_mode: str = ""
    entity_cap: int = 5            # Maximum entities to pass to LLM
    must_acknowledge_scene: bool = False
    must_include_anchor_type: str = ""   # e.g. "entertainment" for child anchors
    # Hybrid response planning
    primary_goal: str = ""         # Dominant intent label for response structure
    secondary_filters: list[str] = Field(default_factory=list)
    dominant_context_type: str = ""   # e.g. "cinema_and_movies", "shopping", "gifting"
    fact_first: bool = False           # True: lead with facts, add context tail
    concierge_tail_allowed: bool = True  # Allow brief concierge suggestion after facts
    # ── Response Mode Resolver fields ────────────────────────────────
    # response_mode: behavioural mode selected by the response mode resolver.
    # Sits above playbooks and strategies; governs HOW to respond.
    response_mode: str = Field(
        default="",
        description=(
            "Behavioural response mode: direct_factual | guided_recommendation | "
            "hybrid_plan | best_effort_shortlist | context_acknowledgement | "
            "graceful_recovery | clarification_request"
        ),
    )
    confidence_level: str = Field(
        default="",
        description="Interpretation confidence classification: high | medium | low",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Debug Enrichment
# ═══════════════════════════════════════════════════════════════════════════


class DebugEnrichment(BaseModel):
    """Rich observability data collected across nodes for concierge intelligence."""

    inferred_scene_notes: list[str] = Field(default_factory=list)
    semantic_match_explanations: list[str] = Field(default_factory=list)
    ranking_explanations: list[str] = Field(default_factory=list)
    playbook_rejection_reasons: list[str] = Field(default_factory=list)
    candidate_count_before_dedupe: int = 0
    final_entity_count: int = 0
    response_strategy_reason: str = ""
    last_refinement_applied: str = ""
    deduped_entity_count: int = 0
    # Dual-flow observability
    flow_type: str = ""
    flow_routing_reason: str = ""
    fact_scope: str = ""
    fact_entity_type: str = ""
    retrieval_priority: str = ""
    playbook_suppressed: list[str] = Field(default_factory=list)
    # Hybrid-intent observability
    primary_intent: str = ""
    secondary_intents: list[str] = Field(default_factory=list)
    modifiers: list[str] = Field(default_factory=list)
    dominant_context_type: str = ""
    # Domain lock + response strategy observability
    domain_locked: bool = False
    filter_applied: bool = False
    response_strategy_resolved: str = ""
    # Playbook selection observability
    selected_playbook_id: str = ""
    selected_playbook_reason: str = ""
    # ── Experience Layer observability (set by generate_response) ─────
    response_experience_mode: str = ""
    experience_cta_type: str = ""
    experience_itinerary_allowed: bool = False
    experience_refinement_ack: str = ""
    experience_followup_hint: str = ""
    # ── Interpretation contract (set by interpret_turn) ────────────────
    interpretation_contract: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured output of interpret_turn: primary_intent, scenario, "
            "modifiers, message_kind, flow_type, active_topic"
        ),
    )
    # ── Dedupe observability (set by rank_and_dedupe) ─────────────────
    dedupe_key_used: str = ""
    duplicate_entities_collapsed: int = 0
    canonical_name_normalization_notes: list[str] = Field(default_factory=list)

    # ── Query normalization observability (set by interpret_turn) ──────
    normalized_query: str = ""
    canonical_query_pattern: str = ""

    # ── Topic lock observability (set by update_memory) ───────────────
    topic_lock: str = ""
    topic_lock_confidence: float = 0.0

    # ── Retrieval discipline observability (set by decide_retrieval) ───
    retrieval_discipline_reason: str = ""

    # ── Playbook selection observability (extended) ────────────────────
    suppressed_playbooks: list[str] = Field(default_factory=list)
    selection_reason: str = ""

    # ── Scene memory patch debug (set by update_scene_memory) ──────────
    scene_update_reason: str = ""
    continuity_preserved: bool = False
    shopping_task_updates: list[str] = Field(default_factory=list)
    scenario_persisted: bool = False
    topic_switch_detected: bool = False
    # True when scene context is rich enough that the LLM must NOT ask a clarifying question.
    scene_sufficient: bool = False

    # ── Compose context patch debug (set by compose_context) ───────────
    dominant_context_reason: str = ""
    candidate_scope: str = ""
    off_topic_entities_suppressed: int = 0
    shopping_scope_applied: bool = False
    overview_followup_preserved: bool = False
    topic_blocks_suppressed: list[str] = Field(default_factory=list)
    off_topic_entity_names_suppressed: list[str] = Field(default_factory=list)
    candidate_scope_reason: str = ""

    # ── Rank-and-dedupe patch debug (set by rank_and_dedupe) ───────────
    ranking_scope: str = ""
    suppressed_off_topic_count: int = 0
    strongest_surviving_entity_reason: str = ""

    # ── Continuity / dominant task scope debug ─────────────────────────
    dominant_task_scope: str = ""
    shopping_task_active: bool = False
    family_override_applied: bool = False
    playbook_bias_applied: list[str] = Field(default_factory=list)
    continuity_resolved_topic: str = ""

    # ── Response Mode Resolver debug (set by choose_strategy) ──────────
    response_mode: str = ""
    confidence_level: str = ""
    response_mode_reason: str = ""
    fallback_applied: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# Observability
# ═══════════════════════════════════════════════════════════════════════════


class NodeTraceEntry(BaseModel):
    """Trace record for a single node execution."""

    node: str
    started_at: str = ""
    ended_at: str = ""
    latency_ms: float = 0.0
    summary: str = ""
    output_keys: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Top-level Graph State
# ═══════════════════════════════════════════════════════════════════════════


class ConciergeState(BaseModel):
    """
    The full state object passed between LangGraph nodes.

    Accumulating fields (messages, node_trace, warnings) use
    ``Annotated[list, operator.add]`` so nodes can append without
    reading the full list first.
    """

    # ── 1. Session Identity ───────────────────────────────────────────
    session_id: str = ""
    tenant_id: str = "al_nakheel_plaza_28"
    mall_id: str = "al_nakheel_plaza_28"
    turn_id: str = ""

    # ── 1b. Lightweight session continuity (NO raw LLM outputs) ──────
    last_intent: str = ""
    conversation_mode: str = ""
    last_successful_playbook: str = ""
    last_response_shape: str = ""
    preferred_categories: list[str] = Field(default_factory=list)

    # ── 1c. Dual-flow routing ─────────────────────────────────────────
    flow_type: FLOW_TYPE = ""
    flow_routing_reason: str = ""
    retrieval_priority: Literal["high", "medium", "low", ""] = ""
    # ── 1d. Hybrid intent bundle (set by route_flow) ──────────────────
    primary_intent: str = ""
    secondary_intents: list[str] = Field(default_factory=list)
    modifiers: list[str] = Field(default_factory=list)
    dominant_context_type: str = ""
    # ── 1f. Continuity contract — shared between nodes ────────────────
    # dominant_task_scope: the current active task scope (e.g. "kids_outerwear")
    # Used by resolve_playbooks, compose_context, rank_and_dedupe to stay consistent.
    dominant_task_scope: str = ""
    continuity_resolved_topic: str = ""
    # ── 1e. Domain lock + response strategy (set by route_flow) ──────
    domain_locked: bool = False          # True when prior factual intent is locked
    response_strategy: str = ""          # Resolved strategy name (e.g. filtered_factual_list)
    filter_applied: bool = False         # True when secondary filters are actively applied
    # Factual-flow state
    fact_scope: FACT_SCOPE = ""
    fact_entity_type: str = ""
    fact_query_entity: str = ""
    fact_response_mode: FACT_RESPONSE_MODE = ""
    fact_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Exact context assembled by compose_fact_response_context",
    )

    # ── 1g. Language ──────────────────────────────────────────────────
    # "ar" | "en" — auto-detected or set by the client UI toggle.
    # Flows through every prompt-building node so the LLM always responds
    # in the guest's preferred language.
    detected_language: str = "en"

    # ── 2. User Input ─────────────────────────────────────────────────
    raw_user_message: str = ""
    normalized_user_message: str = ""
    expanded_query: str = ""

    # ── 3. Scope ──────────────────────────────────────────────────────
    mode: Literal["single_mall"] = "single_mall"
    active_mall_id: str = "al_nakheel_plaza_28"

    # ── 4. Conversation History (append-only via reducer) ─────────────
    messages: Annotated[list[Message], operator.add] = Field(default_factory=list)

    # ── 5. Interpreted Intent ─────────────────────────────────────────
    intent: InterpretedIntent = Field(default_factory=InterpretedIntent)

    # ── 6. Scene Memory (persists across turns) ───────────────────────
    scene: SceneMemory = Field(default_factory=SceneMemory)

    # ── 7. Playbook Resolution ────────────────────────────────────────
    playbook: PlaybookResolution = Field(default_factory=PlaybookResolution)

    # ── 8. Context Composition ────────────────────────────────────────
    context: ContextComposition = Field(default_factory=ContextComposition)

    # ── 9. Retrieval ──────────────────────────────────────────────────
    retrieval: RetrievalDecision = Field(default_factory=RetrievalDecision)

    # ── 10. Tenant Config / Session Tuning ────────────────────────────
    active_tenant_parameters: Any = Field(
        default=None,
        description="TenantConfig instance loaded at session start.",
    )
    active_session_tuning: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # ── 11. Response Planning ─────────────────────────────────────────
    response_plan: ResponsePlan = Field(default_factory=ResponsePlan)

    # ── 12. Generated Response ────────────────────────────────────────
    final_response_text: str = ""
    response_debug_summary: str = ""

    # ── 13. Debug Enrichment (written by multiple nodes) ─────────────
    debug_enrichment: DebugEnrichment = Field(default_factory=DebugEnrichment)

    # ── 14. Observability (append-only via reducer) ───────────────────
    node_trace: Annotated[list[NodeTraceEntry], operator.add] = Field(
        default_factory=list,
    )
    latency_by_node: dict[str, float] = Field(default_factory=dict)
    evaluator_stub: dict[str, Any] = Field(default_factory=dict)
    warnings: Annotated[list[str], operator.add] = Field(default_factory=list)
