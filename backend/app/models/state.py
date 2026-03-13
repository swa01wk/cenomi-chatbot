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

    # ── 13. Observability (append-only via reducer) ───────────────────
    node_trace: Annotated[list[NodeTraceEntry], operator.add] = Field(
        default_factory=list,
    )
    latency_by_node: dict[str, float] = Field(default_factory=dict)
    evaluator_stub: dict[str, Any] = Field(default_factory=dict)
    warnings: Annotated[list[str], operator.add] = Field(default_factory=list)
