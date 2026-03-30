"""
Multi-mall v1 — cross-mall brand resolution, fact context ordering, flow hints.

Gold routing expectations from the product plan (no LLM calls).
"""

from __future__ import annotations

from app.models.state import ConciergeState, InterpretedIntent, SceneMemory
from app.nodes.generate_response import _format_fact_context
from app.nodes.interpret_turn import _detect_flow_type_candidate
from app.services.cross_mall_brand import resolve_cross_mall_brand_query


def _state(
    msg: str,
    *,
    domain: str = "cross_mall",
    sub_intent: str = "cross_mall_search",
    scene: SceneMemory | None = None,
    fact_query_entity: str = "",
    mall_id: str = "al_nakheel_plaza_28",
) -> ConciergeState:
    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind="followup",
        confidence=0.9,
    )
    return ConciergeState(
        session_id="s1",
        mall_id=mall_id,
        raw_user_message=msg,
        normalized_user_message=msg,
        intent=intent,
        scene=scene or SceneMemory(),
        fact_query_entity=fact_query_entity,
        flow_type="factual",
    )


class TestResolveCrossMallBrandQuery:
    def test_where_else_uses_last_resolved_entity(self):
        scene = SceneMemory(last_resolved_entity="Zara")
        st = _state("where else can I find it?", scene=scene)
        assert resolve_cross_mall_brand_query(st) == "Zara"

    def test_empty_when_only_pronouns(self):
        st = _state("where else?", scene=SceneMemory())
        assert resolve_cross_mall_brand_query(st) == ""

    def test_explicit_brand_in_message(self):
        st = _state("which of your malls have Starbucks?", scene=SceneMemory())
        assert "starbucks" in resolve_cross_mall_brand_query(st).lower()

    def test_fact_query_entity_fallback(self):
        # Phrase strips to empty → use pipeline entity from availability / retrieval.
        st = _state(
            "at other malls too?",
            scene=SceneMemory(),
            fact_query_entity="H&M",
        )
        assert resolve_cross_mall_brand_query(st) == "H&M"

    def test_active_shortlist_fallback(self):
        st = _state(
            "any other mall?",
            scene=SceneMemory(active_shortlist=["Pull & Bear"]),
        )
        assert resolve_cross_mall_brand_query(st) == "Pull & Bear"


class TestFormatFactContextCrossMall:
    def test_current_mall_section_before_other_malls(self):
        fact_ctx = {
            "scope": "cross_mall_availability",
            "cross_mall_brand_query": "Zara",
            "cross_mall_at_home": [
                {
                    "name": "Zara",
                    "entity_type": "stores",
                    "floor": "Ground",
                    "is_home_mall": True,
                },
            ],
            "cross_mall_other": [
                {
                    "name": "Zara",
                    "mall_name": "Mall of Arabia",
                    "entity_type": "stores",
                    "floor": "L1",
                },
            ],
        }
        text = _format_fact_context(fact_ctx, None)
        assert text.index("AT YOUR CURRENT MALL") < text.index("AT OTHER CENOMI MALLS")


class TestCrossMallFlowHints:
    """Classifier output is simulated; flow hints must stay separated."""

    def test_single_mall_brand_is_not_cross_mall_scope(self):
        ft, scope, et = _detect_flow_type_candidate(
            "Do you have Nike?",
            "shopping",
            "brand_availability",
            {},
        )
        assert ft == "factual"
        assert scope == "brand_availability"
        assert et == "store"

    def test_cross_mall_search_hint(self):
        ft, scope, et = _detect_flow_type_candidate(
            "Which of your malls have Nike?",
            "cross_mall",
            "cross_mall_search",
            {},
        )
        assert ft == "factual"
        assert scope == "cross_mall_availability"
        assert et == "brand"
