"""
Acceptance tests for the Response Mode Resolver.

Covers the six canonical cases from the spec plus supporting unit tests
for confidence classification, vagueness detection, and cross-domain detection.

Run with:
    pytest backend/tests/test_response_mode_resolver.py -v
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
    ShoppingTask,
)
from app.services.response_mode_resolver import (
    BEST_EFFORT_SHORTLIST,
    CLARIFICATION_REQUEST,
    CONTEXT_ACKNOWLEDGEMENT,
    DIRECT_FACTUAL,
    GRACEFUL_RECOVERY,
    GUIDED_RECOMMENDATION,
    HYBRID_PLAN,
    resolve_response_mode,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_state(
    raw_msg: str = "",
    domain: str = "general",
    sub_intent: str = "general_inquiry",
    message_kind: str = "fresh_request",
    flow_type: str = "concierge",
    primary_intent: str = "",
    secondary_intents: list[str] | None = None,
    modifiers: list[str] | None = None,
    confidence: float = 0.75,
    companions: list[str] | None = None,
    occasion: str = "",
    topic_lock: str = "",
    topic_lock_confidence: float = 0.0,
    active_topic: str = "",
    scenario: str = "",
    user_role: str = "",
    playbook_id: str = "",
    playbook_confidence: float = 0.0,
    raw_signals: dict | None = None,
) -> ConciergeState:
    scene = SceneMemory(
        companions=companions or [],
        occasion=occasion,
        topic_lock=topic_lock,
        topic_lock_confidence=topic_lock_confidence,
        active_topic=active_topic,
        scenario=scenario,
        user_role=user_role,
    )
    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=confidence,
        primary_intent=primary_intent,
        secondary_intents=secondary_intents or [],
        modifiers=modifiers or [],
        raw_signals=raw_signals or {},
    )
    playbook = PlaybookResolution(
        selected_playbook=playbook_id,
        playbook_confidence=playbook_confidence,
    )
    return ConciergeState(
        session_id="test-rmr",
        mall_id="al_nakheel_plaza_28",
        raw_user_message=raw_msg,
        normalized_user_message=raw_msg,
        intent=intent,
        scene=scene,
        flow_type=flow_type,
        primary_intent=primary_intent,
        secondary_intents=secondary_intents or [],
        modifiers=modifiers or [],
        playbook=playbook,
        response_plan=ResponsePlan(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Acceptance: VAGUE → best_effort_shortlist
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skip(reason="v1.6 thin-policy resolver: vague→best_effort_shortlist now requires llm_response_mode_hint; covered by test_v16_state_and_nodes.py")
class TestVagueQuery:
    """AC-1: 'anything interesting here?' → best_effort_shortlist"""

    def test_anything_interesting_here(self):
        state = _make_state(
            raw_msg="anything interesting here?",
            domain="exploration",
            sub_intent="open_exploration",
            primary_intent="discovery",
            confidence=0.65,
        )
        mode, conf, reason, fallback = resolve_response_mode(state)
        assert mode == BEST_EFFORT_SHORTLIST, (
            f"Expected best_effort_shortlist, got: {mode} (reason={reason})"
        )

    def test_what_to_do(self):
        state = _make_state(
            raw_msg="what to do here?",
            domain="exploration",
            sub_intent="activity_suggestion",
            primary_intent="discovery",
            confidence=0.60,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == BEST_EFFORT_SHORTLIST

    def test_general_shopping_no_context(self):
        """Vague general shopping → best_effort_shortlist."""
        state = _make_state(
            raw_msg="show me something",
            domain="shopping",
            sub_intent="general_shopping",
            primary_intent="shopping_recommendation",
            confidence=0.65,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == BEST_EFFORT_SHORTLIST

    def test_fallback_applied_true_for_vague(self):
        state = _make_state(
            raw_msg="anything interesting here?",
            domain="exploration",
            sub_intent="open_exploration",
            confidence=0.60,
        )
        _, _, _, fallback = resolve_response_mode(state)
        assert fallback is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Acceptance: CONTEXT-SETTING → context_acknowledgement
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skip(reason="v1.6 thin-policy resolver: context_acknowledgement now set by LLM hint; covered by test_v16_state_and_nodes.py")
class TestContextSetting:
    """AC-2: 'I am here with my family' → context_acknowledgement"""

    def test_i_am_here_with_family(self):
        state = _make_state(
            raw_msg="i am here with my family",
            domain="exploration",
            sub_intent="activity_suggestion",
            message_kind="context_setting",
            primary_intent="discovery",
            companions=["family"],
            confidence=0.80,
        )
        mode, _, reason, _ = resolve_response_mode(state)
        assert mode == CONTEXT_ACKNOWLEDGEMENT, (
            f"Expected context_acknowledgement, got: {mode} (reason={reason})"
        )

    def test_i_am_here_with_the_kid(self):
        state = _make_state(
            raw_msg="i am here with the kid",
            message_kind="context_setting",
            companions=["child"],
            confidence=0.80,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == CONTEXT_ACKNOWLEDGEMENT

    def test_i_am_a_bridesmaid(self):
        state = _make_state(
            raw_msg="i am a bridesmaid",
            message_kind="context_setting",
            user_role="bridesmaid",
            scenario="wedding_related",
            confidence=0.80,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == CONTEXT_ACKNOWLEDGEMENT

    def test_fallback_applied_false_for_context_setting(self):
        state = _make_state(
            raw_msg="i am here with my girlfriend",
            message_kind="context_setting",
            confidence=0.80,
        )
        _, _, _, fallback = resolve_response_mode(state)
        assert fallback is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Acceptance: CROSS-INTENT → hybrid_plan
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skip(reason="v1.6 thin-policy resolver: hybrid_plan now set by LLM hint; covered by test_v16_state_and_nodes.py")
class TestCrossIntent:
    """AC-3: 'food and movies' → hybrid_plan"""

    def test_food_and_movies(self):
        state = _make_state(
            raw_msg="food and movies",
            domain="dining",
            sub_intent="general_dining",
            primary_intent="dining_recommendation",
            secondary_intents=["add_dining_step"],
            confidence=0.70,
        )
        mode, _, reason, _ = resolve_response_mode(state)
        assert mode == HYBRID_PLAN, (
            f"Expected hybrid_plan, got: {mode} (reason={reason})"
        )

    def test_movies_and_food(self):
        """Order-independent cross-domain detection."""
        state = _make_state(
            raw_msg="movies and food",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            secondary_intents=["add_dining_step"],
            confidence=0.75,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == HYBRID_PLAN

    def test_before_movie_constraint_triggers_hybrid(self):
        state = _make_state(
            raw_msg="something to eat before the movie",
            domain="dining",
            sub_intent="quick_bite",
            primary_intent="dining_recommendation",
            secondary_intents=["before_movie_constraint"],
            confidence=0.75,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == HYBRID_PLAN

    def test_after_movie_constraint_triggers_hybrid(self):
        state = _make_state(
            raw_msg="where can we eat after the movie",
            domain="dining",
            sub_intent="general_dining",
            primary_intent="dining_recommendation",
            secondary_intents=["after_movie_constraint"],
            confidence=0.70,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == HYBRID_PLAN

    def test_raw_cross_domain_dining_shopping(self):
        """Raw query with shop + eat without secondary_intents still triggers hybrid."""
        state = _make_state(
            raw_msg="shop and eat",
            domain="shopping",
            sub_intent="general_shopping",
            primary_intent="shopping_recommendation",
            confidence=0.70,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == HYBRID_PLAN

    def test_single_domain_no_hybrid(self):
        """Single-domain query must NOT trigger hybrid_plan."""
        state = _make_state(
            raw_msg="suggest restaurants",
            domain="dining",
            sub_intent="general_dining",
            primary_intent="dining_recommendation",
            confidence=0.80,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode != HYBRID_PLAN


# ─────────────────────────────────────────────────────────────────────────────
# 4. Acceptance: PARTIAL → best_effort_shortlist
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skip(reason="v1.6 thin-policy resolver: partial queries now use guided_recommendation; outdated contract")
class TestPartialQuery:
    """AC-4: 'something nice for my son' → best_effort_shortlist"""

    def test_something_nice_for_my_son(self):
        state = _make_state(
            raw_msg="something nice for my son",
            domain="shopping",
            sub_intent="general_shopping",
            primary_intent="shopping_recommendation",
            modifiers=["gift_friendly", "kid_friendly"],
            confidence=0.65,
        )
        mode, _, reason, _ = resolve_response_mode(state)
        assert mode == BEST_EFFORT_SHORTLIST, (
            f"Expected best_effort_shortlist, got: {mode} (reason={reason})"
        )

    def test_something_for_kids(self):
        state = _make_state(
            raw_msg="something for kids",
            domain="shopping",
            sub_intent="general_shopping",
            primary_intent="shopping_recommendation",
            modifiers=["kid_friendly", "family_friendly"],
            confidence=0.65,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == BEST_EFFORT_SHORTLIST

    def test_anything_for_dinner(self):
        """Vague dining query → best_effort_shortlist."""
        state = _make_state(
            raw_msg="anything for dinner",
            domain="dining",
            sub_intent="general_dining",
            primary_intent="dining_recommendation",
            confidence=0.65,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == BEST_EFFORT_SHORTLIST


# ─────────────────────────────────────────────────────────────────────────────
# 5. Acceptance: BROKEN INPUT → graceful_recovery
# ─────────────────────────────────────────────────────────────────────────────

class TestBrokenInput:
    """AC-5: 'asdf' → clarification_request (graceful handling of gibberish)"""

    def test_gibberish_asdf(self):
        # v1.6: gibberish/unsupported input → graceful_recovery (explain capabilities)
        state = _make_state(
            raw_msg="asdf",
            domain="general",
            sub_intent="general_inquiry",
            primary_intent="unsupported",
            confidence=0.1,
            raw_signals={"unsupported": True},
        )
        mode, conf, reason, fallback = resolve_response_mode(state)
        assert mode == GRACEFUL_RECOVERY, (
            f"Expected graceful_recovery for gibberish, got: {mode} (reason={reason})"
        )
        assert fallback is True

    def test_very_low_confidence(self):
        """Very low confidence without explicit unsupported → graceful_recovery."""
        state = _make_state(
            raw_msg="xyz123 qwerty",
            domain="general",
            sub_intent="general_inquiry",
            primary_intent="",
            confidence=0.20,
        )
        mode, _, _, fallback = resolve_response_mode(state)
        assert mode == GRACEFUL_RECOVERY
        assert fallback is True

    def test_unsupported_flag_in_raw_signals(self):
        # v1.6: unsupported raw signal + no visit context → graceful_recovery
        state = _make_state(
            raw_msg="randomwords",
            domain="general",
            sub_intent="general_inquiry",
            confidence=0.15,
            raw_signals={"unsupported": True},
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == GRACEFUL_RECOVERY

    def test_confidence_level_is_low_for_broken(self):
        state = _make_state(
            raw_msg="asdf",
            primary_intent="unsupported",
            confidence=0.1,
            raw_signals={"unsupported": True},
        )
        _, conf, _, _ = resolve_response_mode(state)
        assert conf == "low"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Acceptance: FOLLOW-UP stays in movie context
# ─────────────────────────────────────────────────────────────────────────────

class TestFollowUpResolution:
    """
    AC-6: 'show me movies' then 'with kid'
          → second turn stays in movie context (direct_factual or guided)
    """

    def test_with_kid_followup_stays_in_movie_context(self):
        state = _make_state(
            raw_msg="with kid",
            domain="entertainment",
            sub_intent="movie_showtime",
            message_kind="followup",
            primary_intent="movie_lookup",
            modifiers=["kid_friendly"],
            confidence=0.70,
            topic_lock="movies",
            topic_lock_confidence=0.8,
            active_topic="entertainment",
        )
        mode, _, reason, _ = resolve_response_mode(state)
        assert mode in (DIRECT_FACTUAL, GUIDED_RECOMMENDATION), (
            f"Expected direct_factual or guided_recommendation for movie follow-up, "
            f"got: {mode} (reason={reason})"
        )
        assert mode != BEST_EFFORT_SHORTLIST, (
            "Follow-up with strong topic_lock must NOT become best_effort_shortlist"
        )
        assert mode != GRACEFUL_RECOVERY

    def test_movie_refinement_stays_in_movie_context(self):
        """'any action movies?' after movie listing stays in movie context."""
        state = _make_state(
            raw_msg="any action movies",
            domain="entertainment",
            sub_intent="movie_showtime",
            message_kind="refinement",
            primary_intent="movie_lookup",
            confidence=0.75,
            topic_lock="movies",
            topic_lock_confidence=0.9,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode in (DIRECT_FACTUAL, GUIDED_RECOMMENDATION)

    def test_short_followup_dining_context(self):
        """Short follow-up in dining context stays guided (not best_effort)."""
        state = _make_state(
            raw_msg="anything quick",
            domain="dining",
            sub_intent="quick_bite",
            message_kind="constraint_refinement",
            primary_intent="dining_recommendation",
            confidence=0.65,
            topic_lock="dining",
            topic_lock_confidence=0.7,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode != GRACEFUL_RECOVERY
        assert mode != BEST_EFFORT_SHORTLIST

    def test_follow_up_without_topic_lock_can_be_shortlist(self):
        """Without topic_lock, a vague short follow-up MAY become best_effort_shortlist."""
        state = _make_state(
            raw_msg="something nice",
            domain="shopping",
            sub_intent="general_shopping",
            message_kind="followup",
            primary_intent="shopping_recommendation",
            confidence=0.55,
            topic_lock="",  # no lock
            topic_lock_confidence=0.0,
        )
        mode, _, _, _ = resolve_response_mode(state)
        # No specific assertion on mode — just ensure it doesn't crash
        assert mode in (
            BEST_EFFORT_SHORTLIST, GUIDED_RECOMMENDATION,
            DIRECT_FACTUAL, GRACEFUL_RECOVERY,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. High-confidence clear intents
# ─────────────────────────────────────────────────────────────────────────────

class TestHighConfidenceClearIntent:
    """AC-2A: Clear intent + high confidence → direct_factual or guided_recommendation."""

    def test_show_me_movies_direct_factual(self):
        """v1.6: 'show me movies' on factual flow → direct_factual (fallback uses flow_type)."""
        state = _make_state(
            raw_msg="show me movies",
            domain="entertainment",
            sub_intent="movie_showtime",
            primary_intent="movie_lookup",
            flow_type="factual",
            confidence=0.90,
        )
        mode, conf, _, _ = resolve_response_mode(state)
        assert mode == DIRECT_FACTUAL
        assert conf == "high"

    def test_where_is_zara_direct_factual(self):
        """'where is Zara' → direct_factual."""
        state = _make_state(
            raw_msg="where is zara",
            domain="navigation",
            sub_intent="location_query",
            primary_intent="location_lookup",
            flow_type="factual",
            confidence=0.90,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == DIRECT_FACTUAL

    def test_suggest_restaurants_guided(self):
        """'suggest restaurants' → guided_recommendation."""
        state = _make_state(
            raw_msg="suggest restaurants",
            domain="dining",
            sub_intent="general_dining",
            primary_intent="dining_recommendation",
            confidence=0.80,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode in (GUIDED_RECOMMENDATION, BEST_EFFORT_SHORTLIST)

    def test_gift_recommendation_guided(self):
        """'I want to buy a gift' (precise sub-intent) → guided_recommendation."""
        state = _make_state(
            raw_msg="i want to buy a gift",
            domain="shopping",
            sub_intent="gift_recommendation",
            primary_intent="gift_shopping",
            confidence=0.85,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == GUIDED_RECOMMENDATION

    def test_playbook_match_guided(self):
        """Strong playbook match → guided_recommendation."""
        state = _make_state(
            raw_msg="plan a date night",
            domain="dining",
            sub_intent="romantic_dining",
            primary_intent="dining_recommendation",
            confidence=0.80,
            playbook_id="pb-date-plan",
            playbook_confidence=0.75,
        )
        mode, _, _, _ = resolve_response_mode(state)
        assert mode == GUIDED_RECOMMENDATION


# Note: TestClassifyConfidence removed — classify_confidence was renamed to the
# private _classify_confidence in v1.6 (thin-policy refactor). Confidence
# classification is now fully driven by the LLM hint; the helper is an
# implementation detail not part of the public resolver contract.

# ─────────────────────────────────────────────────────────────────────────────
# 8. Response mode debug fields round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestDebugFields:
    """Verify the resolver returns all four fields with correct types."""

    def test_returns_four_fields(self):
        state = _make_state(
            raw_msg="suggest a restaurant",
            domain="dining",
            sub_intent="general_dining",
            confidence=0.75,
        )
        result = resolve_response_mode(state)
        assert len(result) == 4

    def test_response_mode_is_string(self):
        state = _make_state()
        mode, _, _, _ = resolve_response_mode(state)
        assert isinstance(mode, str)

    def test_confidence_level_valid(self):
        state = _make_state(confidence=0.8)
        _, conf, _, _ = resolve_response_mode(state)
        assert conf in ("high", "medium", "low")

    def test_reason_is_non_empty_string(self):
        state = _make_state(
            raw_msg="anything interesting here?",
            domain="exploration",
            sub_intent="open_exploration",
            confidence=0.60,
        )
        _, _, reason, _ = resolve_response_mode(state)
        assert isinstance(reason, str) and len(reason) > 0

    def test_fallback_applied_is_bool(self):
        state = _make_state()
        _, _, _, fallback = resolve_response_mode(state)
        assert isinstance(fallback, bool)

    def test_graceful_recovery_for_unsupported_input(self):
        """v1.6: unsupported/gibberish → graceful_recovery with fallback=True."""
        state = _make_state(
            primary_intent="unsupported",
            confidence=0.10,
            raw_signals={"unsupported": True},
        )
        mode, _, _, fallback = resolve_response_mode(state)
        assert mode == GRACEFUL_RECOVERY
        assert fallback is True

    def test_context_setting_medium_confidence_returns_context_ack(self):
        # v1.6: context_setting + medium confidence → context_acknowledgement in fallback
        state = _make_state(
            message_kind="context_setting",
            confidence=0.55,  # medium: >= 0.45 and < 0.75
        )
        mode, _, _, fallback = resolve_response_mode(state)
        assert mode == CONTEXT_ACKNOWLEDGEMENT
        assert fallback is False
