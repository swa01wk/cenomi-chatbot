"""
Tenant runtime — translates TenantConfig into concrete pipeline decisions.

This module is consumed by the router, context builder, generator, and
retriever nodes.  It never mutates the config — it reads parameters and
returns decisions, scores, or prompt fragments.

Usage pattern in a pipeline node:

    from app.services.tenant_runtime import TenantRuntime
    rt = TenantRuntime(state.active_tenant_parameters)
    if rt.should_skip_retrieval(intent):
        ...
    ranked = rt.apply_ranking_biases(candidates, session_signals)
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.tenant import TenantConfig

logger = logging.getLogger(__name__)


class TenantRuntime:
    """Stateless helper that reads a TenantConfig and produces runtime decisions."""

    def __init__(self, config: TenantConfig):
        self.c = config

    # ═══════════════════════════════════════════════════════════════════
    # Strategy selection
    # ═══════════════════════════════════════════════════════════════════

    def select_strategy(
        self,
        candidate_strategies: list[str],
        intent: str,
        context_signals: list[str] | None = None,
    ) -> str:
        """
        Pick the best response strategy given tenant weights and context.

        Each candidate strategy is scored by its base weight from
        strategy_weights, then boosted by signal overlap.
        """
        weights = self.c.strategy_weights.model_dump()
        signals = set(s.lower() for s in (context_signals or []))
        signals.add(intent.lower())

        signal_strategy_map = {
            "family": ["family_plan"],
            "kid": ["family_plan"],
            "children": ["family_plan"],
            "gift": ["gift_formula"],
            "present": ["gift_formula"],
            "movie": ["movie_plus_food"],
            "cinema": ["movie_plus_food"],
            "budget": ["budget_plan"],
            "cheap": ["budget_plan"],
            "itinerary": ["mini_itinerary"],
            "plan": ["mini_itinerary"],
            "solo": ["solo_plan"],
            "alone": ["solo_plan"],
        }

        scores: dict[str, float] = {}
        for strategy in candidate_strategies:
            base = weights.get(strategy, 0.5)
            boost = 0.0
            for signal, boosted_strategies in signal_strategy_map.items():
                if signal in " ".join(signals) and strategy in boosted_strategies:
                    boost += 0.3
            scores[strategy] = base + boost

        best = max(scores, key=lambda s: scores[s])
        logger.debug("Strategy selection: %s (scores: %s)", best, scores)
        return best

    def normalize_strategy_weights(self) -> dict[str, float]:
        """Return strategy weights normalized to sum to 1.0."""
        raw = self.c.strategy_weights.model_dump()
        total = sum(raw.values()) or 1.0
        return {k: round(v / total, 4) for k, v in raw.items()}

    # ═══════════════════════════════════════════════════════════════════
    # Clarification / answer-first decisions
    # ═══════════════════════════════════════════════════════════════════

    def should_clarify(
        self,
        ambiguity_score: float,
        turn_count: int,
        has_prior_answer: bool,
    ) -> bool:
        """
        Decide whether to ask a clarifying question.

        Returns False (answer first) when:
        - ambiguity is below the tolerance threshold
        - answer_first_bias exceeds 1 - ambiguity_score
        - we've already asked max_followups_before_answer clarifications
        """
        cp = self.c.clarification

        if turn_count >= cp.max_followups_before_answer and not has_prior_answer:
            return False

        if ambiguity_score < (1.0 - cp.ambiguity_tolerance):
            return False

        if cp.answer_first_bias > (1.0 - ambiguity_score):
            return False

        return True

    def assumption_strength(self) -> float:
        """How confident the bot should be when filling in gaps."""
        return self.c.clarification.assumption_aggressiveness

    # ═══════════════════════════════════════════════════════════════════
    # Context composition
    # ═══════════════════════════════════════════════════════════════════

    def score_context_block(
        self,
        block_type: str,
        session_signals: dict[str, Any] | None = None,
    ) -> float:
        """
        Score a context block for inclusion priority.

        Higher score = inject earlier / allocate more token budget.
        """
        cw = self.c.context_weights
        signals = session_signals or {}

        type_weight_map: dict[str, str] = {
            "playbook": "prioritize_playbooks",
            "semantic_tags": "prioritize_semantic_tags",
            "current_topic": "prioritize_current_topic",
            "current_area": "prioritize_current_area",
            "current_mall": "prioritize_current_mall",
        }

        signal_weight_map: dict[str, str] = {
            "family": "prioritize_family_signals",
            "kids": "prioritize_kid_signals",
            "budget": "prioritize_budget_signals",
            "romantic": "prioritize_romantic_signals",
            "exact_entity": "prioritize_exact_entity_examples",
        }

        score = getattr(cw, type_weight_map.get(block_type, ""), 0.5)

        for signal_key, weight_attr in signal_weight_map.items():
            if signals.get(signal_key):
                signal_weight = getattr(cw, weight_attr, 0.5)
                score = max(score, signal_weight)

        return round(score, 3)

    def rank_context_blocks(
        self,
        blocks: list[dict[str, Any]],
        session_signals: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Sort context blocks by tenant-weighted priority, highest first."""
        for block in blocks:
            block["_priority"] = self.score_context_block(
                block.get("type", ""), session_signals
            )
        blocks.sort(key=lambda b: b["_priority"], reverse=True)
        return blocks

    # ═══════════════════════════════════════════════════════════════════
    # Retrieval decisions
    # ═══════════════════════════════════════════════════════════════════

    def should_skip_retrieval(self, intent: str, is_vague: bool = False) -> bool:
        """
        Decide whether to skip vector retrieval and answer from context alone.

        Returns True when the retrieval policy says to avoid retrieval
        for this kind of query.
        """
        rp = self.c.retrieval

        if is_vague and rp.avoid_retrieval_for_vague_queries:
            return True

        recommendation_intents = {
            "recommend", "suggest", "what_should", "where_can",
            "help_me_find", "ideas", "options",
        }
        if any(kw in intent.lower() for kw in recommendation_intents):
            return rp.avoid_retrieval_for_general_recommendations

        return False

    def requires_exact_lookup(self, query_type: str) -> bool:
        """Check if a query type requires exact retrieval (no LLM guessing)."""
        rp = self.c.retrieval
        lookup_map: dict[str, bool] = {
            "store_timing": rp.require_exact_lookup_for_store_timing,
            "store_hours": rp.require_exact_lookup_for_store_timing,
            "offers": rp.require_exact_lookup_for_live_offers,
            "promotions": rp.require_exact_lookup_for_live_offers,
            "events": rp.require_exact_lookup_for_events,
            "loyalty": rp.require_exact_lookup_for_loyalty,
            "rewards": rp.require_exact_lookup_for_loyalty,
        }
        return lookup_map.get(query_type, False)

    # ═══════════════════════════════════════════════════════════════════
    # Ranking
    # ═══════════════════════════════════════════════════════════════════

    def apply_ranking_biases(
        self,
        candidates: list[dict[str, Any]],
        session_signals: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Re-score candidate entities using tenant ranking biases
        and session-detected signals.

        Each candidate dict must have a 'score' float and optional
        'tags', 'price_range', 'audience_fit' fields.
        """
        rb = self.c.ranking
        signals = session_signals or {}
        entity_params = self.c.entity_params

        for item in candidates:
            bonus = 0.0
            tags = set(item.get("tags", []) + item.get("audience_fit", []))
            price = item.get("price_range", "mid_range")
            entity_id = item.get("entity_id", "")

            if price in ("budget",) and signals.get("budget"):
                bonus += rb.favor_budget * 0.2
            if price in ("premium", "luxury") and signals.get("premium"):
                bonus += rb.favor_premium * 0.2
            if "family_friendly" in tags and signals.get("family"):
                bonus += rb.favor_family_friendly * 0.2
            if "kid_friendly" in tags and signals.get("kids"):
                bonus += rb.favor_kid_friendly * 0.2
            if "couple_friendly" in tags and signals.get("romantic"):
                bonus += rb.favor_couple_friendly * 0.2
            if item.get("is_anchor"):
                bonus += rb.favor_anchor_entities * 0.1

            ep = entity_params.get(entity_id)
            if ep and not ep.suppressed:
                bonus += ep.recommendation_boost

            item["score"] = round(item.get("score", 0.0) + bonus, 4)
            item["_ranking_bonus"] = round(bonus, 4)

        candidates.sort(key=lambda x: x["score"], reverse=True)

        if rb.favor_diversity_vs_confidence > 0.5 and len(candidates) > 3:
            candidates = self._inject_diversity(candidates)

        return candidates

    @staticmethod
    def _inject_diversity(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Post-ranking diversity pass: if the top 3 share a category,
        swap in the highest-scoring entity from a different category.
        """
        if len(candidates) < 4:
            return candidates

        top_types = [c.get("entity_type") or c.get("category", "") for c in candidates[:3]]
        if len(set(top_types)) > 1:
            return candidates

        dominant = top_types[0]
        for i, c in enumerate(candidates[3:], start=3):
            alt_type = c.get("entity_type") or c.get("category", "")
            if alt_type != dominant:
                candidates[2], candidates[i] = candidates[i], candidates[2]
                break

        return candidates

    # ═══════════════════════════════════════════════════════════════════
    # Session adaptation
    # ═══════════════════════════════════════════════════════════════════

    def should_preserve_signal(self, signal_name: str) -> bool:
        """Check if a session signal should be carried forward to the next turn."""
        sa = self.c.session_adaptation
        preserve_map: dict[str, float] = {
            "companions": sa.preserve_companions_strongly,
            "budget": sa.preserve_budget_strongly,
            "current_area": sa.preserve_current_area_strongly,
            "active_topic": sa.preserve_active_topic_strongly,
        }
        threshold = 0.5
        return preserve_map.get(signal_name, 0.5) > threshold

    def is_short_followup_aggressive(self) -> bool:
        """Whether to aggressively interpret short messages as continuations."""
        return self.c.session_adaptation.reinterpret_short_followups_aggressively > 0.6

    def correction_overrides_scope(self) -> float:
        """How strongly a correction should override prior context scope."""
        return self.c.session_adaptation.correction_overrides_previous_scope

    # ═══════════════════════════════════════════════════════════════════
    # Response generation hints
    # ═══════════════════════════════════════════════════════════════════

    def tone_instructions(self) -> str:
        """Generate a natural-language tone directive for the LLM system prompt."""
        t = self.c.tone
        parts: list[str] = []

        if t.warmth > 0.7:
            parts.append("Be warm and welcoming")
        elif t.warmth < 0.3:
            parts.append("Be matter-of-fact")

        if t.directness > 0.7:
            parts.append("get straight to the point")
        if t.formality < 0.3:
            parts.append("use a casual conversational tone")
        elif t.formality > 0.7:
            parts.append("maintain a polished professional tone")

        if t.concierge_confidence > 0.8:
            parts.append("speak with confident authority about the mall")

        if t.emoji_level > 0.5:
            parts.append("use emojis where they feel natural")
        elif t.emoji_level < 0.2:
            parts.append("avoid emojis")

        if t.promotional_intensity > 0.6:
            parts.append("actively highlight deals and promotions")
        elif t.promotional_intensity < 0.2:
            parts.append("mention promotions only when directly relevant")

        if t.practicalness_vs_exploratory > 0.6:
            parts.append("prioritize practical actionable advice")
        elif t.practicalness_vs_exploratory < 0.4:
            parts.append("feel free to suggest exploratory options")

        return ". ".join(parts) + "." if parts else "Be helpful and friendly."

    def response_shape_instructions(self) -> dict[str, Any]:
        """Return structured response shape directives for the generator."""
        rs = self.c.response_shape
        return {
            "length": rs.default_response_length,
            "shortlist_size": rs.default_shortlist_size,
            "itinerary_steps": rs.default_itinerary_steps,
            "location_detail": rs.location_detail_level,
            "suggest_next_step": rs.suggest_next_step,
            "format": rs.paragraph_vs_bullets,
            "recommendation_density": rs.recommendation_density,
            "exact_name_usage_bias": rs.exact_name_usage_bias,
        }
