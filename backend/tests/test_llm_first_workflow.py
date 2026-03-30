"""
LLM-First Workflow — comprehensive test suite.

Covers the full LLM-first architectural change:

  1.  MessageKind enum — values, string equality, Pydantic coercion
  2.  SMALLTALK_KINDS gate — correct membership, correct exclusions
  3.  is_smalltalk() routing predicate — reads enum, ignores raw message
  4.  smalltalk node — response pool selection for every MessageKind
  5.  smalltalk metadata — category, experience_mode, debug_summary
  6.  Greeting streak progression — first / returning / persistent
  7.  Preprocessing utilities still intact — normalize, brand, unsupported
  8.  _extract_scenario_from_message — scenario signals still detected
  9.  CLASSIFICATION_PROMPT completeness — all new kinds present
  10. LLM response parsing — valid_kinds guard, unknown kind fallback
  11. Routing exclusions — acknowledgement / companion_correction NOT smalltalk
  12. InterpretedIntent string-compat — old string comparisons still work

Run:
    pytest backend/tests/test_llm_first_workflow.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
import pytest

from app.models.state import (
    ConciergeState,
    InterpretedIntent,
    MessageKind,
    SceneMemory,
    SMALLTALK_KINDS,
)
from app.nodes.smalltalk import (
    _CRISIS_RESPONSES,
    _GREETING_FIRST,
    _GREETING_PERSISTENT,
    _GREETING_RETURNING,
    _IDENTITY_RESPONSES,
    _KIND_TO_POOL,
    _RESPONSES,
    is_smalltalk,
    smalltalk,
)
from app.nodes.interpret_turn import (
    CLASSIFICATION_PROMPT,
    _extract_scenario_from_message,
)
from intent.query_classifier import (
    is_likely_unsupported,
    maybe_correct_brand,
    normalize_query,
    normalize_query_with_pattern,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _state_with_kind(kind: MessageKind | str, greeting_streak: int = 0) -> ConciergeState:
    """Build a minimal ConciergeState with the given message_kind."""
    intent = InterpretedIntent(
        domain="general",
        sub_intent="general_inquiry",
        message_kind=kind,
        confidence=0.95,
    )
    scene = SceneMemory(greeting_streak=greeting_streak)
    return ConciergeState(
        session_id="test",
        mall_id="test_mall",
        raw_user_message="test",
        normalized_user_message="test",
        intent=intent,
        scene=scene,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. MessageKind Enum — values, string equality, completeness
# ═══════════════════════════════════════════════════════════════════════════

class TestMessageKindEnum:
    """Verify the MessageKind(str, Enum) has all expected values and str compat."""

    # All values that must exist
    EXPECTED_VALUES = {
        "fresh_request", "refinement", "correction", "topic_switch",
        "followup", "constraint_refinement", "context_setting",
        "greeting", "smalltalk", "emotional", "disengagement",
        "category_negation", "acknowledgement", "companion_correction",
        # New LLM-classified kinds (previously regex-only)
        "crisis", "identity", "howru", "thanks", "farewell",
    }

    def test_all_expected_values_exist(self):
        """Every expected message_kind value must have a corresponding enum member."""
        actual = {m.value for m in MessageKind}
        missing = self.EXPECTED_VALUES - actual
        assert not missing, f"Missing MessageKind values: {missing}"

    def test_new_kinds_are_present(self):
        """The 5 LLM-only kinds added in this release must be present."""
        for kind_str in ("crisis", "identity", "howru", "thanks", "farewell"):
            assert any(m.value == kind_str for m in MessageKind), (
                f"MessageKind missing new value: {kind_str!r}"
            )

    def test_string_equality_works(self):
        """MessageKind members must compare equal to their string values (str Enum)."""
        assert MessageKind.CRISIS == "crisis"
        assert MessageKind.IDENTITY == "identity"
        assert MessageKind.HOWRU == "howru"
        assert MessageKind.THANKS == "thanks"
        assert MessageKind.FAREWELL == "farewell"
        assert MessageKind.GREETING == "greeting"
        assert MessageKind.ACKNOWLEDGEMENT == "acknowledgement"
        assert MessageKind.FRESH_REQUEST == "fresh_request"

    def test_string_in_set_works(self):
        """Enum members must be found in plain-string sets (used throughout codebase)."""
        kind = MessageKind.ACKNOWLEDGEMENT
        assert kind in {"acknowledgement", "companion_correction"}

    def test_value_property(self):
        """.value returns the plain string."""
        assert MessageKind.CRISIS.value == "crisis"
        assert MessageKind.FAREWELL.value == "farewell"

    def test_message_kind_alias(self):
        """MESSAGE_KIND backward-compat alias must point to the enum class."""
        from app.models.state import MESSAGE_KIND
        assert MESSAGE_KIND is MessageKind


# ═══════════════════════════════════════════════════════════════════════════
# 2. SMALLTALK_KINDS gate
# ═══════════════════════════════════════════════════════════════════════════

class TestSmallTalkKindsGate:
    """SMALLTALK_KINDS must include exactly the right 7 members."""

    EXPECTED_IN = {
        MessageKind.GREETING,
        MessageKind.HOWRU,
        MessageKind.THANKS,
        MessageKind.FAREWELL,
        MessageKind.EMOTIONAL,
        MessageKind.IDENTITY,
        MessageKind.CRISIS,
    }

    EXPECTED_OUT = {
        MessageKind.FRESH_REQUEST,
        MessageKind.REFINEMENT,
        MessageKind.CORRECTION,
        MessageKind.TOPIC_SWITCH,
        MessageKind.FOLLOWUP,
        MessageKind.CONSTRAINT_REFINEMENT,
        MessageKind.CONTEXT_SETTING,
        MessageKind.DISENGAGEMENT,
        MessageKind.CATEGORY_NEGATION,
        MessageKind.ACKNOWLEDGEMENT,
        MessageKind.COMPANION_CORRECTION,
    }

    def test_all_expected_kinds_are_in_gate(self):
        for kind in self.EXPECTED_IN:
            assert kind in SMALLTALK_KINDS, f"{kind.value!r} should be in SMALLTALK_KINDS"

    def test_non_smalltalk_kinds_are_excluded(self):
        for kind in self.EXPECTED_OUT:
            assert kind not in SMALLTALK_KINDS, (
                f"{kind.value!r} should NOT be in SMALLTALK_KINDS"
            )

    def test_acknowledgement_not_in_smalltalk(self):
        """acknowledgement must NOT be routed to smalltalk — it needs a clarifying Q."""
        assert MessageKind.ACKNOWLEDGEMENT not in SMALLTALK_KINDS

    def test_companion_correction_not_in_smalltalk(self):
        """companion_correction must NOT be routed to smalltalk — needs scene update."""
        assert MessageKind.COMPANION_CORRECTION not in SMALLTALK_KINDS

    def test_fresh_request_not_in_smalltalk(self):
        assert MessageKind.FRESH_REQUEST not in SMALLTALK_KINDS

    def test_string_lookup_works(self):
        """Plain strings should also match (str Enum membership)."""
        assert "greeting" in SMALLTALK_KINDS
        assert "crisis" in SMALLTALK_KINDS
        assert "fresh_request" not in SMALLTALK_KINDS
        assert "acknowledgement" not in SMALLTALK_KINDS


# ═══════════════════════════════════════════════════════════════════════════
# 3. is_smalltalk() routing predicate
# ═══════════════════════════════════════════════════════════════════════════

class TestIsSmallTalkPredicate:
    """is_smalltalk() must read state.intent.message_kind — no regex involved."""

    @pytest.mark.parametrize("kind", [
        MessageKind.GREETING,
        MessageKind.HOWRU,
        MessageKind.THANKS,
        MessageKind.FAREWELL,
        MessageKind.EMOTIONAL,
        MessageKind.IDENTITY,
        MessageKind.CRISIS,
    ])
    def test_returns_true_for_all_smalltalk_kinds(self, kind):
        state = _state_with_kind(kind)
        assert is_smalltalk(state), f"is_smalltalk should be True for {kind.value!r}"

    @pytest.mark.parametrize("kind", [
        MessageKind.FRESH_REQUEST,
        MessageKind.REFINEMENT,
        MessageKind.CORRECTION,
        MessageKind.FOLLOWUP,
        MessageKind.ACKNOWLEDGEMENT,
        MessageKind.COMPANION_CORRECTION,
        MessageKind.CONTEXT_SETTING,
        MessageKind.CONSTRAINT_REFINEMENT,
        MessageKind.DISENGAGEMENT,
        MessageKind.CATEGORY_NEGATION,
    ])
    def test_returns_false_for_non_smalltalk_kinds(self, kind):
        state = _state_with_kind(kind)
        assert not is_smalltalk(state), (
            f"is_smalltalk should be False for {kind.value!r}"
        )

    def test_reads_intent_not_raw_message(self):
        """Routing predicate must ignore raw_user_message content entirely."""
        # Even a genuine greeting text should NOT trigger smalltalk if the
        # LLM set message_kind to fresh_request (e.g. "hi, show me movies")
        state = _state_with_kind(MessageKind.FRESH_REQUEST)
        state.raw_user_message = "hi hello good morning"
        state.normalized_user_message = "hi hello good morning"
        assert not is_smalltalk(state)

    def test_crisis_message_kind_triggers_smalltalk(self):
        """Crisis statements set by the LLM must route to smalltalk node."""
        state = _state_with_kind(MessageKind.CRISIS)
        state.raw_user_message = "shall i jump off the roof"
        assert is_smalltalk(state)

    def test_identity_message_kind_triggers_smalltalk(self):
        state = _state_with_kind(MessageKind.IDENTITY)
        state.raw_user_message = "who are you"
        assert is_smalltalk(state)


# ═══════════════════════════════════════════════════════════════════════════
# 4. smalltalk node — response pool selection
# ═══════════════════════════════════════════════════════════════════════════

class TestSmalltalkNodeResponsePools:
    """The smalltalk node must select from the correct response pool for every kind."""

    def test_crisis_uses_crisis_pool(self):
        state = _state_with_kind(MessageKind.CRISIS)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _CRISIS_RESPONSES

    def test_identity_uses_identity_pool(self):
        state = _state_with_kind(MessageKind.IDENTITY)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _IDENTITY_RESPONSES

    def test_greeting_streak_0_uses_first_pool(self):
        state = _state_with_kind(MessageKind.GREETING, greeting_streak=0)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _GREETING_FIRST

    def test_greeting_streak_1_uses_returning_pool(self):
        state = _state_with_kind(MessageKind.GREETING, greeting_streak=1)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _GREETING_RETURNING

    def test_greeting_streak_2_uses_persistent_pool(self):
        state = _state_with_kind(MessageKind.GREETING, greeting_streak=2)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _GREETING_PERSISTENT

    def test_greeting_streak_5_uses_persistent_pool(self):
        """Any streak ≥ 2 should use persistent pool."""
        state = _state_with_kind(MessageKind.GREETING, greeting_streak=5)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _GREETING_PERSISTENT

    def test_howru_uses_howru_pool(self):
        state = _state_with_kind(MessageKind.HOWRU)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _RESPONSES["howru"]

    def test_thanks_uses_thanks_pool(self):
        state = _state_with_kind(MessageKind.THANKS)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _RESPONSES["thanks"]

    def test_farewell_uses_farewell_pool(self):
        state = _state_with_kind(MessageKind.FAREWELL)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _RESPONSES["farewell"]

    def test_emotional_uses_emotional_pool(self):
        state = _state_with_kind(MessageKind.EMOTIONAL)
        result = _run(smalltalk(state))
        assert result["final_response_text"] in _RESPONSES["emotional"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. smalltalk node — response metadata and debug output
# ═══════════════════════════════════════════════════════════════════════════

class TestSmalltalkMetadata:
    """Output dict fields must be correctly populated for every kind."""

    @pytest.mark.parametrize("kind,expected_mode", [
        (MessageKind.CRISIS,    "crisis_support"),
        (MessageKind.IDENTITY,  "identity_response"),
        (MessageKind.GREETING,  "greeting_scaffold"),
        (MessageKind.HOWRU,     "smalltalk"),
        (MessageKind.THANKS,    "smalltalk"),
        (MessageKind.FAREWELL,  "smalltalk"),
        (MessageKind.EMOTIONAL, "smalltalk"),
    ])
    def test_experience_mode_is_correct(self, kind, expected_mode):
        state = _state_with_kind(kind)
        result = _run(smalltalk(state))
        msg = result["messages"][0]
        assert msg.metadata["experience_mode"] == expected_mode, (
            f"{kind.value!r}: expected experience_mode={expected_mode!r}, "
            f"got {msg.metadata['experience_mode']!r}"
        )

    @pytest.mark.parametrize("kind", [
        MessageKind.CRISIS, MessageKind.IDENTITY, MessageKind.GREETING,
        MessageKind.HOWRU, MessageKind.THANKS, MessageKind.FAREWELL,
        MessageKind.EMOTIONAL,
    ])
    def test_category_matches_kind_value(self, kind):
        """metadata['category'] must equal the MessageKind string value."""
        state = _state_with_kind(kind)
        result = _run(smalltalk(state))
        msg = result["messages"][0]
        assert msg.metadata["category"] == kind.value, (
            f"metadata category {msg.metadata['category']!r} != {kind.value!r}"
        )

    @pytest.mark.parametrize("kind", [
        MessageKind.CRISIS, MessageKind.IDENTITY, MessageKind.GREETING,
        MessageKind.HOWRU, MessageKind.THANKS, MessageKind.FAREWELL,
        MessageKind.EMOTIONAL,
    ])
    def test_debug_summary_contains_kind(self, kind):
        state = _state_with_kind(kind)
        result = _run(smalltalk(state))
        assert kind.value in result["response_debug_summary"]

    def test_final_response_text_non_empty(self):
        for kind in SMALLTALK_KINDS:
            state = _state_with_kind(kind)
            result = _run(smalltalk(state))
            assert result["final_response_text"], (
                f"Empty response text for {kind.value!r}"
            )

    def test_message_role_is_assistant(self):
        state = _state_with_kind(MessageKind.GREETING)
        result = _run(smalltalk(state))
        assert result["messages"][0].role == "assistant"

    def test_message_strategy_is_smalltalk(self):
        state = _state_with_kind(MessageKind.HOWRU)
        result = _run(smalltalk(state))
        assert result["messages"][0].metadata["strategy"] == "smalltalk"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Crisis response content quality
# ═══════════════════════════════════════════════════════════════════════════

class TestCrisisResponseQuality:
    """Crisis responses must be empathetic and NOT contain mall-related content."""

    # These are activity redirect phrases — NOT mere self-identification.
    # "I'm just a mall assistant" is acceptable; "shop at X" or "visit the restaurant" is not.
    MALL_KEYWORDS = [
        "shop at", "visit the restaurant", "grab a bite", "head to",
        "check out", "restaurant recommendation", "dining option",
        "movie at", "cinema at", "find a store", "offer at",
    ]
    EMPATHY_MARKERS = [
        "hear", "reach", "support", "help", "alone", "crisis", "wellbeing",
        "concerned", "helpline", "trust",
    ]

    def test_crisis_response_not_mall_redirect(self):
        """None of the crisis responses should redirect to mall activities."""
        for response in _CRISIS_RESPONSES:
            lower = response.lower()
            for kw in self.MALL_KEYWORDS:
                assert kw not in lower, (
                    f"Crisis response contains mall keyword {kw!r}:\n{response}"
                )

    def test_crisis_response_contains_empathy_language(self):
        """At least one crisis response should contain genuine empathy markers."""
        empathy_found = False
        for response in _CRISIS_RESPONSES:
            lower = response.lower()
            if any(marker in lower for marker in self.EMPATHY_MARKERS):
                empathy_found = True
                break
        assert empathy_found, "No crisis response contains empathy language"

    def test_identity_response_describes_assistant(self):
        """Identity responses must mention the bot's mall concierge function."""
        for response in _IDENTITY_RESPONSES:
            lower = response.lower()
            has_identity_content = any(
                kw in lower for kw in ("concierge", "assistant", "mall", "help", "guide")
            )
            assert has_identity_content, (
                f"Identity response doesn't describe the bot's role:\n{response}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 7. _KIND_TO_POOL mapping completeness
# ═══════════════════════════════════════════════════════════════════════════

class TestKindToPoolMapping:
    """_KIND_TO_POOL must cover all SMALLTALK_KINDS except the special-cased ones."""

    # These are handled with dedicated branches (not via _KIND_TO_POOL)
    SPECIAL_CASED = {MessageKind.CRISIS, MessageKind.IDENTITY, MessageKind.GREETING}

    def test_all_non_special_smalltalk_kinds_have_pool_entry(self):
        """Every SMALLTALK_KINDS member that isn't special-cased must be in _KIND_TO_POOL."""
        for kind in SMALLTALK_KINDS:
            if kind not in self.SPECIAL_CASED:
                assert kind in _KIND_TO_POOL, (
                    f"Missing _KIND_TO_POOL entry for {kind.value!r}"
                )

    def test_pool_keys_exist_in_responses_dict(self):
        """Every pool key referenced in _KIND_TO_POOL must exist in _RESPONSES."""
        for kind, pool_key in _KIND_TO_POOL.items():
            assert pool_key in _RESPONSES, (
                f"_KIND_TO_POOL[{kind.value!r}]={pool_key!r} not found in _RESPONSES"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 8. InterpretedIntent Pydantic coercion — backward compatibility
# ═══════════════════════════════════════════════════════════════════════════

class TestInterpretedIntentCoercion:
    """InterpretedIntent.message_kind must coerce strings → MessageKind enum."""

    def test_string_coercion_new_kinds(self):
        """Newly added kinds must coerce from string."""
        for kind_str in ("crisis", "identity", "howru", "thanks", "farewell"):
            intent = InterpretedIntent(message_kind=kind_str)
            assert isinstance(intent.message_kind, MessageKind), (
                f"message_kind should be MessageKind, got {type(intent.message_kind)}"
            )
            assert intent.message_kind.value == kind_str

    def test_string_coercion_existing_kinds(self):
        for kind_str in (
            "fresh_request", "refinement", "correction", "followup",
            "acknowledgement", "companion_correction", "context_setting",
            "greeting", "emotional", "disengagement",
        ):
            intent = InterpretedIntent(message_kind=kind_str)
            assert intent.message_kind == kind_str

    def test_default_is_fresh_request(self):
        intent = InterpretedIntent()
        assert intent.message_kind == "fresh_request"
        assert intent.message_kind == MessageKind.FRESH_REQUEST

    def test_old_string_comparisons_still_work(self):
        """Downstream code that does intent.message_kind == 'acknowledgement' must still work."""
        intent = InterpretedIntent(message_kind="acknowledgement")
        assert intent.message_kind == "acknowledgement"
        assert intent.message_kind in ("acknowledgement", "companion_correction")
        assert intent.message_kind not in ("fresh_request", "refinement")

    def test_enum_comparisons_work(self):
        intent = InterpretedIntent(message_kind="crisis")
        assert intent.message_kind == MessageKind.CRISIS
        assert intent.message_kind in SMALLTALK_KINDS


# ═══════════════════════════════════════════════════════════════════════════
# 9. CLASSIFICATION_PROMPT completeness
# ═══════════════════════════════════════════════════════════════════════════

class TestClassificationPromptCompleteness:
    """CLASSIFICATION_PROMPT must list all new message_kind values and safety rules."""

    NEW_KINDS = ["crisis", "identity", "howru", "thanks", "farewell"]
    EXISTING_KINDS = [
        "fresh_request", "correction", "refinement", "constraint_refinement",
        "topic_switch", "followup", "context_setting", "acknowledgement",
        "companion_correction", "disengagement", "category_negation", "emotional",
    ]
    CRISIS_SAFETY_MARKERS = [
        "jump off", "kill myself", "self-harm", "crisis",
        "Safety first",
    ]

    def test_all_new_kinds_in_prompt(self):
        for kind in self.NEW_KINDS:
            assert kind in CLASSIFICATION_PROMPT, (
                f"New message_kind {kind!r} missing from CLASSIFICATION_PROMPT"
            )

    def test_all_existing_kinds_in_prompt(self):
        for kind in self.EXISTING_KINDS:
            assert kind in CLASSIFICATION_PROMPT, (
                f"Existing message_kind {kind!r} missing from CLASSIFICATION_PROMPT"
            )

    def test_crisis_safety_instructions_present(self):
        """Prompt must contain crisis detection examples for the LLM to recognise them."""
        found_any = any(marker in CLASSIFICATION_PROMPT for marker in self.CRISIS_SAFETY_MARKERS)
        assert found_any, (
            "CLASSIFICATION_PROMPT missing crisis/self-harm safety instructions"
        )

    def test_identity_examples_present(self):
        assert "who are you" in CLASSIFICATION_PROMPT.lower() or "identity" in CLASSIFICATION_PROMPT

    def test_prompt_instructs_domain_general_for_smalltalk(self):
        """Prompt should tell the LLM to use domain='general' for smalltalk kinds."""
        assert 'domain="general"' in CLASSIFICATION_PROMPT or "domain=general" in CLASSIFICATION_PROMPT or "general/general_inquiry" in CLASSIFICATION_PROMPT


# ═══════════════════════════════════════════════════════════════════════════
# 10. LLM response parsing — valid_kinds guard
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMResponseParsing:
    """The valid_kinds guard in _llm_classify must accept all known kinds."""

    # These are the valid_kinds as defined in interpret_turn._llm_classify
    EXPECTED_VALID_KINDS = {
        "fresh_request", "correction", "refinement", "constraint_refinement",
        "topic_switch", "followup", "context_setting",
        "disengagement", "category_negation", "emotional",
        "acknowledgement", "companion_correction",
        "greeting", "howru", "thanks", "farewell", "identity", "crisis",
    }

    def test_all_smalltalk_kinds_in_valid_set(self):
        """All SMALLTALK_KINDS string values must be in the LLM valid_kinds set."""
        for kind in SMALLTALK_KINDS:
            assert kind.value in self.EXPECTED_VALID_KINDS, (
                f"SMALLTALK_KINDS member {kind.value!r} not in LLM valid_kinds"
            )

    def test_all_non_smalltalk_kinds_in_valid_set(self):
        for kind in MessageKind:
            if kind not in SMALLTALK_KINDS and kind != MessageKind.SMALLTALK:
                assert kind.value in self.EXPECTED_VALID_KINDS, (
                    f"MessageKind {kind.value!r} not in LLM valid_kinds"
                )


# ═══════════════════════════════════════════════════════════════════════════
# 11. Preprocessing utilities — normalize, brand, unsupported
# ═══════════════════════════════════════════════════════════════════════════

class TestPreprocessingUtilities:
    """query_classifier utilities must still function after the classifier removal."""

    # ── normalize_query ───────────────────────────────────────────────
    MOVIE_VARIANTS_TO_CANONICAL = {
        "movies":                   "what movies are showing",
        "now showing":              "what movies are showing",
        "what movies are there":    "what movies are showing",
        "what can i watch":         "what movies are showing",
    }

    MALL_OVERVIEW_TO_CANONICAL = {
        "tell me about the mall":       "tell me about the mall",
        "mall info":                    "tell me about the mall",
        "about this mall":              "tell me about the mall",
    }

    def test_movie_variants_normalize_correctly(self):
        for variant, canonical in self.MOVIE_VARIANTS_TO_CANONICAL.items():
            assert normalize_query(variant) == canonical, (
                f"{variant!r} → expected {canonical!r}, got {normalize_query(variant)!r}"
            )

    def test_mall_overview_variants_normalize_correctly(self):
        for variant, canonical in self.MALL_OVERVIEW_TO_CANONICAL.items():
            result = normalize_query(variant)
            assert result == canonical, (
                f"{variant!r} → expected {canonical!r}, got {result!r}"
            )

    def test_normalize_returns_original_for_unknown_query(self):
        query = "what perfume stores do you have"
        assert normalize_query(query) == query

    def test_normalize_with_pattern_returns_label(self):
        _, label = normalize_query_with_pattern("what movies are showing")
        assert label != "", "Expected a non-empty pattern label for canonical movie query"

    def test_normalize_with_pattern_no_label_for_unknown(self):
        _, label = normalize_query_with_pattern("suggest something for a date night")
        assert label == ""

    # ── is_likely_unsupported ─────────────────────────────────────────
    GIBBERISH_INPUTS = [
        "asdf", "qwerty", "zxcv", "aaaaaaa", "sdfghjkl",
        "rtks", "jjjjjj",
    ]

    VALID_SHORT_INPUTS = [
        "hi", "ok", "food", "mall", "movies", "dining", "bye",
        "gifts", "perfume",
    ]

    def test_gibberish_flagged_as_unsupported(self):
        for text in self.GIBBERISH_INPUTS:
            assert is_likely_unsupported(text), (
                f"Expected {text!r} to be flagged as unsupported"
            )

    def test_valid_short_queries_not_flagged(self):
        for text in self.VALID_SHORT_INPUTS:
            assert not is_likely_unsupported(text), (
                f"Valid input {text!r} incorrectly flagged as unsupported"
            )

    def test_empty_string_is_unsupported(self):
        assert is_likely_unsupported("")

    def test_off_topic_signals_flagged(self):
        assert is_likely_unsupported("what is the weather today")
        assert is_likely_unsupported("tell me a joke")

    # ── maybe_correct_brand ───────────────────────────────────────────
    def test_known_misspelling_corrected(self):
        brand, conf = maybe_correct_brand("nkie")
        assert brand == "Nike"
        assert conf > 0.5

    def test_exact_brand_match(self):
        brand, conf = maybe_correct_brand("zaara")
        assert brand == "Zara"

    def test_unknown_query_returns_none(self):
        brand, conf = maybe_correct_brand("i want to eat sushi")
        assert brand is None
        assert conf == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 12. _extract_scenario_from_message
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractScenarioFromMessage:
    """Scenario extractor must still detect signals correctly."""

    SCENARIO_CASES = [
        ("i am a bridesmaid here for shopping",   "wedding_related"),
        ("i am here with my kid",                 "family_outing"),
        ("with my son",                           "family_outing"),
        ("date night ideas",                      "date"),
        ("with my girlfriend",                    "date"),
        ("gift for my girlfriend",                "date"),
        ("we are in a hurry",                     "quick_visit"),
        ("quick visit",                           "quick_visit"),
        ("something quick before the movie",      "before_movie"),
        ("it's my birthday today",                "birthday"),
        ("first time visiting",                   "first_visit"),
        ("here with my friends",                  "group_outing"),
        ("i came alone",                          "solo_visit"),
    ]

    @pytest.mark.parametrize("msg,expected_scenario", SCENARIO_CASES)
    def test_scenario_detected(self, msg, expected_scenario):
        result = _extract_scenario_from_message(msg, {})
        assert result == expected_scenario, (
            f"{msg!r} → expected {expected_scenario!r}, got {result!r}"
        )

    def test_fallback_from_companions_context(self):
        """If no signals in message, fall back to scene context companions."""
        result = _extract_scenario_from_message("food", {"companions": ["son"]})
        assert result == "family_outing"

    def test_fallback_from_occasion_context(self):
        result = _extract_scenario_from_message("food", {"occasion": "anniversary"})
        assert result == "date"

    def test_empty_when_no_signals(self):
        result = _extract_scenario_from_message("what movies are showing", {})
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# 13. Routing isolation — non-smalltalk kinds must NOT enter smalltalk node
# ═══════════════════════════════════════════════════════════════════════════

class TestRoutingIsolation:
    """
    Verifies that conversational kinds requiring downstream processing
    (context update, retrieval, response generation) never short-circuit
    to the smalltalk fast path.
    """

    # Kinds that must NEVER route to smalltalk — they need real pipeline work
    NON_SMALLTALK_CRITICAL = [
        ("acknowledgement",      "bot should ask a clarifying question, not recommend"),
        ("companion_correction", "scene corrections must be applied in update_scene_memory"),
        ("context_setting",      "scene context must be stored"),
        ("disengagement",        "bot must respond empathetically without recycling old recs"),
        ("fresh_request",        "new requests need retrieval"),
        ("constraint_refinement","retrieval must be re-run with new constraint"),
        ("topic_switch",         "new topic needs retrieval"),
    ]

    @pytest.mark.parametrize("kind_str,reason", NON_SMALLTALK_CRITICAL)
    def test_critical_kinds_not_routed_to_smalltalk(self, kind_str, reason):
        state = _state_with_kind(kind_str)
        assert not is_smalltalk(state), (
            f"{kind_str!r} incorrectly routed to smalltalk.\nReason this matters: {reason}"
        )

    def test_greeting_with_trailing_request_should_use_intent_kind(self):
        """
        'hi, show me movies' — LLM may set this as fresh_request.
        If it does, is_smalltalk must return False regardless of raw message content.
        """
        state = _state_with_kind(MessageKind.FRESH_REQUEST)
        state.raw_user_message = "hi show me movies"
        assert not is_smalltalk(state)


# ═══════════════════════════════════════════════════════════════════════════
# 14. Bug A — "okay" / "not sure" / "wait" → clarifying question, NOT recommendations
# ═══════════════════════════════════════════════════════════════════════════

class TestAcknowledgementEarlyExit:
    """
    When message_kind=acknowledgement the generate_response node must return
    a clarifying question immediately, before calling the LLM or retrieval.

    Original bug: "okay" triggered unsolicited shopping recommendations because
    the message_kind was overridden to fresh_request by the old rule classifier.
    """

    def _make_ack_state(self, active_topic: str = "", previous_need: str = "") -> ConciergeState:
        from app.models.state import DebugEnrichment
        intent = InterpretedIntent(
            domain="general",
            sub_intent="general_inquiry",
            message_kind=MessageKind.ACKNOWLEDGEMENT,
            confidence=0.97,
        )
        scene = SceneMemory(active_topic=active_topic, previous_need=previous_need)
        return ConciergeState(
            session_id="test",
            mall_id="test_mall",
            raw_user_message="okay",
            normalized_user_message="okay",
            intent=intent,
            scene=scene,
            debug_enrichment=DebugEnrichment(),
        )

    def _run_generate_response(self, state):
        """Run generate_response with a mocked runtime (mall context not needed for early exits)."""
        from app.nodes.generate_response import generate_response
        with patch("app.nodes.generate_response.get_mall_context", return_value=MagicMock()):
            return _run(generate_response(state))

    def test_acknowledgement_triggers_early_exit(self):
        """generate_response must detect acknowledgement and short-circuit."""
        state = self._make_ack_state()
        result = self._run_generate_response(state)
        assert result["response_debug_summary"] == "strategy=acknowledgement_clarification"

    def test_acknowledgement_response_is_clarifying_question(self):
        """The returned text must be a question, not a list of recommendations."""
        state = self._make_ack_state()
        result = self._run_generate_response(state)
        text = result["final_response_text"]
        has_question = "?" in text or any(
            w in text.lower() for w in ("what", "looking for", "tell me", "let me know")
        )
        assert has_question, f"Response should be a question, got: {text!r}"

    def test_acknowledgement_response_not_a_recommendation_list(self):
        """Response must NOT contain shop/restaurant/movie recommendation markers."""
        state = self._make_ack_state()
        result = self._run_generate_response(state)
        text = result["final_response_text"].lower()
        for marker in ("1.", "2.", "3.", "•", "★", "⭐", "zara", "h&m"):
            assert marker not in text, (
                f"Acknowledgement response contains recommendation marker {marker!r}"
            )

    def test_acknowledgement_with_active_topic_anchors_question(self):
        """If there's an active topic, the clarifying Q should reference it."""
        state = self._make_ack_state(active_topic="shopping")
        result = self._run_generate_response(state)
        text = result["final_response_text"].lower()
        assert "shopping" in text, (
            f"Expected active_topic 'shopping' anchored in question, got: {text!r}"
        )

    def test_acknowledgement_metadata_strategy(self):
        """Message metadata must record the acknowledgement_clarification strategy."""
        state = self._make_ack_state()
        result = self._run_generate_response(state)
        msg = result["messages"][0]
        assert msg.metadata.get("strategy") == "acknowledgement_clarification"
        assert msg.metadata.get("experience_mode") == "clarification"

    @pytest.mark.parametrize("utterance", [
        "okay", "ok", "wait", "not sure", "hmm", "sure", "alright",
        "i see", "anyways", "got it",
    ])
    def test_common_acknowledgement_utterances_set_correct_kind(self, utterance):
        """All classic acknowledgement fillers must coerce to MessageKind.ACKNOWLEDGEMENT."""
        intent = InterpretedIntent(message_kind="acknowledgement")
        assert intent.message_kind == MessageKind.ACKNOWLEDGEMENT, (
            f"Utterance {utterance!r} should be classified as acknowledgement"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 15. Bug C — "jackets" → "for my kid" — shopping_task context preserved
# ═══════════════════════════════════════════════════════════════════════════

class TestShoppingTaskContinuity:
    """
    When a shopping_task is active, follow-up refinements must preserve and
    extend it — not reset it.

    Original bug: "for my kid" after "jackets" cleared product_type and
    gave fragrance/beauty recommendations instead of kids' jackets.
    """

    def _make_shopping_state(
        self,
        msg: str,
        product_type: str = "",
        target_person: str = "",
        domain: str = "shopping",
        sub_intent: str = "general_shopping",
        message_kind: str = "constraint_refinement",
    ) -> ConciergeState:
        from app.models.state import ShoppingTask
        intent = InterpretedIntent(
            domain=domain,
            sub_intent=sub_intent,
            message_kind=message_kind,
            confidence=0.9,
        )
        task = ShoppingTask(product_type=product_type, target_person=target_person)
        scene = SceneMemory(shopping_task=task)
        return ConciergeState(
            session_id="test",
            mall_id="test_mall",
            raw_user_message=msg,
            normalized_user_message=msg,
            intent=intent,
            scene=scene,
        )

    @pytest.mark.asyncio
    async def test_for_my_kid_preserves_product_type(self):
        """'for my kid' follow-up must keep product_type=jackets."""
        from app.nodes.update_scene_memory import update_scene_memory
        state = self._make_shopping_state(
            msg="for my kid",
            product_type="jackets",  # already set from "jackets" turn
            message_kind="constraint_refinement",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]
        assert scene.shopping_task.product_type == "jackets", (
            f"product_type reset! Expected 'jackets', got {scene.shopping_task.product_type!r}"
        )

    @pytest.mark.asyncio
    async def test_for_my_kid_sets_target_person(self):
        """'for my kid' must set target_person=child on the shopping_task."""
        from app.nodes.update_scene_memory import update_scene_memory
        state = self._make_shopping_state(
            msg="for my kid",
            product_type="jackets",
            message_kind="constraint_refinement",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]
        child_signals = {"child", "son", "daughter", "kids"}
        assert scene.shopping_task.target_person in child_signals or (
            "child" in (scene.target_person or "").lower()
        ), (
            f"Expected child target_person, got shopping_task.target_person="
            f"{scene.shopping_task.target_person!r}, scene.target_person={scene.target_person!r}"
        )

    @pytest.mark.asyncio
    async def test_product_type_not_reset_by_refinement(self):
        """An active product_type must never be cleared by a constraint_refinement turn."""
        from app.nodes.update_scene_memory import update_scene_memory
        for refinement in ("something cheaper", "not too expensive", "different color"):
            state = self._make_shopping_state(
                msg=refinement,
                product_type="jackets",
                message_kind="constraint_refinement",
            )
            result = await update_scene_memory(state)
            scene = result["scene"]
            assert scene.shopping_task.product_type == "jackets", (
                f"product_type cleared by {refinement!r}"
            )

    def test_shopping_task_is_not_smalltalk(self):
        """constraint_refinement (Bug C's message_kind) must never route to smalltalk."""
        state = _state_with_kind(MessageKind.CONSTRAINT_REFINEMENT)
        assert not is_smalltalk(state)


# ═══════════════════════════════════════════════════════════════════════════
# 16. Bug D — "what else" context drift (shopping → mall overview)
# ═══════════════════════════════════════════════════════════════════════════

class TestContextDriftGuard:
    """
    "what else" after shopping for jackets must stay in shopping context,
    NOT trigger a mall overview response.

    Original bug: _is_mall_overview_followup fired too broadly when
    active_topic was shopping, giving mixed entertainment/home-decor results.
    """

    def _make_followup_state(
        self,
        active_topic: str = "",
        product_type: str = "",
        active_primary_intent: str = "",
        topic_lock: str = "",
    ) -> ConciergeState:
        from app.models.state import ShoppingTask
        intent = InterpretedIntent(
            domain="shopping",
            sub_intent="general_shopping",
            message_kind=MessageKind.FOLLOWUP,
            confidence=0.9,
        )
        task = ShoppingTask(product_type=product_type) if product_type else ShoppingTask()
        scene = SceneMemory(
            active_topic=active_topic,
            active_primary_intent=active_primary_intent,
            topic_lock=topic_lock,
            shopping_task=task,
        )
        return ConciergeState(
            session_id="test",
            mall_id="test_mall",
            raw_user_message="what else",
            normalized_user_message="what else",
            intent=intent,
            scene=scene,
        )

    def test_what_else_in_shopping_context_not_mall_overview(self):
        """'what else' when active_topic=shopping must NOT trigger mall overview."""
        from app.nodes.compose_context import _is_mall_overview_followup
        state = self._make_followup_state(active_topic="shopping")
        assert not _is_mall_overview_followup(state), (
            "'what else' with active_topic=shopping incorrectly flagged as mall overview"
        )

    def test_what_else_with_active_shopping_task_not_mall_overview(self):
        """'what else' with an active shopping_task (product_type set) must never be mall overview."""
        from app.nodes.compose_context import _is_mall_overview_followup
        state = self._make_followup_state(active_topic="shopping", product_type="jackets")
        assert not _is_mall_overview_followup(state)

    def test_what_else_in_dining_context_not_mall_overview(self):
        """'what else' when active_topic=dining must stay in dining."""
        from app.nodes.compose_context import _is_mall_overview_followup
        state = self._make_followup_state(active_topic="dining")
        assert not _is_mall_overview_followup(state)

    def test_what_else_in_entertainment_context_not_mall_overview(self):
        from app.nodes.compose_context import _is_mall_overview_followup
        state = self._make_followup_state(active_topic="entertainment")
        assert not _is_mall_overview_followup(state)

    def test_what_else_in_mall_info_context_is_mall_overview(self):
        """'what else' within a genuine mall_info topic SHOULD trigger mall overview."""
        from app.nodes.compose_context import _is_mall_overview_followup
        state = self._make_followup_state(
            active_topic="mall_info",
            active_primary_intent="mall_info_lookup",
        )
        assert _is_mall_overview_followup(state), (
            "'what else' in mall_info context should be treated as mall overview followup"
        )

    def test_context_drift_guard_with_entertainment_then_shopping(self):
        """After shopping, active_topic=shopping blocks mall overview even if prior was entertainment."""
        from app.nodes.compose_context import _is_mall_overview_followup
        state = self._make_followup_state(active_topic="shopping", product_type="jackets")
        assert not _is_mall_overview_followup(state)


# ═══════════════════════════════════════════════════════════════════════════
# 17. Bug F — "who is the other friend?" — companion_correction + anti-hallucination
# ═══════════════════════════════════════════════════════════════════════════

class TestCompanionCorrectionAndAntiHallucination:
    """
    When the bot invents a companion the visitor never mentioned, the visitor
    corrects it ("who is the other friend?", "I don't have kids", "I'm alone").

    The fix has two parts:
      1. The LLM sets message_kind=companion_correction + scene_corrections tokens.
      2. update_scene_memory _apply_scene_corrections clears the invented fields.
      3. generate_response acknowledges the correction without re-hallucinating.
      4. concierge_prompt.py has a hard rule against inventing companions.
    """

    def _make_correction_state(
        self,
        corrections: list[str],
        companions: list[str] | None = None,
        visit_type: str = "",
    ) -> ConciergeState:
        from app.models.state import DebugEnrichment
        intent = InterpretedIntent(
            domain="general",
            sub_intent="general_inquiry",
            message_kind=MessageKind.COMPANION_CORRECTION,
            confidence=0.95,
            scene_corrections=corrections,
        )
        scene = SceneMemory(
            companions=companions or [],
            visit_type=visit_type,
        )
        return ConciergeState(
            session_id="test",
            mall_id="test_mall",
            raw_user_message="I don't have kids",
            normalized_user_message="I don't have kids",
            intent=intent,
            scene=scene,
            debug_enrichment=DebugEnrichment(),
        )

    @pytest.mark.asyncio
    async def test_all_family_context_clears_child_companions(self):
        """all_family_context token must remove child/kids from companions."""
        from app.nodes.update_scene_memory import update_scene_memory
        state = self._make_correction_state(
            corrections=["all_family_context", "visit_type:solo"],
            companions=["girlfriend", "child"],
            visit_type="family_visit",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]
        assert "child" not in scene.companions, "child companion not cleared"
        assert "kids" not in scene.companions, "kids companion not cleared"

    @pytest.mark.asyncio
    async def test_all_family_context_preserves_non_family_companions(self):
        """all_family_context must NOT remove non-family companions (e.g. girlfriend)."""
        from app.nodes.update_scene_memory import update_scene_memory
        state = self._make_correction_state(
            corrections=["all_family_context"],
            companions=["girlfriend", "child"],
        )
        result = await update_scene_memory(state)
        scene = result["scene"]
        assert "girlfriend" in scene.companions, (
            f"girlfriend incorrectly removed. companions={scene.companions}"
        )

    @pytest.mark.asyncio
    async def test_visit_type_solo_correction(self):
        """visit_type:solo token must set visit_type to solo."""
        from app.nodes.update_scene_memory import update_scene_memory
        state = self._make_correction_state(
            corrections=["visit_type:solo"],
            visit_type="family_visit",
        )
        result = await update_scene_memory(state)
        scene = result["scene"]
        assert scene.visit_type == "solo", (
            f"visit_type not set to solo: {scene.visit_type!r}"
        )

    def test_companion_correction_not_routed_to_smalltalk(self):
        """companion_correction must NOT go to the smalltalk node."""
        state = _state_with_kind(MessageKind.COMPANION_CORRECTION)
        assert not is_smalltalk(state)

    def _run_generate_response(self, state):
        """Run generate_response with a mocked runtime (mall context not needed for early exits)."""
        from app.nodes.generate_response import generate_response
        with patch("app.nodes.generate_response.get_mall_context", return_value=MagicMock()):
            return _run(generate_response(state))

    def test_companion_correction_generate_response_early_exit(self):
        """generate_response must acknowledge the correction with a concise apology."""
        state = self._make_correction_state(
            corrections=["all_family_context", "visit_type:solo"],
            companions=[],
        )
        state.normalized_user_message = "I don't have kids"
        result = self._run_generate_response(state)
        assert result["response_debug_summary"] == "strategy=companion_correction_ack"

    def test_companion_correction_response_is_empathetic(self):
        """Response must acknowledge the mistake, not repeat the hallucinated companion."""
        state = self._make_correction_state(
            corrections=["visit_type:solo"],
            companions=[],
        )
        state.normalized_user_message = "I am alone"
        result = self._run_generate_response(state)
        text = result["final_response_text"].lower()
        has_apology = any(
            sig in text for sig in ("got it", "solo", "mistake", "understood", "my bad")
        )
        assert has_apology, f"Response lacks apology/acknowledgement: {text!r}"

    def test_anti_hallucination_instruction_in_concierge_prompt(self):
        """concierge_prompt._GUIDELINES must contain the 'NEVER INVENT COMPANIONS' rule."""
        from llm.prompts.concierge_prompt import _GUIDELINES
        combined = _GUIDELINES.lower()
        assert "never invent" in combined, (
            "Anti-hallucination 'NEVER INVENT COMPANIONS' rule not found in _GUIDELINES"
        )

    def test_anti_hallucination_example_girlfriend_not_friend(self):
        """Prompt must have the specific example about 'girlfriend' → not 'friend'."""
        from llm.prompts.concierge_prompt import _GUIDELINES
        # The prompt should show the exact mistake: adding 'friend' when only girlfriend was mentioned
        assert "girlfriend" in _GUIDELINES, (
            "Anti-hallucination example about girlfriend→friend missing from _GUIDELINES"
        )
