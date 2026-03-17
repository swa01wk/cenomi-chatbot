"""
Playbook engine — matches user context to scenario playbooks
and ranks entities according to playbook strategies.

The engine:
1. Selects the best-matching playbook based on detected intent/context
2. Filters entities using preferred semantic tags and entity types
3. Ranks the shortlist using priority scores and ranking biases
4. Applies exclusion rules and fallback logic
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.playbook import ScenarioPlaybook
from app.models.semantic import SemanticProfile

logger = logging.getLogger(__name__)


class PlaybookEngine:
    """Matches intents to playbooks and ranks entities accordingly."""

    def __init__(self, playbooks: list[ScenarioPlaybook] | None = None):
        self._playbooks = playbooks or []

    def load_playbooks(self, data: list[dict[str, Any]]) -> None:
        self._playbooks = [ScenarioPlaybook(**p) for p in data]
        logger.info("Loaded %d playbooks", len(self._playbooks))

    def match_playbook(
        self,
        intent: str,
        context_signals: list[str] | None = None,
    ) -> ScenarioPlaybook | None:
        """
        Find the best-matching playbook for a given intent.

        Scores each playbook by how many of its trigger conditions
        are present in the intent string and context signals.
        """
        signals = set((context_signals or []) + [intent.lower()])
        best: ScenarioPlaybook | None = None
        best_score = 0.0

        for pb in self._playbooks:
            score = 0.0
            for trigger in pb.trigger_conditions:
                trigger_lower = trigger.lower()
                for signal in signals:
                    if signal in trigger_lower or trigger_lower in signal:
                        score += 1.0
                        break
            if score > best_score:
                best_score = score
                best = pb

        if best:
            logger.info("Matched playbook '%s' (score %.1f)", best.playbook_id, best_score)
        return best

    def rank_entities(
        self,
        playbook: ScenarioPlaybook,
        profiles: list[SemanticProfile],
    ) -> list[dict[str, Any]]:
        """
        Rank semantic profiles according to playbook strategy.

        Returns a sorted shortlist of entity IDs with scores.
        """
        scored: list[dict[str, Any]] = []

        for profile in profiles:
            if profile.entity_type in playbook.do_not_include:
                continue

            if any(tag in profile.semantic_tags for tag in playbook.do_not_include):
                continue

            score = 0.0

            tag_overlap = set(profile.semantic_tags) & set(playbook.preferred_semantic_tags)
            score += len(tag_overlap) * 0.15

            for tag, bias in playbook.ranking_biases.items():
                if tag in profile.semantic_tags or tag in profile.audience_fit:
                    score += bias * 0.3

            playbook_score = profile.priority_scores.get(playbook.scenario, 0.0)
            score += playbook_score * 0.5

            if playbook.preferred_entity_types:
                if profile.entity_type in playbook.preferred_entity_types:
                    score += 0.1

            scored.append({
                "entity_id": profile.entity_id,
                "entity_type": profile.entity_type,
                "score": round(score, 3),
                "matched_tags": list(tag_overlap),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)

        limit = playbook.shortlist_size_hint or 10
        shortlist = scored[:limit]

        logger.info(
            "Ranked %d entities for playbook '%s', returning top %d",
            len(scored),
            playbook.playbook_id,
            len(shortlist),
        )
        return shortlist

    def get_response_guidance(self, playbook: ScenarioPlaybook) -> dict[str, Any]:
        """Extract response-shaping guidance from a playbook."""
        return {
            "scenario": playbook.scenario,
            "response_shape_hint": playbook.response_shape_hint,
            "shortlist_size_hint": playbook.shortlist_size_hint,
            "next_step_hint": playbook.next_step_hint,
            "concierge_reasoning_notes": playbook.concierge_reasoning_notes,
            "fallback_rules": playbook.fallback_rules,
        }
