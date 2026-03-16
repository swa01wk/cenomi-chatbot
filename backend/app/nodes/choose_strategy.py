"""
Choose Strategy node — selects the response generation strategy.

CONTRACT
────────
  Purpose:  Given intent, scene, playbook, continuity anchor, continuity
            resolution, and tenant config, select the best response strategy
            and its shape hint.  Resolve the response_policy_profile and
            response_contract for the current turn.  Uses tenant strategy
            weights and the continuity anchor's domain/strategy for modulation.
            When the continuity resolution says "refinement", strongly prefer
            the anchor's last strategy over a fresh pick.
  Reads:    intent, scene, playbook, continuity_anchor, thread_preservation,
            continuity_resolution, active_tenant_parameters
  Writes:   response_plan (ResponsePlan), response_policy_profile,
            response_contract
  Failure:  No clear fit → fallback_guided_response
  Routing:  Always → compose_context
"""

from __future__ import annotations

from app.models.state import (
    ConciergeState,
    ResponseContract,
    ResponsePlan,
    ResponsePolicyProfile,
)
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
    anchor = state.continuity_anchor
    thread_pres = state.thread_preservation
    continuity_res = state.continuity_resolution

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

    # Continuity-aware strategy selection:
    # When the resolver says "refinement" or "extend", strongly prefer
    # the anchor's last strategy to maintain conversational consistency.
    if continuity_res.continuity_type == "refinement" and anchor.last_strategy:
        if chosen == "fallback_guided_response":
            chosen = anchor.last_strategy
        elif anchor.last_strategy != chosen:
            # Unless the new match is clearly better (playbook-backed), keep anchor
            if not (playbook.selected_playbook and playbook.playbook_confidence > 0.6):
                chosen = anchor.last_strategy

    # Legacy fallback: preserve thread strategy if no better match
    if (
        thread_pres.preserve_current_topic
        and anchor.last_strategy
        and chosen == "fallback_guided_response"
    ):
        chosen = anchor.last_strategy

    behavior_score = 0.0
    policy_profile = ResponsePolicyProfile()

    if state.active_tenant_parameters:
        try:
            rt = TenantRuntime(state.active_tenant_parameters)

            rp = rt.c.response_policy
            policy_profile = ResponsePolicyProfile(
                style_family=rp.style_family,
                always_acknowledge_scene=rp.always_acknowledge_scene,
                prefer_short_contextual_intro=rp.prefer_short_contextual_intro,
                prefer_curated_shortlist=rp.prefer_curated_shortlist,
                default_shortlist_size=rp.default_shortlist_size,
                max_shortlist_size=rp.max_shortlist_size,
                prefer_one_line_reasons=rp.prefer_one_line_reasons,
                show_location_only_when_useful=rp.show_location_only_when_useful,
                deprioritize_floor_zone_details=rp.deprioritize_floor_zone_details,
                prefer_followup_narrowing_question=rp.prefer_followup_narrowing_question,
                avoid_brochure_tone=rp.avoid_brochure_tone,
                avoid_repeating_full_mall_name=rp.avoid_repeating_full_mall_name,
                prefer_decision_help_over_description=rp.prefer_decision_help_over_description,
                preserve_thread_continuity=rp.preserve_thread_continuity,
                preserve_topic_on_short_followups=rp.preserve_topic_on_short_followups,
                prefer_contextual_grouping=rp.prefer_contextual_grouping,
                prefer_price_range_when_exact_price_missing=rp.prefer_price_range_when_exact_price_missing,
            )

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

            is_continuation = continuity_res.continuity_type in ("refinement", "correction")
            is_followup = intent.message_kind in ("followup", "refinement")
            behavior_score = rt.score_strategy_behavior(
                maintains_continuity=(
                    (is_continuation or is_followup) and bool(state.scene.active_topic)
                ),
                produces_shortlist=chosen in (
                    "shortlist_recommendation", "gift_formula", "budget_plan",
                ),
                acknowledges_scene=bool(
                    state.scene.companions or state.scene.occasion or state.scene.budget
                ),
                useful_followup=rp.prefer_followup_narrowing_question,
                requires_clarification=False,
                wrong_thread=False,
            )

            if rt.should_preserve_topic(intent.message_kind, len(state.raw_user_message)):
                if intent.message_kind == "topic_switch":
                    intent = intent.model_copy(update={"message_kind": "followup"})

        except Exception:
            behavior_score = 0.0

    shape = _STRATEGY_SHAPES.get(chosen, "conversational")
    constraints = _build_constraints(intent, state.scene, anchor, continuity_res)

    plan = ResponsePlan(
        chosen_strategy=chosen,
        response_shape_hint=shape,
        response_constraints=constraints,
    )

    contract = _build_response_contract(policy_profile, chosen, state)

    return {
        "response_plan": plan,
        "response_policy_profile": policy_profile,
        "response_contract": contract,
        "_trace_summary": (
            f"Strategy: {chosen} | shape: {shape} | "
            f"behavior_score: {behavior_score:.2f} | "
            f"continuity: {continuity_res.continuity_type} | "
            f"contract: intro={contract.intro_style}, "
            f"shortlist={contract.shortlist_size}, "
            f"price={contract.exact_price_mode}"
        ),
    }


def _build_response_contract(
    policy: ResponsePolicyProfile,
    strategy: str,
    state: ConciergeState,
) -> ResponseContract:
    """Translate the resolved policy + strategy into a binding response contract."""
    continuity_res = state.continuity_resolution
    is_continuation = continuity_res.continuity_type in ("refinement", "correction")
    anchor_strong = state.continuity_anchor.is_strong

    # Intro style — continuity-aware
    if is_continuation and anchor_strong:
        intro_style: str = "topic_continuation"
    elif policy.always_acknowledge_scene and (
        state.scene.companions or state.scene.occasion
    ):
        intro_style = "scene_aware"
    elif policy.prefer_short_contextual_intro:
        intro_style = "scene_aware"
    else:
        intro_style = "fresh_greeting"

    # Shortlist size
    shortlist_size = policy.default_shortlist_size
    if strategy in ("direct_fact", "exploration_overview"):
        shortlist_size = min(shortlist_size, 4)
    shortlist_size = min(shortlist_size, policy.max_shortlist_size)

    # Explanation style
    explanation_style = "one_line_reason" if policy.prefer_one_line_reasons else "paragraph"

    # Location visibility
    if policy.show_location_only_when_useful:
        location_vis = "when_useful"
    elif policy.deprioritize_floor_zone_details:
        location_vis = "when_useful"
    else:
        location_vis = "always"

    # Follow-up style
    if policy.prefer_followup_narrowing_question:
        followup_style = "narrowing_question"
    else:
        followup_style = "open_ended"

    # Price mode — always range_only since we don't have live data
    if policy.prefer_price_range_when_exact_price_missing:
        price_mode = "range_only"
    else:
        price_mode = "suppress"

    # If this is a price question refinement, force range_only
    is_price_question = any(
        cue in state.normalized_user_message.lower()
        for cue in ("price", "how much", "cost", "pricing", "expensive", "cheap", "affordable")
    )
    if is_price_question and anchor_strong:
        price_mode = "range_only"

    return ResponseContract(
        intro_style=intro_style,
        shortlist_size=shortlist_size,
        explanation_style=explanation_style,
        location_visibility=location_vis,
        followup_style=followup_style,
        continuity_requirement=is_continuation and anchor_strong,
        brochure_tone_forbidden=policy.avoid_brochure_tone,
        exact_price_mode=price_mode,
    )


def _build_constraints(intent, scene, anchor, continuity_res) -> list[str]:
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
    if intent.is_refinement_of_current_topic:
        constraints.append("refine_current_shortlist")
    if anchor.audience:
        constraints.append(f"audience_bias:{anchor.audience}")

    # Continuity-specific constraints
    if continuity_res.continuity_type == "refinement":
        for dim in continuity_res.refinement_dimensions:
            constraints.append(f"refine_dimension:{dim}")
    if continuity_res.thread_action == "preserve" and anchor.is_strong:
        constraints.append("maintain_thread_context")

    return constraints
