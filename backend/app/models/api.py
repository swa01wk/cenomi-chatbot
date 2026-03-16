"""
API request/response models.

These are the contracts between frontend and backend.
Internal state models live in state.py; these are the serialization boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Chat
# ═══════════════════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=4000)
    session_id: str | None = None
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    debug: bool = False
    context: dict | None = Field(
        default=None,
        description="Optional client-side context hints",
    )


class DebugPayload(BaseModel):
    """Detailed pipeline debug metadata returned when debug=True."""

    turn_id: str = ""
    session_id: str = ""
    mall_id: str = ""

    intent_domain: str = ""
    intent_sub: str = ""
    intent_confidence: float = 0.0
    message_kind: str = ""
    is_refinement_of_current_topic: bool = False
    detected_refinement_cues: list[str] = Field(default_factory=list)

    scene_summary: dict[str, Any] = Field(default_factory=dict)
    continuity_anchor: dict[str, Any] = Field(default_factory=dict)
    thread_preservation: dict[str, Any] = Field(default_factory=dict)

    # Continuity resolution (Part 1)
    continuity_resolution: dict[str, Any] = Field(
        default_factory=dict,
        description="Formal continuity resolver output: type, action, confidence, reason.",
    )
    thread_preservation_decision: dict[str, Any] = Field(
        default_factory=dict,
        description="Thread preservation verdict with reason.",
    )

    selected_playbook: str = ""
    playbook_confidence: float = 0.0
    matched_playbooks: list[str] = Field(default_factory=list)

    # Playbook precedence candidates (Part 2)
    expected_playbook_candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All scored playbook candidates with precedence scores.",
    )

    chosen_strategy: str = ""
    response_shape: str = ""
    response_contract: dict[str, Any] = Field(default_factory=dict)

    # Response contract name (Part 4)
    response_contract_used: str = Field(
        default="ai_findr",
        description="The response formatter contract applied to this turn.",
    )

    selected_topic_blocks: list[str] = Field(default_factory=list)
    selected_entities: list[dict[str, Any]] = Field(default_factory=list)
    selected_semantic_signals: list[str] = Field(default_factory=list)
    ranking_notes: list[str] = Field(default_factory=list)

    candidate_reason_map: list[dict[str, Any]] = Field(default_factory=list)
    narrowing_followup: dict[str, Any] = Field(default_factory=dict)

    # Shortlist reason map (Part 3)
    shortlist_reason_map: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-entity reason entries from the shortlist ranker.",
    )

    retrieval_needed: bool = False
    retrieval_reason: str = ""
    retrieval_targets: list[str] = Field(default_factory=list)
    retrieval_results_count: int = 0

    latency_by_node: dict[str, float] = Field(default_factory=dict)
    total_latency_ms: float = 0.0
    node_count: int = 0

    warnings: list[str] = Field(default_factory=list)
    node_trace: list[dict[str, Any]] = Field(default_factory=list)


class SessionSummary(BaseModel):
    """Compact session state for the chat response."""

    session_id: str = ""
    turn_count: int = 0
    active_topic: str = ""
    companions: list[str] = Field(default_factory=list)
    occasion: str = ""
    budget: str = ""
    active_shortlist: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    message: str
    session_state: SessionSummary | None = None
    sources: list[dict] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    debug: DebugPayload | None = None


# ═══════════════════════════════════════════════════════════════════════════
# Session
# ═══════════════════════════════════════════════════════════════════════════


class SessionResetRequest(BaseModel):
    session_id: str
    mall_id: str = "cenomi_mall_01"


class SessionResetResponse(BaseModel):
    session_id: str
    status: str = "reset"


class SessionDetailResponse(BaseModel):
    session_id: str
    mall_id: str = ""
    turn_count: int = 0
    scene: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Feedback
# ═══════════════════════════════════════════════════════════════════════════


class FeedbackRequest(BaseModel):
    """Full feedback payload from the UI."""

    session_id: str
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    turn_id: str = ""
    message_id: str | None = None
    user_message: str = ""
    assistant_response: str = ""
    feedback_type: str = Field(..., description="thumbs_up or thumbs_down")
    feedback_reasons: list[str] = Field(default_factory=list)
    feedback_text: str = ""
    strategy_used: str = ""
    playbook_used: str = ""
    selected_entities: list[dict[str, Any]] = Field(default_factory=list)
    selected_context_blocks: list[str] = Field(default_factory=list)


class FeedbackResponse(BaseModel):
    feedback_id: str
    status: str
    session_tuning_applied: bool = False
    normalized_signal_count: int = 0


class SessionFeedbackResponse(BaseModel):
    """All feedback for a session."""

    session_id: str
    explicit_events: list[dict[str, Any]] = Field(default_factory=list)
    implicit_events: list[dict[str, Any]] = Field(default_factory=list)
    normalized_signals: list[dict[str, Any]] = Field(default_factory=list)
    session_tuning: dict[str, Any] = Field(default_factory=dict)


class TenantSummaryResponse(BaseModel):
    """Aggregated feedback summary for a tenant."""

    tenant_id: str
    mall_id: str
    total_events: int = 0
    overall_approval_rate: float = 0.0
    buckets: list[dict[str, Any]] = Field(default_factory=list)
    recommended_adjustments: list[dict[str, Any]] = Field(default_factory=list)


class PlaybookPerformanceResponse(BaseModel):
    """Performance breakdown by playbook."""

    playbook_buckets: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_gaps: list[dict[str, Any]] = Field(default_factory=list)
