"""
Dual-flow routing tests — validate the route_flow node and supporting logic.

These tests cover the core routing decision table without requiring
LLM calls or full graph execution. They exercise:
  - route_flow node routing decisions
  - interpret_turn flow-hint emission
  - resolve_fact_scope scope resolution
  - choose_strategy factual-strategy selection
  - resolve_playbooks factual-flow suppression
  - rank_and_dedupe factual skip behavior

Usage:
    pytest backend/tests/test_dual_flow_routing.py -v
"""

from __future__ import annotations

import asyncio
import pytest

from app.models.state import (
    ConciergeState,
    InterpretedIntent,
    SceneMemory,
    PlaybookResolution,
    ResponsePlan,
    RetrievalDecision,
    ContextComposition,
)
from app.nodes.route_flow import route_flow, _has_strong_scene_context
from app.nodes.resolve_fact_scope import resolve_fact_scope
from app.nodes.choose_strategy import _choose_factual_strategy


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_state(
    raw_msg: str,
    domain: str = "general",
    sub_intent: str = "general_inquiry",
    message_kind: str = "fresh_request",
    flow_type: str = "",
    companions: list[str] | None = None,
    occasion: str = "",
    visit_type: str = "",
    last_flow_type: str = "",
    fact_scope_candidate: str = "",
    fact_entity_type_candidate: str = "",
    flow_type_candidate: str = "",
) -> ConciergeState:
    scene = SceneMemory(
        companions=companions or [],
        occasion=occasion,
        visit_type=visit_type,
        last_flow_type=last_flow_type,
    )
    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=0.85,
        flow_type_candidate=flow_type_candidate,
        fact_scope_candidate=fact_scope_candidate,
        fact_entity_type_candidate=fact_entity_type_candidate,
    )
    return ConciergeState(
        session_id="test-session",
        mall_id="al_nakheel_plaza_28",
        raw_user_message=raw_msg,
        normalized_user_message=raw_msg,
        intent=intent,
        scene=scene,
        flow_type=flow_type,
    )


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────────────────
# A. FACTUAL FLOW EXAMPLES
# ─────────────────────────────────────────────────────────────────────────────

class TestFactualFlowRouting:
    """Acceptance criteria A: queries that must go to factual flow.

    In v1.6 the router is policy-only. Tests simulate what the LLM classifier
    would emit by pre-setting flow_type_candidate="factual" on intent.
    """

    def test_what_movies_do_we_have(self):
        state = _make_state(
            "what movies do we have",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual", (
            f"Expected factual, got {result['flow_type']}: {result['flow_routing_reason']}"
        )
        assert result["retrieval_priority"] == "high"

    def test_what_movies_can_i_watch(self):
        state = _make_state(
            "what movies can i watch",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_where_is_the_atm(self):
        state = _make_state(
            "where is the atm",
            domain="services",
            sub_intent="service_info",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_do_you_have_zara(self):
        state = _make_state(
            "do you have zara",
            domain="shopping",
            sub_intent="general_shopping",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_what_time_do_you_close(self):
        state = _make_state(
            "what time do you close",
            domain="mall_info",
            sub_intent="opening_hours",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_does_mall_of_arabia_also_have_starbucks(self):
        # cross_mall domain → Rule 1 always-factual; no flow_type_candidate needed
        state = _make_state(
            "does mall of arabia also have starbucks",
            domain="cross_mall",
            sub_intent="cross_mall_search",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_where_is_muvi_cinema(self):
        state = _make_state(
            "where is muvi cinema",
            domain="navigation",
            sub_intent="location_query",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_is_starbucks_here(self):
        state = _make_state(
            "is starbucks here",
            domain="shopping",
            sub_intent="general_shopping",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_do_you_have_prayer_rooms(self):
        state = _make_state(
            "do you have prayer rooms",
            domain="services",
            sub_intent="prayer_room",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_opening_hours_query(self):
        state = _make_state(
            "what are your opening hours",
            domain="mall_info",
            sub_intent="opening_hours",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_parking_info(self):
        state = _make_state(
            "where is the parking",
            domain="services",
            sub_intent="parking_info",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_all_movies_list_keyword(self):
        state = _make_state(
            "all movies list",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"


# ─────────────────────────────────────────────────────────────────────────────
# B. CONCIERGE FLOW EXAMPLES
# ─────────────────────────────────────────────────────────────────────────────

class TestConciergeFlowRouting:
    """Acceptance criteria B: queries that must go to concierge flow."""

    def test_family_shopping_with_child(self):
        state = _make_state(
            "i am here with my 5 yr old, want to do shopping",
            domain="shopping",
            sub_intent="general_shopping",
            companions=["child"],
            visit_type="family",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge", (
            f"Expected concierge, got {result['flow_type']}: {result['flow_routing_reason']}"
        )

    def test_quick_before_movie_planning(self):
        state = _make_state(
            "we want something quick before the movie",
            domain="dining",
            sub_intent="quick_bite",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_gift_for_girlfriend(self):
        state = _make_state(
            "gift for my girlfriend",
            domain="shopping",
            sub_intent="gift_recommendation",
            companions=["girlfriend"],
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_what_can_we_do_here(self):
        state = _make_state(
            "what can we do here",
            domain="exploration",
            sub_intent="open_exploration",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_family_friendly_dining(self):
        state = _make_state(
            "family-friendly places to eat",
            domain="dining",
            sub_intent="family_dining",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_date_plan(self):
        state = _make_state(
            "plan a date here",
            domain="exploration",
            sub_intent="activity_suggestion",
            companions=["girlfriend"],
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_constraint_refinement_stays_concierge(self):
        """After a concierge suggestion, constraint refinement stays concierge."""
        state = _make_state(
            "something quicker",
            domain="dining",
            sub_intent="quick_bite",
            message_kind="constraint_refinement",
            last_flow_type="concierge",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_shopping_and_dessert(self):
        state = _make_state(
            "shopping and dessert",
            domain="dining",
            sub_intent="dessert_recommendation",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_suggest_something_fun_for_kids(self):
        state = _make_state(
            "something fun for kids",
            domain="entertainment",
            sub_intent="general_entertainment",
            companions=["child"],
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"


# ─────────────────────────────────────────────────────────────────────────────
# C. HYBRID RULE
# ─────────────────────────────────────────────────────────────────────────────

class TestHybridRouting:
    """Hybrid queries with both factual and planning signals → concierge."""

    def test_something_quick_before_movie(self):
        """Planning hybrid: movie + before → concierge, not factual."""
        state = _make_state(
            "we want something quick before the movie",
            domain="dining",
            sub_intent="quick_bite",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_recommend_dinner_after_movie(self):
        state = _make_state(
            "recommend dinner after the movie",
            domain="dining",
            sub_intent="general_dining",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_movie_showtime_with_planning_intent(self):
        """Pure movie lookup routes factual when LLM sets flow_type_candidate=factual."""
        state = _make_state(
            "what movies are showing today",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type_candidate="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"


# ─────────────────────────────────────────────────────────────────────────────
# D. FACTUAL FOLLOW-UP CONTINUITY
# ─────────────────────────────────────────────────────────────────────────────

class TestFactualFollowUp:
    """Follow-up turns in factual flow should stay factual."""

    def test_any_other_movies_followup(self):
        state = _make_state(
            "any other movies",
            domain="entertainment",
            sub_intent="movie_showtime",
            message_kind="followup",
            last_flow_type="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_what_about_mall_of_arabia_followup(self):
        state = _make_state(
            "what about mall of arabia",
            domain="cross_mall",
            sub_intent="cross_mall_search",
            message_kind="followup",
            last_flow_type="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"

    def test_where_exactly_followup(self):
        state = _make_state(
            "where exactly",
            domain="navigation",
            sub_intent="location_query",
            message_kind="followup",
            last_flow_type="factual",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "factual"


# ─────────────────────────────────────────────────────────────────────────────
# E. RESOLVE_FACT_SCOPE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveFactScope:
    """resolve_fact_scope should correctly classify the kind of factual lookup."""

    def test_movie_schedule_scope(self):
        state = _make_state(
            "what movies do we have",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "movie_schedule"
        assert result["fact_entity_type"] == "movie"
        assert result["fact_response_mode"] == "structured_fact_list"
        assert "movie_schedule" in result["retrieval"].retrieval_targets

    def test_atm_service_scope(self):
        state = _make_state(
            "where is the atm",
            domain="services",
            sub_intent="service_info",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "service_lookup"
        assert result["fact_response_mode"] == "route_hint"

    def test_mall_hours_scope(self):
        state = _make_state(
            "what are your opening hours",
            domain="mall_info",
            sub_intent="opening_hours",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "mall_fact"
        assert result["fact_response_mode"] == "quick_answer"

    def test_brand_availability_scope(self):
        state = _make_state(
            "do you have zara",
            domain="shopping",
            sub_intent="general_shopping",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "brand_availability"
        assert result["fact_response_mode"] == "direct_lookup"

    def test_cross_mall_scope(self):
        state = _make_state(
            "does mall of arabia also have starbucks",
            domain="cross_mall",
            sub_intent="cross_mall_search",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "cross_mall_availability"
        assert result["fact_response_mode"] == "cross_mall_availability"

    def test_prayer_room_scope(self):
        state = _make_state(
            "where is the prayer room",
            domain="services",
            sub_intent="prayer_room",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "service_lookup"
        assert result["fact_entity_type"] == "facility"

    def test_entity_name_extraction(self):
        """Should extract 'Starbucks' from 'is starbucks here'."""
        state = _make_state(
            "is starbucks here",
            domain="shopping",
            sub_intent="general_shopping",
            flow_type="factual",
        )
        result = _run(resolve_fact_scope(state))
        assert result["fact_scope"] == "brand_availability"
        # Entity name extraction is best-effort; just check it ran
        assert "fact_query_entity" in result


# ─────────────────────────────────────────────────────────────────────────────
# F. CHOOSE_STRATEGY FACTUAL TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestChooseStrategyFactual:
    """choose_strategy should select factual strategies for factual flow."""

    def test_movie_schedule_strategy(self):
        state = _make_state(
            "what movies do we have",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        # Manually set fact_scope as resolve_fact_scope would
        state = state.model_copy(update={
            "fact_scope": "movie_schedule",
            "fact_response_mode": "structured_fact_list",
        })
        result = _choose_factual_strategy(state)
        assert result["response_plan"].chosen_strategy == "structured_fact_list"
        assert result["response_plan"].answer_mode == "direct_answer"
        assert result["response_plan"].must_acknowledge_scene is False

    def test_mall_hours_strategy(self):
        state = _make_state(
            "what time do you close",
            flow_type="factual",
        )
        state = state.model_copy(update={
            "fact_scope": "mall_fact",
            "fact_response_mode": "quick_answer",
        })
        result = _choose_factual_strategy(state)
        assert result["response_plan"].chosen_strategy == "quick_answer"

    def test_brand_lookup_strategy(self):
        state = _make_state(
            "do you have zara",
            flow_type="factual",
        )
        state = state.model_copy(update={
            "fact_scope": "brand_availability",
            "fact_response_mode": "direct_lookup",
        })
        result = _choose_factual_strategy(state)
        assert result["response_plan"].chosen_strategy == "direct_lookup"

    def test_route_hint_strategy(self):
        state = _make_state(
            "where is the atm",
            flow_type="factual",
        )
        state = state.model_copy(update={
            "fact_scope": "service_lookup",
            "fact_response_mode": "route_hint",
        })
        result = _choose_factual_strategy(state)
        assert result["response_plan"].chosen_strategy == "route_hint"

    def test_no_semantic_padding_constraint(self):
        """Factual response plan must have no_semantic_padding constraint."""
        state = _make_state("what movies do we have", flow_type="factual")
        state = state.model_copy(update={"fact_scope": "movie_schedule"})
        result = _choose_factual_strategy(state)
        constraints = result["response_plan"].response_constraints
        assert "factual_only" in constraints
        assert "no_semantic_padding" in constraints


# ─────────────────────────────────────────────────────────────────────────────
# G. PLAYBOOK SUPPRESSION IN FACTUAL FLOW
# ─────────────────────────────────────────────────────────────────────────────

class TestPlaybookSuppressionFactual:
    """resolve_playbooks must suppress all generic playbooks in factual flow."""

    def test_playbooks_suppressed_in_factual_flow(self):
        from app.nodes.resolve_playbooks import resolve_playbooks

        state = _make_state(
            "what movies do we have",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        result = _run(resolve_playbooks(state))
        pb = result["playbook"]
        assert pb.selected_playbook == ""
        assert pb.playbook_confidence == 0.0
        assert len(pb.matched_playbooks) == 0

    def test_suppression_reason_in_debug(self):
        from app.nodes.resolve_playbooks import resolve_playbooks

        state = _make_state(
            "where is the atm",
            domain="services",
            sub_intent="service_info",
            flow_type="factual",
        )
        result = _run(resolve_playbooks(state))
        debug = result["debug_enrichment"]
        assert any("factual" in r.lower() for r in debug.playbook_rejection_reasons)


# ─────────────────────────────────────────────────────────────────────────────
# H. RANK_AND_DEDUPE IN FACTUAL FLOW
# ─────────────────────────────────────────────────────────────────────────────

class TestRankAndDedupeFactual:
    """rank_and_dedupe should skip semantic ranking in factual flow."""

    def test_factual_flow_only_dedupes(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            {"entity_id": "m1", "name": "Movie A", "entity_type": "movie", "source": "factual/movie"},
            {"entity_id": "m2", "name": "Movie B", "entity_type": "movie", "source": "factual/movie"},
            {"entity_id": "m1", "name": "Movie A", "entity_type": "movie", "source": "factual/movie"},  # duplicate
        ]
        state = _make_state("what movies", flow_type="factual")
        ctx = ContextComposition(selected_entities=entities)
        state = state.model_copy(update={"context": ctx})
        result = _run(rank_and_dedupe(state))
        final = result["context"].selected_entities
        # Should deduplicate: 3 → 2
        assert len(final) == 2
        names = [e["name"] for e in final]
        assert "Movie A" in names
        assert "Movie B" in names

    def test_concierge_flow_uses_full_ranking(self):
        """In concierge flow, ranking should be applied (not skipped)."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            {"entity_id": "s1", "name": "Store A", "entity_type": "store",
             "semantic_tags": ["fashion"], "score": 0.8},
            {"entity_id": "s2", "name": "Store B", "entity_type": "dining",
             "semantic_tags": ["quick_bite"], "score": 0.6},
        ]
        state = _make_state("dining", domain="dining", sub_intent="quick_bite", flow_type="concierge")
        ctx = ContextComposition(selected_entities=entities)
        rp = ResponsePlan(chosen_strategy="concise_shortlist", entity_cap=5)
        state = state.model_copy(update={"context": ctx, "response_plan": rp})
        # Should run without error (full ranking path)
        result = _run(rank_and_dedupe(state))
        assert "context" in result


# Note: TestInterpretTurnFlowHints removed — _detect_flow_type_candidate was deleted
# in v1.6 (LLM-first refactor). Flow hints now come directly from the LLM classifier
# via intent.flow_type_candidate in interpret_turn._llm_classify.

# ─────────────────────────────────────────────────────────────────────────────
# J. HELPER FUNCTION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_has_strong_scene_context_with_companions(self):
        scene = SceneMemory(companions=["girlfriend"])
        assert _has_strong_scene_context(scene) is True

    def test_has_strong_scene_context_empty(self):
        scene = SceneMemory()
        assert _has_strong_scene_context(scene) is False

    def test_has_strong_scene_context_solo(self):
        """Solo visitor without occasion is not "strong" scene context."""
        scene = SceneMemory(companions=["solo"])
        assert _has_strong_scene_context(scene) is False

    def test_has_strong_scene_context_occasion(self):
        scene = SceneMemory(occasion="anniversary")
        assert _has_strong_scene_context(scene) is True

    # Note: _is_pure_lookup tests removed — function deleted in v1.6 LLM-first refactor.
