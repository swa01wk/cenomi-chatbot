"""
Tests for hybrid-intent routing and scene extraction.

Validates the acceptance criteria from the hybrid-intent routing spec:
1. "any movies with the kid?" → factual flow, child scene captured, movie primary
2. "shopping with my child" → concierge flow, shopping primary, child modifier
3. "something quick before the movie" → concierge flow, quick+near_cinema modifiers
4. "gift for my girlfriend" → concierge flow, gifting primary, romantic modifier
5. Follow-up: "what movies are there?" then "anything with the kid?" → stays movie mode
"""

from __future__ import annotations

import asyncio
import pytest

from app.models.state import ConciergeState, InterpretedIntent, SceneMemory
from app.nodes.route_flow import route_flow, _has_strong_scene_context
from app.nodes.update_scene_memory import update_scene_memory


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_state(
    message: str = "",
    domain: str = "",
    sub_intent: str = "",
    flow_type: str = "",
    scene: SceneMemory | None = None,
    primary_intent: str = "",
    secondary_intents: list[str] | None = None,
    modifiers: list[str] | None = None,
    message_kind: str = "fresh_request",
    dominant_context_type: str = "",
    flow_type_candidate: str = "",
) -> ConciergeState:
    return ConciergeState(
        raw_user_message=message,
        normalized_user_message=message,
        flow_type=flow_type,
        intent=InterpretedIntent(
            domain=domain,
            sub_intent=sub_intent,
            message_kind=message_kind,
            primary_intent=primary_intent,
            secondary_intents=secondary_intents or [],
            modifiers=modifiers or [],
            flow_type_candidate=flow_type_candidate,
        ),
        scene=scene or SceneMemory(),
        primary_intent=primary_intent,
        secondary_intents=secondary_intents or [],
        modifiers=modifiers or [],
        dominant_context_type=dominant_context_type,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Hybrid intent bundle extraction
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="_extract_hybrid_intent_bundle removed in v1.6 — hybrid intent is LLM-classified now")
class TestHybridIntentBundle:
    """Test _extract_hybrid_intent_bundle from interpret_turn.py"""

    def test_movie_with_kid_primary_is_movie_lookup(self): pass
    def test_shopping_with_child_primary_is_shopping(self): pass
    def test_something_quick_before_movie_modifiers(self): pass
    def test_gift_for_girlfriend(self): pass
    def test_near_cinema_and_not_expensive(self): pass
    def test_family_context_does_not_overwrite_domain_primary(self): pass
    def test_modifiers_do_not_contain_duplicates(self): pass


# ═══════════════════════════════════════════════════════════════════════════
# 2. Scene extraction — companion/child detection
# ═══════════════════════════════════════════════════════════════════════════

class TestSceneCompanionExtraction:
    """Test that update_scene_memory correctly extracts companion context."""

    @pytest.mark.asyncio
    async def test_with_the_kid_sets_child_companion(self):
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        child_terms = {"child", "kids", "children", "kid"}
        assert any(c in child_terms for c in scene.companions), (
            f"Expected child/kids in companions, got {scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_with_my_child_sets_child_companion(self):
        state = _make_state(
            message="shopping with my child",
            domain="shopping",
            sub_intent="general_shopping",
            flow_type="concierge",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        child_terms = {"child", "kids", "children", "kid"}
        assert any(c in child_terms for c in scene.companions), (
            f"Expected child/kids in companions, got {scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_with_girlfriend_sets_girlfriend_companion(self):
        # v1.6: LLM may store girlfriend in target_person (shopping_task) or companions
        state = _make_state(
            message="gift for my girlfriend",
            domain="shopping",
            sub_intent="gift_recommendation",
            flow_type="concierge",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        has_companion = "girlfriend" in scene.companions or "partner" in scene.companions
        has_target = "girlfriend" in (scene.target_person or "")
        has_shopping_task = "girlfriend" in (
            (scene.shopping_task.target_person or "") if scene.shopping_task else ""
        )
        assert has_companion or has_target or has_shopping_task, (
            f"Expected girlfriend captured in companions/target_person, "
            f"got companions={scene.companions} target={scene.target_person}"
        )

    @pytest.mark.asyncio
    async def test_with_friends_sets_friends_companion(self):
        state = _make_state(
            message="with friends looking for a restaurant",
            domain="dining",
            sub_intent="general_dining",
            flow_type="concierge",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        friend_terms = {"friends", "friend", "group", "companions"}
        assert any(c in friend_terms for c in scene.companions), (
            f"Expected 'friends' in companions, got {scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_child_audience_inferred_from_companion(self):
        # v1.6: audience may be set via companions; check either audience OR companions
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        audience = scene.audience or []
        child_companions = {"child", "kids", "children", "kid"}
        companion_ok = any(c in child_companions for c in scene.companions)
        audience_ok = any(tag in audience for tag in ("family_friendly", "kid_friendly", "parent_with_child"))
        assert companion_ok or audience_ok, (
            f"Expected family audience/companion tags. audience={audience} companions={scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_factual_flow_still_extracts_child_companion(self):
        """Even in factual flow, child companion must be extracted."""
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",  # ← factual path should still extract child
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        child_terms = {"child", "kids", "children", "kid"}
        assert any(c in child_terms for c in scene.companions), (
            f"Factual flow must still extract child companion! Got {scene.companions}"
        )

    # Note: _extract_hybrid_companions tests removed — function deleted in v1.6.
    # Companion extraction is now performed by the LLM delta in update_scene_memory.


# ═══════════════════════════════════════════════════════════════════════════
# 3. Route flow — hybrid routing decisions
# ═══════════════════════════════════════════════════════════════════════════

class TestHybridRouteFlow:
    """Test that route_flow makes the correct flow decision for hybrid queries."""

    @pytest.mark.asyncio
    async def test_movies_with_kid_routes_factual(self):
        """'any movies with the kid?' → factual flow (LLM sets flow_type_candidate=factual)."""
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly", "family_friendly"],
            flow_type_candidate="factual",
        )
        result = await route_flow(state)
        assert result["flow_type"] == "factual", (
            f"Expected factual flow for movie+kid query, got {result['flow_type']}: "
            f"{result['flow_routing_reason']}"
        )

    @pytest.mark.asyncio
    async def test_movies_with_kid_sets_secondary_intents_in_result(self):
        """Route flow must propagate secondary_intents and modifiers."""
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
        )
        result = await route_flow(state)
        assert "family_filter" in result.get("secondary_intents", []), (
            f"secondary_intents not propagated: {result}"
        )
        assert "kid_friendly" in result.get("modifiers", []), (
            f"modifiers not propagated: {result}"
        )

    @pytest.mark.asyncio
    async def test_something_quick_before_movie_routes_concierge(self):
        """'something quick before the movie' → concierge (recommendation intent)."""
        state = _make_state(
            message="something quick before the movie",
            domain="dining",
            sub_intent="quick_bite",
            primary_intent="dining_recommendation",
            secondary_intents=["before_movie_constraint"],
            modifiers=["quick_stop", "time_sensitive", "near_cinema"],
        )
        result = await route_flow(state)
        assert result["flow_type"] == "concierge", (
            f"Expected concierge flow for quick-before-movie, got {result['flow_type']}: "
            f"{result['flow_routing_reason']}"
        )

    @pytest.mark.asyncio
    async def test_gift_for_girlfriend_routes_concierge(self):
        """'gift for my girlfriend' → concierge (shopping recommendation)."""
        state = _make_state(
            message="gift for my girlfriend",
            domain="shopping",
            sub_intent="gift_recommendation",
            primary_intent="gift_shopping",
            secondary_intents=["romantic_filter"],
            modifiers=["romantic", "couple_friendly"],
        )
        result = await route_flow(state)
        assert result["flow_type"] == "concierge", (
            f"Expected concierge flow for gifting, got {result['flow_type']}: "
            f"{result['flow_routing_reason']}"
        )

    @pytest.mark.asyncio
    async def test_shopping_with_child_routes_concierge(self):
        """'shopping with my child' → concierge (shopping recommendation + family filter)."""
        state = _make_state(
            message="shopping with my child",
            domain="shopping",
            sub_intent="general_shopping",
            primary_intent="shopping_recommendation",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
        )
        result = await route_flow(state)
        assert result["flow_type"] == "concierge", (
            f"Expected concierge flow for shopping+child, got {result['flow_type']}: "
            f"{result['flow_routing_reason']}"
        )

    @pytest.mark.asyncio
    async def test_child_companion_alone_does_not_override_movie_factual(self):
        """
        Having child in scene must NOT divert a factual movie query to concierge.
        The family context should be a FILTER, not an intent replacement.
        """
        scene = SceneMemory(
            companions=["child"],
            visit_type="family_visit",
            audience=["family_friendly", "kid_friendly"],
        )
        state = _make_state(
            message="what movies are showing?",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=[],
            modifiers=[],
            scene=scene,
            flow_type_candidate="factual",
        )
        result = await route_flow(state)
        assert result["flow_type"] == "factual", (
            f"Child companion in scene must NOT override factual movie query! "
            f"Got {result['flow_type']}: {result['flow_routing_reason']}"
        )

    @pytest.mark.asyncio
    async def test_followup_movie_with_kid_stays_factual(self):
        """
        Follow-up: 'what movies are there?' → 'anything with the kid?'
        Should stay in movie/factual mode with family filter added.
        """
        scene = SceneMemory(
            active_topic="entertainment",
            last_flow_type="factual",
            active_fact_scope="movie_schedule",
            active_primary_intent="movie_lookup",
            companions=["child"],
            audience=["family_friendly", "kid_friendly"],
        )
        state = _make_state(
            message="anything with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
            message_kind="followup",
            scene=scene,
        )
        result = await route_flow(state)
        assert result["flow_type"] == "factual", (
            f"Follow-up with kid filter must stay factual! "
            f"Got {result['flow_type']}: {result['flow_routing_reason']}"
        )

    def test_has_strong_scene_context_ignores_child_only(self):
        """
        _has_strong_scene_context with only child companion should NOT
        return True (child alone is handled as filter, not intent trigger).
        """
        scene = SceneMemory(companions=["child"])
        assert not _has_strong_scene_context(scene), (
            "Child companion alone should NOT trigger strong scene context "
            "(it must not override factual primary intent)"
        )

    def test_has_strong_scene_context_girlfriend_returns_true(self):
        """Girlfriend companion IS strong scene context."""
        scene = SceneMemory(companions=["girlfriend"])
        assert _has_strong_scene_context(scene), (
            "Girlfriend companion should trigger strong scene context"
        )

    def test_has_strong_scene_context_occasion_returns_true(self):
        """Occasion set returns True."""
        scene = SceneMemory(occasion="anniversary")
        assert _has_strong_scene_context(scene)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Primary intent preserved through routing
# ═══════════════════════════════════════════════════════════════════════════

class TestPrimaryIntentPreservation:
    """Ensure primary_intent is propagated through route_flow results."""

    @pytest.mark.asyncio
    async def test_movie_primary_intent_preserved_in_result(self):
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
        )
        result = await route_flow(state)
        assert result.get("primary_intent") == "movie_lookup", (
            f"Expected primary_intent=movie_lookup in result, got {result.get('primary_intent')}"
        )

    @pytest.mark.asyncio
    async def test_shopping_primary_intent_preserved(self):
        state = _make_state(
            message="shopping with my child",
            domain="shopping",
            sub_intent="general_shopping",
            primary_intent="shopping_recommendation",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
        )
        result = await route_flow(state)
        assert result.get("primary_intent") == "shopping_recommendation", (
            f"Expected shopping_recommendation, got {result.get('primary_intent')}"
        )

    @pytest.mark.asyncio
    async def test_modifiers_preserved_in_route_result(self):
        state = _make_state(
            message="closer to cinema and not expensive",
            domain="dining",
            sub_intent="general_dining",
            primary_intent="dining_recommendation",
            secondary_intents=["proximity_filter", "budget_filter"],
            modifiers=["near_cinema", "budget_sensitive"],
        )
        result = await route_flow(state)
        for mod in ["near_cinema", "budget_sensitive"]:
            assert mod in result.get("modifiers", []), (
                f"Modifier {mod} not preserved in result: {result.get('modifiers')}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Utility helpers
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="_is_pure_lookup and _extract_hybrid_intent_bundle removed in v1.6")
class TestUtilityHelpers:
    def test_is_pure_lookup_movie_list(self): pass
    def test_is_pure_lookup_where_is(self): pass
    def test_is_not_pure_lookup_with_kid(self): pass
    def test_hybrid_bundle_no_crash_on_empty_message(self): pass
    def test_hybrid_bundle_no_crash_on_unknown_domain(self): pass


# ═══════════════════════════════════════════════════════════════════════════
# 6. Acceptance criteria integration checks
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="_extract_hybrid_intent_bundle removed in v1.6 — AC tests superseded by test_llm_first_workflow.py")
class TestAcceptanceCriteria:
    """
    Validates the 5 acceptance criteria from the hybrid-intent routing spec.
    These are unit-level checks on routing and scene extraction only
    (full E2E tests require LLM integration).
    """

    @pytest.mark.asyncio
    async def test_ac1_movies_with_kid(self):
        """
        AC1: 'any movies with the kid?'
        - child scene captured ✓
        - movie intent remains primary ✓
        - flow is factual ✓
        """
        msg = "any movies with the kid?"

        # Scene extraction
        state = _make_state(
            message=msg,
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        scene_result = await update_scene_memory(state)
        scene: SceneMemory = scene_result["scene"]
        assert "child" in scene.companions, "AC1: child not in companions"

        # Hybrid bundle
        primary, secondary, modifiers, _ = _extract_hybrid_intent_bundle(
            msg, "entertainment", "movie_showtime"
        )
        assert primary == "movie_lookup", f"AC1: primary not movie_lookup, got {primary}"
        assert "family_filter" in secondary, f"AC1: family_filter not in secondary={secondary}"

        # Routing
        route_state = _make_state(
            message=msg,
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
        )
        route_result = await route_flow(route_state)
        assert route_result["flow_type"] == "factual", (
            f"AC1: flow not factual, got {route_result['flow_type']}"
        )

    @pytest.mark.asyncio
    async def test_ac2_shopping_with_child(self):
        """
        AC2: 'shopping with my child'
        - shopping remains primary ✓
        - child/family becomes modifier ✓
        - scene captures child companion ✓
        """
        msg = "shopping with my child"

        state = _make_state(
            message=msg,
            domain="shopping",
            sub_intent="general_shopping",
            flow_type="concierge",
        )
        scene_result = await update_scene_memory(state)
        scene: SceneMemory = scene_result["scene"]
        assert "child" in scene.companions, "AC2: child not captured in scene"

        primary, secondary, modifiers, _ = _extract_hybrid_intent_bundle(
            msg, "shopping", "general_shopping"
        )
        assert primary == "shopping_recommendation", f"AC2: primary={primary} not shopping"
        assert "family_filter" in secondary or "kid_friendly" in modifiers, (
            "AC2: family context not in secondary/modifiers"
        )

    @pytest.mark.asyncio
    async def test_ac3_something_quick_before_movie(self):
        """
        AC3: 'something quick before the movie'
        - concierge flow ✓
        - quick + before_movie modifiers preserved ✓
        """
        msg = "something quick before the movie"

        state = _make_state(
            message=msg,
            domain="dining",
            sub_intent="quick_bite",
            primary_intent="dining_recommendation",
            secondary_intents=["before_movie_constraint"],
            modifiers=["quick_stop", "time_sensitive"],
        )
        result = await route_flow(state)
        assert result["flow_type"] == "concierge", (
            f"AC3: Expected concierge, got {result['flow_type']}"
        )

        primary, secondary, modifiers, _ = _extract_hybrid_intent_bundle(
            msg, "dining", "quick_bite"
        )
        has_quick = "quick_stop" in modifiers or "time_sensitive" in modifiers
        has_before_movie = "before_movie_constraint" in secondary or "before_movie" in modifiers
        assert has_quick or has_before_movie, (
            f"AC3: quick/before_movie not captured. secondary={secondary} modifiers={modifiers}"
        )

    @pytest.mark.asyncio
    async def test_ac4_gift_for_girlfriend(self):
        """
        AC4: 'gift for my girlfriend'
        - gifting remains primary ✓
        - romantic modifier ✓
        - target_person captured ✓
        """
        msg = "gift for my girlfriend"

        state = _make_state(
            message=msg,
            domain="shopping",
            sub_intent="gift_recommendation",
            flow_type="concierge",
        )
        scene_result = await update_scene_memory(state)
        scene: SceneMemory = scene_result["scene"]
        assert scene.target_person in ("girlfriend", "partner", "") or "girlfriend" in scene.companions, (
            f"AC4: girlfriend not captured. target_person={scene.target_person} "
            f"companions={scene.companions}"
        )

        primary, secondary, modifiers, _ = _extract_hybrid_intent_bundle(
            msg, "shopping", "gift_recommendation"
        )
        assert primary == "gift_shopping", f"AC4: primary={primary} not gift_shopping"
        has_romantic = "romantic" in modifiers or "couple_friendly" in modifiers
        assert has_romantic, f"AC4: romantic modifier not in {modifiers}"

    @pytest.mark.asyncio
    async def test_ac5_followup_movie_then_kid_filter(self):
        """
        AC5: 'what movies are there?' → 'anything with the kid?'
        - stays in movie mode ✓
        - family filter added ✓
        - does NOT collapse into dining ✓
        """
        # Simulate state after first turn (movie lookup)
        scene = SceneMemory(
            active_topic="entertainment",
            last_flow_type="factual",
            active_fact_scope="movie_schedule",
            active_primary_intent="movie_lookup",
        )

        # Second turn: "anything with the kid?"
        msg = "anything with the kid?"
        state = _make_state(
            message=msg,
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly"],
            message_kind="followup",
            scene=scene,
        )
        result = await route_flow(state)
        assert result["flow_type"] == "factual", (
            f"AC5: follow-up must stay factual (movie mode), got {result['flow_type']}"
        )
        assert result.get("primary_intent") == "movie_lookup", (
            f"AC5: primary_intent must stay movie_lookup, got {result.get('primary_intent')}"
        )
        assert "family_filter" in result.get("secondary_intents", []), (
            f"AC5: family_filter must be in secondary_intents, got {result.get('secondary_intents')}"
        )
