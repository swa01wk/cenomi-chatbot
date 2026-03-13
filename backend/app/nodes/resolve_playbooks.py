"""
Resolve Playbooks node — matches current scene against scenario playbooks.

CONTRACT
────────
  Purpose:  Score available playbooks against intent + scene using the
            PlaybookEngine loaded from real mall data.
            Select the best-matching playbook if one clears the threshold.
  Reads:    intent, scene, active_tenant_parameters
  Writes:   playbook (PlaybookResolution)
  Failure:  No match → empty resolution; downstream uses strategy defaults
  Routing:  Always → choose_strategy
"""

from __future__ import annotations

from app.models.state import ConciergeState, PlaybookResolution
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context

_CONFIDENCE_THRESHOLD = 0.25


@traced_node("resolve_playbooks")
async def resolve_playbooks(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene

    scene_signals = _collect_scene_signals(scene, intent)

    mall_ctx = get_mall_context()

    matched_pb = mall_ctx.match_playbook(
        intent=f"{intent.domain}/{intent.sub_intent}",
        context_signals=scene_signals,
    )

    if matched_pb:
        ranked_entities = mall_ctx.rank_for_playbook(matched_pb)
        entity_count = len(ranked_entities)
        resolution = PlaybookResolution(
            matched_playbooks=[matched_pb.playbook_id],
            selected_playbook=matched_pb.playbook_id,
            playbook_confidence=min(1.0, 0.5 + entity_count * 0.05),
        )
        return {
            "playbook": resolution,
            "_trace_summary": (
                f"Playbook: {matched_pb.playbook_id} "
                f"({entity_count} ranked entities)"
            ),
        }

    scored = _score_fallback(intent, scene)
    matched = [pb_id for pb_id, _ in scored]
    selected = scored[0][0] if scored else ""
    confidence = scored[0][1] if scored else 0.0

    resolution = PlaybookResolution(
        matched_playbooks=matched,
        selected_playbook=selected,
        playbook_confidence=confidence,
    )

    return {
        "playbook": resolution,
        "_trace_summary": f"Playbook: {selected or 'none'} (conf={confidence})",
    }


def _collect_scene_signals(scene, intent) -> list[str]:
    signals: list[str] = []
    signals.extend(scene.companions)
    if scene.occasion:
        signals.append(scene.occasion)
    if scene.budget:
        signals.append(scene.budget)
    signals.extend(scene.audience)
    if intent.domain:
        signals.append(intent.domain)
    if intent.sub_intent:
        signals.append(intent.sub_intent)
    if scene.active_topic:
        signals.append(scene.active_topic)
    return signals


# Lightweight fallback for when the PlaybookEngine doesn't match
_FALLBACK_TRIGGERS: dict[str, dict] = {
    "pb-romantic-dinner": {
        "domains": {"dining"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "date", "romantic", "anniversary",
        },
    },
    "pb-family-visit": {
        "domains": {"dining", "entertainment", "shopping"},
        "scene_signals": {"family", "kids", "children", "kid_friendly", "family_friendly"},
    },
    "pb-gift-recommendation": {
        "domains": {"shopping"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "birthday", "anniversary",
        },
    },
    "pb-quick-bite": {
        "domains": {"dining"},
        "scene_signals": {"quick_visit", "before_movie"},
    },
    "pb-movie-night": {
        "domains": {"entertainment", "dining"},
        "scene_signals": {"before_movie", "after_movie"},
    },
    "pb-solo-visit": {
        "domains": {"dining", "shopping", "entertainment"},
        "scene_signals": {"solo", "solo_friendly"},
    },
    "pb-budget-plan": {
        "domains": {"dining", "shopping", "entertainment"},
        "scene_signals": {"budget"},
    },
}


def _score_fallback(intent, scene) -> list[tuple[str, float]]:
    scene_tokens: set[str] = set()
    scene_tokens.update(scene.companions)
    if scene.occasion:
        scene_tokens.add(scene.occasion)
    if scene.budget:
        scene_tokens.add(scene.budget)
    scene_tokens.update(scene.audience)

    scored: list[tuple[str, float]] = []
    for pb_id, triggers in _FALLBACK_TRIGGERS.items():
        score = 0.0
        if intent.domain in triggers["domains"]:
            score += 0.4
        overlap = scene_tokens & triggers["scene_signals"]
        if overlap:
            score += 0.3 * (len(overlap) / max(len(triggers["scene_signals"]), 1))
        if score >= _CONFIDENCE_THRESHOLD:
            scored.append((pb_id, round(score, 3)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
