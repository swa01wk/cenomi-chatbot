"""
Response Mode Resolver — thin policy layer over the LLM classifier hint.

The response_mode is primarily determined by the LLM classifier in
interpret_turn.py (intent.response_mode_hint).  This module applies
only a small set of deterministic business-policy overrides that the
LLM cannot reliably enforce from text alone (hard capability limits,
out-of-scope detection, topic-lock hard rules).

Response modes
──────────────
  direct_factual          – concise, structured, exact data
  guided_recommendation   – shortlist (3–5) with light contextual framing
  hybrid_plan             – single combined answer for cross-domain queries
  best_effort_shortlist   – safe diverse options; avoids over-specific claims
  context_acknowledgement – acknowledge situation; offer 2–4 next-step directions
  graceful_recovery       – no hallucination; explain capabilities; clarify
  clarification_request   – bot cannot help as-is; ask for clarification or explain limitation

Returns
───────
  (response_mode, confidence_level, reason, fallback_applied)
"""

from __future__ import annotations

import re as _re

from app.models.state import ConciergeState

# ─────────────────────────────────────────────────────────────────────────────
# Mode constants
# ─────────────────────────────────────────────────────────────────────────────

DIRECT_FACTUAL = "direct_factual"
GUIDED_RECOMMENDATION = "guided_recommendation"
HYBRID_PLAN = "hybrid_plan"
BEST_EFFORT_SHORTLIST = "best_effort_shortlist"
CONTEXT_ACKNOWLEDGEMENT = "context_acknowledgement"
GRACEFUL_RECOVERY = "graceful_recovery"
CLARIFICATION_REQUEST = "clarification_request"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# ─────────────────────────────────────────────────────────────────────────────
# Hard business-policy constants (retained as deterministic rules)
# ─────────────────────────────────────────────────────────────────────────────

# External venue requests → always graceful_recovery (out of scope)
_OUT_OF_SCOPE_SIGNALS: tuple[str, ...] = (
    "nearby the mall", "near the mall",
    "outside the mall", "restaurants near here",
    "restaurants near the mall", "nearby restaurants",
    "near here", "near by",
)

# Actions the bot cannot perform → clarification_request
_UNSUPPORTED_CAPABILITY_PATTERNS: tuple[str, ...] = (
    "book me a taxi", "book a taxi", "call me a taxi", "get me a taxi",
    "order a taxi", "call a taxi", "order an uber", "book an uber",
    "call me an uber", "hail a taxi", "arrange a taxi",
    "order food for me", "can you order food", "online ordering, can you order",
    "can you place an order", "place my order for me", "order for me",
    "can you book me", "book me a",
    "can you order",
    "deliver food", "food delivery for me",
    "place an order",
)

# Topic locks that should stay factual on factual flow
_FACTUAL_LOCKED_TOPICS: frozenset[str] = frozenset({
    "movies", "entertainment", "cinema", "movie_schedule", "movie_lookup",
})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _raw(state: ConciergeState) -> str:
    return (state.normalized_user_message or state.raw_user_message or "").lower().strip()


def _classify_confidence(state: ConciergeState) -> str:
    """
    Classify interpretation confidence as high / medium / low.

    Uses the LLM-reported confidence score as the primary signal,
    with simple thresholds.  Unsupported/gibberish always → low.
    """
    primary_intent = state.intent.primary_intent or state.primary_intent or ""
    if (
        primary_intent == "unsupported"
        or state.intent.raw_signals.get("unsupported", False)
        or state.intent.raw_signals.get("is_gibberish", False)
    ):
        return CONFIDENCE_LOW

    confidence = state.intent.confidence
    msg_kind = state.intent.message_kind

    # Context-setting turns: LLM already classified the intent clarity
    if msg_kind == "context_setting":
        if confidence >= 0.75:
            return CONFIDENCE_HIGH
        if confidence >= 0.45:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    if confidence >= 0.75:
        return CONFIDENCE_HIGH
    if confidence >= 0.45:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _has_strong_visit_context(state: ConciergeState) -> bool:
    """Return True when there is established scene context for this conversation."""
    scene = state.scene
    return bool(
        (scene.companions and scene.companions != ["solo"])
        or scene.occasion
        or scene.budget
        or scene.scenario
        or scene.visit_type
        or getattr(scene, "implicit_goal", None)
        or getattr(scene, "user_role", None)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main resolver
# ─────────────────────────────────────────────────────────────────────────────

def resolve_response_mode(
    state: ConciergeState,
) -> tuple[str, str, str, bool]:
    """
    Resolve the response mode for this turn.

    Returns
    ───────
    (response_mode, confidence_level, reason, fallback_applied)
    """
    raw = _raw(state)
    intent = state.intent
    scene = state.scene
    primary_intent = intent.primary_intent or state.primary_intent or ""
    confidence_level = _classify_confidence(state)

    # ── Hard override: out-of-scope external venue requests ───────────────────
    if any(p in raw for p in _OUT_OF_SCOPE_SIGNALS) and any(
        kw in raw for kw in ("restaurant", "eat", "food", "halal", "dining", "cafe")
    ):
        return (
            GRACEFUL_RECOVERY,
            CONFIDENCE_LOW,
            "out-of-scope: external/nearby venues requested — only in-mall advice available",
            True,
        )

    # ── Hard override: unsupported capability requests ────────────────────────
    if any(p in raw for p in _UNSUPPORTED_CAPABILITY_PATTERNS):
        return (
            CLARIFICATION_REQUEST,
            CONFIDENCE_HIGH,
            "unsupported capability: bot cannot perform this action; will clarify limitations",
            False,
        )

    # ── Hard override: unintelligible / gibberish input ───────────────────────
    is_hard_unsupported = (
        primary_intent == "unsupported"
        or intent.raw_signals.get("unsupported", False)
        or intent.raw_signals.get("is_gibberish", False)
    )
    if is_hard_unsupported and not _has_strong_visit_context(state):
        return (
            GRACEFUL_RECOVERY,
            CONFIDENCE_LOW,
            "unsupported or unintelligible input — graceful recovery (explain capabilities)",
            True,
        )

    # ── LLM message_kind: context declarations → always context_acknowledgement ──
    # The LLM has explicitly classified this turn as context-setting or a
    # companion correction (visitor declaring who they are / occasion / updating
    # companion info — no specific request made).
    # Honour this signal at any confidence level; the visitor needs acknowledgement
    # first, then next-step options.
    if intent.message_kind in {"context_setting", "companion_correction"}:
        return (
            CONTEXT_ACKNOWLEDGEMENT,
            confidence_level,
            f"LLM-classified message_kind={intent.message_kind!r} — acknowledge context, offer next steps",
            False,
        )

    # ── Trust the LLM-set response_mode_hint when present ────────────────────
    llm_hint = intent.response_mode_hint or ""
    if llm_hint:
        # Apply topic-lock hard rule: short follow-ups in established factual topic
        # must stay direct_factual (LLM might downgrade to guided on a short follow-up)
        is_short = len(raw.split()) <= 5
        is_followup_kind = intent.message_kind in ("followup", "refinement", "constraint_refinement")
        topic = (scene.topic_lock or "").lower()
        if (
            is_short
            and is_followup_kind
            and topic in _FACTUAL_LOCKED_TOPICS
            and state.flow_type == "factual"
        ):
            if confidence_level == CONFIDENCE_LOW:
                confidence_level = CONFIDENCE_MEDIUM
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"short follow-up resolved via topic_lock={topic!r} (factual flow)",
                False,
            )

        return (
            llm_hint,
            confidence_level,
            f"LLM-classified response_mode={llm_hint!r} (confidence={intent.confidence:.2f})",
            False,
        )

    # ── Fallback: derive from flow_type and confidence ────────────────────────
    # Only reached when the LLM left response_mode_hint empty.

    if state.flow_type == "factual":
        return (
            DIRECT_FACTUAL,
            confidence_level,
            f"factual flow fallback: {intent.sub_intent}",
            False,
        )

    if confidence_level == CONFIDENCE_HIGH:
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            f"concierge flow, high confidence: {primary_intent}",
            False,
        )

    if confidence_level == CONFIDENCE_MEDIUM:
        if intent.message_kind == "context_setting":
            return (
                CONTEXT_ACKNOWLEDGEMENT,
                confidence_level,
                "context_setting message kind — acknowledge situation, offer next steps",
                False,
            )
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            f"concierge flow, medium confidence: {primary_intent}",
            False,
        )

    # Low confidence
    if not primary_intent and not _has_strong_visit_context(state):
        return (
            GRACEFUL_RECOVERY,
            CONFIDENCE_LOW,
            "unknown intent with no visit context — graceful recovery",
            True,
        )

    return (
        BEST_EFFORT_SHORTLIST,
        CONFIDENCE_LOW,
        "low confidence with identifiable context — safe shortlist",
        True,
    )
