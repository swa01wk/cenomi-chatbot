"""
Resolve Playbooks node — matches current scene against scenario playbooks
with a formal precedence algorithm.

CONTRACT
────────
  Purpose:  Score available playbooks against intent + scene using the
            PlaybookEngine loaded from real mall data.
            Apply precedence rules:
              1. Product-specific outranks generic
              2. Audience-specific outranks generic
              3. Continuity-matching outranks unrelated
              4. Cross-domain playbooks are heavily penalized
            Select the best-matching playbook if one clears the threshold.
  Reads:    intent, scene, continuity_anchor, thread_preservation,
            continuity_resolution, active_tenant_parameters
  Writes:   playbook (PlaybookResolution)
  Failure:  No match → empty resolution; downstream uses strategy defaults
  Routing:  Always → choose_strategy

Precedence score formula:
  score = base_trigger_match
        + domain_match_boost      (0.25 if playbook domain == intent domain)
        + specificity_boost       (product=0.20, audience=0.15, generic=0.0)
        + continuity_boost        (0.25 if playbook matches the anchor)
        + audience_overlap_boost  (0.15 per audience match)
        - cross_domain_penalty    (0.40 if playbook domain != active domain)
"""

from __future__ import annotations

from typing import Any

from app.models.state import ConciergeState, PlaybookResolution
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context

_CONFIDENCE_THRESHOLD = 0.25

# Precedence boost/penalty values
_DOMAIN_MATCH_BOOST = 0.25
_PRODUCT_SPECIFICITY_BOOST = 0.20
_AUDIENCE_SPECIFICITY_BOOST = 0.15
_CONTINUITY_BOOST = 0.25
_AUDIENCE_OVERLAP_BOOST = 0.15
_CROSS_DOMAIN_PENALTY = 0.40
_CONTINUITY_PRIORITY_FACTOR = 0.003


@traced_node("resolve_playbooks")
async def resolve_playbooks(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene
    anchor = state.continuity_anchor
    thread_pres = state.thread_preservation
    continuity_res = state.continuity_resolution

    scene_signals = _collect_scene_signals(scene, intent, anchor)

    mall_ctx = get_mall_context()

    # ── Score all playbooks via the engine ────────────────────────────
    matched_pb = mall_ctx.match_playbook(
        intent=f"{intent.domain}/{intent.sub_intent}",
        context_signals=scene_signals,
    )

    # ── Also run the full precedence scoring on all available playbooks
    all_candidates = _score_all_playbooks_with_precedence(
        intent, scene, anchor, thread_pres, continuity_res, mall_ctx,
    )

    # ── Engine match gets precedence-adjusted ─────────────────────────
    if matched_pb:
        engine_score = _compute_precedence_score(
            matched_pb, intent, scene, anchor, thread_pres, continuity_res,
        )
        ranked_entities = mall_ctx.rank_for_playbook(matched_pb)
        entity_count = len(ranked_entities)
        base_confidence = min(1.0, 0.5 + entity_count * 0.05)
        final_confidence = min(1.0, base_confidence + engine_score * 0.2)

        # Check if any precedence-scored candidate beats the engine match
        best_precedence_id = all_candidates[0]["playbook_id"] if all_candidates else ""
        if (
            best_precedence_id
            and best_precedence_id != matched_pb.playbook_id
            and all_candidates[0]["score"] > engine_score + 0.15
        ):
            # A higher-precedence playbook exists — try to load it
            better_pb = mall_ctx.match_playbook(
                intent=best_precedence_id,
                context_signals=scene_signals,
            )
            if better_pb:
                matched_pb = better_pb
                ranked_entities = mall_ctx.rank_for_playbook(better_pb)
                entity_count = len(ranked_entities)
                final_confidence = min(
                    1.0, 0.5 + entity_count * 0.05 + all_candidates[0]["score"] * 0.2,
                )

        resolution = PlaybookResolution(
            matched_playbooks=[c["playbook_id"] for c in all_candidates[:5]],
            selected_playbook=matched_pb.playbook_id,
            playbook_confidence=round(final_confidence, 3),
            expected_playbook_candidates=all_candidates[:5],
        )
        return {
            "playbook": resolution,
            "_trace_summary": (
                f"Playbook: {matched_pb.playbook_id} "
                f"({entity_count} ranked entities, conf={final_confidence:.2f}, "
                f"candidates={len(all_candidates)})"
            ),
        }

    # ── Fallback: use precedence-scored candidates ────────────────────
    if all_candidates:
        matched = [c["playbook_id"] for c in all_candidates]
        selected = all_candidates[0]["playbook_id"]
        confidence = all_candidates[0]["score"]

        resolution = PlaybookResolution(
            matched_playbooks=matched[:5],
            selected_playbook=selected,
            playbook_confidence=round(confidence, 3),
            expected_playbook_candidates=all_candidates[:5],
        )
    else:
        # Legacy fallback scoring
        scored = _score_fallback(intent, scene, anchor, thread_pres, continuity_res)
        matched = [pb_id for pb_id, _ in scored]
        selected = scored[0][0] if scored else ""
        confidence = scored[0][1] if scored else 0.0

        resolution = PlaybookResolution(
            matched_playbooks=matched,
            selected_playbook=selected,
            playbook_confidence=round(confidence, 3),
            expected_playbook_candidates=[
                {"playbook_id": pid, "score": s} for pid, s in scored[:5]
            ],
        )

    return {
        "playbook": resolution,
        "_trace_summary": f"Playbook: {resolution.selected_playbook or 'none'} (conf={confidence:.3f})",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Precedence scoring
# ═══════════════════════════════════════════════════════════════════════════


def _compute_precedence_score(
    playbook: Any,
    intent: Any,
    scene: Any,
    anchor: Any,
    thread_pres: Any,
    continuity_res: Any,
) -> float:
    """Compute the precedence score for a single playbook."""
    score = 0.0

    # Domain match
    pb_domain = getattr(playbook, "topic_domain", "")
    if pb_domain and pb_domain == intent.domain:
        score += _DOMAIN_MATCH_BOOST

    # Specificity: product-specific > audience-specific > generic
    pb_subdomain = getattr(playbook, "topic_subdomain", "")
    has_audience_tags = bool(
        set(getattr(playbook, "preferred_semantic_tags", []))
        & {"kid_friendly", "family_friendly", "couple_friendly", "solo_friendly"}
    )
    has_product_tags = bool(pb_subdomain)

    if has_product_tags and has_audience_tags:
        score += _PRODUCT_SPECIFICITY_BOOST + _AUDIENCE_SPECIFICITY_BOOST * 0.5
    elif has_product_tags:
        score += _PRODUCT_SPECIFICITY_BOOST
    elif has_audience_tags:
        score += _AUDIENCE_SPECIFICITY_BOOST

    # Continuity alignment
    if anchor.is_strong:
        if pb_domain and pb_domain == anchor.domain:
            score += _CONTINUITY_BOOST * 0.5
        if anchor.last_playbook and anchor.last_playbook == playbook.playbook_id:
            score += _CONTINUITY_BOOST
        if thread_pres.preserve_current_topic:
            score += _CONTINUITY_BOOST * 0.3

    # Continuity priority from the playbook itself
    cp = getattr(playbook, "continuity_priority", 50)
    score += cp * _CONTINUITY_PRIORITY_FACTOR

    # Audience overlap
    scene_audience = set(scene.audience)
    pb_audience = set(getattr(playbook, "preferred_semantic_tags", []))
    audience_overlap = scene_audience & pb_audience
    if audience_overlap:
        score += _AUDIENCE_OVERLAP_BOOST * min(1.0, len(audience_overlap) * 0.5)

    # Cross-domain penalty
    if pb_domain and intent.domain and pb_domain != intent.domain:
        score -= _CROSS_DOMAIN_PENALTY

    return round(score, 4)


def _score_all_playbooks_with_precedence(
    intent, scene, anchor, thread_pres, continuity_res, mall_ctx,
) -> list[dict[str, Any]]:
    """Score every loaded playbook with the precedence algorithm."""
    try:
        engine = mall_ctx._playbook_engine
        if not engine or not engine._playbooks:
            return []
    except AttributeError:
        return []

    candidates: list[dict[str, Any]] = []
    for pb in engine._playbooks:
        score = _compute_precedence_score(pb, intent, scene, anchor, thread_pres, continuity_res)

        # Base trigger match bonus
        signals = set(_collect_scene_signals(scene, intent, anchor) + [
            f"{intent.domain}/{intent.sub_intent}",
        ])
        trigger_hits = 0
        for trigger in pb.trigger_conditions:
            trigger_lower = trigger.lower()
            for signal in signals:
                if signal in trigger_lower or trigger_lower in signal:
                    trigger_hits += 1
                    break
        score += trigger_hits * 0.10

        if score >= _CONFIDENCE_THRESHOLD:
            candidates.append({
                "playbook_id": pb.playbook_id,
                "score": round(score, 4),
                "domain": pb.topic_domain,
                "specificity": "product" if pb.topic_subdomain else (
                    "audience" if set(pb.preferred_semantic_tags) & {
                        "kid_friendly", "family_friendly", "couple_friendly",
                    } else "generic"
                ),
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _collect_scene_signals(scene, intent, anchor) -> list[str]:
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
    if anchor.topic:
        signals.append(anchor.topic)
    if anchor.subtopic:
        signals.append(anchor.subtopic)
    if anchor.audience:
        signals.append(anchor.audience)
    return signals


# ── Fallback scoring for when the PlaybookEngine has no playbooks ────────

_FALLBACK_TRIGGERS: dict[str, dict] = {
    "pb-romantic-dinner": {
        "domains": {"dining"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "date", "romantic", "anniversary",
        },
        "specificity": "audience",
    },
    "pb-family-visit": {
        "domains": {"dining", "entertainment", "shopping"},
        "scene_signals": {"family", "kids", "children", "kid_friendly", "family_friendly"},
        "specificity": "audience",
    },
    "pb-gift-recommendation": {
        "domains": {"shopping"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "birthday", "anniversary",
        },
        "specificity": "product",
    },
    "pb-quick-bite": {
        "domains": {"dining"},
        "scene_signals": {"quick_visit", "before_movie"},
        "specificity": "generic",
    },
    "pb-movie-night": {
        "domains": {"entertainment", "dining"},
        "scene_signals": {"before_movie", "after_movie"},
        "specificity": "product",
    },
    "pb-solo-visit": {
        "domains": {"dining", "shopping", "entertainment"},
        "scene_signals": {"solo", "solo_friendly"},
        "specificity": "audience",
    },
    "pb-budget-plan": {
        "domains": {"dining", "shopping", "entertainment"},
        "scene_signals": {"budget"},
        "specificity": "generic",
    },
}


def _score_fallback(intent, scene, anchor, thread_pres, continuity_res) -> list[tuple[str, float]]:
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

        # Domain match
        if intent.domain in triggers["domains"]:
            score += _DOMAIN_MATCH_BOOST
        else:
            score -= _CROSS_DOMAIN_PENALTY

        # Signal overlap
        overlap = scene_tokens & triggers["scene_signals"]
        if overlap:
            score += 0.3 * (len(overlap) / max(len(triggers["scene_signals"]), 1))

        # Specificity boost
        specificity = triggers.get("specificity", "generic")
        if specificity == "product":
            score += _PRODUCT_SPECIFICITY_BOOST
        elif specificity == "audience" and overlap:
            score += _AUDIENCE_SPECIFICITY_BOOST

        # Continuity alignment
        if thread_pres.preserve_current_topic and anchor.last_playbook == pb_id:
            score += _CONTINUITY_BOOST

        if score >= _CONFIDENCE_THRESHOLD:
            scored.append((pb_id, round(score, 3)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
