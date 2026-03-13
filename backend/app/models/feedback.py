"""
Feedback data models.

Captures structured feedback (explicit + implicit), normalizes it into
actionable signals, tracks session-level tuning, tenant-level aggregates,
and knowledge-gap reports.

Schema design:
  FeedbackEvent          — explicit user feedback (thumbs up/down + reasons)
  ImplicitFeedbackEvent  — inferred from user corrections mid-conversation
  NormalizedFeedbackSignal — canonical signal produced from either source
  SessionTuningSnapshot  — parameter adjustments active in a session
  TenantFeedbackAggregate — rolled-up metrics across sessions
  KnowledgeGapReport     — detected missing playbooks / tags / entities
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════


class FeedbackType(str, Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"


class FeedbackReasonTag(str, Enum):
    NOT_RELEVANT = "not_relevant"
    TOO_GENERIC = "too_generic"
    TOO_LONG = "too_long"
    WRONG_ASSUMPTION = "wrong_assumption"
    WANTED_EXACT_DETAILS = "wanted_exact_details"
    WRONG_STORE_SUGGESTION = "wrong_store_suggestion"
    WRONG_DINING_SUGGESTION = "wrong_dining_suggestion"
    TONE_FELT_ROBOTIC = "tone_felt_robotic"
    ASKED_TOO_MANY_QUESTIONS = "asked_too_many_questions"
    SHOULD_RECOMMEND_SOMETHING_ELSE = "should_recommend_something_else"


class ImplicitSignalType(str, Enum):
    INCORRECT_SCENE_INFERENCE = "incorrect_scene_inference"
    WRONG_CONTEXT = "wrong_context"
    PRICE_MISMATCH = "price_mismatch"
    AUDIENCE_MISMATCH = "audience_mismatch"
    RECOMMENDATION_SCOPE_ERROR = "recommendation_scope_error"
    LOCATION_CORRECTION = "location_correction"
    TIME_CORRECTION = "time_correction"
    PREFERENCE_OVERRIDE = "preference_override"
    COMPARISON_REQUEST = "comparison_request"
    URGENCY_SIGNAL = "urgency_signal"


class NormalizedSignalType(str, Enum):
    RELEVANCE_LOW = "relevance_low"
    SPECIFICITY_LOW = "specificity_low"
    VERBOSITY_HIGH = "verbosity_high"
    INCORRECT_SCENE = "incorrect_scene"
    INCORRECT_SCOPE = "incorrect_scope"
    POOR_RECOMMENDATION_QUALITY = "poor_recommendation_quality"
    INCORRECT_ENTITY_TYPE = "incorrect_entity_type"
    MISSING_PLAYBOOK = "missing_playbook"
    STRATEGY_MISMATCH = "strategy_mismatch"
    TONE_PROBLEM = "tone_problem"


# ═══════════════════════════════════════════════════════════════════════════
# Part 1 — Explicit Feedback Event
# ═══════════════════════════════════════════════════════════════════════════


class FeedbackEvent(BaseModel):
    """Full-fidelity explicit feedback captured from the UI."""

    feedback_id: str
    session_id: str
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    turn_id: str = ""
    user_message: str = ""
    assistant_response: str = ""
    feedback_type: FeedbackType
    feedback_reasons: list[FeedbackReasonTag] = Field(default_factory=list)
    feedback_text: str = ""
    strategy_used: str = ""
    playbook_used: str = ""
    selected_entities: list[dict[str, Any]] = Field(default_factory=list)
    selected_context_blocks: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 — Implicit Feedback Event
# ═══════════════════════════════════════════════════════════════════════════


class ImplicitFeedbackEvent(BaseModel):
    """Feedback inferred from user corrections or refinement messages."""

    event_id: str
    session_id: str
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    turn_id: str = ""
    user_message: str = ""
    previous_assistant_response: str = ""
    detected_signals: list[ImplicitSignalType] = Field(default_factory=list)
    correction_details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# Part 3 — Normalized Feedback Signal
# ═══════════════════════════════════════════════════════════════════════════


class NormalizedFeedbackSignal(BaseModel):
    """
    Canonical feedback signal derived from explicit or implicit events.

    One feedback event can produce multiple normalized signals.
    """

    signal_id: str
    source_event_id: str
    source_type: Literal["explicit", "implicit"]
    session_id: str
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    signal_type: NormalizedSignalType
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    strategy_used: str = ""
    playbook_used: str = ""
    entity_types_involved: list[str] = Field(default_factory=list)
    semantic_tags_involved: list[str] = Field(default_factory=list)
    intent_domain: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# Part 4 — Session Tuning Snapshot
# ═══════════════════════════════════════════════════════════════════════════


class SessionTuningAdjustment(BaseModel):
    """A single parameter tweak applied by the session tuning engine."""

    trigger_signal: NormalizedSignalType
    trigger_reason: str = ""
    group: str
    param: str
    old_value: Any = None
    new_value: Any = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SessionTuningSnapshot(BaseModel):
    """
    Records the parameter adjustments applied during a session.

    The session tuning engine writes these; the pipeline reads
    `active_overrides` to modify behavior within the conversation.
    """

    session_id: str
    mall_id: str = "cenomi_mall_01"
    active_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    adjustment_log: list[SessionTuningAdjustment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# Part 5 — Tenant Feedback Aggregate
# ═══════════════════════════════════════════════════════════════════════════


class AggregationBucket(BaseModel):
    """Rolled-up feedback stats for one dimension slice."""

    dimension: str
    dimension_value: str
    total_feedback: int = 0
    thumbs_up: int = 0
    thumbs_down: int = 0
    approval_rate: float = 0.0
    top_negative_reasons: list[str] = Field(default_factory=list)
    top_signals: list[str] = Field(default_factory=list)
    sample_size: int = 0


class TenantFeedbackAggregate(BaseModel):
    """
    Aggregated feedback for a tenant/mall, sliced by multiple dimensions.

    The tenant parameter tuner reads these to decide what to adjust.
    """

    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    period_start: datetime | None = None
    period_end: datetime | None = None
    total_events: int = 0
    overall_approval_rate: float = 0.0
    buckets: list[AggregationBucket] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# Part 7 — Knowledge Gap Report
# ═══════════════════════════════════════════════════════════════════════════


class KnowledgeGap(BaseModel):
    """A single detected gap in semantic intelligence or playbooks."""

    gap_type: Literal[
        "missing_playbook",
        "missing_semantic_tag",
        "underperforming_entity_type",
        "missing_entity_enrichment",
        "frequent_unresolved_topic",
    ]
    description: str
    evidence_count: int = 0
    example_queries: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    priority: Literal["low", "medium", "high", "critical"] = "medium"


class KnowledgeGapReport(BaseModel):
    """Batch report of detected knowledge gaps for a tenant."""

    report_id: str
    tenant_id: str = "cenomi_mall_01"
    mall_id: str = "cenomi_mall_01"
    gaps: list[KnowledgeGap] = Field(default_factory=list)
    total_feedback_analyzed: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# Part 8 — SQL Schema (as constants for future migration tooling)
# ═══════════════════════════════════════════════════════════════════════════


SQL_SCHEMA = """
-- Feedback storage tables (PostgreSQL)
-- For current phase: JSON-file storage mirrors these structures.

CREATE TABLE IF NOT EXISTS feedback_events (
    feedback_id       TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'cenomi_mall_01',
    mall_id           TEXT NOT NULL DEFAULT 'cenomi_mall_01',
    turn_id           TEXT,
    user_message      TEXT,
    assistant_response TEXT,
    feedback_type     TEXT NOT NULL CHECK (feedback_type IN ('thumbs_up','thumbs_down')),
    feedback_reasons  JSONB DEFAULT '[]',
    feedback_text     TEXT,
    strategy_used     TEXT,
    playbook_used     TEXT,
    selected_entities JSONB DEFAULT '[]',
    selected_context_blocks JSONB DEFAULT '[]',
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_fe_session ON feedback_events(session_id);
CREATE INDEX idx_fe_tenant  ON feedback_events(tenant_id, mall_id);
CREATE INDEX idx_fe_created ON feedback_events(created_at);

CREATE TABLE IF NOT EXISTS implicit_feedback_events (
    event_id          TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'cenomi_mall_01',
    mall_id           TEXT NOT NULL DEFAULT 'cenomi_mall_01',
    turn_id           TEXT,
    user_message      TEXT,
    previous_response TEXT,
    detected_signals  JSONB DEFAULT '[]',
    correction_details JSONB DEFAULT '{}',
    confidence        REAL DEFAULT 0.0,
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_ife_session ON implicit_feedback_events(session_id);

CREATE TABLE IF NOT EXISTS normalized_feedback (
    signal_id         TEXT PRIMARY KEY,
    source_event_id   TEXT NOT NULL,
    source_type       TEXT NOT NULL CHECK (source_type IN ('explicit','implicit')),
    session_id        TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    mall_id           TEXT NOT NULL,
    signal_type       TEXT NOT NULL,
    severity          REAL DEFAULT 0.5,
    strategy_used     TEXT,
    playbook_used     TEXT,
    entity_types      JSONB DEFAULT '[]',
    semantic_tags     JSONB DEFAULT '[]',
    intent_domain     TEXT,
    context           JSONB DEFAULT '{}',
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_nf_tenant   ON normalized_feedback(tenant_id, mall_id);
CREATE INDEX idx_nf_signal   ON normalized_feedback(signal_type);
CREATE INDEX idx_nf_strategy ON normalized_feedback(strategy_used);
CREATE INDEX idx_nf_playbook ON normalized_feedback(playbook_used);

CREATE TABLE IF NOT EXISTS session_tuning_history (
    id                SERIAL PRIMARY KEY,
    session_id        TEXT NOT NULL,
    mall_id           TEXT NOT NULL,
    trigger_signal    TEXT NOT NULL,
    trigger_reason    TEXT,
    param_group       TEXT NOT NULL,
    param_key         TEXT NOT NULL,
    old_value         JSONB,
    new_value         JSONB,
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_sth_session ON session_tuning_history(session_id);

CREATE TABLE IF NOT EXISTS tenant_feedback_aggregates (
    id                SERIAL PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    mall_id           TEXT NOT NULL,
    dimension         TEXT NOT NULL,
    dimension_value   TEXT NOT NULL,
    period_start      TIMESTAMPTZ,
    period_end        TIMESTAMPTZ,
    total_feedback    INT DEFAULT 0,
    thumbs_up         INT DEFAULT 0,
    thumbs_down       INT DEFAULT 0,
    approval_rate     REAL DEFAULT 0.0,
    top_negative_reasons JSONB DEFAULT '[]',
    top_signals       JSONB DEFAULT '[]',
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_tfa_tenant ON tenant_feedback_aggregates(tenant_id, mall_id);
CREATE INDEX idx_tfa_dim    ON tenant_feedback_aggregates(dimension, dimension_value);

CREATE TABLE IF NOT EXISTS knowledge_gap_reports (
    report_id         TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    mall_id           TEXT NOT NULL,
    gaps              JSONB DEFAULT '[]',
    total_analyzed    INT DEFAULT 0,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- Retention: keep raw events 90 days, aggregates 1 year, gap reports indefinitely.
"""
