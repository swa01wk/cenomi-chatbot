"""
Decide Retrieval node — determines if exact fact retrieval is necessary.

CONTRACT
────────
  Purpose:  Evaluate whether the current query requires an exact data lookup
            (e.g., store hours, live offers, showtimes) or can be answered
            from pre-loaded context alone.
            Primary signal: intent.retrieval_needed (set by the LLM classifier).
            Safety net: _EXACT_INTENTS always triggers retrieval regardless of
            LLM signal, to guarantee known factual lookups are never skipped.
            Consults tenant retrieval policy for overrides.
  Reads:    intent (retrieval_needed, sub_intent), active_tenant_parameters
  Writes:   retrieval (RetrievalDecision — retrieval_needed, reason, targets)
  Failure:  Cannot determine → defaults to no retrieval
  Routing:  If retrieval_needed=True  → fetch_exact_facts
            If retrieval_needed=False → generate_response  (skip retrieval)
"""

from __future__ import annotations

from app.models.state import ConciergeState, DebugEnrichment, RetrievalDecision
from app.nodes._tracing import traced_node
from app.services.tenant_runtime import TenantRuntime

# Safety net: these sub-intents always require exact retrieval regardless of
# LLM signal. Kept as a hard guarantee for known factual lookup patterns.
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

    # Primary signal: LLM classifier's retrieval_needed judgement.
    # Safety net: _EXACT_INTENTS always retrieves regardless of LLM signal.
    llm_says_retrieve = state.intent.retrieval_needed
    exact_intent_match = sub in _EXACT_INTENTS

    # Secondary trigger: gift/guided strategies with a specific context (entity query or
    # named target person) should always retrieve so the response LLM has live entity data.
    # This catches cases where the LLM set retrieval_needed=False despite the strategy and
    # scene signals clearly indicating a personalised recommendation is needed.
    _gift_guided_retrieve = (
        not (llm_says_retrieve or exact_intent_match)
        and not skip_via_policy
        and state.response_plan.chosen_strategy in ("gift_formula", "guided_plan")
        and (
            state.intent.entity_query
            or (
                state.scene.target_person
                and state.scene.target_person not in ("", "self")
            )
        )
    )

    if (llm_says_retrieve or exact_intent_match) and not skip_via_policy:
        targets = _identify_targets(sub)
        source = "llm_signal" if llm_says_retrieve else "exact_intent_safety_net"
        decision = RetrievalDecision(
            retrieval_needed=True,
            retrieval_reason=f"Retrieval required [{source}] for sub_intent='{sub}'",
            retrieval_targets=targets,
        )
    elif _gift_guided_retrieve:
        targets = _identify_targets(sub)
        decision = RetrievalDecision(
            retrieval_needed=True,
            retrieval_reason=(
                f"Retrieval required [gift_guided_strategy] for sub_intent='{sub}' "
                f"strategy='{state.response_plan.chosen_strategy}'"
            ),
            retrieval_targets=targets,
        )
    else:
        skip_source = "tenant_policy" if skip_via_policy else "llm_signal"
        decision = RetrievalDecision(
            retrieval_needed=False,
            retrieval_reason=f"No retrieval [{skip_source}] — context-only sufficient",
        )

    discipline_reason = (
        f"sub_intent={sub!r} llm_retrieval_needed={llm_says_retrieve} "
        f"exact_match={exact_intent_match} gift_guided={_gift_guided_retrieve} "
        f"→ {'retrieval_required' if decision.retrieval_needed else 'context_only'}"
        + (" [tenant_policy_skip]" if skip_via_policy else "")
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
        # Factual lookups
        "store_hours": ["entity_hours"],
        "parking_info": ["parking_details"],
        "movie_showtime": ["movie_schedule"],
        "offer_details": ["active_offers"],
        "event_schedule": ["events"],
        "loyalty_info": ["loyalty_program"],
        "location_query": ["entity_location"],
        "prayer_room": ["facility_location"],
        "service_info": ["service_details"],
        # Concierge recommendation lookups — return entity lists for downstream LLM composition
        "general_dining": ["dining_list"],
        "quick_bite": ["dining_list"],
        "family_dining": ["dining_list"],
        "romantic_dining": ["dining_list"],
        "cafe_recommendation": ["dining_list"],
        "dessert_recommendation": ["dining_list"],
        "general_shopping": ["store_list"],
        "fashion_shopping": ["store_list"],
        "gift_recommendation": ["store_list"],
        "perfume_shopping": ["store_list"],
        "jewelry_shopping": ["store_list"],
        "accessories_shopping": ["store_list"],
        "general_entertainment": ["entertainment_list"],
        "activity_suggestion": ["entertainment_list"],
    }
    return mapping.get(sub_intent, ["general_lookup"])
