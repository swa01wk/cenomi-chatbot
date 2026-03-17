"""
Decide Retrieval node — determines if exact fact retrieval is necessary.

CONTRACT
────────
  Purpose:  Evaluate whether the current query requires an exact data lookup
            (e.g., store hours, live offers, showtimes) or can be answered
            from pre-loaded context alone.
            Consults tenant retrieval policy for overrides.
  Reads:    intent, context, active_tenant_parameters
  Writes:   retrieval (RetrievalDecision — retrieval_needed, reason, targets)
  Failure:  Cannot determine → defaults to no retrieval
  Routing:  If retrieval_needed=True  → fetch_exact_facts
            If retrieval_needed=False → generate_response  (skip retrieval)
"""

from __future__ import annotations

from app.models.state import ConciergeState, DebugEnrichment, RetrievalDecision
from app.nodes._tracing import traced_node
from app.services.tenant_runtime import TenantRuntime

_EXACT_INTENTS = {
    "store_hours",
    "parking_info",
    "event_schedule",
    "offer_details",
    "loyalty_info",
    "location_query",
    "movie_showtime",
    "prayer_room",
    "service_info",
}

_SKIP_INTENTS = {
    "general_dining",
    "general_shopping",
    "general_entertainment",
    "gift_recommendation",
    "romantic_dining",
    "quick_bite",
    "family_dining",
    "cafe_recommendation",
    "dessert_recommendation",
    "fashion_shopping",
    "perfume_shopping",
    "jewelry_shopping",
    "accessories_shopping",
    "general_inquiry",
    "open_exploration",
    "activity_suggestion",
    "first_visit_guide",
}


@traced_node("decide_retrieval")
async def decide_retrieval(state: ConciergeState) -> dict:
    sub = state.intent.sub_intent

    skip_via_policy = False
    if state.active_tenant_parameters:
        try:
            rt = TenantRuntime(state.active_tenant_parameters)
            is_vague = state.intent.confidence < 0.5
            skip_via_policy = rt.should_skip_retrieval(sub, is_vague=is_vague)
        except Exception:
            pass

    if sub in _EXACT_INTENTS and not skip_via_policy:
        targets = _identify_targets(sub)
        decision = RetrievalDecision(
            retrieval_needed=True,
            retrieval_reason=f"Sub-intent '{sub}' requires exact data",
            retrieval_targets=targets,
        )
    elif sub in _SKIP_INTENTS or skip_via_policy:
        decision = RetrievalDecision(
            retrieval_needed=False,
            retrieval_reason="General recommendation — context-only",
        )
    else:
        decision = RetrievalDecision(
            retrieval_needed=False,
            retrieval_reason="Default: no retrieval needed",
        )

    discipline_reason = (
        f"sub_intent={sub!r} → {'exact_retrieval_required' if decision.retrieval_needed else 'context_only'}"
        + (f" [tenant_policy_skip]" if skip_via_policy else "")
    )

    return {
        "retrieval": decision,
        "debug_enrichment": DebugEnrichment(
            retrieval_discipline_reason=discipline_reason,
        ),
        "_trace_summary": (
            f"Retrieval: {'YES' if decision.retrieval_needed else 'NO'} "
            f"— {decision.retrieval_reason}"
        ),
    }


def _identify_targets(sub_intent: str) -> list[str]:
    mapping: dict[str, list[str]] = {
        "store_hours": ["entity_hours"],
        "parking_info": ["parking_details"],
        "movie_showtime": ["movie_schedule"],
        "offer_details": ["active_offers"],
        "event_schedule": ["events"],
        "loyalty_info": ["loyalty_program"],
        "location_query": ["entity_location"],
        "prayer_room": ["facility_location"],
        "service_info": ["service_details"],
    }
    return mapping.get(sub_intent, ["general_lookup"])
