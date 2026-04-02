"""
Choose Strategy node — selects the response generation strategy.

CONTRACT
────────
  Purpose:  Given intent, scene, playbook, and tenant config, select
            the best response strategy and its shape hint.
            Prioritizes concierge-guided behavior when visit context is present.
            In factual flow: selects factual strategies only.
  Reads:    intent, scene, playbook, active_tenant_parameters, flow_type,
            fact_scope, fact_response_mode
  Writes:   response_plan (ResponsePlan)
  Failure:  No clear fit → fallback_guided_response (concierge) / direct_lookup (factual)
  Routing:  Always → compose_context (concierge) or next factual node
"""

from __future__ import annotations

from app.models.state import ConciergeState, DebugEnrichment, ResponsePlan
from app.nodes._tracing import traced_node
from app.services.response_mode_resolver import resolve_response_mode
from app.services.tenant_runtime import TenantRuntime

_STRATEGY_RULES: list[tuple[str, dict]] = [
    ("mall_overview", {
        "sub_intents": {
            "overview", "what_is_available", "family_friendliness",
            "facilities_summary",
        },
        "message_kinds": {"fresh_request", "followup", "refinement"},
    }),
    ("direct_fact", {
        "sub_intents": {"opening_hours"},
        "message_kinds": {"fresh_request", "followup"},
    }),
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
    "pb-family-visit": "guided_plan",           # upgraded from mini_itinerary
    "pb-family-shopping": "guided_plan",         # new
    "pb-gift-recommendation": "gift_formula",
    "pb-gift-girlfriend": "gift_formula",
    "pb-gift-family": "gift_formula",
    "pb-quick-bite": "concise_shortlist",        # upgraded from shortlist_recommendation
    "pb-quick-lunch": "concise_shortlist",
    "pb-quick-errand": "concise_shortlist",      # new
    "pb-before-movie": "concise_shortlist",      # new
    "pb-after-movie": "concise_shortlist",       # new
    "pb-dessert-combo": "concise_shortlist",
    "pb-shop-dessert": "concise_shortlist",
    "pb-movie-night": "movie_plus_food",
    "pb-movie-food": "movie_plus_food",
    "pb-kid-movie-food": "guided_plan",          # upgraded
    "pb-solo-visit": "exploration_overview",
    "pb-date-plan": "guided_plan",               # upgraded from shortlist_recommendation
    "pb-budget-plan": "budget_plan",
    "pb-budget-family": "budget_plan",
    "pb-luxury-shop": "shortlist_recommendation",
    "pb-luxury-shopping": "shortlist_recommendation",
    "pb-last-minute-gift": "gift_formula",
    "pb-anniversary": "guided_plan",             # upgraded from mini_itinerary
    "pb-child-activity-parents": "guided_plan",
    "pb-child-activity-parents-shop": "guided_plan",
    "pb-child-activity": "guided_plan",
}

_STRATEGY_SHAPES: dict[str, str] = {
    "mall_overview": "structured_overview",
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
    # Concierge-first shapes
    "guided_plan": "concierge_guided_plan",
    "concise_shortlist": "compact_shortlist",
    "route_plus_plan": "route_with_plan",
    "quick_answer": "brief_refined_answer",
    "route_hint": "brief_answer",
    # Factual-flow shapes
    "structured_fact_list": "fact_list",
    "schedule_answer": "schedule_list",
    "direct_lookup": "yes_no_plus_location",
    "cross_mall_availability": "cross_mall_table",
    "compare_and_recommend": "comparison_list",
    # Hybrid factual shapes
    "filtered_factual_list": "filtered_fact_list",
    "factual_presence": "yes_no_plus_location",
}

# Factual fact_scope → strategy mapping
_FACT_SCOPE_STRATEGY: dict[str, str] = {
    "movie_schedule": "structured_fact_list",
    "mall_fact": "quick_answer",
    "store_lookup": "direct_lookup",
    "service_lookup": "route_hint",
    "cinema_lookup": "route_hint",
    "brand_availability": "direct_lookup",
    "cross_mall_availability": "cross_mall_availability",
    "route_hint": "route_hint",
}

# Child companion signals
_CHILD_COMPANIONS: frozenset[str] = frozenset({
    "child", "kids", "son", "daughter",
})

# Entity caps for each strategy
_STRATEGY_ENTITY_CAPS: dict[str, int] = {
    "guided_plan": 5,
    "concise_shortlist": 4,
    "quick_answer": 3,
    "route_plus_plan": 4,
    "gift_formula": 4,
    "mini_itinerary": 6,
    "movie_plus_food": 4,
    "shortlist_recommendation": 8,
    "exploration_overview": 12,
    "mall_overview": 20,
    "direct_fact": 5,
    "budget_plan": 6,
    "fallback_guided_response": 8,
}


@traced_node("choose_strategy")
async def choose_strategy(state: ConciergeState) -> dict:
    intent = state.intent
    playbook = state.playbook
    scene = state.scene

    # ── Factual flow: select factual-only strategy ────────────────────
    if state.flow_type == "factual":
        return _choose_factual_strategy(state)

    chosen = "fallback_guided_response"
    strategy_reason = "default fallback"

    for strategy, rules in _STRATEGY_RULES:
        if intent.sub_intent in rules["sub_intents"]:
            if intent.message_kind in rules["message_kinds"]:
                chosen = strategy
                strategy_reason = f"sub_intent={intent.sub_intent} matched rule"
                break

    if playbook.selected_playbook and playbook.playbook_confidence > 0.3:
        pb_strategy = _PLAYBOOK_STRATEGY_MAP.get(playbook.selected_playbook)
        if pb_strategy:
            chosen = pb_strategy
            strategy_reason = f"playbook={playbook.selected_playbook} → {pb_strategy}"

    # ── Context-aware override: guided_plan when visit context present ──
    has_child = bool(_CHILD_COMPANIONS & set(scene.companions)) or any(
        d.get("type") == "child" for d in scene.companion_details
    )
    has_companions = bool(scene.companions and scene.companions != ["solo"])
    has_occasion = bool(scene.occasion)
    has_visit_context = has_companions or has_occasion or bool(scene.visit_type)
    is_contextual_query = has_visit_context and intent.domain in (
        "shopping", "dining", "entertainment", "exploration",
    )

    # Exclude category-level lookups from being forced to guided_plan —
    # those should remain shortlist_recommendation
    is_category_query = intent.sub_intent in {
        "general_dining", "general_shopping", "cafe_recommendation",
        "dessert_recommendation", "perfume_shopping", "jewelry_shopping",
        "accessories_shopping", "fashion_shopping",
    }

    if is_contextual_query and not is_category_query and chosen not in (
        "mall_overview", "direct_fact", "gift_formula", "movie_plus_food",
    ):
        chosen = "guided_plan"
        strategy_reason = "contextual visit (companions/occasion/child) → guided_plan"

    # ── Constraint refinement: use quick_answer ───────────────────────
    if intent.message_kind == "constraint_refinement":
        chosen = "quick_answer"
        strategy_reason = "constraint_refinement message_kind → quick_answer"

    # ── Route/proximity queries → route_plus_plan ─────────────────────
    if intent.sub_intent == "location_query":
        if scene.visit_constraints and any(
            c in ("near_cinema_preferred",) for c in scene.visit_constraints
        ):
            chosen = "route_plus_plan"
            strategy_reason = "near_cinema constraint + location → route_plus_plan"

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
    constraints = _build_constraints(intent, scene)
    entity_cap = _STRATEGY_ENTITY_CAPS.get(chosen, 8)

    # ── Product-type entity cap tightening ────────────────────────────
    # When the LLM classifier extracted a specific product_type (e.g. "jackets",
    # "outerwear"), the shopping task is focused enough that the full category
    # list is unhelpful — a tighter shortlist (≤ 10) is both more accurate and
    # more readable. Driven by the LLM classifier's shopping_task output, not keywords.
    if scene.shopping_task.product_type:
        entity_cap = min(entity_cap, 10)

    # ── Family + dining cap override ──────────────────────────────────
    # When a child is present and the query is dining, cap at 3 entities
    # to avoid noisy kiosk/snack-stand results cluttering the response.
    if (
        has_child
        and chosen == "guided_plan"
        and intent.domain == "dining"
    ):
        entity_cap = 3

    # ── Determine answer_mode and tone_mode ───────────────────────────
    if chosen in ("guided_plan", "mini_itinerary", "family_plan"):
        answer_mode = "concierge_guided"
        tone_mode = "compact_human_concierge"
    elif chosen in ("concise_shortlist", "quick_answer"):
        answer_mode = "direct_answer"
        tone_mode = "compact_human_concierge"
    elif chosen in ("mall_overview", "direct_fact", "exploration_overview"):
        answer_mode = "direct_answer"
        tone_mode = "structured"
    else:
        answer_mode = "shortlist"
        tone_mode = "compact_human_concierge"

    # ── must_acknowledge_scene ────────────────────────────────────────
    # Only acknowledge on the first turn the visitor's scene is established,
    # or when scene context changes (scene_acknowledged resets on companion change).
    must_acknowledge = (
        (is_contextual_query or chosen == "guided_plan")
        and not state.scene.scene_acknowledged
    )

    # ── must_include_anchor_type ──────────────────────────────────────
    anchor_type = ""
    if has_child and chosen in ("guided_plan", "mini_itinerary", "family_plan"):
        anchor_type = "entertainment"

    # ── Response Mode Resolver ────────────────────────────────────────
    # Lightweight behavioural decision layer: determines HOW to respond
    # based on confidence, message_kind, intent clarity, and topic lock.
    # Runs after strategy selection so both pieces of information are available.
    response_mode, confidence_level, rm_reason, fallback_applied = (
        resolve_response_mode(state)
    )

    # ── Strategy → hybrid_plan upgrade ───────────────────────────────
    # When a multi-domain strategy is selected, the response mode should
    # reflect that even if the LLM didn't explicitly set hybrid_plan.
    # This uses the LLM-driven strategy signal (which is selected based on
    # intent + playbook) to infer the appropriate response mode.
    _HYBRID_STRATEGIES = frozenset({
        "movie_plus_food", "mini_itinerary", "day_plan",
        "route_plus_plan", "family_plan",
    })
    if chosen in _HYBRID_STRATEGIES and response_mode in {
        "guided_recommendation", "best_effort_shortlist", ""
    }:
        response_mode = "hybrid_plan"
        rm_reason = f"multi-domain strategy '{chosen}' → hybrid_plan inferred"
        fallback_applied = False

    # ── graceful_recovery shape: distinguish unintelligible vs off-topic ──
    # is_gibberish=True  → visitor's message was random/nonsensical → ask them to rephrase
    # is_gibberish=False → message was intelligible but off-topic (jokes, weather, etc.)
    #                      → acknowledge it can't help + pivot to what it can offer
    if response_mode == "graceful_recovery":
        if state.intent.is_gibberish:
            shape = "graceful_recovery_unintelligible"
        else:
            shape = "graceful_recovery_offtopic"

    plan = ResponsePlan(
        chosen_strategy=chosen,
        response_shape_hint=shape,
        response_constraints=constraints,
        answer_mode=answer_mode,
        tone_mode=tone_mode,
        entity_cap=entity_cap,
        must_acknowledge_scene=must_acknowledge,
        must_include_anchor_type=anchor_type,
        response_mode=response_mode,
        confidence_level=confidence_level,
    )

    # Merge response-mode debug into existing debug_enrichment (preserve prior fields)
    existing_de: DebugEnrichment = state.debug_enrichment
    updated_de = existing_de.model_copy(update={
        "response_mode": response_mode,
        "confidence_level": confidence_level,
        "response_mode_reason": rm_reason,
        "fallback_applied": fallback_applied,
    })

    return {
        "response_plan": plan,
        "debug_enrichment": updated_de,
        "_trace_summary": (
            f"Strategy: {chosen} | shape: {shape} | reason: {strategy_reason} | "
            f"response_mode: {response_mode} [{confidence_level}]"
        ),
    }


def _choose_factual_strategy(state: ConciergeState) -> dict:
    """
    Select the appropriate strategy for factual-flow queries.

    Hard rule: movie/showtime queries with successful retrieval must NEVER
    choose fallback_guided_response.

    When secondary filters are active on a list-type scope (movie schedule,
    service list) → upgrade to filtered_factual_list so the response composer
    applies genre/audience filtering in its instructions.
    """
    from app.models.state import ResponsePlan

    scope = state.fact_scope or ""
    response_mode = state.fact_response_mode or ""
    secondary_intents = state.secondary_intents or []
    modifiers = state.modifiers or []

    # ── Check if pre-resolved response_strategy from route_flow should win ──
    # route_flow already resolves the response_strategy using the full
    # primary_intent + filter combination.  Map it to the closest strategy.
    route_strategy = state.response_strategy or ""
    if route_strategy == "filtered_factual_list":
        chosen = "filtered_factual_list"
        reason = f"factual flow: route_flow resolved filtered_factual_list (secondary={secondary_intents})"
    # ── Use fact_response_mode from resolve_fact_scope if set ────────
    elif response_mode and response_mode in _STRATEGY_SHAPES:
        chosen = response_mode
        reason = f"factual flow: fact_response_mode={response_mode}"
        # Upgrade to filtered_factual_list when filters are present on list scopes
        _FILTERABLE_SCOPES = {"movie_schedule", "service_lookup", "store_lookup"}
        _ACTIVE_FILTERS = frozenset({"family_filter", "kid_friendly", "budget_filter",
                                     "romantic_filter", "proximity_filter"})
        has_active_filter = bool(_ACTIVE_FILTERS & (set(secondary_intents) | set(modifiers)))
        if has_active_filter and scope in _FILTERABLE_SCOPES:
            chosen = "filtered_factual_list"
            reason += f" → upgraded to filtered_factual_list (filters={secondary_intents})"
    elif scope in _FACT_SCOPE_STRATEGY:
        chosen = _FACT_SCOPE_STRATEGY[scope]
        reason = f"factual flow: fact_scope={scope} → {chosen}"
    else:
        chosen = "direct_lookup"
        reason = "factual flow: default direct_lookup"

    shape = _STRATEGY_SHAPES.get(chosen, "brief_answer")
    entity_cap = _STRATEGY_ENTITY_CAPS.get(chosen, 10)

    # Build response constraints — include filter hints when active
    constraints = ["answer_first", "factual_only", "no_semantic_padding"]
    if secondary_intents:
        constraints.append(f"secondary_filters:{','.join(secondary_intents)}")
    if modifiers:
        constraints.append(f"modifiers:{','.join(modifiers)}")

    # ── Response Mode Resolver (factual path) ────────────────────────
    response_mode, confidence_level, rm_reason, fallback_applied = (
        resolve_response_mode(state)
    )

    plan = ResponsePlan(
        chosen_strategy=chosen,
        response_shape_hint=shape,
        response_constraints=constraints,
        answer_mode="direct_answer",
        tone_mode="structured",
        entity_cap=entity_cap,
        must_acknowledge_scene=False,
        must_include_anchor_type="",
        secondary_filters=list(secondary_intents),
        response_mode=response_mode,
        confidence_level=confidence_level,
    )

    existing_de: DebugEnrichment = state.debug_enrichment
    updated_de = existing_de.model_copy(update={
        "response_mode": response_mode,
        "confidence_level": confidence_level,
        "response_mode_reason": rm_reason,
        "fallback_applied": fallback_applied,
    })

    return {
        "response_plan": plan,
        "debug_enrichment": updated_de,
        "_trace_summary": (
            f"Strategy[factual]: {chosen} | shape: {shape} | reason: {reason} | "
            f"response_mode: {response_mode} [{confidence_level}]"
        ),
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
    if intent.message_kind == "constraint_refinement":
        constraints.append("refine_existing_suggestion")
    if scene.visit_constraints:
        constraints.extend(scene.visit_constraints)
    return constraints
