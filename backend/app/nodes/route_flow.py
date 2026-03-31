"""
Route Flow node — determines the active flow type for this turn.

CONTRACT
────────
  Purpose:  Apply deterministic business-policy rules on top of the LLM
            classifier's flow_type decision to produce the authoritative
            routing result: "concierge" or "factual".
            Sets flow_type, flow_routing_reason, retrieval_priority, and the
            hybrid intent bundle (primary_intent, secondary_intents, modifiers,
            dominant_context_type).
            Also resolves domain_locked, response_strategy, and filter_applied.
  Reads:    intent (including flow_type_candidate set by LLM classifier),
            scene, last_flow_type (from scene), raw_user_message
  Writes:   flow_type, flow_routing_reason, retrieval_priority, primary_intent,
            secondary_intents, modifiers, dominant_context_type,
            domain_locked, response_strategy, filter_applied
  Failure:  Falls back to concierge to preserve existing behavior
  Routing:  → graph builder uses flow_type to branch to concierge or factual path

Routing policy (in priority order):
  0. Domain lock: active factual primary intent → enforce factual across follow-ups
     (only released by explicit topic_switch or cross-domain sub_intent)
  1. Cross-mall queries → always factual
  2. Context-setting / companion_correction / acknowledgement → always concierge
  3. Constraint refinement → inherit prior flow
  4. Follow-up of factual turn → stay factual (unless pivot to planning context)
  5. Trust LLM classifier's flow_type_candidate
  6. Default → concierge
"""

from __future__ import annotations

import logging

from app.models.state import ConciergeState
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# ── Business-policy constants ───────────────────────────────────────────────

# Primary intent labels that stay in factual flow even when scene context is present
# (companions/occasion act as FILTERS, not intent replacements)
_FACTUAL_PRIMARY_INTENTS: frozenset[str] = frozenset({
    "movie_lookup",
    "location_lookup",
    "mall_fact_lookup",
    "service_lookup",
    "cross_mall_lookup",
    "offer_lookup",
    "brand_availability",
    "movie_showtime",
    "store_hours",
    "opening_hours",
    "mall_overview",
})

# Sub-intents that signal a genuine cross-domain switch — releases a factual domain lock
_DOMAIN_SWITCH_SUB_INTENTS: frozenset[str] = frozenset({
    "general_dining", "romantic_dining", "quick_bite", "family_dining",
    "cafe_recommendation", "dessert_recommendation",
    "general_shopping", "gift_recommendation", "fashion_shopping",
    "perfume_shopping", "jewelry_shopping", "accessories_shopping",
    "activity_suggestion",
    "open_exploration",
    "general_entertainment",
    "family_filter",
    "companion_context",
})

# ── Response strategy tables ─────────────────────────────────────────────────

_PRIMARY_INTENT_RESPONSE_STRATEGY: dict[str, str] = {
    "movie_lookup":              "factual_list",
    "movie_showtime":            "factual_list",
    "brand_availability":        "factual_presence",
    "location_lookup":           "factual_presence",
    "mall_fact_lookup":          "structured_overview",
    "service_lookup":            "factual_list",
    "cross_mall_lookup":         "factual_presence",
    "offer_lookup":              "factual_list",
    "store_hours":               "factual_presence",
    "opening_hours":             "structured_overview",
    "shopping_recommendation":   "guided_plan",
    "dining_recommendation":     "guided_plan",
    "gift_recommendation":       "guided_plan",
    "concierge_recommendation":  "guided_plan",
    "exploration":               "guided_plan",
}

_FILTER_STRATEGY_UPGRADES: dict[tuple[str, str], str] = {
    ("movie_lookup",   "family_filter"):   "filtered_factual_list",
    ("movie_lookup",   "kid_friendly"):    "filtered_factual_list",
    ("movie_lookup",   "budget_filter"):   "filtered_factual_list",
    ("service_lookup", "family_filter"):   "filtered_factual_list",
    ("movie_showtime", "family_filter"):   "filtered_factual_list",
    ("movie_showtime", "kid_friendly"):    "filtered_factual_list",
}


@traced_node("route_flow")
async def route_flow(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene

    flow_type = ""
    routing_reason = ""
    retrieval_priority = "medium"
    domain_locked = False

    primary_intent = intent.primary_intent or ""
    secondary_intents = list(intent.secondary_intents)
    modifiers = list(intent.modifiers)
    dominant_context_type = state.dominant_context_type or ""

    # Inherit filters/modifiers from prior scene memory (for follow-up turns)
    for f in (scene.active_secondary_filters or []):
        if f not in secondary_intents:
            secondary_intents.append(f)
    for m in (scene.active_modifiers or []):
        if m not in modifiers:
            modifiers.append(m)

    # ── 0. Domain lock: preserve factual primary intent across turns ──────────
    # When the user established a factual domain in a prior turn, subsequent
    # follow-ups stay in that domain.  Companions/scene signals act as FILTERS.
    # Only an explicit topic_switch or cross-domain sub_intent releases the lock.
    prior_factual_intent = scene.active_primary_intent or ""
    if prior_factual_intent in _FACTUAL_PRIMARY_INTENTS:
        is_explicit_switch = (
            intent.message_kind == "topic_switch"
            or intent.sub_intent in _DOMAIN_SWITCH_SUB_INTENTS
            # Cross-domain secondary intents (dining+movie, coffee+movie) release lock
            or bool({"add_dining_step", "add_coffee_step"} & set(secondary_intents))
        )
        if not is_explicit_switch:
            flow_type = "factual"
            routing_reason = (
                f"Domain lock: prior factual intent '{prior_factual_intent}' preserved; "
                f"secondary signals {secondary_intents or []} applied as filters only"
            )
            retrieval_priority = "high"
            if not primary_intent or primary_intent not in _FACTUAL_PRIMARY_INTENTS:
                primary_intent = prior_factual_intent
            domain_locked = True
        else:
            # Explicit domain switch — clear stale factual primary intent
            if not primary_intent or primary_intent in _FACTUAL_PRIMARY_INTENTS:
                primary_intent = "concierge_recommendation"

    # ── 1. Always-factual: cross-mall ─────────────────────────────────────────
    if not flow_type and (
        intent.domain == "cross_mall" or intent.sub_intent == "cross_mall_search"
    ):
        flow_type = "factual"
        routing_reason = "Cross-mall brand availability query requires exact retrieval"
        retrieval_priority = "high"
        if not primary_intent:
            primary_intent = "cross_mall_lookup"

    # ── 2. Context-setting / companion_correction / acknowledgement → concierge ─
    elif not flow_type and intent.message_kind in (
        "context_setting", "companion_correction", "acknowledgement",
    ):
        flow_type = "concierge"
        if intent.message_kind == "context_setting":
            routing_reason = (
                "Context-setting: user declared role/companions/occasion; "
                "route to concierge for acknowledgement and next-step options."
            )
            if not primary_intent:
                primary_intent = "discovery"
        elif intent.message_kind == "companion_correction":
            routing_reason = (
                "Companion correction: user correcting false companion assumption; "
                "acknowledge and ask what visitor actually wants."
            )
            primary_intent = "companion_correction_response"
        else:
            routing_reason = (
                "Acknowledgement: non-actionable filler — "
                "bot must ask a clarifying question."
            )
            primary_intent = "clarification_request"

    # ── 3. Constraint refinement → inherit prior flow ─────────────────────────
    elif not flow_type and intent.message_kind == "constraint_refinement":
        prior_flow = scene.last_flow_type or "concierge"
        flow_type = prior_flow
        routing_reason = f"Constraint refinement continues prior {prior_flow} flow"
        if not primary_intent and scene.active_primary_intent:
            primary_intent = scene.active_primary_intent

    # ── 4. Follow-up of factual turn → stay factual ───────────────────────────
    elif not flow_type and (
        scene.last_flow_type == "factual"
        and intent.message_kind in ("followup", "refinement")
    ):
        if intent.domain in ("dining", "shopping") and _has_strong_scene_context(scene):
            flow_type = "concierge"
            routing_reason = "User pivoted from factual follow-up into planning/dining context"
        else:
            flow_type = "factual"
            routing_reason = "Follow-up of previous factual turn — maintaining factual flow"
            retrieval_priority = "medium"
            if not primary_intent and scene.active_primary_intent:
                primary_intent = scene.active_primary_intent

    # ── 5. Trust the LLM classifier's flow_type decision ─────────────────────
    elif not flow_type and intent.flow_type_candidate:
        flow_type = intent.flow_type_candidate
        routing_reason = f"LLM classifier determined flow_type={flow_type}"
        if flow_type == "factual":
            retrieval_priority = "high"

    # ── 6. Default → concierge ────────────────────────────────────────────────
    if not flow_type:
        flow_type = "concierge"
        routing_reason = "Default concierge flow — no strong factual signals detected"

    # ── Resolve response strategy ─────────────────────────────────────────────
    response_strategy = _resolve_response_strategy(
        primary_intent, secondary_intents, modifiers, flow_type,
    )

    filter_applied = bool(secondary_intents or modifiers) and flow_type == "factual"

    # Topic lock stability tracking
    current_topic_lock = scene.topic_lock
    current_lock_confidence = scene.topic_lock_confidence
    if current_topic_lock and intent.message_kind == "topic_switch":
        current_lock_confidence = max(0.0, current_lock_confidence - 0.4)
    elif current_topic_lock and intent.message_kind in ("followup", "constraint_refinement"):
        current_lock_confidence = min(1.0, current_lock_confidence + 0.05)

    logger.info(
        "route_flow: %s → %s (primary=%s secondary=%s modifiers=%s priority=%s "
        "locked=%s strategy=%s topic_lock=%s lock_conf=%.2f) | %s",
        f"{intent.domain}/{intent.sub_intent}",
        flow_type,
        primary_intent,
        secondary_intents,
        modifiers,
        retrieval_priority,
        domain_locked,
        response_strategy,
        current_topic_lock,
        current_lock_confidence,
        routing_reason,
    )

    return {
        "flow_type": flow_type,
        "flow_routing_reason": routing_reason,
        "retrieval_priority": retrieval_priority,
        "primary_intent": primary_intent,
        "secondary_intents": secondary_intents,
        "modifiers": modifiers,
        "dominant_context_type": dominant_context_type,
        "domain_locked": domain_locked,
        "response_strategy": response_strategy,
        "filter_applied": filter_applied,
        "debug_enrichment": {
            "flow_type": flow_type,
            "flow_routing_reason": routing_reason,
            "retrieval_priority": retrieval_priority,
            "domain_locked": domain_locked,
            "response_strategy_resolved": response_strategy,
            "filter_applied": filter_applied,
            "topic_lock": current_topic_lock,
            "topic_lock_confidence": round(current_lock_confidence, 2),
        },
        "_trace_summary": (
            f"Flow routed → {flow_type} primary={primary_intent} "
            f"secondary={secondary_intents} locked={domain_locked} "
            f"strategy={response_strategy} (priority={retrieval_priority}) "
            f"topic_lock={current_topic_lock or 'none'} | "
            f"{routing_reason[:80]}"
        ),
    }


def _has_strong_scene_context(scene) -> bool:
    """
    True when the scene has enough context to warrant concierge treatment.

    Child/kid companions alone are intentionally excluded from has_companions
    because child context is a bias (filter on results), not a hard intent
    override.  The family_visit visit_type still counts — it is set only when
    the user explicitly declares a family outing.
    """
    _CHILD_COMPANION_TYPES: frozenset[str] = frozenset({
        "child", "kids", "son", "daughter",
    })
    has_companions = bool(
        scene.companions
        and any(c not in {"solo"} | _CHILD_COMPANION_TYPES for c in scene.companions)
    )
    has_occasion = bool(scene.occasion and scene.occasion not in ("before_movie", "after_movie"))
    has_visit_type = bool(scene.visit_type)
    has_goal = bool(scene.goal or scene.implicit_goal)
    has_scenario = bool(scene.scenario)
    return has_companions or has_occasion or has_visit_type or has_goal or has_scenario


def _resolve_response_strategy(
    primary_intent: str,
    secondary_intents: list[str],
    modifiers: list[str],
    flow_type: str,
) -> str:
    """
    Map primary_intent + secondary filters to a canonical response strategy name.
    """
    base_strategy = _PRIMARY_INTENT_RESPONSE_STRATEGY.get(primary_intent, "")

    if not base_strategy:
        if flow_type == "factual":
            return "factual_list"
        return "guided_plan"

    for (intent_key, filter_key), upgraded in _FILTER_STRATEGY_UPGRADES.items():
        if primary_intent == intent_key and (
            filter_key in secondary_intents or filter_key in modifiers
        ):
            return upgraded

    return base_strategy


def is_factual_flow(state: ConciergeState) -> bool:
    """Helper used by graph builder and other nodes to check flow type."""
    return state.flow_type == "factual"
