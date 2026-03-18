"""
Acceptance-patch test suite — covers the 6 acceptance flows from the
architecture-tuning / code-level patch spec.

Tests verify:
  A. Movie equivalence (same factual behavior for variant queries)
  B. Mall overview continuity (follow-up stays in mall overview, no drift)
  C. Context-setting (scene updated, not collapsed to narrow answer)
  D. Shopping task persistence (multi-turn coherent task, no leakage)
  E. Scenario specificity (bridesmaid → wedding scenario, no family-shopping)
  F. Deduplication (name-clash variants with different entity_ids collapse)

Run with:
    pytest backend/tests/test_acceptance_patch.py -v
"""

from __future__ import annotations

import asyncio
import pytest

from app.models.state import (
    ConciergeState,
    ContextComposition,
    DebugEnrichment,
    InterpretedIntent,
    PlaybookResolution,
    ResponsePlan,
    SceneMemory,
    ShoppingTask,
)
from app.nodes.rank_and_dedupe import rank_and_dedupe, _dedupe_key
from app.nodes.update_scene_memory import update_scene_memory, _extract_shopping_task
from app.nodes.compose_context import (
    _is_mall_overview_followup,
    _is_movie_context_followup,
    _resolve_shopping_task_category,
)
from app.nodes.route_flow import route_flow
from intent.query_classifier import normalize_query


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_state(
    raw_msg: str,
    domain: str = "general",
    sub_intent: str = "general_inquiry",
    message_kind: str = "fresh_request",
    flow_type: str = "concierge",
    primary_intent: str = "",
    secondary_intents: list[str] | None = None,
    modifiers: list[str] | None = None,
    companions: list[str] | None = None,
    occasion: str = "",
    visit_type: str = "",
    last_flow_type: str = "",
    active_primary_intent: str = "",
    active_topic: str = "",
    topic_lock: str = "",
    active_fact_scope: str = "",
    scenario: str = "",
    user_role: str = "",
    visit_constraints: list[str] | None = None,
    active_shortlist: list[str] | None = None,
    shopping_task: ShoppingTask | None = None,
    entities: list[dict] | None = None,
    chosen_strategy: str = "shortlist_recommendation",
) -> ConciergeState:
    scene = SceneMemory(
        companions=companions or [],
        occasion=occasion,
        visit_type=visit_type,
        last_flow_type=last_flow_type,
        active_primary_intent=active_primary_intent,
        active_topic=active_topic,
        topic_lock=topic_lock,
        active_fact_scope=active_fact_scope,
        scenario=scenario,
        user_role=user_role,
        visit_constraints=visit_constraints or [],
        active_shortlist=active_shortlist or [],
        shopping_task=shopping_task or ShoppingTask(),
    )
    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=0.85,
        primary_intent=primary_intent,
        secondary_intents=secondary_intents or [],
        modifiers=modifiers or [],
    )
    return ConciergeState(
        session_id="test-acc-session",
        mall_id="al_nakheel_plaza_28",
        raw_user_message=raw_msg,
        normalized_user_message=raw_msg,
        intent=intent,
        scene=scene,
        flow_type=flow_type,
        primary_intent=primary_intent,
        secondary_intents=secondary_intents or [],
        modifiers=modifiers or [],
        context=ContextComposition(selected_entities=entities or []),
        response_plan=ResponsePlan(chosen_strategy=chosen_strategy),
    )


# ─────────────────────────────────────────────────────────────────────────────
# A. MOVIE EQUIVALENCE
# ─────────────────────────────────────────────────────────────────────────────

class TestMovieEquivalence:
    """
    AC-A: 'show me movies' and 'what movies do we have' must produce the
    same routing behavior (both → factual flow, same normalized form).
    """

    MOVIE_VARIANTS = [
        "show me movies",
        "what movies do we have",
        "what movies are there",
        "what movies are showing",
        "now showing",
        "movies",
    ]

    def test_movie_variants_normalize_to_same_form(self):
        """All movie query variants must normalize to the same canonical form."""
        canonical = normalize_query("what movies are showing")
        for variant in self.MOVIE_VARIANTS:
            assert normalize_query(variant) == canonical, (
                f"{variant!r} → {normalize_query(variant)!r}, "
                f"expected {canonical!r}"
            )

    def test_movie_variants_route_factual(self):
        """All movie variants must route to factual flow."""
        for variant in self.MOVIE_VARIANTS:
            state = _make_state(
                variant,
                domain="entertainment",
                sub_intent="movie_showtime",
                primary_intent="movie_lookup",
            )
            result = _run(route_flow(state))
            assert result["flow_type"] == "factual", (
                f"{variant!r}: expected factual, got {result['flow_type']}: "
                f"{result.get('flow_routing_reason', '')}"
            )

    def test_show_me_movies_same_as_what_movies_do_we_have(self):
        """Explicit AC-A check: these two specific variants produce the same flow."""
        norm_a = normalize_query("show me movies")
        norm_b = normalize_query("what movies do we have")
        assert norm_a == norm_b, (
            f"'show me movies' → {norm_a!r}, "
            f"'what movies do we have' → {norm_b!r}: must be equal"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B. MALL OVERVIEW CONTINUITY
# ─────────────────────────────────────────────────────────────────────────────

class TestMallOverviewContinuity:
    """
    AC-B: After 'tell me about the mall', a follow-up like 'more about the mall'
    must remain in mall overview context — no shopping drift.
    """

    def test_more_about_mall_normalizes_to_mall_overview(self):
        """'more about the mall' normalizes to 'tell me about the mall'."""
        assert normalize_query("more about the mall") == normalize_query("tell me about the mall")

    def test_mall_overview_followup_detected(self):
        """_is_mall_overview_followup() returns True for 'more about the mall'."""
        state = _make_state(
            "more about the mall",
            domain="mall_info",
            sub_intent="mall_overview",
            message_kind="followup",
            active_topic="mall_info",
            active_primary_intent="mall_overview",
        )
        assert _is_mall_overview_followup(state) is True

    def test_mall_overview_followup_not_triggered_for_shopping(self):
        """Shopping queries should NOT trigger the mall overview lock."""
        state = _make_state(
            "show me clothes",
            domain="shopping",
            sub_intent="fashion_shopping",
            message_kind="fresh_request",
            active_topic="shopping",
        )
        assert _is_mall_overview_followup(state) is False

    def test_mall_overview_continuation_phrase_detected(self):
        """Short follow-up phrases trigger the mall overview lock even without followup kind."""
        for phrase in ("what else", "tell me more", "more", "go on"):
            state = _make_state(
                phrase,
                domain="mall_info",
                message_kind="fresh_request",   # even without followup kind
                active_topic="mall_info",
                active_primary_intent="mall_overview",
            )
            assert _is_mall_overview_followup(state) is True, (
                f"Expected mall_overview lock for: {phrase!r}"
            )

    def test_mall_overview_update_scene_preserves_topic(self):
        """Scene update on 'more about the mall' must NOT change active_topic."""
        state = _make_state(
            "more about the mall",
            domain="mall_info",
            sub_intent="mall_overview",
            message_kind="followup",
            active_topic="mall_info",
        )
        result = _run(update_scene_memory(state))
        assert result["scene"].active_topic in ("mall_info", ""), (
            f"active_topic drifted to: {result['scene'].active_topic}"
        )
        debug = result.get("debug_enrichment", DebugEnrichment())
        assert debug.continuity_preserved is True


# ─────────────────────────────────────────────────────────────────────────────
# C. CONTEXT-SETTING
# ─────────────────────────────────────────────────────────────────────────────

class TestContextSetting:
    """
    AC-C: 'i am here with the kid' must:
    - be classified as context_setting
    - update scene (companions, audience, visit_type)
    - NOT be forced into dining-only answer
    """

    def test_context_setting_updates_companions(self):
        """Scene after 'i am here with the kid' must contain child companion."""
        state = _make_state(
            "i am here with the kid",
            domain="experience",
            sub_intent="activity_suggestion",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        assert any(c in scene.companions for c in ("child", "kids")), (
            f"Expected child in companions, got: {scene.companions}"
        )

    def test_context_setting_updates_audience(self):
        """Scene after child context must have family audience tags."""
        state = _make_state(
            "i am here with the kid",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        family_tags = {"family_friendly", "kid_friendly", "parent_with_child", "family"}
        assert family_tags & set(scene.audience), (
            f"Expected family audience tags, got: {scene.audience}"
        )

    def test_context_setting_does_not_force_dining(self):
        """context_setting message kind must NOT collapse to a narrow dining result."""
        state = _make_state(
            "i am here with the kid",
            domain="experience",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        # goal should not be forced to "dining"
        assert scene.goal != "dining", (
            f"context_setting forced goal to 'dining' incorrectly: {scene.goal}"
        )

    def test_context_setting_routes_concierge(self):
        """'i am here with the kid' must route to concierge."""
        state = _make_state(
            "i am here with the kid",
            domain="experience",
            sub_intent="activity_suggestion",
            message_kind="context_setting",
            flow_type="",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge"

    def test_context_setting_preserves_continuity(self):
        """Scene update for context_setting must set continuity_preserved=True."""
        state = _make_state(
            "i am here with the kid",
            message_kind="context_setting",
            active_topic="shopping",
        )
        result = _run(update_scene_memory(state))
        debug = result.get("debug_enrichment", DebugEnrichment())
        assert debug.continuity_preserved is True, (
            "context_setting should preserve topic continuity"
        )


# ─────────────────────────────────────────────────────────────────────────────
# D. SHOPPING TASK PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

class TestShoppingTaskPersistence:
    """
    AC-D: Multi-turn shopping task flow must produce one coherent ShoppingTask.

    Turn 1: "I want to buy jackets"
    Turn 2: "for my 5 year old son"
    Turn 3: "what's the price"
    Turn 4: "something affordable"
    """

    def _apply_turn(
        self,
        msg: str,
        scene: SceneMemory,
        message_kind: str = "fresh_request",
        domain: str = "shopping",
        sub_intent: str = "fashion_shopping",
    ) -> SceneMemory:
        """Helper: run update_scene_memory for one turn, return updated scene."""
        state = ConciergeState(
            session_id="test-shopping",
            mall_id="al_nakheel_plaza_28",
            raw_user_message=msg,
            normalized_user_message=msg.lower(),
            intent=InterpretedIntent(
                domain=domain,
                sub_intent=sub_intent,
                message_kind=message_kind,
                confidence=0.85,
            ),
            scene=scene,
            flow_type="concierge",
        )
        result = _run(update_scene_memory(state))
        return result["scene"]

    def test_turn1_creates_shopping_task(self):
        """Turn 1: 'I want to buy jackets' creates shopping_task with product_type=jacket."""
        scene = SceneMemory()
        scene = self._apply_turn("i want to buy jackets", scene)
        assert scene.shopping_task.product_type == "jacket", (
            f"Expected product_type=jacket, got: {scene.shopping_task.product_type}"
        )
        assert scene.shopping_task.product_category == "outerwear"
        assert scene.shopping_task.shopping_stage in ("discovery", "refinement")

    def test_turn2_refines_with_age_and_gender(self):
        """Turn 2: 'for my 5 year old son' must update target_age, target_person, category."""
        scene = SceneMemory(
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="outerwear",
                shopping_stage="discovery",
            ),
            goal="shopping",
            active_topic="shopping",
        )
        scene = self._apply_turn(
            "for my 5 year old son",
            scene,
            message_kind="constraint_refinement",
        )
        task = scene.shopping_task
        assert task.target_age == 5, f"Expected target_age=5, got: {task.target_age}"
        assert task.target_person in ("son",), f"Expected target_person=son, got: {task.target_person}"
        assert task.target_gender == "boy", f"Expected target_gender=boy, got: {task.target_gender}"
        assert "kids" in task.product_category.lower(), (
            f"Expected kids category, got: {task.product_category}"
        )

    def test_turn2_does_not_result_in_no_changes(self):
        """
        Critical: 'for my 5 year old son' must NOT produce 'Constraint refinement: no changes'.
        The shopping_task_updates list must be non-empty.
        """
        scene = SceneMemory(
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="outerwear",
                shopping_stage="discovery",
            ),
            goal="shopping",
            active_topic="shopping",
        )
        state = ConciergeState(
            session_id="test-shopping",
            mall_id="al_nakheel_plaza_28",
            raw_user_message="for my 5 year old son",
            normalized_user_message="for my 5 year old son",
            intent=InterpretedIntent(
                domain="shopping",
                message_kind="constraint_refinement",
                confidence=0.85,
            ),
            scene=scene,
            flow_type="concierge",
        )
        result = _run(update_scene_memory(state))
        debug = result.get("debug_enrichment", DebugEnrichment())
        assert debug.shopping_task_updates, (
            "Expected non-empty shopping_task_updates for 'for my 5 year old son', "
            f"got: {debug.shopping_task_updates}"
        )

    def test_turn3_advances_shopping_stage_to_price_guidance(self):
        """Turn 3: "what's the price" advances shopping_stage to price_guidance."""
        scene = SceneMemory(
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="kids_outerwear",
                target_age=5,
                target_person="son",
                target_gender="boy",
                shopping_stage="refinement",
            ),
            goal="shopping",
            active_topic="shopping",
        )
        scene = self._apply_turn(
            "what's the price",
            scene,
            message_kind="constraint_refinement",
            sub_intent="price_inquiry",
        )
        assert scene.shopping_task.shopping_stage == "price_guidance", (
            f"Expected price_guidance, got: {scene.shopping_task.shopping_stage}"
        )

    def test_turn4_sets_budget_preference(self):
        """Turn 4: 'something affordable' sets budget_preference=affordable."""
        scene = SceneMemory(
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="kids_outerwear",
                target_age=5,
                shopping_stage="price_guidance",
            ),
            goal="shopping",
            active_topic="shopping",
        )
        scene = self._apply_turn(
            "something affordable",
            scene,
            message_kind="constraint_refinement",
        )
        assert scene.shopping_task.budget_preference == "affordable", (
            f"Expected budget_preference=affordable, got: "
            f"{scene.shopping_task.budget_preference}"
        )

    def test_full_flow_task_coherent_across_all_turns(self):
        """End-to-end: all 4 turns produce one coherent ShoppingTask."""
        scene = SceneMemory()

        # Turn 1
        scene = self._apply_turn("i want to buy jackets", scene)
        # Turn 2 — constraint_refinement
        scene = self._apply_turn(
            "for my 5 year old son", scene, message_kind="constraint_refinement"
        )
        # Turn 3 — price inquiry
        scene = self._apply_turn(
            "what's the price", scene, message_kind="constraint_refinement"
        )
        # Turn 4 — budget refinement
        scene = self._apply_turn(
            "something affordable", scene, message_kind="constraint_refinement"
        )

        task = scene.shopping_task
        assert task.product_type == "jacket", f"product_type drifted: {task.product_type}"
        assert "kids" in task.product_category.lower(), (
            f"category not kids-scoped: {task.product_category}"
        )
        assert task.target_age == 5, f"target_age lost: {task.target_age}"
        assert task.budget_preference == "affordable", (
            f"budget_preference lost: {task.budget_preference}"
        )

    def test_shopping_task_scopes_retrieval_category(self):
        """Shopping task scope helper maps kids_outerwear → 'kids' category."""
        scene = SceneMemory(
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="kids_outerwear",
            )
        )
        result = _resolve_shopping_task_category(scene)
        assert result == "kids", f"Expected 'kids', got: {result}"

    def test_rank_and_dedupe_suppresses_dining_for_shopping_task(self):
        """Dining entities must score lower when shopping task is active."""
        entities = [
            {"entity_id": "s1", "name": "H&M Kids", "entity_type": "store",
             "score": 0.7, "semantic_tags": ["kids", "fashion"]},
            {"entity_id": "d1", "name": "McDonald's", "entity_type": "dining",
             "score": 0.8, "semantic_tags": ["fast_food", "family_friendly"]},
            {"entity_id": "s2", "name": "Mothercare", "entity_type": "store",
             "score": 0.65, "semantic_tags": ["kids", "children"]},
        ]
        state = _make_state(
            "jackets for my son",
            domain="shopping",
            sub_intent="fashion_shopping",
            message_kind="fresh_request",
            flow_type="concierge",
            shopping_task=ShoppingTask(
                product_type="jacket",
                product_category="kids_outerwear",
                target_age=5,
                target_gender="boy",
            ),
            entities=entities,
            chosen_strategy="shortlist_recommendation",
        )
        result = _run(rank_and_dedupe(state))
        final = result["context"].selected_entities
        final_names = [e["name"] for e in final]
        # McDonald's should rank below or equal to kids fashion stores
        mcdonalds_pos = next(
            (i for i, e in enumerate(final) if e["name"] == "McDonald's"), len(final)
        )
        hm_pos = next(
            (i for i, e in enumerate(final) if e["name"] == "H&M Kids"), len(final)
        )
        assert hm_pos < mcdonalds_pos or mcdonalds_pos == len(final), (
            f"H&M Kids should rank above McDonald's in kids shopping task. "
            f"Order: {final_names}"
        )
        # Debug should report suppressed count
        debug = result.get("debug_enrichment", DebugEnrichment())
        assert debug.suppressed_off_topic_count >= 1


# ─────────────────────────────────────────────────────────────────────────────
# E. SCENARIO SPECIFICITY
# ─────────────────────────────────────────────────────────────────────────────

class TestScenarioSpecificity:
    """
    AC-E: 'im here for shopping ; i am bridesmaid' must:
    - set scenario = wedding_related
    - NOT collapse to family-shopping playbook
    - NOT set visit_type = family_visit
    """

    def test_bridesmaid_sets_wedding_scenario(self):
        """'i am bridesmaid' must set scenario=wedding_related."""
        state = _make_state(
            "im here for shopping i am bridesmaid",
            domain="shopping",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        assert scene.scenario == "wedding_related", (
            f"Expected scenario=wedding_related, got: {scene.scenario}"
        )

    def test_bridesmaid_sets_user_role(self):
        """'i am bridesmaid' must set user_role=bridesmaid."""
        state = _make_state(
            "i am a bridesmaid",
            domain="shopping",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        assert scene.user_role == "bridesmaid", (
            f"Expected user_role=bridesmaid, got: {scene.user_role}"
        )

    def test_bridesmaid_gets_elegant_style_intent(self):
        """Bridesmaid role must inject 'elegant' into style_intent."""
        state = _make_state(
            "i am a bridesmaid looking for something elegant",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        assert "elegant" in scene.style_intent, (
            f"Expected elegant in style_intent, got: {scene.style_intent}"
        )

    def test_bridesmaid_does_not_set_family_visit(self):
        """Bridesmaid context must NOT trigger family_visit visit_type."""
        state = _make_state(
            "im here for shopping i am bridesmaid",
            message_kind="context_setting",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        assert scene.visit_type != "family_visit", (
            f"bridesmaid should NOT get family_visit, got: {scene.visit_type}"
        )

    def test_bridesmaid_routes_concierge_not_factual(self):
        """Wedding scenario must route to concierge."""
        state = _make_state(
            "im here for shopping i am bridesmaid",
            domain="shopping",
            sub_intent="general_shopping",
            message_kind="context_setting",
            scenario="wedding_related",
            user_role="bridesmaid",
            primary_intent="shopping_recommendation",
            flow_type="",
        )
        result = _run(route_flow(state))
        assert result["flow_type"] == "concierge", (
            f"Expected concierge for bridesmaid, got: {result['flow_type']}: "
            f"{result.get('flow_routing_reason', '')}"
        )

    def test_bridesmaid_primary_intent_not_family_shopping(self):
        """Bridesmaid context must NOT produce primary_intent=family_shopping."""
        state = _make_state(
            "im here for shopping i am bridesmaid",
            domain="shopping",
            sub_intent="general_shopping",
            message_kind="context_setting",
            scenario="wedding_related",
            user_role="bridesmaid",
            primary_intent="shopping_recommendation",
            flow_type="",
        )
        result = _run(route_flow(state))
        assert result.get("primary_intent") not in ("family_shopping",), (
            f"primary_intent must not be family_shopping for bridesmaid context: "
            f"{result.get('primary_intent')}"
        )

    def test_scenario_persists_across_refinement(self):
        """scenario=wedding_related must persist after a refinement turn."""
        state = _make_state(
            "something affordable",
            domain="shopping",
            message_kind="constraint_refinement",
            scenario="wedding_related",
            user_role="bridesmaid",
        )
        result = _run(update_scene_memory(state))
        scene: SceneMemory = result["scene"]
        assert scene.scenario == "wedding_related", (
            f"scenario should persist across refinement, got: {scene.scenario}"
        )
        debug = result.get("debug_enrichment", DebugEnrichment())
        assert debug.scenario_persisted is True


# ─────────────────────────────────────────────────────────────────────────────
# F. DEDUPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplication:
    """
    AC-F: Entities with the same normalized name but DIFFERENT entity_ids
    must be collapsed in concierge mode (name-clash dedup fix).
    """

    def test_name_clash_collapses_in_concierge_mode(self):
        """
        'Season Accessorize' (e1) and 'season accessorize' (e2) must collapse
        even though they have different entity_ids.
        """
        entities = [
            {"entity_id": "e1", "name": "Season Accessorize", "entity_type": "store", "score": 0.9},
            {"entity_id": "e2", "name": "season accessorize", "entity_type": "store", "score": 0.8},
            {"entity_id": "e3", "name": "Zara", "entity_type": "store", "score": 0.85},
        ]
        state = _make_state(
            "shopping",
            domain="shopping",
            sub_intent="general_shopping",
            flow_type="concierge",
            entities=entities,
            chosen_strategy="shortlist_recommendation",
        )
        result = _run(rank_and_dedupe(state))
        final = result["context"].selected_entities
        names_lower = [e["name"].lower() for e in final]
        # Only one variant of season accessorize should survive
        season_count = sum(1 for n in names_lower if "season" in n)
        assert season_count == 1, (
            f"Expected 1 season accessorize variant, got {season_count}: {names_lower}"
        )
        assert len(final) == 2, (
            f"Expected 2 entities after dedup (season + zara), got {len(final)}: "
            f"{[e['name'] for e in final]}"
        )

    def test_higher_scored_variant_survives(self):
        """The higher-scored entity_id variant must survive name-clash dedup."""
        entities = [
            {"entity_id": "e1", "name": "Season Accessorize", "entity_type": "store", "score": 0.9},
            {"entity_id": "e2", "name": "season accessorize", "entity_type": "store", "score": 0.8},
        ]
        state = _make_state(
            "shopping",
            flow_type="concierge",
            entities=entities,
            chosen_strategy="shortlist_recommendation",
        )
        result = _run(rank_and_dedupe(state))
        final = result["context"].selected_entities
        # e1 came first and was registered first — should be the survivor
        assert len(final) == 1
        assert final[0]["entity_id"] == "e1", (
            f"Expected e1 (higher score) to survive, got: {final[0]}"
        )

    def test_same_entity_id_collapses_in_both_flows(self):
        """Entities with identical entity_ids collapse regardless of flow."""
        entities = [
            {"entity_id": "e1", "name": "Starbucks", "entity_type": "cafe", "score": 0.9},
            {"entity_id": "e1", "name": "Starbucks Coffee", "entity_type": "cafe", "score": 0.7},
            {"entity_id": "e2", "name": "Costa", "entity_type": "cafe", "score": 0.6},
        ]
        for flow in ("factual", "concierge"):
            state = _make_state(
                "coffee",
                domain="dining",
                sub_intent="cafe_recommendation",
                flow_type=flow,
                entities=entities,
            )
            result = _run(rank_and_dedupe(state))
            final = result["context"].selected_entities
            ids = [e["entity_id"] for e in final]
            assert ids.count("e1") == 1, (
                f"[{flow}] e1 should appear exactly once, ids={ids}"
            )
            assert len(final) == 2, (
                f"[{flow}] Expected 2 entities after dedup, got {len(final)}"
            )

    def test_normalization_notes_populated_on_name_clash(self):
        """rank_and_dedupe must populate canonical_name_normalization_notes on collapse."""
        entities = [
            {"entity_id": "e1", "name": "H&M", "entity_type": "store", "score": 0.8},
            {"entity_id": "e2", "name": "H & M", "entity_type": "store", "score": 0.7},
        ]
        state = _make_state(
            "fashion",
            domain="shopping",
            sub_intent="fashion_shopping",
            flow_type="concierge",
            entities=entities,
            chosen_strategy="shortlist_recommendation",
        )
        result = _run(rank_and_dedupe(state))
        debug = result.get("debug_enrichment", DebugEnrichment())
        assert debug.duplicate_entities_collapsed >= 1, (
            "Expected at least 1 collapsed duplicate for H&M / H & M"
        )

    def test_dedupe_key_article_stripping(self):
        """Leading articles must be stripped for correct dedup."""
        assert _dedupe_key("The Body Shop") == _dedupe_key("Body Shop")
        assert _dedupe_key("Al Futtaim") == _dedupe_key("Futtaim")

    def test_dedupe_key_unicode_normalization(self):
        """Unicode variants must normalize to ASCII equivalents."""
        assert _dedupe_key("Café") == _dedupe_key("Cafe")
        assert _dedupe_key("Häagen-Dazs") == _dedupe_key("Haagen Dazs")
