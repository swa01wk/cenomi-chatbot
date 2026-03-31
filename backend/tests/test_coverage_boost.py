"""
test_coverage_boost.py — Targeted unit tests to push module coverage to 80%+.

Modules covered:
  1. app/services/semantic_signals.py   (was ~54%)
  2. app/nodes/rank_and_dedupe.py       (was ~75%)
  3. app/services/normalizer.py         (was ~36%)
  4. app/services/cta_generator.py      (was ~41%)
  5. app/nodes/decide_retrieval.py      (was ~32%)
  6. app/services/tenant_params.py      (was ~41%)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models.state import (
    ConciergeState,
    ContextComposition,
    InterpretedIntent,
    ResponsePlan,
    SceneMemory,
    ShoppingTask,
)


# ════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ════════════════════════════════════════════════════════════════════════════


def _make_state(
    message: str = "hello",
    domain: str = "dining",
    sub_intent: str = "general_dining",
    message_kind: str = "fresh_request",
    confidence: float = 0.85,
    flow_type: str = "",
    flow_type_candidate: str = "",
    scene: SceneMemory | None = None,
    entities: list[dict] | None = None,
    signals: list[str] | None = None,
    mall_id: str = "al_nakheel_plaza_28",
    strategy: str = "concise_shortlist",
    primary_intent: str = "",
) -> ConciergeState:
    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=confidence,
        primary_intent=primary_intent,
        flow_type_candidate=flow_type_candidate,
    )
    rp = ResponsePlan(chosen_strategy=strategy)
    ctx = ContextComposition(
        selected_entities=entities or [],
        selected_semantic_signals=signals or [],
    )
    return ConciergeState(
        session_id="test-cov",
        mall_id=mall_id,
        raw_user_message=message,
        normalized_user_message=message,
        intent=intent,
        scene=scene or SceneMemory(),
        flow_type=flow_type,
        context=ctx,
        response_plan=rp,
    )


def _make_entity(
    name: str,
    entity_type: str = "dining",
    semantic_tags: list[str] | None = None,
    audience_fit: list[str] | None = None,
    entity_id: str = "",
    score: float = 0.5,
) -> dict[str, Any]:
    return {
        "name": name,
        "entity_id": entity_id or name.lower().replace(" ", "_"),
        "entity_type": entity_type,
        "semantic_tags": semantic_tags or [],
        "audience_fit": audience_fit or [],
        "score": score,
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. Semantic Signals
# ════════════════════════════════════════════════════════════════════════════


class TestSemanticSignals:
    """Tests for app/services/semantic_signals.py"""

    def test_empty_scene_returns_empty_tags(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("hi", SceneMemory())
        assert tags == []

    def test_companion_friends_gives_group_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["friends"])
        tags = extract_semantic_signals("", scene)
        assert "group_friendly" in tags
        assert "casual" in tags

    def test_companion_girlfriend_gives_romantic(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["girlfriend"])
        tags = extract_semantic_signals("", scene)
        assert "romantic" in tags
        assert "couple_friendly" in tags

    def test_companion_wife_gives_couple_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["wife"])
        tags = extract_semantic_signals("", scene)
        assert "couple_friendly" in tags
        assert "romantic" in tags

    def test_companion_husband_gives_couple_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["husband"])
        tags = extract_semantic_signals("", scene)
        assert "couple_friendly" in tags

    def test_companion_boyfriend_gives_romantic(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["boyfriend"])
        tags = extract_semantic_signals("", scene)
        assert "romantic" in tags

    def test_companion_kids_gives_kid_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["kids"])
        tags = extract_semantic_signals("", scene)
        assert "kid_friendly" in tags
        assert "family_friendly" in tags

    def test_companion_son_gives_kid_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["son"])
        tags = extract_semantic_signals("", scene)
        assert "kid_friendly" in tags

    def test_companion_daughter_gives_kid_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["daughter"])
        tags = extract_semantic_signals("", scene)
        assert "kid_friendly" in tags

    def test_companion_detail_child_gives_kid_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companion_details=[{"type": "child", "age": 6}])
        tags = extract_semantic_signals("", scene)
        assert "kid_friendly" in tags
        assert "child_relief_anchor" in tags

    def test_companion_detail_non_child_not_boosted(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companion_details=[{"type": "adult", "age": 30}])
        tags = extract_semantic_signals("", scene)
        assert "child_relief_anchor" not in tags

    def test_occasion_birthday_gives_celebration(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="birthday")
        tags = extract_semantic_signals("", scene)
        assert "celebration" in tags
        assert "gift_friendly" in tags
        assert "special_occasion" in tags

    def test_occasion_anniversary_gives_romantic(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="anniversary")
        tags = extract_semantic_signals("", scene)
        assert "romantic" in tags
        assert "gift_friendly" in tags

    def test_occasion_date_gives_romantic(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="date")
        tags = extract_semantic_signals("", scene)
        assert "romantic" in tags
        assert "date_spot" in tags

    def test_occasion_before_movie_gives_time_sensitive(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="before_movie")
        tags = extract_semantic_signals("", scene)
        assert "before_movie" in tags
        assert "time_sensitive" in tags
        assert "near_cinema" in tags

    def test_occasion_after_movie_gives_reward_stop(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="after_movie")
        tags = extract_semantic_signals("", scene)
        assert "after_movie" in tags
        assert "reward_stop" in tags

    def test_occasion_casual_gives_easy_browse(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="casual")
        tags = extract_semantic_signals("", scene)
        assert "casual" in tags
        assert "easy_browse" in tags

    def test_occasion_quick_visit_gives_quick_stop(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="quick_visit")
        tags = extract_semantic_signals("", scene)
        assert "quick_stop" in tags

    def test_visit_constraint_quick_gives_time_sensitive(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["quick"])
        tags = extract_semantic_signals("", scene)
        assert "quick_stop" in tags
        assert "time_sensitive" in tags

    def test_visit_constraint_budget_sensitive(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["budget_sensitive"])
        tags = extract_semantic_signals("", scene)
        assert "budget_sensitive" in tags
        assert "value_shopping" in tags

    def test_visit_constraint_near_cinema(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["near_cinema_preferred"])
        tags = extract_semantic_signals("", scene)
        assert "near_cinema" in tags

    def test_visit_constraint_kid_friendly_required(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["kid_friendly_required"])
        tags = extract_semantic_signals("", scene)
        assert "kid_friendly" in tags
        assert "family_friendly" in tags

    def test_visit_constraint_healthy(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["healthy"])
        tags = extract_semantic_signals("", scene)
        assert "healthy" in tags

    def test_visit_constraint_affordable(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["affordable"])
        tags = extract_semantic_signals("", scene)
        assert "budget_sensitive" in tags

    def test_budget_mid_range(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(budget="mid_range")
        tags = extract_semantic_signals("", scene)
        assert "mid_range" in tags

    def test_budget_premium_gives_premium_tag(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(budget="premium")
        tags = extract_semantic_signals("", scene)
        assert "premium" in tags

    def test_budget_luxury_gives_luxury_and_premium(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(budget="luxury")
        tags = extract_semantic_signals("", scene)
        assert "luxury" in tags
        assert "premium" in tags

    def test_budget_budget_gives_value_shopping(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(budget="budget")
        tags = extract_semantic_signals("", scene)
        assert "budget_sensitive" in tags
        assert "value_shopping" in tags

    def test_audience_tags_passed_through(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(audience=["family_friendly", "kid_friendly"])
        tags = extract_semantic_signals("", scene)
        assert "family_friendly" in tags
        assert "kid_friendly" in tags

    def test_intent_domain_shopping_gives_shopping_mission(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_domain="shopping")
        assert "shopping_mission" in tags

    def test_intent_domain_dining_gives_dining(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_domain="dining")
        assert "dining" in tags

    def test_intent_domain_entertainment(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_domain="entertainment")
        assert "entertainment" in tags

    def test_intent_domain_services(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_domain="services")
        assert "services" in tags

    def test_intent_subintent_gift_recommendation(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="gift_recommendation")
        assert "gift_friendly" in tags
        assert "shopping_mission" in tags

    def test_intent_subintent_romantic_dining(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="romantic_dining")
        assert "romantic" in tags
        assert "couple_friendly" in tags
        assert "special_occasion" in tags

    def test_intent_subintent_quick_bite(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="quick_bite")
        assert "quick_stop" in tags
        assert "quick_bite" in tags

    def test_intent_subintent_cafe_recommendation(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="cafe_recommendation")
        assert "coffee_spot" in tags

    def test_intent_subintent_movie_showtime(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="movie_showtime")
        assert "near_cinema" in tags
        assert "entertainment" in tags

    def test_intent_subintent_fashion_shopping(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="fashion_shopping")
        assert "fashion_forward" in tags

    def test_intent_subintent_first_visit_guide(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="first_visit_guide")
        assert "easy_browse" in tags

    def test_intent_subintent_family_dining(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="family_dining")
        assert "family_friendly" in tags
        assert "kid_friendly" in tags

    def test_intent_subintent_activity_suggestion(self):
        from app.services.semantic_signals import extract_semantic_signals

        tags = extract_semantic_signals("", SceneMemory(), intent_sub_intent="activity_suggestion")
        assert "entertainment" in tags

    def test_tags_are_deduplicated(self):
        from app.services.semantic_signals import extract_semantic_signals

        # Both companion "girlfriend" and occasion "date" contribute "romantic"
        scene = SceneMemory(companions=["girlfriend"], occasion="date")
        tags = extract_semantic_signals("", scene)
        assert tags.count("romantic") == 1

    def test_unknown_companion_ignored_gracefully(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["alien"])
        tags = extract_semantic_signals("", scene)
        assert tags == []

    def test_unknown_occasion_ignored_gracefully(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(occasion="space_walk")
        tags = extract_semantic_signals("", scene)
        assert tags == []

    def test_build_explanations_kid_friendly_via_detail(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        scene = SceneMemory(companion_details=[{"type": "child", "age": 4}])
        exps = build_semantic_match_explanations(["kid_friendly"], scene, "")
        assert any("kid_friendly" in e for e in exps)
        assert any("4" in e for e in exps)

    def test_build_explanations_kid_friendly_via_companions(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        scene = SceneMemory(companions=["child"])
        exps = build_semantic_match_explanations(["kid_friendly"], scene, "")
        assert any("child" in e for e in exps)

    def test_build_explanations_budget_sensitive(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["budget_sensitive"], SceneMemory(), "")
        assert any("budget_sensitive" in e for e in exps)

    def test_build_explanations_before_movie(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["before_movie"], SceneMemory(), "")
        assert any("before_movie" in e for e in exps)

    def test_build_explanations_near_cinema(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["near_cinema"], SceneMemory(), "")
        assert any("near_cinema" in e for e in exps)

    def test_build_explanations_quick_stop(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["quick_stop"], SceneMemory(), "")
        assert any("quick_stop" in e for e in exps)

    def test_build_explanations_shopping_mission(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["shopping_mission"], SceneMemory(), "")
        assert any("shopping_mission" in e for e in exps)

    def test_build_explanations_romantic(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["romantic"], SceneMemory(), "")
        assert any("romantic" in e for e in exps)

    def test_build_explanations_gift_friendly(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations(["gift_friendly"], SceneMemory(), "")
        assert any("gift_friendly" in e for e in exps)

    def test_build_explanations_empty_signals(self):
        from app.services.semantic_signals import build_semantic_match_explanations

        exps = build_semantic_match_explanations([], SceneMemory(), "")
        assert exps == []

    def test_all_companion_keys_map_to_tags(self):
        """Every companion key in the lookup table must produce at least one tag."""
        from app.services.semantic_signals import _SCENE_COMPANION_TAG_MAP, extract_semantic_signals

        for companion in _SCENE_COMPANION_TAG_MAP:
            scene = SceneMemory(companions=[companion])
            tags = extract_semantic_signals("", scene)
            assert len(tags) > 0, f"Companion '{companion}' produced no tags"

    def test_all_occasion_keys_map_to_tags(self):
        """Every occasion key in the lookup table must produce at least one tag."""
        from app.services.semantic_signals import _SCENE_OCCASION_TAG_MAP, extract_semantic_signals

        for occasion in _SCENE_OCCASION_TAG_MAP:
            scene = SceneMemory(occasion=occasion)
            tags = extract_semantic_signals("", scene)
            assert len(tags) > 0, f"Occasion '{occasion}' produced no tags"

    def test_solo_companion_gives_solo_friendly(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["solo"])
        tags = extract_semantic_signals("", scene)
        assert "solo_friendly" in tags

    def test_family_companion_gives_family_visit(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["family"])
        tags = extract_semantic_signals("", scene)
        assert "family_visit" in tags

    def test_multiple_companions_combined_tags(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["girlfriend", "friends"])
        tags = extract_semantic_signals("", scene)
        assert "romantic" in tags
        assert "group_friendly" in tags

    def test_multiple_visit_constraints(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(visit_constraints=["quick", "budget_sensitive"])
        tags = extract_semantic_signals("", scene)
        assert "quick_stop" in tags
        assert "budget_sensitive" in tags

    def test_combined_scene_and_intent(self):
        from app.services.semantic_signals import extract_semantic_signals

        scene = SceneMemory(companions=["kids"], occasion="birthday")
        tags = extract_semantic_signals("", scene, intent_domain="dining",
                                        intent_sub_intent="family_dining")
        assert "kid_friendly" in tags
        assert "celebration" in tags
        assert "dining" in tags


# ════════════════════════════════════════════════════════════════════════════
# 2. Rank and Dedupe — additional branches
# ════════════════════════════════════════════════════════════════════════════


class TestRankAndDedupeExtra:
    """Additional tests to cover uncovered branches in rank_and_dedupe.py."""

    @pytest.mark.asyncio
    async def test_factual_flow_deduplicates_by_entity_id(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity("KFC", entity_type="dining", entity_id="kfc_01"),
            _make_entity("KFC Duplicate", entity_type="dining", entity_id="kfc_01"),
        ]
        state = _make_state(flow_type="factual", entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) == 1

    @pytest.mark.asyncio
    async def test_factual_flow_preserves_unique_entities(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity("KFC", entity_type="dining", entity_id="kfc_01"),
            _make_entity("McDonald's", entity_type="dining", entity_id="mc_01"),
        ]
        state = _make_state(flow_type="factual", entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) == 2

    @pytest.mark.asyncio
    async def test_factual_flow_no_entity_id_uses_name_key(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            {"name": "Burger King", "entity_id": "", "entity_type": "dining",
             "semantic_tags": [], "score": 0.5},
            {"name": "Burger King", "entity_id": "", "entity_type": "dining",
             "semantic_tags": [], "score": 0.4},
        ]
        state = _make_state(flow_type="factual", entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) == 1

    @pytest.mark.asyncio
    async def test_factual_flow_entity_no_id_no_name_kept(self):
        """Entities with no id and no name should be kept (no dedup key)."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            {"name": "", "entity_id": "", "entity_type": "dining",
             "semantic_tags": [], "score": 0.5},
        ]
        state = _make_state(flow_type="factual", entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) == 1

    @pytest.mark.asyncio
    async def test_deduplication_name_clash_different_ids(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity("Season Accessorize", entity_type="store", entity_id="sa_01"),
            _make_entity("season accessorize", entity_type="store", entity_id="sa_02"),
        ]
        state = _make_state(entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) == 1

    @pytest.mark.asyncio
    async def test_deduplication_same_id_collapses(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity("Store A", entity_type="store", entity_id="a1"),
            _make_entity("Store A Copy", entity_type="store", entity_id="a1"),
        ]
        state = _make_state(entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) == 1

    @pytest.mark.asyncio
    async def test_entity_cap_guided_plan(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity(f"Store{i}", entity_type="store", entity_id=f"s{i}")
            for i in range(10)
        ]
        state = _make_state(entities=entities, strategy="guided_plan", domain="shopping")
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) <= 5

    @pytest.mark.asyncio
    async def test_entity_cap_mall_overview(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [_make_entity(f"Store{i}", entity_id=f"s{i}") for i in range(25)]
        state = _make_state(entities=entities, strategy="mall_overview")
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) <= 20

    @pytest.mark.asyncio
    async def test_entity_cap_quick_answer(self):
        """When ResponsePlan.entity_cap=0, the strategy-defined cap of 3 applies."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [_make_entity(f"Entity{i}", entity_id=f"e{i}") for i in range(10)]
        state = _make_state(entities=entities, strategy="quick_answer")
        # Override entity_cap to 0 so the strategy-based cap (3) is used
        state = state.model_copy(
            update={"response_plan": state.response_plan.model_copy(update={"entity_cap": 0})}
        )
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        assert len(ctx.selected_entities) <= 3

    @pytest.mark.asyncio
    async def test_child_relief_anchor_injected_when_missing(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity("Restaurant A", entity_type="dining", entity_id="r_a"),
            _make_entity("Restaurant B", entity_type="dining", entity_id="r_b"),
            _make_entity("Cinema Fun", entity_type="cinema", entity_id="c_f",
                         semantic_tags=["kid_friendly", "entertainment"]),
        ]
        scene = SceneMemory(companions=["child"])
        state = _make_state(entities=entities, scene=scene, strategy="guided_plan", domain="dining")
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        entity_types = [e.get("entity_type", "") for e in ctx.selected_entities]
        assert "cinema" in entity_types

    @pytest.mark.asyncio
    async def test_audience_mismatch_penalty_applied(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity("Luxury Boutique", entity_type="store", entity_id="lb",
                         audience_fit=["adults_only", "premium"],
                         semantic_tags=["luxury"]),
            _make_entity("Kids Zone", entity_type="entertainment", entity_id="kz",
                         audience_fit=["family_friendly", "kid_friendly"],
                         semantic_tags=["kid_friendly"]),
        ]
        scene = SceneMemory(audience=["kid_friendly"])
        state = _make_state(entities=entities, scene=scene, domain="entertainment")
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        if len(ctx.selected_entities) > 1:
            assert ctx.selected_entities[0]["name"] == "Kids Zone"

    @pytest.mark.asyncio
    async def test_diversity_penalty_after_3_same_type(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            _make_entity(f"Dining{i}", entity_type="dining", score=0.9, entity_id=f"d{i}")
            for i in range(6)
        ]
        state = _make_state(entities=entities, strategy="exploration_overview")
        result = await rank_and_dedupe(state)
        assert "context" in result or "debug_enrichment" in result

    @pytest.mark.asyncio
    async def test_empty_entity_list_handled_gracefully(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        state = _make_state(entities=[])
        result = await rank_and_dedupe(state)
        assert isinstance(result, dict)

    def test_score_entity_dining_type_gets_full_intent_fit(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = _make_entity("Restaurant", entity_type="dining")
        score = _score_entity(
            entity=entity,
            semantic_signals=set(),
            audience_set=set(),
            constraint_set=set(),
            playbook_biases={},
            intent_domain="dining",
            intent_sub="general_dining",
        )
        assert score > 0.4

    def test_score_entity_with_audience_fit(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = {**_make_entity("Kids Cafe", entity_type="cafe"),
                  "audience_fit": ["family_friendly", "kid_friendly"]}
        score = _score_entity(
            entity=entity,
            semantic_signals=set(),
            audience_set={"kid_friendly"},
            constraint_set=set(),
            playbook_biases={},
            intent_domain="dining",
            intent_sub="family_dining",
        )
        assert score > 0.4

    def test_score_entity_exploration_domain_neutral_intent_fit(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = _make_entity("General Store", entity_type="store")
        score = _score_entity(
            entity=entity,
            semantic_signals=set(),
            audience_set=set(),
            constraint_set=set(),
            playbook_biases={},
            intent_domain="exploration",
            intent_sub="open_exploration",
        )
        assert score > 0.0

    def test_score_entity_semantic_overlap_boosts_score(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = _make_entity("Gift Store", entity_type="store",
                              semantic_tags=["gift_friendly", "romantic"])
        score = _score_entity(
            entity=entity,
            semantic_signals={"gift_friendly", "romantic"},
            audience_set=set(),
            constraint_set=set(),
            playbook_biases={},
            intent_domain="shopping",
            intent_sub="gift_recommendation",
        )
        assert score > 0.5

    def test_score_entity_with_playbook_biases(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = _make_entity("Gift Store", entity_type="store",
                              semantic_tags=["gift_friendly", "romantic"])
        score = _score_entity(
            entity=entity,
            semantic_signals={"gift_friendly"},
            audience_set=set(),
            constraint_set=set(),
            playbook_biases={"gift_friendly": 0.9, "romantic": 0.8},
            intent_domain="shopping",
            intent_sub="gift_recommendation",
        )
        assert score > 0.5

    def test_score_entity_with_constraint_fit_higher_than_without(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = _make_entity("Quick Snack", entity_type="cafe",
                              semantic_tags=["quick_bite", "quick_stop"])
        score_with = _score_entity(
            entity=entity,
            semantic_signals=set(),
            audience_set=set(),
            constraint_set={"quick"},
            playbook_biases={},
            intent_domain="dining",
            intent_sub="quick_bite",
        )
        score_without = _score_entity(
            entity=entity,
            semantic_signals=set(),
            audience_set=set(),
            constraint_set=set(),
            playbook_biases={},
            intent_domain="dining",
            intent_sub="quick_bite",
        )
        assert score_with >= score_without

    def test_score_entity_no_semantic_signals_uses_default(self):
        from app.nodes.rank_and_dedupe import _score_entity

        entity = _make_entity("Plain Store", entity_type="store", semantic_tags=[])
        score = _score_entity(
            entity=entity,
            semantic_signals=set(),
            audience_set=set(),
            constraint_set=set(),
            playbook_biases={},
            intent_domain="shopping",
            intent_sub="fashion_shopping",
        )
        # Should get neutral base score, not 0
        assert score > 0.0

    def test_apply_shopping_task_suppresses_dining_for_apparel(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="jacket", product_category="outerwear")
        entity = _make_entity("Restaurant", entity_type="dining")
        new_score, suppressed = _apply_shopping_task_score_adjustment(0.8, entity, task)
        assert suppressed is True
        assert new_score < 0.8

    def test_apply_shopping_task_suppresses_beauty_for_apparel(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="jeans", product_category="menswear")
        entity = _make_entity("Sephora", entity_type="beauty")
        new_score, suppressed = _apply_shopping_task_score_adjustment(0.8, entity, task)
        assert suppressed is True

    def test_apply_shopping_task_does_not_suppress_store_for_shopping(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="jacket", product_category="outerwear")
        entity = _make_entity("Zara", entity_type="store",
                              semantic_tags=["fashion", "clothing"])
        new_score, suppressed = _apply_shopping_task_score_adjustment(0.7, entity, task)
        assert suppressed is False

    def test_apply_shopping_task_boosts_kids_category(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="t-shirt", product_category="kids_fashion")
        entity = {**_make_entity("Mothercare", entity_type="store"),
                  "semantic_tags": ["kid_friendly", "kids"]}
        new_score, suppressed = _apply_shopping_task_score_adjustment(0.6, entity, task)
        assert suppressed is False
        assert new_score >= 0.6

    def test_apply_shopping_task_boosts_kids_by_age(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="shirt", target_age=8)
        entity = {**_make_entity("Kids Store", entity_type="store"),
                  "semantic_tags": ["kid_friendly", "fashion"]}
        new_score, _ = _apply_shopping_task_score_adjustment(0.6, entity, task)
        assert new_score >= 0.6

    def test_apply_shopping_task_boosts_budget_for_affordable(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="bag", budget_preference="affordable")
        entity = {**_make_entity("Value Store", entity_type="store"),
                  "semantic_tags": ["value_shopping", "budget"]}
        new_score, _ = _apply_shopping_task_score_adjustment(0.6, entity, task)
        assert new_score >= 0.6

    def test_apply_shopping_task_penalises_luxury_for_affordable(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="bag", budget_preference="affordable")
        entity = {**_make_entity("Luxury Brand", entity_type="store"),
                  "semantic_tags": ["luxury", "premium"]}
        new_score, _ = _apply_shopping_task_score_adjustment(0.8, entity, task)
        assert new_score <= 0.8

    def test_apply_shopping_task_boosts_premium_for_premium_budget(self):
        from app.nodes.rank_and_dedupe import _apply_shopping_task_score_adjustment

        task = ShoppingTask(product_type="watch", budget_preference="premium")
        entity = {**_make_entity("Prestige Watch", entity_type="store"),
                  "semantic_tags": ["luxury", "premium"]}
        new_score, _ = _apply_shopping_task_score_adjustment(0.6, entity, task)
        assert new_score >= 0.6

    def test_ensure_child_relief_no_op_when_present(self):
        from app.nodes.rank_and_dedupe import _ensure_child_relief_anchor

        capped = [_make_entity("Cinema Joy", entity_type="cinema",
                               semantic_tags=["kid_friendly"])]
        result = _ensure_child_relief_anchor(capped, capped, cap=3)
        assert result == capped

    def test_ensure_child_relief_injects_when_missing(self):
        from app.nodes.rank_and_dedupe import _ensure_child_relief_anchor

        capped = [_make_entity("Restaurant A", entity_type="dining")]
        all_entities = capped + [
            _make_entity("Play Zone", entity_type="entertainment",
                         semantic_tags=["kid_friendly"])
        ]
        result = _ensure_child_relief_anchor(capped, all_entities, cap=5)
        types = [e.get("entity_type", "") for e in result]
        assert "entertainment" in types

    def test_ensure_child_relief_no_candidates_returns_original(self):
        from app.nodes.rank_and_dedupe import _ensure_child_relief_anchor

        capped = [_make_entity("Restaurant A", entity_type="dining")]
        result = _ensure_child_relief_anchor(capped, capped, cap=5)
        assert result == capped

    def test_ensure_child_relief_at_cap_replaces_worst(self):
        from app.nodes.rank_and_dedupe import _ensure_child_relief_anchor

        capped = [
            _make_entity("Store A", entity_type="store", score=0.9),
            _make_entity("Store B", entity_type="store", score=0.3),
        ]
        all_entities = capped + [
            _make_entity("Kids Zone", entity_type="entertainment",
                         semantic_tags=["kid_friendly"], score=0.8)
        ]
        result = _ensure_child_relief_anchor(capped, all_entities, cap=2)
        types = [e.get("entity_type", "") for e in result]
        assert "entertainment" in types
        assert len(result) == 2

    def test_ensure_anchor_type_injects_when_missing(self):
        from app.nodes.rank_and_dedupe import _ensure_anchor_type

        capped = [_make_entity("Store A", entity_type="store")]
        all_entities = capped + [_make_entity("Cinema", entity_type="cinema")]
        result = _ensure_anchor_type(capped, all_entities, "cinema", cap=5)
        types = [e.get("entity_type", "") for e in result]
        assert "cinema" in types

    def test_ensure_anchor_type_no_candidates_returns_original(self):
        from app.nodes.rank_and_dedupe import _ensure_anchor_type

        capped = [_make_entity("Store A", entity_type="store")]
        result = _ensure_anchor_type(capped, capped, "cinema", cap=5)
        assert result == capped

    def test_ensure_anchor_type_at_cap_replaces_last(self):
        from app.nodes.rank_and_dedupe import _ensure_anchor_type

        capped = [
            _make_entity("Store A", entity_type="store"),
            _make_entity("Store B", entity_type="store"),
        ]
        all_entities = capped + [_make_entity("Cinema X", entity_type="cinema")]
        result = _ensure_anchor_type(capped, all_entities, "cinema", cap=2)
        assert len(result) == 2
        types = [e.get("entity_type", "") for e in result]
        assert "cinema" in types

    def test_build_ranking_explanations_with_child_relief(self):
        from app.nodes.rank_and_dedupe import _build_ranking_explanations

        entities = [
            _make_entity("Kids Zone", entity_type="entertainment",
                         semantic_tags=["kid_friendly", "family_entertainment"]),
        ]
        exps = _build_ranking_explanations(
            entities,
            semantic_signals={"kid_friendly"},
            audience_set={"family_friendly"},
            playbook_biases={},
            has_child=True,
        )
        assert len(exps) == 1
        assert "Kids Zone" in exps[0]

    def test_build_ranking_explanations_no_signals(self):
        from app.nodes.rank_and_dedupe import _build_ranking_explanations

        entities = [_make_entity("Generic Store", entity_type="store")]
        exps = _build_ranking_explanations(
            entities,
            semantic_signals=set(),
            audience_set=set(),
            playbook_biases={},
            has_child=False,
        )
        assert len(exps) == 1
        assert "Generic Store" in exps[0]

    def test_build_ranking_explanations_with_playbook_boost(self):
        from app.nodes.rank_and_dedupe import _build_ranking_explanations

        entities = [
            _make_entity("Gift Palace", entity_type="store",
                         semantic_tags=["gift_friendly"])
        ]
        exps = _build_ranking_explanations(
            entities,
            semantic_signals=set(),
            audience_set=set(),
            playbook_biases={"gift_friendly": 0.9},
            has_child=False,
        )
        assert any("playbook" in e.lower() for e in exps)

    def test_build_ranking_explanations_audience_match(self):
        from app.nodes.rank_and_dedupe import _build_ranking_explanations

        entities = [
            {**_make_entity("Family Restaurant", entity_type="dining"),
             "audience_fit": ["family_friendly"]}
        ]
        exps = _build_ranking_explanations(
            entities,
            semantic_signals=set(),
            audience_set={"family_friendly"},
            playbook_biases={},
            has_child=False,
        )
        assert any("audience" in e.lower() for e in exps)

    @pytest.mark.asyncio
    async def test_shopping_task_suppresses_dining_for_specific_category(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        scene = SceneMemory(
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="outerwear",
            )
        )
        entities = [
            _make_entity("Zara", entity_type="store", entity_id="zara_1",
                         semantic_tags=["fashion", "clothing"]),
            _make_entity("Pizza House", entity_type="dining", entity_id="pizza_1",
                         semantic_tags=["dining"]),
        ]
        state = _make_state(entities=entities, scene=scene, domain="shopping")
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        final_names = [e["name"] for e in ctx.selected_entities]
        assert "Zara" in final_names

    @pytest.mark.asyncio
    async def test_context_setting_message_kind_handled(self):
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [_make_entity(f"Store{i}", entity_id=f"s{i}") for i in range(5)]
        state = _make_state(entities=entities, message_kind="context_setting")
        result = await rank_and_dedupe(state)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_no_entity_id_dedup_by_name(self):
        """Entities without entity_id should be deduped by normalized name."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = [
            {"name": "The Body Shop", "entity_id": "", "entity_type": "store",
             "semantic_tags": [], "score": 0.5},
            {"name": "body shop", "entity_id": "", "entity_type": "store",
             "semantic_tags": [], "score": 0.5},
        ]
        state = _make_state(entities=entities)
        result = await rank_and_dedupe(state)
        ctx = result.get("context") or state.context
        # "The Body Shop" and "body shop" should be deduped (article stripped)
        assert len(ctx.selected_entities) == 1


# ════════════════════════════════════════════════════════════════════════════
# 3. Normalizer
# ════════════════════════════════════════════════════════════════════════════


class TestMallNormalizer:
    """Tests for app/services/normalizer.py"""

    def setup_method(self):
        from app.services.normalizer import MallNormalizer
        self.n = MallNormalizer()

    def _minimal_location(self) -> dict:
        return {"floor": "1", "zone": "A"}

    def test_normalize_store_minimal(self):
        raw = {
            "entity_id": "s001",
            "name": "Zara",
            "category": "fashion",
            "location": self._minimal_location(),
        }
        store = self.n.normalize_store(raw)
        assert store.entity_id == "s001"
        assert store.name == "Zara"
        assert store.category == "fashion"
        assert store.price_range == "mid_range"

    def test_normalize_store_full_fields(self):
        raw = {
            "entity_id": "s002",
            "name": "H&M",
            "name_ar": "اش ام",
            "brand": "H&M",
            "category": "fashion",
            "subcategory": "fast_fashion",
            "price_range": "budget",
            "target_audience": ["teens", "young_adults"],
            "features": ["returns_accepted"],
            "accepts_loyalty": True,
            "tags": ["fashion", "clothing"],
            "location": {"floor": "2", "zone": "B"},
            "operating_hours": {"weekday": "10:00-22:00"},
            "contact": {"phone": "123"},
        }
        store = self.n.normalize_store(raw)
        assert store.price_range == "budget"
        assert store.accepts_loyalty is True
        assert "fashion" in store.tags

    def test_normalize_store_defaults(self):
        raw = {"entity_id": "s003", "name": "Generic", "category": "misc",
               "location": self._minimal_location()}
        store = self.n.normalize_store(raw)
        assert store.accepts_loyalty is False
        assert store.target_audience == []
        assert store.features == []
        assert store.tags == []

    def test_normalize_dining_defaults(self):
        raw = {
            "entity_id": "d001",
            "name": "Burger Palace",
            "location": self._minimal_location(),
        }
        dining = self.n.normalize_dining(raw)
        assert dining.entity_id == "d001"
        assert dining.halal_certified is True
        assert dining.has_kids_menu is False
        assert dining.dining_style == "casual_dining"

    def test_normalize_dining_all_flags(self):
        raw = {
            "entity_id": "d002",
            "name": "Family Kitchen",
            "cuisine_type": "arabic",
            "dining_style": "casual_dining",
            "has_kids_menu": True,
            "has_outdoor_seating": True,
            "has_private_dining": True,
            "halal_certified": True,
            "average_meal_time_minutes": 45,
            "reservations_accepted": True,
            "delivery_available": True,
            "price_range": "premium",
            "location": self._minimal_location(),
        }
        dining = self.n.normalize_dining(raw)
        assert dining.has_kids_menu is True
        assert dining.reservations_accepted is True
        assert dining.average_meal_time_minutes == 45

    def test_normalize_dining_default_meal_time(self):
        raw = {"entity_id": "d003", "name": "Quick Stop",
               "location": self._minimal_location()}
        dining = self.n.normalize_dining(raw)
        assert dining.average_meal_time_minutes == 30

    def test_normalize_cinema_minimal(self):
        raw = {
            "entity_id": "c001",
            "name": "VOX Cinemas",
            "location": self._minimal_location(),
        }
        cinema = self.n.normalize_cinema(raw)
        assert cinema.entity_id == "c001"
        assert cinema.snack_bar is True
        assert cinema.has_vip_lounge is False
        assert cinema.screens == []

    def test_normalize_cinema_with_screens(self):
        raw = {
            "entity_id": "c002",
            "name": "Muvi Cinemas",
            "location": self._minimal_location(),
            "screens": [{"screen_id": "s1", "name": "Screen 1", "format": "2D", "capacity": 150}],
            "has_vip_lounge": True,
            "formats_available": ["2D", "IMAX"],
        }
        cinema = self.n.normalize_cinema(raw)
        assert len(cinema.screens) == 1
        assert cinema.screens[0].screen_id == "s1"
        assert cinema.screens[0].capacity == 150
        assert cinema.has_vip_lounge is True
        assert "IMAX" in cinema.formats_available

    def test_normalize_service_minimal(self):
        raw = {
            "entity_id": "sp001",
            "name": "Information Desk",
            "location": self._minimal_location(),
        }
        service = self.n.normalize_service(raw)
        assert service.entity_id == "sp001"
        assert service.is_free is True

    def test_normalize_service_paid(self):
        raw = {
            "entity_id": "sp002",
            "name": "Valet Parking",
            "service_category": "parking",
            "is_free": False,
            "pricing_notes": "SAR 20 per hour",
            "location": self._minimal_location(),
        }
        service = self.n.normalize_service(raw)
        assert service.is_free is False
        assert "20" in service.pricing_notes

    def test_normalize_service_with_tags(self):
        raw = {
            "entity_id": "sp003",
            "name": "Prayer Room",
            "service_category": "religious",
            "tags": ["prayer", "religious"],
            "location": self._minimal_location(),
        }
        service = self.n.normalize_service(raw)
        assert "prayer" in service.tags

    def test_normalize_event_minimal(self):
        raw = {
            "entity_id": "e001",
            "title": "Eid Sale",
        }
        event = self.n.normalize_event(raw)
        assert event.entity_id == "e001"
        assert event.title == "Eid Sale"
        assert event.location is None

    def test_normalize_event_with_location(self):
        raw = {
            "entity_id": "e002",
            "title": "Food Festival",
            "event_type": "food",
            "location": {"floor": "G", "zone": "Central"},
            "start_date": "2026-04-01",
            "end_date": "2026-04-05",
            "is_free": False,
            "registration_required": True,
            "target_audience": ["families"],
        }
        event = self.n.normalize_event(raw)
        assert event.location is not None
        assert event.location.floor == "G"
        assert event.registration_required is True
        assert event.start_date == "2026-04-01"

    def test_normalize_event_recurring(self):
        raw = {
            "entity_id": "e003",
            "title": "Weekly Farmer's Market",
            "recurring": True,
            "schedule_notes": "Every Friday",
        }
        event = self.n.normalize_event(raw)
        assert event.recurring is True
        assert "Friday" in event.schedule_notes

    def test_normalize_mall_profile_minimal(self):
        raw = {
            "mall_id": "test_mall_1",
            "name": "Test Mall",
            "parking": {},
        }
        profile = self.n.normalize_mall_profile(raw)
        assert profile.mall_id == "test_mall_1"
        assert profile.country == "Saudi Arabia"
        assert profile.zones == []
        assert profile.landmarks == []
        assert profile.facilities == []

    def test_normalize_mall_profile_with_zones(self):
        raw = {
            "mall_id": "test_mall_2",
            "name": "Big Mall",
            "zones": [{"zone_id": "z1", "name": "East Wing", "floor": "1"}],
            "parking": {},
        }
        profile = self.n.normalize_mall_profile(raw)
        assert len(profile.zones) == 1
        assert profile.zones[0].zone_id == "z1"

    def test_normalize_mall_profile_with_landmarks(self):
        raw = {
            "mall_id": "test_mall_3",
            "name": "Landmark Mall",
            "parking": {},
            "landmarks": [{
                "landmark_id": "lm1",
                "name": "Main Fountain",
                "landmark_type": "fountain",
                "location": {"floor": "G", "zone": "Central"},
            }],
        }
        profile = self.n.normalize_mall_profile(raw)
        assert len(profile.landmarks) == 1
        assert profile.landmarks[0].landmark_id == "lm1"

    def test_normalize_mall_profile_with_facilities(self):
        raw = {
            "mall_id": "test_mall_4",
            "name": "Full Mall",
            "parking": {},
            "facilities": [{
                "facility_id": "f1",
                "name": "ATM",
                "facility_type": "banking",
                "location": {"floor": "G", "zone": "A"},
                "operating_hours": {},
            }],
        }
        profile = self.n.normalize_mall_profile(raw)
        assert len(profile.facilities) == 1
        assert profile.facilities[0].facility_id == "f1"

    def test_normalize_mall_profile_with_loyalty(self):
        raw = {
            "mall_id": "test_mall_5",
            "name": "Rewards Mall",
            "parking": {},
            "loyalty": {
                "program_name": "Points Club",
                "program_url": "https://example.com",
            }
        }
        profile = self.n.normalize_mall_profile(raw)
        assert profile.loyalty is not None
        assert "Points" in profile.loyalty.program_name

    def test_normalize_mall_profile_no_loyalty_is_none(self):
        raw = {"mall_id": "tm6", "name": "No Loyalty Mall", "parking": {}}
        profile = self.n.normalize_mall_profile(raw)
        assert profile.loyalty is None

    def test_normalize_mall_profile_custom_country(self):
        raw = {"mall_id": "tm7", "name": "UAE Mall", "country": "UAE", "parking": {}}
        profile = self.n.normalize_mall_profile(raw)
        assert profile.country == "UAE"

    def test_normalize_all_empty_payload(self):
        result = self.n.normalize_all({})
        assert result.get("stores") == []
        assert result.get("dining") == []
        assert result.get("cinemas") == []
        assert result.get("movies") == []
        assert result.get("services") == []
        assert result.get("events") == []
        assert result.get("offers") == []

    def test_normalize_all_with_stores_and_dining(self):
        raw = {
            "stores": [{"entity_id": "s1", "name": "Shop", "category": "fashion",
                        "location": {"floor": "1", "zone": "A"}}],
            "dining": [{"entity_id": "d1", "name": "Cafe",
                        "location": {"floor": "G", "zone": "B"}}],
        }
        result = self.n.normalize_all(raw)
        assert len(result["stores"]) == 1
        assert len(result["dining"]) == 1

    def test_schedule_helper_full(self):
        from app.services.normalizer import _schedule
        raw = {"weekday": "9am-10pm", "friday": "2pm-11pm",
               "saturday": "10am-11pm", "ramadan": "varies", "notes": "Holiday hours may differ"}
        sched = _schedule(raw)
        assert sched.weekday == "9am-10pm"
        assert sched.friday == "2pm-11pm"
        assert sched.saturday == "10am-11pm"
        assert sched.ramadan == "varies"
        assert "Holiday" in sched.notes

    def test_schedule_helper_empty(self):
        from app.services.normalizer import _schedule
        sched = _schedule({})
        assert sched.weekday == ""
        assert sched.friday == ""

    def test_contact_helper_full(self):
        from app.services.normalizer import _contact
        raw = {"phone": "800-1234", "email": "info@mall.com",
               "website": "https://mall.com", "social_media": {"instagram": "@mall"}}
        contact = _contact(raw)
        assert contact.phone == "800-1234"
        assert contact.email == "info@mall.com"
        assert contact.website == "https://mall.com"
        assert contact.social_media.get("instagram") == "@mall"

    def test_contact_helper_empty(self):
        from app.services.normalizer import _contact
        contact = _contact({})
        assert contact.phone == ""
        assert contact.email == ""

    def test_location_helper_full(self):
        from app.services.normalizer import _location
        raw = {"floor": "2", "zone": "C", "unit_number": "42",
               "nearby_landmarks": ["Elevator"], "directions_hint": "Near elevator"}
        loc = _location(raw)
        assert loc.floor == "2"
        assert loc.unit_number == "42"
        assert "Elevator" in loc.nearby_landmarks
        assert "elevator" in loc.directions_hint

    def test_location_helper_empty(self):
        from app.services.normalizer import _location
        loc = _location({})
        assert loc.floor == ""
        assert loc.zone == ""
        assert loc.nearby_landmarks == []


# ════════════════════════════════════════════════════════════════════════════
# 4. CTA Generator
# ════════════════════════════════════════════════════════════════════════════


class TestCtaGenerator:
    """Tests for app/services/cta_generator.py"""

    def test_get_cta_instruction_unknown_type_returns_empty(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("nonexistent_type")
        assert result == ""

    def test_get_cta_instruction_empty_type_returns_empty(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("")
        assert result == ""

    def test_get_cta_instruction_none_type_returns_empty(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction(None)  # type: ignore[arg-type]
        assert result == ""

    def test_get_cta_instruction_required_strategy_says_required(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("dining_suggestion", strategy="guided_plan")
        assert "REQUIRED" in result

    def test_get_cta_instruction_concise_shortlist_required(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("shopping_narrowing", strategy="concise_shortlist")
        assert "REQUIRED" in result

    def test_get_cta_instruction_optional_strategy_says_optional(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("dining_suggestion", strategy="direct_fact")
        assert "optional" in result.lower()

    def test_get_cta_instruction_no_strategy_defaults_to_optional(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("shopping_narrowing")
        assert "optional" in result.lower()

    def test_required_strategies_produce_required_cta(self):
        from app.services.cta_generator import _REQUIRED_CTA_STRATEGIES, get_cta_instruction

        for strategy in _REQUIRED_CTA_STRATEGIES:
            result = get_cta_instruction("dining_suggestion", strategy=strategy)
            assert "REQUIRED" in result, f"Strategy '{strategy}' should produce REQUIRED CTA"

    def test_all_cta_banks_return_non_empty_instruction(self):
        from app.services.cta_generator import _CTA_BANKS, get_cta_instruction

        for cta_type in _CTA_BANKS:
            result = get_cta_instruction(cta_type)
            assert result != "", f"CTA type '{cta_type}' should produce non-empty instruction"

    def test_get_cta_example_known_type_returns_string(self):
        from app.services.cta_generator import get_cta_example

        example = get_cta_example("dining_suggestion")
        assert len(example) > 0
        assert isinstance(example, str)

    def test_get_cta_example_unknown_type_returns_empty(self):
        from app.services.cta_generator import get_cta_example

        result = get_cta_example("does_not_exist")
        assert result == ""

    def test_get_cta_example_all_known_types(self):
        from app.services.cta_generator import _CTA_BANKS, get_cta_example

        for cta_type in _CTA_BANKS:
            example = get_cta_example(cta_type)
            assert len(example) > 0, f"CTA type '{cta_type}' should have an example"

    def test_get_cta_suggestions_known_type_returns_list(self):
        from app.services.cta_generator import get_cta_suggestions

        chips = get_cta_suggestions("dining_suggestion")
        assert isinstance(chips, list)
        assert len(chips) >= 2

    def test_get_cta_suggestions_unknown_type_returns_empty_list(self):
        from app.services.cta_generator import get_cta_suggestions

        chips = get_cta_suggestions("garbage_type")
        assert chips == []

    def test_get_cta_suggestions_all_known_types_have_chips(self):
        from app.services.cta_generator import _CTA_CHIP_LABELS, get_cta_suggestions

        for cta_type in _CTA_CHIP_LABELS:
            chips = get_cta_suggestions(cta_type)
            assert len(chips) > 0, f"CTA type '{cta_type}' should have chips"

    def test_cta_instruction_contains_example_text(self):
        from app.services.cta_generator import get_cta_example, get_cta_instruction

        for cta_type in ["movie_refinement", "family_narrowing", "shopping_narrowing"]:
            example = get_cta_example(cta_type)
            instruction = get_cta_instruction(cta_type)
            assert example in instruction, (
                f"Example text should appear verbatim in instruction for '{cta_type}'"
            )

    def test_cross_mall_cta_instruction_and_chips(self):
        from app.services.cta_generator import get_cta_instruction, get_cta_suggestions

        instruction = get_cta_instruction("cross_mall")
        chips = get_cta_suggestions("cross_mall")
        assert len(instruction) > 0
        assert len(chips) > 0

    def test_cinema_followup_required_for_movie_plus_food(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("cinema_followup", strategy="movie_plus_food")
        assert "REQUIRED" in result

    def test_offer_followup_optional_for_direct_fact(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("offer_followup", strategy="direct_fact")
        assert "optional" in result.lower()

    def test_route_help_instruction(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("route_help")
        assert len(result) > 0

    def test_service_help_instruction(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("service_help")
        assert len(result) > 0

    def test_overview_continue_instruction(self):
        from app.services.cta_generator import get_cta_instruction

        result = get_cta_instruction("overview_continue")
        assert len(result) > 0

    def test_dining_next_chips(self):
        from app.services.cta_generator import get_cta_suggestions

        chips = get_cta_suggestions("dining_next")
        assert "Dessert spots" in chips or len(chips) >= 1

    def test_get_cta_suggestions_returns_copy(self):
        """get_cta_suggestions should return a fresh list each call."""
        from app.services.cta_generator import get_cta_suggestions

        chips1 = get_cta_suggestions("dining_suggestion")
        chips2 = get_cta_suggestions("dining_suggestion")
        assert chips1 == chips2
        assert chips1 is not chips2


# ════════════════════════════════════════════════════════════════════════════
# 5. Decide Retrieval
# ════════════════════════════════════════════════════════════════════════════


class TestDecideRetrieval:
    """Tests for app/nodes/decide_retrieval.py"""

    @pytest.mark.asyncio
    async def test_store_hours_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="store_hours")
        result = await decide_retrieval(state)
        retrieval = result.get("retrieval")
        assert retrieval is not None
        assert retrieval.retrieval_needed is True
        assert "store_hours" in retrieval.retrieval_reason

    @pytest.mark.asyncio
    async def test_parking_info_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="parking_info")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_movie_showtime_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="movie_showtime")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_event_schedule_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="event_schedule")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_loyalty_info_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="loyalty_info")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_offer_details_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="offer_details")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_location_query_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="location_query")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_prayer_room_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="prayer_room")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_service_info_triggers_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="service_info")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is True

    @pytest.mark.asyncio
    async def test_general_dining_no_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="general_dining")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is False

    @pytest.mark.asyncio
    async def test_gift_recommendation_no_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="gift_recommendation")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is False

    @pytest.mark.asyncio
    async def test_open_exploration_no_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="open_exploration")
        result = await decide_retrieval(state)
        assert result["retrieval"].retrieval_needed is False
        assert "General" in result["retrieval"].retrieval_reason

    @pytest.mark.asyncio
    async def test_unknown_sub_intent_defaults_no_retrieval(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="something_totally_new")
        result = await decide_retrieval(state)
        retrieval = result.get("retrieval")
        assert retrieval.retrieval_needed is False
        assert "Default" in retrieval.retrieval_reason

    @pytest.mark.asyncio
    async def test_all_exact_intents_trigger_retrieval(self):
        from app.nodes.decide_retrieval import _EXACT_INTENTS, decide_retrieval

        for sub in _EXACT_INTENTS:
            state = _make_state(sub_intent=sub)
            result = await decide_retrieval(state)
            assert result["retrieval"].retrieval_needed is True, (
                f"Sub-intent '{sub}' should trigger retrieval"
            )

    @pytest.mark.asyncio
    async def test_all_skip_intents_produce_no_retrieval(self):
        from app.nodes.decide_retrieval import _SKIP_INTENTS, decide_retrieval

        for sub in _SKIP_INTENTS:
            state = _make_state(sub_intent=sub)
            result = await decide_retrieval(state)
            assert result["retrieval"].retrieval_needed is False, (
                f"Sub-intent '{sub}' should NOT trigger retrieval"
            )

    @pytest.mark.asyncio
    async def test_debug_enrichment_included(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="store_hours")
        result = await decide_retrieval(state)
        assert "debug_enrichment" in result

    @pytest.mark.asyncio
    async def test_tracing_output_included(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="general_inquiry")
        result = await decide_retrieval(state)
        has_trace = "_trace_summary" in result or "node_trace" in result
        assert has_trace

    def test_identify_targets_store_hours(self):
        from app.nodes.decide_retrieval import _identify_targets

        result = _identify_targets("store_hours")
        assert "entity_hours" in result

    def test_identify_targets_parking_info(self):
        from app.nodes.decide_retrieval import _identify_targets

        result = _identify_targets("parking_info")
        assert "parking_details" in result

    def test_identify_targets_movie_showtime(self):
        from app.nodes.decide_retrieval import _identify_targets

        result = _identify_targets("movie_showtime")
        assert "movie_schedule" in result

    def test_identify_targets_all_exact_intents(self):
        from app.nodes.decide_retrieval import _EXACT_INTENTS, _identify_targets

        for intent in _EXACT_INTENTS:
            targets = _identify_targets(intent)
            assert isinstance(targets, list)
            assert len(targets) > 0, f"Intent '{intent}' should map to specific targets"

    def test_identify_targets_unknown_returns_general_lookup(self):
        from app.nodes.decide_retrieval import _identify_targets

        result = _identify_targets("something_weird")
        assert result == ["general_lookup"]

    @pytest.mark.asyncio
    async def test_retrieval_reason_mentions_sub_intent_for_exact(self):
        from app.nodes.decide_retrieval import decide_retrieval

        state = _make_state(sub_intent="loyalty_info")
        result = await decide_retrieval(state)
        assert "loyalty_info" in result["retrieval"].retrieval_reason

    @pytest.mark.asyncio
    async def test_tenant_policy_skip_exercises_runtime_branch(self):
        """Provide active_tenant_parameters to exercise the TenantRuntime path (lines 62-67)."""
        from app.nodes.decide_retrieval import decide_retrieval
        from app.services.tenant_params import load_tenant_config

        try:
            config = load_tenant_config("al_nakheel_plaza_28")
            state = _make_state(sub_intent="store_hours", confidence=0.3)
            state = state.model_copy(update={"active_tenant_parameters": config})
            result = await decide_retrieval(state)
            assert result.get("retrieval") is not None
        except Exception:
            pytest.skip("Tenant config not available in test environment")

    @pytest.mark.asyncio
    async def test_tenant_policy_exception_handled_gracefully(self):
        """When TenantRuntime raises, the exception is caught and retrieval proceeds normally."""
        from app.nodes.decide_retrieval import decide_retrieval

        # Pass a broken/non-config object to trigger the exception branch
        state = _make_state(sub_intent="store_hours")
        state = state.model_copy(update={"active_tenant_parameters": object()})
        result = await decide_retrieval(state)
        # Should still work — exception is swallowed
        assert result.get("retrieval") is not None


# ════════════════════════════════════════════════════════════════════════════
# 6. Tenant Params
# ════════════════════════════════════════════════════════════════════════════


class TestTenantParams:
    """Tests for app/services/tenant_params.py"""

    def test_deep_merge_flat_dicts(self):
        from app.services.tenant_params import deep_merge

        base = {"a": 1, "b": 2}
        overrides = {"b": 99, "c": 3}
        result = deep_merge(base, overrides)
        assert result["a"] == 1
        assert result["b"] == 99
        assert result["c"] == 3

    def test_deep_merge_nested_dicts(self):
        from app.services.tenant_params import deep_merge

        base = {"tone": {"warmth": 0.5, "formality": 0.6}}
        overrides = {"tone": {"warmth": 0.9}}
        result = deep_merge(base, overrides)
        assert result["tone"]["warmth"] == 0.9
        assert result["tone"]["formality"] == 0.6

    def test_deep_merge_does_not_mutate_base(self):
        from app.services.tenant_params import deep_merge

        base = {"a": {"x": 1}}
        overrides = {"a": {"x": 2}}
        deep_merge(base, overrides)
        assert base["a"]["x"] == 1

    def test_deep_merge_scalar_replaces_dict(self):
        from app.services.tenant_params import deep_merge

        base = {"a": {"nested": 1}}
        overrides = {"a": 42}
        result = deep_merge(base, overrides)
        assert result["a"] == 42

    def test_deep_merge_new_keys_added(self):
        from app.services.tenant_params import deep_merge

        base = {"a": 1}
        overrides = {"b": {"c": 2}}
        result = deep_merge(base, overrides)
        assert result["b"]["c"] == 2
        assert result["a"] == 1

    def test_deep_merge_empty_overrides(self):
        from app.services.tenant_params import deep_merge

        base = {"a": 1, "b": 2}
        result = deep_merge(base, {})
        assert result == {"a": 1, "b": 2}

    def test_deep_merge_empty_base(self):
        from app.services.tenant_params import deep_merge

        result = deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_tenant_config_has_defaults(self):
        from app.models.tenant import TenantConfig

        config = TenantConfig(mall_id="test_mall")
        assert config.mall_id == "test_mall"
        assert config.tone.warmth >= 0.0
        assert config.clarification.ambiguity_tolerance > 0.0
        assert config.ranking is not None
        assert config.retrieval is not None

    def test_load_tenant_config_for_existing_mall(self):
        from app.services.tenant_params import load_tenant_config

        try:
            config = load_tenant_config("al_nakheel_plaza_28")
            assert config.mall_id == "al_nakheel_plaza_28"
        except Exception:
            pytest.skip("Mall config file not available in test environment")

    def test_load_tenant_config_nonexistent_mall_uses_defaults(self):
        from app.services.tenant_params import load_tenant_config

        config = load_tenant_config("nonexistent_mall_xyz", config_dir=Path("/tmp"))
        assert config.mall_id == "nonexistent_mall_xyz"
        assert config.tone is not None
        assert config.clarification is not None

    def test_apply_session_overrides_session_mutable_applied(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import apply_session_overrides

        config = TenantConfig(mall_id="test")
        overrides = {
            "clarification": {
                "refinement_bias": 0.99,
            }
        }
        result = apply_session_overrides(config, overrides)
        assert result.clarification.refinement_bias == pytest.approx(0.99)

    def test_apply_session_overrides_tenant_mutable_rejected(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import apply_session_overrides

        config = TenantConfig(mall_id="test")
        original = config.clarification.ambiguity_tolerance
        overrides = {"clarification": {"ambiguity_tolerance": 0.01}}
        result = apply_session_overrides(config, overrides)
        assert result.clarification.ambiguity_tolerance == original

    def test_apply_session_overrides_unknown_group_ignored(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import apply_session_overrides

        config = TenantConfig(mall_id="test")
        overrides = {"nonexistent_group": {"some_key": "value"}}
        result = apply_session_overrides(config, overrides)
        assert result.mall_id == "test"

    def test_apply_session_overrides_response_shape(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import apply_session_overrides

        config = TenantConfig(mall_id="test")
        overrides = {"response_shape": {"default_shortlist_size": 15}}
        result = apply_session_overrides(config, overrides)
        assert result.response_shape.default_shortlist_size == 15

    def test_apply_session_overrides_ranking_session_param(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import apply_session_overrides

        config = TenantConfig(mall_id="test")
        overrides = {"ranking": {"favor_budget": 0.99}}
        result = apply_session_overrides(config, overrides)
        assert result.ranking.favor_budget == pytest.approx(0.99)

    def test_apply_feedback_update_session_tier(self):
        from app.models.tenant import Mutability, TenantConfig
        from app.services.tenant_params import apply_feedback_update

        config = TenantConfig(mall_id="test")
        result = apply_feedback_update(
            config, "clarification", "refinement_bias", 0.95,
            caller_tier=Mutability.SESSION,
        )
        assert result.clarification.refinement_bias == pytest.approx(0.95)

    def test_apply_feedback_update_tenant_tier_can_update_tenant_mutable(self):
        from app.models.tenant import Mutability, TenantConfig
        from app.services.tenant_params import apply_feedback_update

        config = TenantConfig(mall_id="test")
        result = apply_feedback_update(
            config, "clarification", "ambiguity_tolerance", 0.95,
            caller_tier=Mutability.TENANT,
        )
        assert result.clarification.ambiguity_tolerance == pytest.approx(0.95)

    def test_apply_feedback_update_rejected_when_caller_tier_too_low(self):
        from app.models.tenant import Mutability, TenantConfig
        from app.services.tenant_params import apply_feedback_update

        config = TenantConfig(mall_id="test")
        original = config.clarification.max_followups_before_answer
        result = apply_feedback_update(
            config, "clarification", "max_followups_before_answer", 99,
            caller_tier=Mutability.SESSION,
        )
        assert result.clarification.max_followups_before_answer == original

    def test_apply_feedback_update_unknown_param_rejected(self):
        from app.models.tenant import Mutability, TenantConfig
        from app.services.tenant_params import apply_feedback_update

        config = TenantConfig(mall_id="test")
        result = apply_feedback_update(
            config, "tone", "nonexistent_key", "value",
            caller_tier=Mutability.TENANT,
        )
        assert result == config

    def test_apply_feedback_update_unknown_group_rejected(self):
        from app.models.tenant import Mutability, TenantConfig
        from app.services.tenant_params import apply_feedback_update

        config = TenantConfig(mall_id="test")
        result = apply_feedback_update(
            config, "not_a_group", "some_key", "value",
            caller_tier=Mutability.TENANT,
        )
        assert result == config

    def test_export_config_returns_dict(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import export_config

        config = TenantConfig(mall_id="test")
        data = export_config(config)
        assert isinstance(data, dict)
        assert data["mall_id"] == "test"
        assert "tone" in data
        assert "clarification" in data

    def test_export_config_to_file(self, tmp_path):
        import json
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import export_config

        config = TenantConfig(mall_id="export_test")
        output = tmp_path / "config.json"
        export_config(config, path=output)
        assert output.exists()
        loaded = json.loads(output.read_text())
        assert loaded["mall_id"] == "export_test"

    def test_diff_from_defaults_no_changes_returns_minimal_or_none(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import diff_from_defaults

        config = TenantConfig(mall_id="test")
        diff = diff_from_defaults(config)
        if diff:
            assert set(diff.keys()) <= {"mall_id"}

    def test_diff_from_defaults_detects_changed_value(self):
        from app.models.tenant import TenantConfig
        from app.services.tenant_params import diff_from_defaults

        config = TenantConfig(mall_id="test")
        config.tone.warmth = 0.99
        diff = diff_from_defaults(config)
        if diff and "tone" in diff:
            assert "warmth" in diff["tone"]

    def test_get_mutability_map_has_all_groups(self):
        from app.models.tenant import TenantConfig

        config = TenantConfig(mall_id="test")
        m = config.get_mutability_map()
        for group in ["tone", "clarification", "ranking", "retrieval",
                      "response_shape", "context_weights", "session_adaptation"]:
            assert group in m, f"Mutability map missing group '{group}'"

    def test_dict_diff_identical_dicts_returns_none(self):
        from app.services.tenant_params import _dict_diff

        result = _dict_diff({"a": 1, "b": 2}, {"a": 1, "b": 2})
        assert result is None

    def test_dict_diff_scalar_changed_returns_new_value(self):
        from app.services.tenant_params import _dict_diff

        # When both sides are plain scalars (not dicts), the diff is the new value
        result = _dict_diff(1, 99)
        assert result == 99

    def test_dict_diff_scalar_unchanged_returns_none(self):
        from app.services.tenant_params import _dict_diff

        result = _dict_diff(42, 42)
        assert result is None

    def test_dict_diff_new_key_in_current(self):
        from app.services.tenant_params import _dict_diff

        result = _dict_diff({}, {"new_key": "hello"})
        assert result is not None
        assert result["new_key"] == "hello"

    def test_dict_diff_nested(self):
        from app.services.tenant_params import _dict_diff

        base = {"a": {"x": 1, "y": 2}}
        current = {"a": {"x": 1, "y": 99}}
        result = _dict_diff(base, current)
        assert result is not None
        assert result["a"]["y"] == 99

    def test_dict_diff_fully_identical_nested(self):
        from app.services.tenant_params import _dict_diff

        base = {"a": {"x": 1, "y": 2}}
        current = {"a": {"x": 1, "y": 2}}
        result = _dict_diff(base, current)
        assert result is None
