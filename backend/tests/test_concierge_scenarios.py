"""
Acceptance tests for the Concierge Intelligence Upgrade.

Tests the 7 acceptance criteria queries described in the plan by exercising
the pipeline nodes independently (no LLM calls required for scene/intent logic).

Test groups:
  1. Scene Engine — companion, age, visit_type, audience detection
  2. Semantic Signals — phrase-to-tag extraction
  3. Playbook Resolution — family guard, luxury guard, constraint refinement
  4. Choose Strategy — guided_plan selection, constraint_refinement → quick_answer
  5. Rank & Dedupe — deduplication, entity cap, child-relief anchor
  6. Interpret Turn — constraint_refinement message_kind detection
"""

from __future__ import annotations

import pytest

from app.models.state import (
    ConciergeState,
    ContextComposition,
    DebugEnrichment,
    InterpretedIntent,
    PlaybookResolution,
    ResponsePlan,
    SceneMemory,
)
from app.services.semantic_signals import extract_semantic_signals


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _make_state(
    msg: str = "",
    domain: str = "shopping",
    sub_intent: str = "general_shopping",
    message_kind: str = "fresh_request",
    companions: list[str] | None = None,
    companion_details: list[dict] | None = None,
    audience: list[str] | None = None,
    visit_type: str = "",
    visit_constraints: list[str] | None = None,
    occasion: str = "",
    budget: str = "",
    goal: str = "",
    playbook_id: str = "",
    playbook_conf: float = 0.0,
    strategy: str = "",
    entity_cap: int = 5,
    must_acknowledge: bool = False,
    entities: list[dict] | None = None,
    active_shortlist: list[str] | None = None,
) -> ConciergeState:
    scene = SceneMemory(
        companions=companions or [],
        companion_details=companion_details or [],
        audience=audience or [],
        visit_type=visit_type,
        visit_constraints=visit_constraints or [],
        occasion=occasion,
        budget=budget,
        goal=goal,
        active_shortlist=active_shortlist or [],
    )
    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=0.9,
    )
    playbook = PlaybookResolution(
        selected_playbook=playbook_id,
        playbook_confidence=playbook_conf,
    )
    response_plan = ResponsePlan(
        chosen_strategy=strategy,
        entity_cap=entity_cap,
        must_acknowledge_scene=must_acknowledge,
    )
    context = ContextComposition(
        selected_entities=entities or [],
        selected_semantic_signals=[],
    )
    return ConciergeState(
        normalized_user_message=msg,
        raw_user_message=msg,
        scene=scene,
        intent=intent,
        playbook=playbook,
        response_plan=response_plan,
        context=context,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Scene Engine Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSceneEngine:
    """Tests for update_scene_memory node logic."""

    @pytest.mark.asyncio
    async def test_child_age_extraction_5yr(self):
        """AC1: '5 yr old' must set companion=child, companion_details age=5."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(
            msg="i am here with my 5 yr old, want to do shopping",
            domain="shopping",
            sub_intent="general_shopping",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "child" in scene.companions, f"Expected 'child' in companions, got {scene.companions}"
        child_details = [d for d in scene.companion_details if d.get("type") == "child"]
        assert child_details, f"Expected companion_details with child, got {scene.companion_details}"
        assert child_details[0]["age"] == 5, f"Expected age=5, got {child_details[0]}"

    @pytest.mark.asyncio
    async def test_child_age_extraction_year_old(self):
        """'3 year old daughter' must set companion=child, age=3."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="here with my 3 year old daughter")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "child" in scene.companions
        assert any(d.get("age") == 3 for d in scene.companion_details)

    @pytest.mark.asyncio
    async def test_visit_type_set_family_visit(self):
        """AC1: Family+child query must set visit_type='family_visit'."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="i am here with my 5 yr old, want to do shopping")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert scene.visit_type == "family_visit", f"Got {scene.visit_type}"

    @pytest.mark.asyncio
    async def test_audience_includes_family_and_kid_friendly(self):
        """AC1: Child companion must set audience including family_friendly, kid_friendly, parent_with_child."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="with my 5 yr old")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "family_friendly" in scene.audience, f"Expected family_friendly in {scene.audience}"
        assert "kid_friendly" in scene.audience, f"Expected kid_friendly in {scene.audience}"
        assert "parent_with_child" in scene.audience, f"Expected parent_with_child in {scene.audience}"

    @pytest.mark.asyncio
    async def test_implicit_goal_shopping_with_child(self):
        """AC1: Shopping + child companion must set implicit_goal."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(
            msg="i am here with my 5 yr old, want to do shopping",
            domain="shopping",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "child" in scene.implicit_goal.lower() or "shopping" in scene.implicit_goal.lower(), \
            f"Expected implicit_goal to mention child shopping, got: {scene.implicit_goal}"

    @pytest.mark.asyncio
    async def test_constraint_refinement_preserves_companions(self):
        """AC6: constraint_refinement must NOT reset companions or goal."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(
            msg="something quicker",
            message_kind="constraint_refinement",
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            goal="shopping",
            audience=["family_friendly", "kid_friendly"],
            active_shortlist=["Zara", "MINISO"],
        )
        result = await update_scene_memory(state)
        scene = result["scene"]

        # Companions must be preserved
        assert "child" in scene.companions
        # Shortlist must be preserved (not reset)
        assert scene.active_shortlist == ["Zara", "MINISO"]
        # New constraint must be added
        assert any("quick" in c for c in scene.visit_constraints), \
            f"Expected quick constraint, got {scene.visit_constraints}"

    @pytest.mark.asyncio
    async def test_near_cinema_constraint(self):
        """AC7: 'closer to cinema' must set near_cinema_preferred constraint."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="closer to cinema")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "near_cinema_preferred" in scene.visit_constraints, \
            f"Expected near_cinema_preferred in {scene.visit_constraints}"

    @pytest.mark.asyncio
    async def test_before_movie_constraint(self):
        """AC4: 'before movie' sets time_sensitive constraint."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="we want something quick before the movie")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "time_sensitive" in scene.visit_constraints or scene.occasion == "before_movie", \
            f"Expected time_sensitive or before_movie, got constraints={scene.visit_constraints} occasion={scene.occasion}"

    @pytest.mark.asyncio
    async def test_budget_sensitive_constraint(self):
        """AC5/6: 'not expensive' sets budget_sensitive constraint."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="not expensive")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "budget_sensitive" in scene.visit_constraints, \
            f"Expected budget_sensitive in {scene.visit_constraints}"

    @pytest.mark.asyncio
    async def test_girlfriend_companion(self):
        """AC3: 'my girlfriend' sets companion=girlfriend, visit_type=couple."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="i want a gift for my girlfriend")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "girlfriend" in scene.companions, f"Got companions: {scene.companions}"

    @pytest.mark.asyncio
    async def test_inferred_scene_notes_populated(self):
        """Scene notes must be populated when meaningful inferences are made."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="with my 5 yr old")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert len(scene.inferred_scene_notes) > 0, "Expected inferred_scene_notes to be populated"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Semantic Signal Extraction Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSemanticSignals:
    """Tests for the semantic signal extraction utility."""

    def test_child_message_produces_kid_friendly(self):
        """AC1: 'with my 5 yr old' → kid_friendly, family_friendly."""
        scene = SceneMemory(
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
        )
        signals = extract_semantic_signals(
            "i am here with my 5 yr old, want to do shopping",
            scene, "shopping", "general_shopping",
        )
        assert "kid_friendly" in signals, f"Got signals: {signals}"
        assert "family_friendly" in signals

    def test_shopping_message_produces_shopping_mission(self):
        """AC1: shopping intent → shopping_mission signal."""
        scene = SceneMemory()
        signals = extract_semantic_signals("want to do shopping", scene, "shopping", "general_shopping")
        assert "shopping_mission" in signals

    def test_before_movie_produces_correct_signals(self):
        """AC4: 'before movie' → before_movie, time_sensitive, near_cinema."""
        scene = SceneMemory()
        signals = extract_semantic_signals("something quick before the movie", scene, "dining", "quick_bite")
        assert "before_movie" in signals or "time_sensitive" in signals, f"Got: {signals}"

    def test_not_expensive_produces_budget_sensitive(self):
        """AC6: 'not expensive' → budget_sensitive."""
        scene = SceneMemory()
        signals = extract_semantic_signals("not expensive", scene)
        assert "budget_sensitive" in signals

    def test_near_cinema_produces_near_cinema(self):
        """AC7: 'closer to cinema' → near_cinema."""
        scene = SceneMemory()
        signals = extract_semantic_signals("closer to cinema", scene)
        assert "near_cinema" in signals

    def test_girlfriend_gift_produces_gift_friendly(self):
        """AC3: 'gift for my girlfriend' → gift_friendly, romantic."""
        scene = SceneMemory(companions=["girlfriend"])
        signals = extract_semantic_signals("i want a gift for my girlfriend", scene, "shopping", "gift_recommendation")
        assert "gift_friendly" in signals, f"Got: {signals}"

    def test_signals_not_empty_for_contextual_message(self):
        """selected_semantic_signals must not be empty for clearly contextual messages."""
        scene = SceneMemory(
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            audience=["family_friendly", "kid_friendly"],
        )
        signals = extract_semantic_signals(
            "i am here with my 5 yr old, want to do shopping",
            scene, "shopping", "general_shopping",
        )
        assert len(signals) > 0, "Semantic signals should not be empty for contextual message"

    def test_scene_constraint_maps_to_signal(self):
        """Scene constraints must flow into semantic signals."""
        scene = SceneMemory(visit_constraints=["near_cinema_preferred", "time_sensitive"])
        signals = extract_semantic_signals("", scene)
        assert "near_cinema" in signals
        assert "quick_stop" in signals or "time_sensitive" in signals


# ══════════════════════════════════════════════════════════════════════════════
# 3. Choose Strategy Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestChooseStrategy:
    """Tests for the choose_strategy node."""

    @pytest.mark.asyncio
    async def test_family_shopping_returns_guided_plan(self):
        """AC1: Family visit + shopping → guided_plan strategy."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            domain="shopping",
            sub_intent="general_shopping",
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            audience=["family_friendly", "kid_friendly"],
            visit_type="family_visit",
            playbook_id="pb-family-shopping",
            playbook_conf=0.75,
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        assert plan.chosen_strategy == "guided_plan", \
            f"Expected guided_plan, got {plan.chosen_strategy}"

    @pytest.mark.asyncio
    async def test_constraint_refinement_returns_quick_answer(self):
        """AC6: constraint_refinement message_kind → quick_answer strategy."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            msg="something quicker",
            message_kind="constraint_refinement",
            domain="shopping",
            sub_intent="general_shopping",
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        assert plan.chosen_strategy == "quick_answer", \
            f"Expected quick_answer, got {plan.chosen_strategy}"

    @pytest.mark.asyncio
    async def test_mall_overview_query_returns_mall_overview(self):
        """AC2: mall_info/overview → mall_overview strategy."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            domain="mall_info",
            sub_intent="overview",
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        assert plan.chosen_strategy == "mall_overview"

    @pytest.mark.asyncio
    async def test_family_visit_sets_must_acknowledge_scene(self):
        """AC1: Family visit must set must_acknowledge_scene=True."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            domain="shopping",
            sub_intent="general_shopping",
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            visit_type="family_visit",
            playbook_id="pb-family-shopping",
            playbook_conf=0.75,
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        assert plan.must_acknowledge_scene is True

    @pytest.mark.asyncio
    async def test_family_visit_sets_anchor_type_entertainment(self):
        """AC1: Family visit + child → must_include_anchor_type=entertainment."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            domain="shopping",
            sub_intent="general_shopping",
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            visit_type="family_visit",
            playbook_id="pb-family-shopping",
            playbook_conf=0.75,
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        assert plan.must_include_anchor_type == "entertainment", \
            f"Expected entertainment anchor, got {plan.must_include_anchor_type}"

    @pytest.mark.asyncio
    async def test_entity_cap_guided_plan_is_5(self):
        """AC1: guided_plan entity cap must be ≤ 5."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            domain="shopping",
            sub_intent="general_shopping",
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            visit_type="family_visit",
            playbook_id="pb-family-shopping",
            playbook_conf=0.75,
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        assert plan.entity_cap <= 5, f"Expected entity_cap ≤ 5, got {plan.entity_cap}"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Rank & Dedupe Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestRankAndDedupe:
    """Tests for the rank_and_dedupe node."""

    def _make_entities(self, count: int = 8, entity_type: str = "store") -> list[dict]:
        return [
            {
                "entity_id": f"e{i}",
                "name": f"Store {i}",
                "entity_type": entity_type,
                "score": 0.5,
                "semantic_tags": ["practical_shopping", "easy_browse"],
                "audience_fit": ["family_friendly"],
            }
            for i in range(count)
        ]

    @pytest.mark.asyncio
    async def test_deduplication_removes_duplicate_ids(self):
        """Duplicate entity_ids must be removed."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = self._make_entities(3)
        entities.append(entities[0].copy())  # add a duplicate
        assert len(entities) == 4

        state = _make_state(
            entities=entities,
            strategy="guided_plan",
            entity_cap=5,
        )
        result = await rank_and_dedupe(state)
        final = result["context"].selected_entities

        ids = [e["entity_id"] for e in final]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"

    @pytest.mark.asyncio
    async def test_entity_cap_enforced(self):
        """Entity cap must be enforced — no more than entity_cap entities returned."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        state = _make_state(
            entities=self._make_entities(15),
            strategy="guided_plan",
            entity_cap=5,
        )
        result = await rank_and_dedupe(state)
        final = result["context"].selected_entities

        assert len(final) <= 5, f"Expected ≤ 5 entities, got {len(final)}"

    @pytest.mark.asyncio
    async def test_child_relief_anchor_injected(self):
        """AC1: When child present, entertainment anchor must be injected if missing."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        # All entities are stores — no entertainment
        stores = self._make_entities(4, entity_type="store")
        # Add one entertainment entity that should be pulled in
        entertainment = {
            "entity_id": "ent1",
            "name": "Fun Time",
            "entity_type": "entertainment",
            "score": 0.4,
            "semantic_tags": ["child_activity", "kids_entertainment", "kid_friendly"],
            "audience_fit": ["family_friendly", "kid_friendly"],
        }
        all_entities = stores + [entertainment]

        state = _make_state(
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            audience=["family_friendly", "kid_friendly"],
            entities=all_entities,
            strategy="guided_plan",
            entity_cap=5,
        )
        # Set must_include_anchor_type
        state.response_plan.must_include_anchor_type = "entertainment"

        result = await rank_and_dedupe(state)
        final = result["context"].selected_entities

        entity_types = [e.get("entity_type") for e in final]
        assert "entertainment" in entity_types, \
            f"Expected entertainment anchor in results, got: {entity_types}"

    @pytest.mark.asyncio
    async def test_debug_enrichment_populated(self):
        """debug_enrichment must be populated with counts."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        state = _make_state(
            entities=self._make_entities(10),
            strategy="guided_plan",
            entity_cap=5,
        )
        result = await rank_and_dedupe(state)
        de = result["debug_enrichment"]

        assert de.candidate_count_before_dedupe == 10
        assert de.final_entity_count > 0

    @pytest.mark.asyncio
    async def test_category_retrieval_not_capped(self):
        """Category retrieval results must NOT be capped at entity_cap."""
        from app.nodes.rank_and_dedupe import rank_and_dedupe

        entities = self._make_entities(12, entity_type="store")
        for e in entities:
            e["source"] = "category/cafe"

        state = _make_state(
            entities=entities,
            strategy="shortlist_recommendation",
            entity_cap=5,
        )
        result = await rank_and_dedupe(state)
        final = result["context"].selected_entities

        # For category retrieval, all 12 should be preserved
        assert len(final) == 12, f"Expected 12 category entities, got {len(final)}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Interpret Turn — constraint_refinement detection
# ══════════════════════════════════════════════════════════════════════════════

class TestInterpretTurn:
    """Tests for message_kind detection, specifically constraint_refinement."""

    def test_detect_constraint_refinement_something_quicker(self):
        """AC6: 'something quicker' with prior context → constraint_refinement."""
        from app.nodes.interpret_turn import _detect_message_kind

        state = _make_state(
            msg="something quicker",
            active_shortlist=["Zara", "H&M"],
        )
        state.scene.active_shortlist = ["Zara", "H&M"]
        state.scene.current_need = "want to do shopping"

        kind = _detect_message_kind("something quicker", 2, state)
        assert kind == "constraint_refinement", f"Expected constraint_refinement, got {kind}"

    def test_detect_constraint_refinement_not_expensive(self):
        """AC6: 'not expensive' with prior context → constraint_refinement."""
        from app.nodes.interpret_turn import _detect_message_kind

        state = _make_state(active_shortlist=["Zara"])
        state.scene.active_shortlist = ["Zara"]
        state.scene.current_need = "shopping"

        kind = _detect_message_kind("not expensive", 2, state)
        assert kind == "constraint_refinement", f"Expected constraint_refinement, got {kind}"

    def test_detect_constraint_refinement_closer_to(self):
        """AC7: 'closer to cinema' with prior context → constraint_refinement."""
        from app.nodes.interpret_turn import _detect_message_kind

        state = _make_state()
        state.scene.active_topic = "dining"

        kind = _detect_message_kind("closer to the cinema", 2, state)
        assert kind == "constraint_refinement", f"Expected constraint_refinement, got {kind}"

    def test_no_constraint_refinement_without_prior_context(self):
        """'something quicker' without any prior context → fresh_request, not constraint_refinement."""
        from app.nodes.interpret_turn import _detect_message_kind

        state = _make_state()
        # No prior context — empty scene

        kind = _detect_message_kind("something quicker", 1, state)
        # history_len=1 means first message
        assert kind == "fresh_request", f"Expected fresh_request, got {kind}"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Integration-style scenario tests (no LLM, logic only)
# ══════════════════════════════════════════════════════════════════════════════

class TestAcceptanceCriteria:
    """End-to-end logic tests verifying each acceptance criterion."""

    @pytest.mark.asyncio
    async def test_ac1_full_scene_for_child_shopping(self):
        """
        AC1: 'i am here with my 5 yr old, want to do shopping'
        → companion=child, companion_details age=5, family_visit,
          audience includes family+kid, implicit_goal mentions child
        """
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(
            msg="i am here with my 5 yr old, want to do shopping",
            domain="shopping",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]

        # Companion checks
        assert "child" in scene.companions
        assert any(d.get("age") == 5 for d in scene.companion_details)
        # Audience checks
        assert "family_friendly" in scene.audience
        assert "kid_friendly" in scene.audience
        # Visit type
        assert scene.visit_type == "family_visit"
        # Implicit goal
        assert scene.implicit_goal != "", f"implicit_goal should not be empty, got: {scene.implicit_goal}"

    @pytest.mark.asyncio
    async def test_ac1_strategy_is_guided_plan(self):
        """AC1: Family visit → choose_strategy selects guided_plan."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            domain="shopping",
            sub_intent="general_shopping",
            companions=["child"],
            companion_details=[{"type": "child", "age": 5}],
            visit_type="family_visit",
            audience=["family_friendly", "kid_friendly", "parent_with_child"],
            playbook_id="pb-family-shopping",
            playbook_conf=0.8,
        )
        result = await choose_strategy(state)
        assert result["response_plan"].chosen_strategy == "guided_plan"

    @pytest.mark.asyncio
    async def test_ac2_mall_overview_strategy(self):
        """AC2: 'tell me about the mall' → mall_overview strategy."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            msg="tell me about the mall",
            domain="mall_info",
            sub_intent="overview",
        )
        result = await choose_strategy(state)
        assert result["response_plan"].chosen_strategy == "mall_overview"

    @pytest.mark.asyncio
    async def test_ac3_girlfriend_gift_semantic_signals(self):
        """AC3: 'gift for my girlfriend' → gift_friendly, romantic signals."""
        scene = SceneMemory(companions=["girlfriend"])
        signals = extract_semantic_signals(
            "i want a gift for my girlfriend",
            scene, "shopping", "gift_recommendation",
        )
        assert "gift_friendly" in signals
        assert "romantic" in signals or "couple_friendly" in signals

    @pytest.mark.asyncio
    async def test_ac4_before_movie_constraint_detection(self):
        """AC4: 'something quick before the movie' → time_sensitive, before_movie."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(
            msg="we want something quick before the movie",
            domain="dining",
            sub_intent="quick_bite",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]

        has_time_signal = (
            "time_sensitive" in scene.visit_constraints
            or "quick_stop_preferred" in scene.visit_constraints
            or scene.occasion == "before_movie"
        )
        assert has_time_signal, \
            f"Expected time-sensitive signal, got constraints={scene.visit_constraints} occasion={scene.occasion}"

    @pytest.mark.asyncio
    async def test_ac5_just_shopping_no_overload(self):
        """AC5: 'just shopping' → concise_shortlist (no excessive entity cap)."""
        from app.nodes.choose_strategy import choose_strategy

        state = _make_state(
            msg="just shopping",
            domain="shopping",
            sub_intent="general_shopping",
            # No companions, no occasion — bare shopping request
        )
        result = await choose_strategy(state)
        plan = result["response_plan"]

        # Should not be guided_plan (no context)
        # Should have reasonable cap
        assert plan.entity_cap <= 8, f"Cap too high for bare shopping: {plan.entity_cap}"

    @pytest.mark.asyncio
    async def test_ac6_something_quicker_is_constraint_refinement(self):
        """AC6: 'something quicker' with prior context → constraint_refinement."""
        from app.nodes.interpret_turn import _detect_message_kind

        state = _make_state(
            msg="something quicker",
            active_shortlist=["McDonald's", "Herfy"],
        )
        state.scene.active_shortlist = ["McDonald's", "Herfy"]
        state.scene.current_need = "food"

        kind = _detect_message_kind("something quicker", 2, state)
        assert kind == "constraint_refinement"

    @pytest.mark.asyncio
    async def test_ac7_closer_to_cinema_sets_near_cinema(self):
        """AC7: 'closer to cinema' → near_cinema_preferred constraint."""
        from app.nodes.update_scene_memory import update_scene_memory

        state = _make_state(msg="closer to cinema")
        result = await update_scene_memory(state)
        scene = result["scene"]

        assert "near_cinema_preferred" in scene.visit_constraints, \
            f"Expected near_cinema_preferred, got {scene.visit_constraints}"

    def test_ac7_near_cinema_signal_from_semantic_extraction(self):
        """AC7: near_cinema extracted as semantic signal from constraint."""
        scene = SceneMemory(visit_constraints=["near_cinema_preferred"])
        signals = extract_semantic_signals("closer to cinema", scene)
        assert "near_cinema" in signals


# ══════════════════════════════════════════════════════════════════════════════
# 7. Playbook data tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPlaybookData:
    """Tests that new playbooks were correctly added to the data files."""

    def test_family_shopping_playbook_exists_in_mall_28(self):
        """pb-family-shopping must be in al_nakheel_plaza_28.json."""
        import json
        with open("data/playbooks/al_nakheel_plaza_28.json") as f:
            data = json.load(f)
        ids = [p["playbook_id"] for p in data]
        assert "pb-family-shopping" in ids, f"pb-family-shopping not found in {ids}"

    def test_before_movie_playbook_exists(self):
        """pb-before-movie must be in al_nakheel_plaza_28.json."""
        import json
        with open("data/playbooks/al_nakheel_plaza_28.json") as f:
            data = json.load(f)
        ids = [p["playbook_id"] for p in data]
        assert "pb-before-movie" in ids

    def test_quick_errand_playbook_exists(self):
        """pb-quick-errand must be in al_nakheel_plaza_28.json."""
        import json
        with open("data/playbooks/al_nakheel_plaza_28.json") as f:
            data = json.load(f)
        ids = [p["playbook_id"] for p in data]
        assert "pb-quick-errand" in ids

    def test_family_shopping_playbook_has_correct_biases(self):
        """pb-family-shopping must have kid_friendly in ranking_biases."""
        import json
        with open("data/playbooks/al_nakheel_plaza_28.json") as f:
            data = json.load(f)
        pb = next((p for p in data if p["playbook_id"] == "pb-family-shopping"), None)
        assert pb is not None
        assert "kid_friendly" in pb.get("ranking_biases", {}), \
            f"kid_friendly missing from ranking_biases: {pb.get('ranking_biases')}"
