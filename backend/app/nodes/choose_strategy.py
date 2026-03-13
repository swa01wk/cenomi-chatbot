"""
Choose Strategy node — selects the response generation strategy.

CONTRACT
────────
  Purpose:  Given intent, scene, playbook, and tenant config, select
            the best response strategy and its shape hint.
            Uses tenant strategy weights for modulation.
  Reads:    intent, scene, playbook, active_tenant_parameters
  Writes:   response_plan (ResponsePlan)
  Failure:  No clear fit → fallback_guided_response
  Routing:  Always → compose_context
"""

from __future__ import annotations

from app.models.state import ConciergeState, ResponsePlan
from app.nodes._tracing import traced_node
from app.services.tenant_runtime import TenantRuntime

_STRATEGY_RULES: list[tuple[str, dict]] = [
    ("exploration_overview", {
        "sub_intents": {
            "open_exploration", "activity_suggestion", "first_visit_guide",
        },
        "message_kinds": {"fresh_request", "followup"},
    }),
    ("direct_fact", {
        "sub_intents": {
            "store_hours", "parking_info", "location_query",
            "service_info", "prayer_room",
        },
        "message_kinds": {"fresh_request", "followup"},
    }),
    ("shortlist_recommendation", {
        "sub_intents": {
            "general_dining", "general_shopping", "general_entertainment",
            "cafe_recommendation", "dessert_recommendation", "fashion_shopping",
        },
        "message_kinds": {"fresh_request", "refinement", "followup"},
    }),
    ("gift_formula", {
        "sub_intents": {"gift_recommendation"},
        "message_kinds": {"fresh_request", "refinement"},
    }),
    ("movie_plus_food", {
        "sub_intents": {"movie_showtime"},
        "message_kinds": {"fresh_request"},
    }),
    ("mini_itinerary", {
        "sub_intents": {"family_dining"},
        "message_kinds": {"fresh_request"},
    }),
]

_PLAYBOOK_STRATEGY_MAP: dict[str, str] = {
    "pb-romantic-dinner": "shortlist_recommendation",
    "pb-family-visit": "mini_itinerary",
    "pb-gift-recommendation": "gift_formula",
    "pb-gift-girlfriend": "gift_formula",
    "pb-gift-family": "gift_formula",
    "pb-quick-bite": "shortlist_recommendation",
    "pb-quick-lunch": "shortlist_recommendation",
    "pb-dessert-combo": "shortlist_recommendation",
    "pb-movie-night": "movie_plus_food",
    "pb-movie-food": "movie_plus_food",
    "pb-kid-movie-food": "movie_plus_food",
    "pb-solo-visit": "exploration_overview",
    "pb-date-plan": "shortlist_recommendation",
    "pb-budget-plan": "budget_plan",
    "pb-budget-family": "budget_plan",
    "pb-luxury-shopping": "shortlist_recommendation",
    "pb-last-minute-gift": "gift_formula",
    "pb-anniversary": "mini_itinerary",
    "pb-child-activity-parents": "family_plan",
}

_STRATEGY_SHAPES: dict[str, str] = {
    "direct_fact": "brief_answer",
    "shortlist_recommendation": "numbered_shortlist",
    "mini_itinerary": "step_by_step",
    "gift_formula": "curated_picks",
    "movie_plus_food": "combo_suggestion",
    "family_plan": "structured_itinerary",
    "solo_plan": "casual_shortlist",
    "budget_plan": "value_focused_list",
    "exploration_overview": "mini_itinerary",
    "fallback_guided_response": "conversational",
}


@traced_node("choose_strategy")
async def choose_strategy(state: ConciergeState) -> dict:
    intent = state.intent
    playbook = state.playbook

    chosen = "fallback_guided_response"
    for strategy, rules in _STRATEGY_RULES:
        if intent.sub_intent in rules["sub_intents"]:
            if intent.message_kind in rules["message_kinds"]:
                chosen = strategy
                break

    if playbook.selected_playbook and playbook.playbook_confidence > 0.4:
        pb_strategy = _PLAYBOOK_STRATEGY_MAP.get(playbook.selected_playbook)
        if pb_strategy:
            chosen = pb_strategy

    if state.active_tenant_parameters:
        try:
            rt = TenantRuntime(state.active_tenant_parameters)
            candidates = [chosen, "fallback_guided_response"]
            if chosen != "fallback_guided_response":
                candidates.append(chosen)
            weighted = rt.select_strategy(
                candidate_strategies=list(set(candidates)),
                intent=f"{intent.domain}/{intent.sub_intent}",
                context_signals=state.scene.companions + state.scene.audience,
            )
            if weighted != "fallback_guided_response" or chosen == "fallback_guided_response":
                chosen = weighted
        except Exception:
            pass

    shape = _STRATEGY_SHAPES.get(chosen, "conversational")
    constraints = _build_constraints(intent, state.scene)

    plan = ResponsePlan(
        chosen_strategy=chosen,
        response_shape_hint=shape,
        response_constraints=constraints,
    )

    return {
        "response_plan": plan,
        "_trace_summary": f"Strategy: {chosen} | shape: {shape}",
    }


def _build_constraints(intent, scene) -> list[str]:
    constraints = ["answer_first", "ground_in_real_entities"]
    if scene.budget:
        constraints.append(f"respect_budget:{scene.budget}")
    if scene.companions:
        constraints.append(f"consider_companions:{','.join(scene.companions)}")
    if scene.rejected_options:
        constraints.append(f"exclude_rejected:{','.join(scene.rejected_options)}")
    if intent.message_kind == "correction":
        constraints.append("acknowledge_correction")
    if intent.message_kind == "refinement":
        constraints.append("build_on_previous")
    return constraints
