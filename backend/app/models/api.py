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
    tenant_id: str = "al_nakheel_plaza_28"
    mall_id: str = "al_nakheel_plaza_28"
    debug: bool = False
    context: dict | None = Field(
        default=None,
        description="Optional client-side context hints",
    )
    time_of_day: str = Field(
        default="",
        description=(
            "Optional time-of-day hint from the client (browser local time). "
            "Accepted values: 'morning' (6–11), 'afternoon' (11–17), "
            "'evening' (17–21), 'late_night' (21–6). "
            "Used for contextual enrichment (e.g. peak-time dining, closing times)."
        ),
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

    scene_summary: dict[str, Any] = Field(default_factory=dict)

    selected_playbook: str = ""
    playbook_confidence: float = 0.0
    matched_playbooks: list[str] = Field(default_factory=list)

    chosen_strategy: str = ""
    response_shape: str = ""

    selected_topic_blocks: list[str] = Field(default_factory=list)
    selected_entities: list[dict[str, Any]] = Field(default_factory=list)
    selected_semantic_signals: list[str] = Field(default_factory=list)
    ranking_notes: list[str] = Field(default_factory=list)

    retrieval_needed: bool = False
    retrieval_reason: str = ""
    retrieval_targets: list[str] = Field(default_factory=list)
    retrieval_results_count: int = 0

    latency_by_node: dict[str, float] = Field(default_factory=dict)
    total_latency_ms: float = 0.0
    node_count: int = 0

    # Response Mode Resolver fields
    response_mode: str = ""
    confidence_level: str = ""
    response_mode_reason: str = ""
    fallback_applied: bool = False
    flow_type: str = ""

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
    target_person: str = ""
    visit_type: str = ""
    goal: str = ""


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
    last_intent: str = ""
    conversation_mode: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Feedback
# ═══════════════════════════════════════════════════════════════════════════


class FeedbackRequest(BaseModel):
    """Full feedback payload from the UI."""

    session_id: str
    tenant_id: str = "al_nakheel_plaza_28"
    mall_id: str = "al_nakheel_plaza_28"
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
