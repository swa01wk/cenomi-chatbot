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

MESSAGE_KIND = Literal[
    "fresh_request",
    "refinement",
    "correction",
    "topic_switch",
    "followup",
]


class InterpretedIntent(BaseModel):
    """Result of intent interpretation for the current turn."""

    domain: str = ""
    sub_intent: str = ""
    message_kind: MESSAGE_KIND = "fresh_request"
    confidence: float = 0.0
    raw_signals: dict[str, Any] = Field(default_factory=dict)
    is_refinement_of_current_topic: bool = Field(
        default=False,
        description="True when the message refines or narrows the current thread.",
    )
    detected_refinement_cues: list[str] = Field(
        default_factory=list,
        description=(
            "Short tokens that triggered refinement detection "
            "(e.g. 'affordable', 'for my son', 'price')."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Scene Memory
# ═══════════════════════════════════════════════════════════════════════════


class SceneMemory(BaseModel):
    """
    Accumulated conversational context about the visitor's situation.

    Persists across turns and is updated incrementally by the
    update_scene_memory node.  Captures who the visitor is, what they
    need, and where they are in their mall journey.
    """

    visit_type: str = ""
    companions: list[str] = Field(default_factory=list)
    occasion: str = ""
    budget: str = ""
    audience: list[str] = Field(default_factory=list)
    current_area: str = ""
    current_need: str = ""
    active_topic: str = ""
    previous_topic: str = ""
    active_shortlist: list[str] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)
    current_preferences: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Continuity Anchor — preserves topic thread across turns
# ═══════════════════════════════════════════════════════════════════════════


class ContinuityAnchor(BaseModel):
    """
    Tracks the active conversational thread so that short follow-ups
    ("affordable", "for my son", "price?") stay within the current topic
    instead of triggering a spurious topic switch.

    Updated by update_scene_memory; consumed by resolve_playbooks,
    choose_strategy, and compose_context.
    """

    domain: str = Field(default="", description="Top-level domain: shopping, dining, entertainment …")
    topic: str = Field(default="", description="Specific topic: jackets, italian_food, movie_showtime …")
    subtopic: str = Field(default="", description="Narrowing dimension: kids_jackets, affordable_jackets …")
    audience: str = Field(default="", description="Active audience bias: kids, couple, family, solo …")
    budget: str = Field(default="", description="Active budget lens: budget, mid_range, premium, luxury")
    occasion: str = Field(default="", description="Active occasion: birthday, date, casual, before_movie …")
    last_successful_shortlist: list[str] = Field(
        default_factory=list,
        description="Entity names from the most recent successful shortlist response.",
    )
    last_playbook: str = Field(default="", description="Playbook ID used in the last turn.")
    last_strategy: str = Field(default="", description="Strategy used in the last turn.")

    @property
    def is_strong(self) -> bool:
        """A continuity anchor is 'strong' when both domain and topic are set."""
        return bool(self.domain and self.topic)


# ═══════════════════════════════════════════════════════════════════════════
# Response Policy Profile — resolved tenant policy for this turn
# ═══════════════════════════════════════════════════════════════════════════


class ResponsePolicyProfile(BaseModel):
    """
    Snapshot of the resolved tenant response-policy values for the current
    turn.  Built by choose_strategy from TenantConfig.response_policy so
    that downstream nodes (compose_context, generate_response) can read
    policy without re-querying the tenant config.
    """

    style_family: str = "ai_findr"
    always_acknowledge_scene: bool = True
    prefer_short_contextual_intro: bool = True
    prefer_curated_shortlist: bool = True
    default_shortlist_size: int = 3
    max_shortlist_size: int = 5
    prefer_one_line_reasons: bool = True
    show_location_only_when_useful: bool = True
    deprioritize_floor_zone_details: bool = True
    prefer_followup_narrowing_question: bool = True
    avoid_brochure_tone: bool = True
    avoid_repeating_full_mall_name: bool = True
    prefer_decision_help_over_description: bool = True
    preserve_thread_continuity: bool = True
    preserve_topic_on_short_followups: bool = True
    prefer_contextual_grouping: bool = True
    prefer_price_range_when_exact_price_missing: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# Response Contract — binding instructions for generate_response
# ═══════════════════════════════════════════════════════════════════════════


INTRO_STYLE = Literal["scene_aware", "topic_continuation", "fresh_greeting", "none"]
EXPLANATION_STYLE = Literal["one_line_reason", "paragraph", "none"]
LOCATION_VISIBILITY = Literal["always", "when_useful", "never"]
FOLLOWUP_STYLE = Literal["narrowing_question", "open_ended", "none"]
EXACT_PRICE_MODE = Literal["range_only", "tier_label", "suppress"]


class ResponseContract(BaseModel):
    """
    Concrete, per-turn instructions that generate_response must follow.

    Built by choose_strategy + compose_context so the generator has a
    single, unambiguous spec.  The generator should not re-derive these
    from tenant config.
    """

    intro_style: INTRO_STYLE = "scene_aware"
    shortlist_size: int = Field(default=3, ge=1, le=10)
    explanation_style: EXPLANATION_STYLE = "one_line_reason"
    location_visibility: LOCATION_VISIBILITY = "when_useful"
    followup_style: FOLLOWUP_STYLE = "narrowing_question"
    continuity_requirement: bool = Field(
        default=True,
        description="When True the response must reference the active thread.",
    )
    brochure_tone_forbidden: bool = True
    exact_price_mode: EXACT_PRICE_MODE = "range_only"


# ═══════════════════════════════════════════════════════════════════════════
# Candidate Reason Map — one-line reasons for each shortlist entity
# ═══════════════════════════════════════════════════════════════════════════


REASON_TYPE = Literal[
    "audience_fit",
    "budget_fit",
    "occasion_fit",
    "vibe_match",
    "proximity",
    "popularity",
    "general",
]


class CandidateReasonEntry(BaseModel):
    """
    A shortlist-ready explanation for why a specific entity was selected.

    compose_context produces one entry per shortlisted entity.
    generate_response uses these verbatim or paraphrases them.
    """

    entity_name: str
    chosen_reason_type: REASON_TYPE = "general"
    one_line_reason: str = ""
    followup_hint: str = Field(
        default="",
        description="Optional micro-hint the generator can append (e.g. 'ask about kids menu').",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Narrowing Follow-up Opportunity
# ═══════════════════════════════════════════════════════════════════════════


class NarrowingFollowupOpportunity(BaseModel):
    """
    Indicates whether the current shortlist can be usefully narrowed and,
    if so, provides a ready-to-use follow-up question.

    Generated by compose_context; consumed by generate_response to
    decide the closing line of the reply.
    """

    is_useful: bool = False
    suggested_question: str = ""
    narrowing_dimension: str = Field(
        default="",
        description="Which dimension the follow-up narrows: budget, audience, cuisine, vibe …",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Thread Preservation Decision
# ═══════════════════════════════════════════════════════════════════════════


class ThreadPreservationDecision(BaseModel):
    """
    Explicit routing-layer verdict on whether to preserve the current
    conversational thread or start a new one.

    Set by interpret_turn / update_scene_memory; consumed by
    resolve_playbooks and choose_strategy to avoid accidental
    topic resets on short follow-ups.
    """

    preserve_current_topic: bool = True
    reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Continuity Resolution — formal classification of message continuity
# ═══════════════════════════════════════════════════════════════════════════

CONTINUITY_TYPE = Literal[
    "fresh_request",
    "refinement",
    "correction",
    "topic_switch",
]

THREAD_ACTION = Literal[
    "preserve",
    "extend",
    "reset",
    "archive_and_switch",
]


class ContinuityResolution(BaseModel):
    """
    Formal verdict from the continuity resolver on how the current message
    relates to the active conversational thread.

    Produced by the ContinuityResolver service; consumed by
    update_scene_memory, resolve_playbooks, choose_strategy, and
    compose_context.
    """

    continuity_type: CONTINUITY_TYPE = "fresh_request"
    thread_action: THREAD_ACTION = "reset"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""
    inherited_domain: str = Field(
        default="",
        description="Domain carried forward from the anchor when thread is preserved.",
    )
    inherited_topic: str = Field(
        default="",
        description="Topic carried forward from the anchor when thread is preserved.",
    )
    refinement_dimensions: list[str] = Field(
        default_factory=list,
        description="Which dimensions the refinement narrows: audience, budget, price, vibe.",
    )
    should_carry_audience: bool = True
    should_carry_budget: bool = True
    should_carry_shortlist: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# Playbook Resolution
# ═══════════════════════════════════════════════════════════════════════════


class PlaybookResolution(BaseModel):
    """Which scenario playbooks matched and which was selected."""

    matched_playbooks: list[str] = Field(default_factory=list)
    selected_playbook: str = ""
    playbook_confidence: float = 0.0
    expected_playbook_candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All scored candidates with their precedence scores for debug.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Context Composition
# ═══════════════════════════════════════════════════════════════════════════


class ContextComposition(BaseModel):
    """The assembled context blocks for this turn's LLM prompt."""

    selected_topic_blocks: list[str] = Field(default_factory=list)
    selected_entities: list[dict[str, Any]] = Field(default_factory=list)
    selected_semantic_signals: list[str] = Field(default_factory=list)
    ranking_notes: list[str] = Field(default_factory=list)


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
    "direct_fact",
    "shortlist_recommendation",
    "mini_itinerary",
    "gift_formula",
    "family_plan",
    "solo_plan",
    "movie_plus_food",
    "budget_plan",
    "fallback_guided_response",
]


class ResponsePlan(BaseModel):
    """How the generator should shape its response."""

    chosen_strategy: str = ""
    response_shape_hint: str = ""
    response_constraints: list[str] = Field(default_factory=list)


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
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    turn_id: str = ""

    # ── 2. User Input ─────────────────────────────────────────────────
    raw_user_message: str = ""
    normalized_user_message: str = ""

    # ── 3. Scope ──────────────────────────────────────────────────────
    mode: Literal["single_mall"] = "single_mall"
    active_mall_id: str = "cenomi_mall_01"

    # ── 4. Conversation History (append-only via reducer) ─────────────
    messages: Annotated[list[Message], operator.add] = Field(default_factory=list)

    # ── 5. Interpreted Intent ─────────────────────────────────────────
    intent: InterpretedIntent = Field(default_factory=InterpretedIntent)

    # ── 6. Scene Memory (persists across turns) ───────────────────────
    scene: SceneMemory = Field(default_factory=SceneMemory)

    # ── 7. Continuity Anchor (persists across turns) ──────────────────
    continuity_anchor: ContinuityAnchor = Field(default_factory=ContinuityAnchor)

    # ── 8. Thread Preservation Decision ───────────────────────────────
    thread_preservation: ThreadPreservationDecision = Field(
        default_factory=ThreadPreservationDecision,
    )

    # ── 8b. Continuity Resolution ─────────────────────────────────────
    continuity_resolution: ContinuityResolution = Field(
        default_factory=ContinuityResolution,
    )

    # ── 9. Playbook Resolution ────────────────────────────────────────
    playbook: PlaybookResolution = Field(default_factory=PlaybookResolution)

    # ── 10. Context Composition ───────────────────────────────────────
    context: ContextComposition = Field(default_factory=ContextComposition)

    # ── 11. Retrieval ─────────────────────────────────────────────────
    retrieval: RetrievalDecision = Field(default_factory=RetrievalDecision)

    # ── 12. Tenant Config / Session Tuning ────────────────────────────
    active_tenant_parameters: Any = Field(
        default=None,
        description="TenantConfig instance loaded at session start.",
    )
    active_session_tuning: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # ── 13. Response Policy Profile (resolved for this turn) ──────────
    response_policy_profile: ResponsePolicyProfile = Field(
        default_factory=ResponsePolicyProfile,
    )

    # ── 14. Response Planning ─────────────────────────────────────────
    response_plan: ResponsePlan = Field(default_factory=ResponsePlan)

    # ── 15. Response Contract (binding spec for generator) ────────────
    response_contract: ResponseContract = Field(default_factory=ResponseContract)

    # ── 16. Candidate Reason Map (one-line reasons per entity) ────────
    candidate_reason_map: list[CandidateReasonEntry] = Field(default_factory=list)

    # ── 17. Narrowing Follow-up Opportunity ───────────────────────────
    narrowing_followup: NarrowingFollowupOpportunity = Field(
        default_factory=NarrowingFollowupOpportunity,
    )

    # ── 18. Generated Response ────────────────────────────────────────
    final_response_text: str = ""
    response_debug_summary: str = ""

    # ── 19. Observability (append-only via reducer) ───────────────────
    node_trace: Annotated[list[NodeTraceEntry], operator.add] = Field(
        default_factory=list,
    )
    latency_by_node: dict[str, float] = Field(default_factory=dict)
    evaluator_stub: dict[str, Any] = Field(default_factory=dict)
    warnings: Annotated[list[str], operator.add] = Field(default_factory=list)
