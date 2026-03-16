"""
Shortlist Ranker — selects 3-5 best-fit options with one-line reasons.

Scoring dimensions:
  1. Playbook alignment     — entity matches the active playbook's tags/types
  2. Audience fit           — entity suits the detected audience (kids, couple, family)
  3. Budget fit             — entity's price band matches the visitor's budget
  4. Scenario fit           — entity fits the occasion/vibe
  5. Differentiation bonus  — diverse options score higher than duplicates
  6. Continuity bonus       — entities from the prior shortlist get a small carry-over boost
  7. Cross-domain penalty   — entities outside the active domain are heavily penalized

The ranker also generates:
  - one-line reason per selected entity
  - a narrowing follow-up opportunity if the shortlist can be usefully narrowed
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.state import (
    CandidateReasonEntry,
    ConciergeState,
    ContinuityAnchor,
    NarrowingFollowupOpportunity,
    ResponseContract,
    SceneMemory,
)

logger = logging.getLogger(__name__)

# ── Scoring weights ──────────────────────────────────────────────────────

_W_PLAYBOOK_TAG = 0.20
_W_AUDIENCE_FIT = 0.25
_W_BUDGET_FIT = 0.20
_W_SCENARIO_FIT = 0.15
_W_BASE_SCORE = 0.10
_W_CONTINUITY_CARRY = 0.08
_W_DIFFERENTIATION = 0.10
_CROSS_DOMAIN_PENALTY = 0.40


class ShortlistRanker:
    """
    Stateless ranker that scores and selects a curated shortlist of
    3-5 entities with per-item reasons and a narrowing follow-up.
    """

    def rank(
        self,
        entities: list[dict[str, Any]],
        state: ConciergeState,
    ) -> tuple[
        list[dict[str, Any]],
        list[CandidateReasonEntry],
        NarrowingFollowupOpportunity,
    ]:
        scene = state.scene
        anchor = state.continuity_anchor
        contract = state.response_contract

        scored = self._score_all(entities, scene, anchor)
        shortlist = self._select_shortlist(scored, contract.shortlist_size)
        reasons = self._build_reasons(shortlist, scene, anchor)
        narrowing = self._build_narrowing(shortlist, scene, anchor, contract)

        return shortlist, reasons, narrowing

    # ── Scoring ──────────────────────────────────────────────────────────

    def _score_all(
        self,
        entities: list[dict[str, Any]],
        scene: SceneMemory,
        anchor: ContinuityAnchor,
    ) -> list[dict[str, Any]]:
        for entity in entities:
            score = entity.get("score", 0.0) * _W_BASE_SCORE
            tags = set(entity.get("semantic_tags", []) + entity.get("matched_tags", []))
            audience_fit = set(entity.get("audience_fit", []))

            # Audience fit
            if scene.audience:
                overlap = audience_fit & set(scene.audience)
                if overlap:
                    score += _W_AUDIENCE_FIT * min(1.0, len(overlap) * 0.5)

            if anchor.audience:
                audience_tag = f"{anchor.audience}_friendly"
                if audience_tag in audience_fit or anchor.audience in audience_fit:
                    score += _W_AUDIENCE_FIT * 0.5

            # Budget fit
            price_band = entity.get("price_band", "") or ""
            if not price_band:
                price_exp = entity.get("price_expectation", {})
                if isinstance(price_exp, dict):
                    positioning = price_exp.get("rough_price_positioning", "")
                    if "budget" in positioning.lower():
                        price_band = "budget"
                    elif "premium" in positioning.lower() or "high" in positioning.lower():
                        price_band = "premium"

            if scene.budget and price_band:
                if scene.budget == price_band:
                    score += _W_BUDGET_FIT
                elif scene.budget == "budget" and price_band in ("budget", "mid_range"):
                    score += _W_BUDGET_FIT * 0.6
                elif scene.budget == "premium" and price_band in ("premium", "luxury"):
                    score += _W_BUDGET_FIT * 0.6

            # Scenario / occasion fit
            outing_fit = entity.get("outing_fit", [])
            if scene.occasion and scene.occasion in (outing_fit or []):
                score += _W_SCENARIO_FIT
            if scene.occasion and scene.occasion in tags:
                score += _W_SCENARIO_FIT * 0.5

            # Playbook tag alignment
            matched_tags = entity.get("matched_tags", [])
            if matched_tags:
                score += _W_PLAYBOOK_TAG * min(1.0, len(matched_tags) * 0.25)

            # Continuity carry-over
            name = entity.get("name", "")
            if (
                name and anchor.last_successful_shortlist
                and name in anchor.last_successful_shortlist
            ):
                score += _W_CONTINUITY_CARRY

            # Cross-domain penalty
            entity_domain = self._infer_entity_domain(entity)
            if anchor.is_strong and entity_domain and entity_domain != anchor.domain:
                score -= _CROSS_DOMAIN_PENALTY

            entity["_ranker_score"] = round(score, 4)

        entities.sort(key=lambda e: e.get("_ranker_score", 0.0), reverse=True)
        return entities

    def _select_shortlist(
        self,
        scored: list[dict[str, Any]],
        target_size: int,
    ) -> list[dict[str, Any]]:
        """Select target_size entities, injecting diversity."""
        if len(scored) <= target_size:
            return scored

        selected: list[dict[str, Any]] = []
        seen_types: set[str] = set()

        # First pass: top entities
        for entity in scored:
            if len(selected) >= target_size:
                break
            selected.append(entity)
            etype = entity.get("entity_type", "")
            seen_types.add(etype)

        # Diversity check: if all same type and we have alternatives, swap one in
        if len(set(e.get("entity_type", "") for e in selected)) == 1 and len(scored) > target_size:
            dominant_type = selected[0].get("entity_type", "")
            for candidate in scored[target_size:]:
                if candidate.get("entity_type", "") != dominant_type:
                    selected[-1] = candidate
                    break

        return selected

    # ── Reason generation ────────────────────────────────────────────────

    def _build_reasons(
        self,
        shortlist: list[dict[str, Any]],
        scene: SceneMemory,
        anchor: ContinuityAnchor,
    ) -> list[CandidateReasonEntry]:
        entries: list[CandidateReasonEntry] = []
        for entity in shortlist:
            name = entity.get("name", "")
            reasons_data = entity.get("concierge_reasons", {})
            audience_fit = entity.get("audience_fit", [])
            price_exp = entity.get("price_expectation", {})

            reason_type, reason_text, followup = self._pick_best_reason(
                reasons_data, audience_fit, scene, anchor, price_exp,
            )

            # Fallback: generate a reason from semantic tags
            if not reason_text:
                reason_text = self._generate_fallback_reason(entity, scene)

            entries.append(CandidateReasonEntry(
                entity_name=name,
                chosen_reason_type=reason_type,
                one_line_reason=reason_text,
                followup_hint=followup,
            ))
        return entries

    def _pick_best_reason(
        self,
        reasons: dict,
        audience_fit: list[str],
        scene: SceneMemory,
        anchor: ContinuityAnchor,
        price_exp: dict,
    ) -> tuple[str, str, str]:
        if anchor.audience == "kids" and reasons.get("kid_reason"):
            return "audience_fit", reasons["kid_reason"], reasons.get("followup_hint", "")
        if "couple_friendly" in scene.audience and reasons.get("couple_reason"):
            return "audience_fit", reasons["couple_reason"], reasons.get("followup_hint", "")
        if "family_friendly" in scene.audience and reasons.get("family_reason"):
            return "audience_fit", reasons["family_reason"], reasons.get("followup_hint", "")
        if scene.budget == "budget" and reasons.get("budget_reason"):
            return "budget_fit", reasons["budget_reason"], reasons.get("followup_hint", "")
        if scene.occasion == "before_movie" and reasons.get("movie_reason"):
            return "occasion_fit", reasons["movie_reason"], reasons.get("followup_hint", "")
        if reasons.get("gift_reason") and anchor.topic and "gift" in anchor.topic:
            return "occasion_fit", reasons["gift_reason"], reasons.get("followup_hint", "")
        if reasons.get("quick_reason") and scene.occasion in ("quick_visit", "before_movie"):
            return "occasion_fit", reasons["quick_reason"], reasons.get("followup_hint", "")
        if reasons.get("short_reason"):
            return "general", reasons["short_reason"], reasons.get("followup_hint", "")
        if price_exp.get("rough_price_positioning"):
            return "budget_fit", price_exp["rough_price_positioning"], ""
        return "general", "", ""

    def _generate_fallback_reason(
        self,
        entity: dict[str, Any],
        scene: SceneMemory,
    ) -> str:
        tags = entity.get("semantic_tags", [])
        name = entity.get("name", "this option")
        notes = entity.get("concierge_notes", "")
        if notes:
            return notes[:120]

        if "budget" in tags:
            return f"{name} is a budget-friendly option."
        if "kid_friendly" in tags:
            return f"{name} is great for kids."
        if "family_friendly" in tags:
            return f"{name} works well for families."
        if "couple_friendly" in tags:
            return f"{name} is a good pick for couples."
        return ""

    # ── Narrowing follow-up ──────────────────────────────────────────────

    def _build_narrowing(
        self,
        shortlist: list[dict[str, Any]],
        scene: SceneMemory,
        anchor: ContinuityAnchor,
        contract: ResponseContract,
    ) -> NarrowingFollowupOpportunity:
        if len(shortlist) <= 1:
            return NarrowingFollowupOpportunity(is_useful=False)

        candidates: list[tuple[str, str]] = []

        if not scene.budget:
            if anchor.domain == "shopping":
                candidates.append((
                    "budget",
                    "Do you have a price range in mind — say, under 100 SAR?",
                ))
            else:
                candidates.append(("budget", "Do you have a budget range in mind?"))

        if not scene.audience and not anchor.audience:
            candidates.append((
                "audience",
                "Who are you shopping for — yourself, family, or someone special?",
            ))

        if anchor.domain == "dining" and not scene.occasion:
            candidates.append(("occasion", "Is this a quick bite or more of a sit-down meal?"))

        if anchor.domain == "entertainment":
            candidates.append(("preference", "Any genre or type of activity you prefer?"))

        if not candidates:
            return NarrowingFollowupOpportunity(is_useful=False)

        dim, question = candidates[0]
        return NarrowingFollowupOpportunity(
            is_useful=True,
            suggested_question=question,
            narrowing_dimension=dim,
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _infer_entity_domain(entity: dict[str, Any]) -> str:
        etype = entity.get("entity_type", "")
        domain_map = {
            "store": "shopping",
            "dining": "dining",
            "restaurant": "dining",
            "cafe": "dining",
            "cinema": "entertainment",
            "movie": "entertainment",
            "service": "services",
            "event": "entertainment",
        }
        return domain_map.get(etype, "")
