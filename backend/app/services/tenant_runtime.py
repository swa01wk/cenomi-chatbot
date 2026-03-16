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
    # Response policy decisions
    # ═══════════════════════════════════════════════════════════════════

    def effective_shortlist_size(self, context_suggested: int | None = None) -> int:
        """
        Return the shortlist size to use, respecting policy caps.

        Priority: policy max_shortlist_size > policy default > response_shape default.
        If *context_suggested* is provided (e.g. from a playbook), it is clamped
        to policy bounds.
        """
        rp = self.c.response_policy
        base = context_suggested or rp.default_shortlist_size
        return min(base, rp.max_shortlist_size)

    def should_acknowledge_scene(self, scene_has_signals: bool) -> bool:
        """Whether the first response should mirror the visitor's scene."""
        return self.c.response_policy.always_acknowledge_scene and scene_has_signals

    def should_show_location(
        self,
        visitor_asked_directions: bool,
        is_proximity_relevant: bool,
    ) -> bool:
        """
        Decide whether to include floor/zone on a shortlist item.

        When ``show_location_only_when_useful`` is True, location appears only
        when the visitor explicitly asked for it or when proximity matters
        for the decision (e.g. comparing nearby options).
        """
        rp = self.c.response_policy
        if not rp.show_location_only_when_useful:
            return True
        return visitor_asked_directions or is_proximity_relevant

    def should_preserve_topic(self, message_kind: str, message_length: int) -> bool:
        """
        Decide whether a short follow-up should refine the active thread.

        When ``preserve_topic_on_short_followups`` is True and the message is
        short (< 40 chars) and is classified as followup/refinement, the
        runtime tells the interpreter to stay on the active topic.
        """
        rp = self.c.response_policy
        if not rp.preserve_topic_on_short_followups:
            return False
        return message_kind in ("followup", "refinement") and message_length < 40

    def should_provide_price_range(self, has_exact_price: bool) -> bool:
        """Provide a rough price range when exact pricing is unavailable."""
        return (
            self.c.response_policy.prefer_price_range_when_exact_price_missing
            and not has_exact_price
        )

    def response_policy_instructions(self) -> str:
        """
        Generate natural-language instructions that encode the active
        response_policy flags into the LLM system prompt.
        """
        rp = self.c.response_policy
        rules: list[str] = []

        if rp.prefer_curated_shortlist:
            sz = self.effective_shortlist_size()
            rules.append(
                f"Present a curated shortlist of up to {sz} best-fit options "
                f"(never more than {rp.max_shortlist_size})."
            )
        if rp.prefer_short_contextual_intro:
            rules.append(
                "Start with one short contextual sentence, not a generic greeting."
            )
        if rp.always_acknowledge_scene:
            rules.append(
                "Briefly acknowledge the visitor's situation (who they're with, "
                "occasion, budget) before recommendations."
            )
        if rp.prefer_one_line_reasons:
            rules.append(
                "Each recommendation gets at most one line of 'why it fits'."
            )
        if rp.show_location_only_when_useful:
            rules.append(
                "Include floor/zone only when it helps the visitor decide or navigate."
            )
        if rp.deprioritize_floor_zone_details:
            rules.append(
                "Put location details at the end of the line; omit if the visitor "
                "didn't ask for directions."
            )
        if rp.prefer_followup_narrowing_question:
            rules.append(
                "End with a narrowing follow-up question that helps the visitor "
                "decide (e.g. 'Are you looking for something under 300 SAR?'), "
                "not an open-ended 'anything else?'."
            )
        if rp.avoid_brochure_tone:
            rules.append(
                "Never use brochure language ('wide array of', 'boasts', "
                "'plethora'). Sound like a knowledgeable friend."
            )
        if rp.avoid_repeating_full_mall_name:
            rules.append(
                "After the first mention, say 'here' or 'the mall' instead of "
                "the full branded name."
            )
        if rp.prefer_decision_help_over_description:
            rules.append(
                "When describing a place, emphasize what helps the visitor choose "
                "(price positioning, vibe, audience fit), not marketing copy."
            )
        if rp.preserve_thread_continuity:
            rules.append(
                "Reference earlier parts of this conversation when natural."
            )
        if rp.preserve_topic_on_short_followups:
            rules.append(
                "If the visitor sends a short follow-up (e.g. 'affordable?'), "
                "refine the active thread — do NOT switch to a new topic."
            )
        if rp.prefer_contextual_grouping:
            rules.append(
                "Group options by visitor-relevant dimension (vibe, budget tier) "
                "rather than alphabetically."
            )
        if rp.prefer_price_range_when_exact_price_missing:
            rules.append(
                "When exact pricing is unavailable, provide a rough price range "
                "or tier ('mid-range', '~200 SAR') instead of asking or staying silent."
            )

        if not rules:
            return ""
        return "RESPONSE POLICY:\n" + "\n".join(f"- {r}" for r in rules)

    # ═══════════════════════════════════════════════════════════════════
    # Strategy behavior scoring
    # ═══════════════════════════════════════════════════════════════════

    def score_strategy_behavior(
        self,
        *,
        maintains_continuity: bool = False,
        produces_shortlist: bool = False,
        acknowledges_scene: bool = False,
        useful_followup: bool = False,
        over_describes: bool = False,
        requires_clarification: bool = False,
        wrong_thread: bool = False,
        brochure_tone: bool = False,
    ) -> float:
        """
        Score a candidate strategy or response against the behavior weights.

        Returns a net quality score.  Positive signals add their weight;
        negative signals subtract their penalty.  The caller uses this
        to rank strategies or to gate response quality.

        Example:
            score = rt.score_strategy_behavior(
                maintains_continuity=True,
                produces_shortlist=True,
                wrong_thread=False,
            )
            # → 0.85 + 0.80 = 1.65  (high — good fit)
        """
        bw = self.c.strategy_behavior_weights
        score = 0.0

        if maintains_continuity:
            score += bw.continuity_preservation_weight
        if produces_shortlist:
            score += bw.shortlist_quality_weight
        if acknowledges_scene:
            score += bw.scene_acknowledgment_weight
        if useful_followup:
            score += bw.followup_usefulness_weight

        if over_describes:
            score -= bw.overdescription_penalty
        if requires_clarification:
            score -= bw.clarification_penalty
        if wrong_thread:
            score -= bw.wrong_thread_penalty
        if brochure_tone:
            score -= bw.brochure_tone_penalty

        return round(score, 4)

    def apply_behavior_penalty_to_candidates(
        self,
        candidates: list[dict[str, Any]],
        active_topic: str,
    ) -> list[dict[str, Any]]:
        """
        Post-score candidate entities with behavior-weight penalties.

        Penalizes candidates whose topic domain doesn't match the active
        thread (wrong_thread_penalty) and rewards those that support
        shortlist curation (shortlist_quality_weight).
        """
        bw = self.c.strategy_behavior_weights
        for item in candidates:
            penalty = 0.0
            bonus = 0.0

            item_topic = item.get("topic_domain", "") or item.get("category", "")
            if active_topic and item_topic and item_topic.lower() != active_topic.lower():
                penalty += bw.wrong_thread_penalty * 0.15

            if item.get("curated") or item.get("semantic_match_score", 0) > 0.7:
                bonus += bw.shortlist_quality_weight * 0.10

            item["score"] = round(item.get("score", 0.0) + bonus - penalty, 4)
            item["_behavior_adjustment"] = round(bonus - penalty, 4)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

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
            "shortlist_size": self.effective_shortlist_size(rs.default_shortlist_size),
            "itinerary_steps": rs.default_itinerary_steps,
            "location_detail": rs.location_detail_level,
            "suggest_next_step": rs.suggest_next_step,
            "format": rs.paragraph_vs_bullets,
            "recommendation_density": rs.recommendation_density,
            "exact_name_usage_bias": rs.exact_name_usage_bias,
        }
