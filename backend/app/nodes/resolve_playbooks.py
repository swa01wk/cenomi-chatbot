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


# Playbooks that only make sense when the user explicitly asks for a gift/present.
# They must NOT be selected for general activity, date-idea, or exploration queries.
_GIFT_ONLY_PLAYBOOKS: frozenset[str] = frozenset({
    "pb-gift-girlfriend",
    "pb-gift-family",
    "pb-last-minute-gift",
    "pb-gift-recommendation",
})

# Domains / sub-intents where gift playbooks should NOT be selected
_ACTIVITY_DOMAINS: frozenset[str] = frozenset({
    "exploration", "entertainment", "dining",
})
_ACTIVITY_SUB_INTENTS: frozenset[str] = frozenset({
    "activity_suggestion", "open_exploration", "first_visit_guide",
    "general_entertainment", "general_dining", "romantic_dining",
})

# Explicit gift signals in the user message — only if these are present should
# gift playbooks be allowed to win on non-shopping queries.
_EXPLICIT_GIFT_SIGNALS: frozenset[str] = frozenset({
    "gift", "present", "buy", "purchase", "shop for",
})


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

    # ── Guard: prevent gift playbooks from hijacking activity/date queries ──
    if matched_pb and matched_pb.playbook_id in _GIFT_ONLY_PLAYBOOKS:
        msg_lower = state.normalized_user_message.lower()
        has_explicit_gift = any(sig in msg_lower for sig in _EXPLICIT_GIFT_SIGNALS)
        is_activity_query = (
            intent.domain in _ACTIVITY_DOMAINS
            or intent.sub_intent in _ACTIVITY_SUB_INTENTS
        )
        if is_activity_query and not has_explicit_gift:
            # Swap to the date-plan or exploration playbook instead
            override = _find_activity_playbook(intent, scene, mall_ctx, scene_signals)
            if override:
                matched_pb = override

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


def _find_activity_playbook(intent, scene, mall_ctx, scene_signals: list[str]):
    """
    Find a better playbook for activity/date-idea queries when a gift
    playbook incorrectly fired.
    """
    # Couple context → date plan
    couple_companions = {"girlfriend", "boyfriend", "wife", "husband"}
    if couple_companions & set(scene.companions) or scene.visit_type == "couple":
        pb = mall_ctx.match_playbook("pb-date-plan", scene_signals)
        if pb:
            return pb
    # Family context → family visit
    family_companions = {"family", "kids", "son", "daughter"}
    if family_companions & set(scene.companions) or scene.visit_type == "family":
        pb = mall_ctx.match_playbook("pb-family-visit", scene_signals)
        if pb:
            return pb
    return None


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
    "pb-date-plan": {
        # Fires for couple context on ANY domain — activity, dining, entertainment
        "domains": {"exploration", "dining", "entertainment", "shopping"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "date", "romantic", "couple", "couple_friendly",
        },
    },
    "pb-family-visit": {
        "domains": {"dining", "entertainment", "shopping", "exploration"},
        "scene_signals": {"family", "kids", "children", "son", "daughter", "kid_friendly", "family_friendly"},
    },
    "pb-gift-recommendation": {
        # Requires explicit shopping domain — does NOT fire on activity/exploration
        "domains": {"shopping"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "birthday", "anniversary", "gift", "present",
        },
    },
    "pb-quick-bite": {
        "domains": {"dining"},
        "scene_signals": {"quick_visit", "before_movie", "quick"},
    },
    "pb-movie-night": {
        "domains": {"entertainment", "dining"},
        "scene_signals": {"before_movie", "after_movie", "movie"},
    },
    "pb-solo-visit": {
        "domains": {"dining", "shopping", "entertainment", "exploration"},
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
