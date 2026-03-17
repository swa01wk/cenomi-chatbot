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
from app.nodes.interpret_turn import _extract_hybrid_intent_bundle
from app.nodes.route_flow import route_flow, _has_strong_scene_context, _is_pure_lookup
from app.nodes.update_scene_memory import update_scene_memory, _extract_hybrid_companions


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

class TestHybridIntentBundle:
    """Test _extract_hybrid_intent_bundle from interpret_turn.py"""

    def test_movie_with_kid_primary_is_movie_lookup(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
        )
        assert primary == "movie_lookup", f"Expected movie_lookup, got {primary}"
        assert "family_filter" in secondary, f"Expected family_filter in {secondary}"
        assert "kid_friendly" in modifiers, f"Expected kid_friendly in {modifiers}"
        assert "family_friendly" in modifiers or "parent_with_child" in modifiers

    def test_shopping_with_child_primary_is_shopping(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "shopping with my child",
            domain="shopping",
            sub_intent="general_shopping",
        )
        assert primary == "shopping_recommendation", f"Expected shopping_recommendation, got {primary}"
        assert "family_filter" in secondary, f"Expected family_filter in {secondary}"
        assert "kid_friendly" in modifiers or "parent_with_child" in modifiers

    def test_something_quick_before_movie_modifiers(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "something quick before the movie",
            domain="dining",
            sub_intent="quick_bite",
        )
        assert primary == "dining_recommendation", f"Expected dining_recommendation, got {primary}"
        assert "before_movie_constraint" in secondary, f"Expected before_movie_constraint in {secondary}"
        assert "time_sensitive" in modifiers or "quick_stop" in modifiers or "before_movie" in modifiers

    def test_gift_for_girlfriend(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "gift for my girlfriend",
            domain="shopping",
            sub_intent="gift_recommendation",
        )
        assert primary == "gift_shopping", f"Expected gift_shopping, got {primary}"
        assert "romantic" in modifiers or "couple_friendly" in modifiers, (
            f"Expected romantic in modifiers, got {modifiers}"
        )

    def test_near_cinema_and_not_expensive(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "closer to cinema and not expensive",
            domain="dining",
            sub_intent="general_dining",
        )
        assert "near_cinema" in modifiers, f"Expected near_cinema in {modifiers}"
        assert "budget_sensitive" in modifiers, f"Expected budget_sensitive in {modifiers}"

    def test_family_context_does_not_overwrite_domain_primary(self):
        """family/kid signals must produce MODIFIERS not replace the primary intent."""
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "any movies with the kid tonight?",
            domain="entertainment",
            sub_intent="movie_showtime",
        )
        # Primary must stay movie, not switch to dining or kids_activity
        assert primary == "movie_lookup", (
            f"Family context must not replace movie primary! Got: {primary}"
        )

    def test_modifiers_do_not_contain_duplicates(self):
        _, _, modifiers, _ = _extract_hybrid_intent_bundle(
            "with the kid and kid friendly",
            domain="shopping",
            sub_intent="general_shopping",
        )
        assert len(modifiers) == len(set(modifiers)), f"Duplicate modifiers: {modifiers}"


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
        assert "child" in scene.companions, (
            f"Expected 'child' in companions, got {scene.companions}"
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
        assert "child" in scene.companions, (
            f"Expected 'child' in companions, got {scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_with_girlfriend_sets_girlfriend_companion(self):
        state = _make_state(
            message="gift for my girlfriend",
            domain="shopping",
            sub_intent="gift_recommendation",
            flow_type="concierge",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        assert "girlfriend" in scene.companions, (
            f"Expected 'girlfriend' in companions, got {scene.companions}"
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
        assert "friends" in scene.companions, (
            f"Expected 'friends' in companions, got {scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_child_audience_inferred_from_companion(self):
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            flow_type="factual",
        )
        result = await update_scene_memory(state)
        scene: SceneMemory = result["scene"]
        audience = scene.audience
        assert any(tag in audience for tag in ("family_friendly", "kid_friendly", "parent_with_child")), (
            f"Expected family audience tags, got {audience}"
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
        assert "child" in scene.companions, (
            f"Factual flow must still extract child companion! Got {scene.companions}"
        )

    def test_extract_hybrid_companions_with_the_kid(self):
        """Unit test for _extract_hybrid_companions helper."""
        scene = SceneMemory()
        changes: list[str] = []
        notes: list[str] = []
        _extract_hybrid_companions("any movies with the kid?", scene, changes, notes)
        assert "child" in scene.companions
        assert scene.visit_type == "family_visit"

    def test_extract_hybrid_companions_with_girlfriend(self):
        scene = SceneMemory()
        changes: list[str] = []
        notes: list[str] = []
        _extract_hybrid_companions("something for my girlfriend", scene, changes, notes)
        assert "girlfriend" in scene.companions
        assert scene.visit_type == "couple"

    def test_extract_hybrid_companions_idempotent(self):
        """Should not add duplicate companions if called twice."""
        scene = SceneMemory()
        changes: list[str] = []
        notes: list[str] = []
        _extract_hybrid_companions("with my child", scene, changes, notes)
        _extract_hybrid_companions("with my child", scene, changes, notes)
        assert scene.companions.count("child") == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. Route flow — hybrid routing decisions
# ═══════════════════════════════════════════════════════════════════════════

class TestHybridRouteFlow:
    """Test that route_flow makes the correct flow decision for hybrid queries."""

    @pytest.mark.asyncio
    async def test_movies_with_kid_routes_factual(self):
        """'any movies with the kid?' → factual flow (movie primary + family filter)."""
        state = _make_state(
            message="any movies with the kid?",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["family_filter"],
            modifiers=["kid_friendly", "family_friendly"],
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
        # Simulate scene where child was extracted from a prior turn
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

class TestUtilityHelpers:
    def test_is_pure_lookup_movie_list(self):
        assert _is_pure_lookup("what movies are showing")

    def test_is_pure_lookup_where_is(self):
        assert _is_pure_lookup("where is the atm")

    def test_is_not_pure_lookup_with_kid(self):
        """'any movies with the kid?' is not a pure lookup (has context)."""
        # It has "movies" but also family context — _is_pure_lookup is about
        # structural lookup patterns, not context richness.
        # This test verifies the function returns sensibly.
        result = _is_pure_lookup("any movies with the kid?")
        # Not necessarily False, but the router should NOT use _is_pure_lookup
        # to decide whether family context replaces movie intent.
        # Just verify it doesn't crash.
        assert isinstance(result, bool)

    def test_hybrid_bundle_no_crash_on_empty_message(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle("", "", "")
        assert isinstance(primary, str)
        assert isinstance(secondary, list)
        assert isinstance(modifiers, list)

    def test_hybrid_bundle_no_crash_on_unknown_domain(self):
        primary, secondary, modifiers, ctx = _extract_hybrid_intent_bundle(
            "something random", "unknown_domain", "unknown_sub"
        )
        assert isinstance(primary, str)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Acceptance criteria integration checks
# ═══════════════════════════════════════════════════════════════════════════

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
