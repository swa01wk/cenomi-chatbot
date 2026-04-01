"""
Resolve Playbooks node — matches current scene against scenario playbooks.

CONTRACT
────────
  Purpose:  Score available playbooks against intent + scene using the
            PlaybookEngine loaded from real mall data.
            Select the best-matching playbook if one clears the threshold.
            Uses semantic signals for richer matching.
  Reads:    intent, scene, active_tenant_parameters, normalized_user_message,
            flow_type
  Writes:   playbook (PlaybookResolution), debug_enrichment (partial)
  Failure:  No match → empty resolution; downstream uses strategy defaults
  Routing:  Always → choose_strategy

Flow-aware behavior:
  - flow_type == "concierge": full rich playbook resolution (existing behavior)
  - flow_type == "factual":   playbooks are suppressed; only minimal factual
    playbooks (movie_showtime_lookup, service_lookup) may apply for formatting.
    Generic shopping/gift/family playbooks MUST NOT override factual intent.
"""

from __future__ import annotations

from app.models.state import ConciergeState, DebugEnrichment, PlaybookResolution
from app.nodes._tracing import traced_node
from app.nodes.compose_context import _infer_product_category
from app.runtime import get_mall_context
from app.services.semantic_signals import (
    build_semantic_match_explanations,
    extract_semantic_signals,
)

# Playbooks that are inappropriate for factual flow — they must be suppressed
# to prevent semantic recommendations from overriding exact lookups.
_FACTUAL_FLOW_BLOCKED_PLAYBOOKS: frozenset[str] = frozenset({
    "pb-family-shopping",
    "pb-family-visit",
    "pb-gift-recommendation",
    "pb-gift-girlfriend",
    "pb-gift-family",
    "pb-last-minute-gift",
    "pb-romantic-dinner",
    "pb-date-plan",
    "pb-budget-plan",
    "pb-budget-family",
    "pb-luxury-shopping",
    "pb-luxury-dining",
    "pb-fine-dining",
    "pb-solo-visit",
    "pb-anniversary",
    "pb-child-activity-parents",
    "pb-child-activity-parents-shop",
    "pb-child-activity",
    "pb-movie-night",
    "pb-movie-food",
    "pb-kid-movie-food",
    "pb-dessert-combo",
    "pb-shop-dessert",
})

_CONFIDENCE_THRESHOLD = 0.25

# Product categories that are broad/generic enough that family override is still
# appropriate (no specific product task has been created).
# NOTE: empty string ("") intentionally excluded — _infer_product_category
# infers category from product_type, so an empty product_category no longer
# signals "too broad to scope".
_BROAD_SHOPPING_CATEGORIES: frozenset[str] = frozenset({
    "gifts", "fashion", "all_stores",
})

# Playbooks that are appropriate for a specific product shopping task.
# When one of these is selected, family/child context acts as a BIAS, not override.
_PRODUCT_TASK_PLAYBOOKS: list[str] = [
    "pb-category-shopping",
    "pb-quick-errand",
    "pb-child-activity-parents-shop",
]


# Playbooks that only make sense when the user explicitly asks for a gift/present.
# They must NOT be selected for general activity, date-idea, or exploration queries.
_GIFT_ONLY_PLAYBOOKS: frozenset[str] = frozenset({
    "pb-gift-girlfriend",
    "pb-gift-family",
    "pb-last-minute-gift",
    "pb-gift-recommendation",
})

# Luxury playbooks that should NOT fire when children are present
_LUXURY_PLAYBOOKS: frozenset[str] = frozenset({
    "pb-luxury-shopping",
    "pb-luxury-dining",
    "pb-fine-dining",
})

# Domains / sub-intents where gift playbooks should NOT be selected
_ACTIVITY_DOMAINS: frozenset[str] = frozenset({
    "exploration", "entertainment", "dining",
})
_ACTIVITY_SUB_INTENTS: frozenset[str] = frozenset({
    "activity_suggestion", "open_exploration", "first_visit_guide",
    "general_entertainment", "general_dining", "romantic_dining",
})

# Gift intent signals — checked via LLM-classified secondary_intents and
# sub_intent before falling back to a lightweight keyword scan.
_GIFT_SUB_INTENTS: frozenset[str] = frozenset({
    "gift_recommendation",
})
_GIFT_SECONDARY_INTENTS: frozenset[str] = frozenset({
    "gift_for",
})

# Child companion signals
_CHILD_COMPANIONS: frozenset[str] = frozenset({
    "child", "kids", "son", "daughter",
})

# Preferred family shopping playbook IDs (in priority order)
_FAMILY_SHOPPING_PLAYBOOKS: list[str] = [
    "pb-family-shopping",
    "pb-family-visit",
]

# User roles that indicate an occasion/event shopping context
# These must NOT be redirected to family shopping playbooks.
_OCCASION_USER_ROLES: frozenset[str] = frozenset({
    "bridesmaid", "bride", "groom", "maid_of_honor", "best_man",
    "mother_of_bride", "father_of_bride",
})

# Scenarios that require occasion-aware playbooks
_OCCASION_SCENARIOS: frozenset[str] = frozenset({
    "wedding_related",
})

# Preferred occasion playbook IDs (in priority order)
# pb-luxury-shop covers elegant/occasion/wedding shopping
_OCCASION_PLAYBOOKS: list[str] = [
    "pb-luxury-shop",
    "pb-category-shopping",
    "pb-gift-recommendation",
]


@traced_node("resolve_playbooks")
async def resolve_playbooks(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene
    msg = state.normalized_user_message or state.raw_user_message

    rejections: list[str] = []

    # ── Factual flow: suppress all non-factual playbooks ─────────────
    if state.flow_type == "factual":
        # Factual playbooks may still shape response formatting, but
        # concierge/recommendation playbooks must not win.
        # Emit empty resolution — strategy selection handles factual modes.
        suppressed_note = (
            "Factual flow: all generic concierge playbooks suppressed. "
            "Exact retrieval dominates."
        )
        debug = DebugEnrichment(
            playbook_rejection_reasons=[suppressed_note],
            playbook_suppressed=list(_FACTUAL_FLOW_BLOCKED_PLAYBOOKS),
        )
        return {
            "playbook": PlaybookResolution(
                matched_playbooks=[],
                selected_playbook="",
                playbook_confidence=0.0,
            ),
            "debug_enrichment": debug,
            "_trace_summary": "Playbooks suppressed: factual flow",
        }

    # ── Build semantic signals ────────────────────────────────────────
    semantic_signals = extract_semantic_signals(
        msg=msg,
        scene=scene,
        intent_domain=intent.domain,
        intent_sub_intent=intent.sub_intent,
    )

    scene_signals = _collect_scene_signals(scene, intent)
    # Merge semantic signals into scene signals for matching
    all_signals = list(dict.fromkeys(scene_signals + semantic_signals))

    mall_ctx = get_mall_context(state.mall_id)

    # ── Hybrid guard: secondary filters must not replace factual primary intent ─
    # When primary_intent is a factual lookup (movie_lookup, location_lookup, etc.)
    # the playbook engine must not select a concierge planning playbook based on
    # companion signals alone.  Family/kid context is a modifier, not the intent.
    primary_intent = state.primary_intent or ""
    _FACTUAL_PRIMARY_INTENT_PREFIXES = (
        "movie_lookup", "location_lookup", "mall_fact_lookup",
        "service_lookup", "cross_mall_lookup", "brand_availability",
        "movie_showtime", "store_hours", "opening_hours",
    )
    if primary_intent in _FACTUAL_PRIMARY_INTENT_PREFIXES:
        # Factual primary intent — playbooks that would redirect away from the
        # factual answer are rejected; return empty so strategy defaults to factual.
        suppressed_note = (
            f"Hybrid guard: primary_intent='{primary_intent}' is factual; "
            "concierge playbooks suppressed to preserve factual answer structure"
        )
        debug = DebugEnrichment(
            playbook_rejection_reasons=[suppressed_note],
            playbook_suppressed=list(_FACTUAL_FLOW_BLOCKED_PLAYBOOKS),
        )
        return {
            "playbook": PlaybookResolution(
                matched_playbooks=[],
                selected_playbook="",
                playbook_confidence=0.0,
            ),
            "debug_enrichment": debug,
            "_trace_summary": f"Playbooks suppressed: factual primary_intent={primary_intent}",
        }

    # ── Occasion / wedding override (MUST run before family override) ────
    # When the user has declared an occasion role (bridesmaid, bride, etc.) or
    # the scene scenario is wedding_related, route to an occasion-appropriate
    # playbook.  This MUST NOT fall through to the family shopping override.
    is_occasion_role = scene.user_role in _OCCASION_USER_ROLES
    is_occasion_scenario = getattr(scene, "scenario", "") in _OCCASION_SCENARIOS
    is_shopping_context = intent.domain in ("shopping", "exploration") or bool(
        msg.lower().find("shop") != -1 or msg.lower().find("shopping") != -1
    )

    if (is_occasion_role or is_occasion_scenario) and is_shopping_context:
        for pb_id in _OCCASION_PLAYBOOKS:
            occasion_pb = mall_ctx.match_playbook(
                intent=pb_id,
                context_signals=all_signals,
            )
            if occasion_pb:
                ranked_entities = mall_ctx.rank_for_playbook(occasion_pb)
                resolution = PlaybookResolution(
                    matched_playbooks=[occasion_pb.playbook_id],
                    selected_playbook=occasion_pb.playbook_id,
                    playbook_confidence=min(1.0, 0.75 + len(ranked_entities) * 0.02),
                )
                explanations = build_semantic_match_explanations(semantic_signals, scene, msg)
                debug = DebugEnrichment(
                    playbook_rejection_reasons=rejections + [
                        f"Family shopping suppressed: user_role={scene.user_role!r} "
                        f"scenario={getattr(scene, 'scenario', '')!r} "
                        f"→ occasion playbook selected instead"
                    ],
                    semantic_match_explanations=explanations,
                    playbook_suppressed=["pb-family-shopping", "pb-family-visit"],
                )
                return {
                    "playbook": resolution,
                    "debug_enrichment": debug,
                    "_trace_summary": (
                        f"Playbook[occasion_override]: {occasion_pb.playbook_id} "
                        f"(user_role={scene.user_role!r}, scenario="
                        f"{getattr(scene, 'scenario', '')!r})"
                    ),
                }

    # ── Detect whether a specific product shopping task is active ────
    # A specific task (e.g. "jacket", "kids_outerwear") must NOT be overridden
    # by family/child presence.  Family context becomes a bias only.
    has_child = bool(_CHILD_COMPANIONS & set(scene.companions)) or any(
        d.get("type") == "child" for d in scene.companion_details
    )
    _shopping_task = getattr(scene, "shopping_task", None)
    has_specific_shopping_task = bool(
        _shopping_task
        and _shopping_task.product_type
        and _infer_product_category(_shopping_task) not in _BROAD_SHOPPING_CATEGORIES
    )

    is_shopping_domain = intent.domain == "shopping"
    is_family_or_exploration = intent.domain in ("shopping", "exploration", "entertainment", "dining")

    # ── Family shopping override ──────────────────────────────────────
    # Rule: family/child context overrides ONLY when there is no specific product
    # shopping task active.  When a task is active (e.g. "buy jackets for my 5 yr
    # old"), the task scope dominates; family signals are injected as ranking bias.
    #
    # Priority order:
    #   1. explicit product task / fact scope  (has_specific_shopping_task)
    #   2. primary intent / domain
    #   3. scenario
    #   4. modifiers
    #   5. family/child bias  ← only reaches force-select when no task above
    if has_child and is_family_or_exploration and not has_specific_shopping_task:
        for pb_id in _FAMILY_SHOPPING_PLAYBOOKS:
            family_pb = mall_ctx.match_playbook(
                intent=pb_id,
                context_signals=all_signals,
            )
            if family_pb:
                ranked_entities = mall_ctx.rank_for_playbook(family_pb)
                resolution = PlaybookResolution(
                    matched_playbooks=[family_pb.playbook_id],
                    selected_playbook=family_pb.playbook_id,
                    playbook_confidence=min(1.0, 0.7 + len(ranked_entities) * 0.03),
                )
                explanations = build_semantic_match_explanations(semantic_signals, scene, msg)
                debug = DebugEnrichment(
                    playbook_rejection_reasons=rejections,
                    semantic_match_explanations=explanations,
                    family_override_applied=True,
                    playbook_bias_applied=[],
                )
                return {
                    "playbook": resolution,
                    "debug_enrichment": debug,
                    "_trace_summary": (
                        f"Playbook[family_override]: {family_pb.playbook_id} "
                        f"({len(ranked_entities)} entities)"
                    ),
                }
    elif has_child and has_specific_shopping_task:
        # Child present + specific product task: inject family bias into signals
        # so ranking favours kid-friendly stores, but DON'T force-select family playbook.
        _family_bias_signals = ["kid_friendly", "family_friendly", "kids", "child"]
        for sig in _family_bias_signals:
            if sig not in all_signals:
                all_signals.append(sig)
        rejections.append(
            f"Family override suppressed: active shopping_task "
            f"product_type={_shopping_task.product_type!r} "
            f"category={_shopping_task.product_category!r} "
            "takes priority — family signals injected as ranking bias only"
        )

    # ── Normal playbook matching ──────────────────────────────────────
    matched_pb = mall_ctx.match_playbook(
        intent=f"{intent.domain}/{intent.sub_intent}",
        context_signals=all_signals,
    )

    # ── Guard: prevent gift playbooks from hijacking activity/date queries ──
    if matched_pb and matched_pb.playbook_id in _GIFT_ONLY_PLAYBOOKS:
        secondary = set(intent.secondary_intents or [])
        has_explicit_gift = (
            intent.sub_intent in _GIFT_SUB_INTENTS
            or bool(secondary & _GIFT_SECONDARY_INTENTS)
        )
        is_activity_query = (
            intent.domain in _ACTIVITY_DOMAINS
            or intent.sub_intent in _ACTIVITY_SUB_INTENTS
        )
        if is_activity_query and not has_explicit_gift:
            rejections.append(
                f"{matched_pb.playbook_id} rejected: gift playbook on non-gift query "
                f"(domain={intent.domain}, no explicit gift signal)"
            )
            override = _find_activity_playbook(intent, scene, mall_ctx, all_signals)
            if override:
                matched_pb = override
            else:
                matched_pb = None

    # ── Guard: prevent luxury playbooks when children are present ────
    if matched_pb and matched_pb.playbook_id in _LUXURY_PLAYBOOKS and has_child:
        rejections.append(
            f"{matched_pb.playbook_id} rejected: luxury playbook inappropriate "
            f"when child companions are present"
        )
        if has_specific_shopping_task:
            # Specific product task active — reject luxury but don't redirect to
            # family; let normal matching/fallback find the right task playbook.
            matched_pb = None
        else:
            # No specific task — family playbook is the right fallback
            for pb_id in _FAMILY_SHOPPING_PLAYBOOKS:
                family_pb = mall_ctx.match_playbook(pb_id, all_signals)
                if family_pb:
                    matched_pb = family_pb
                    break
            else:
                matched_pb = None

    # ── Compute continuity / dominant task scope for downstream nodes ─
    _dominant_task_scope = ""
    _continuity_resolved_topic = scene.active_topic or intent.domain or ""
    _playbook_bias_applied: list[str] = []
    if has_specific_shopping_task and _shopping_task:
        _dominant_task_scope = _shopping_task.product_category or _shopping_task.product_type
        _continuity_resolved_topic = "shopping"
        if has_child:
            _playbook_bias_applied.append("family_filter")

    if matched_pb:
        ranked_entities = mall_ctx.rank_for_playbook(matched_pb)
        entity_count = len(ranked_entities)
        resolution = PlaybookResolution(
            matched_playbooks=[matched_pb.playbook_id],
            selected_playbook=matched_pb.playbook_id,
            playbook_confidence=min(1.0, 0.5 + entity_count * 0.05),
        )
        explanations = build_semantic_match_explanations(semantic_signals, scene, msg)
        selection_reason = (
            f"PlaybookEngine matched: {matched_pb.playbook_id} "
            f"(domain={intent.domain}, sub_intent={intent.sub_intent})"
        )
        debug = DebugEnrichment(
            playbook_rejection_reasons=rejections,
            semantic_match_explanations=explanations,
            selected_playbook_id=matched_pb.playbook_id,
            selected_playbook_reason=selection_reason,
            family_override_applied=False,
            playbook_bias_applied=_playbook_bias_applied,
            dominant_task_scope=_dominant_task_scope,
            continuity_resolved_topic=_continuity_resolved_topic,
            shopping_task_active=has_specific_shopping_task,
        )
        return {
            "playbook": resolution,
            "debug_enrichment": debug,
            "dominant_task_scope": _dominant_task_scope,
            "continuity_resolved_topic": _continuity_resolved_topic,
            "_trace_summary": (
                f"Playbook: {matched_pb.playbook_id} "
                f"({entity_count} ranked entities)"
            ),
        }

    # ── Fallback scoring ─────────────────────────────────────────────
    scored = _score_fallback(intent, scene, semantic_signals, has_specific_shopping_task)
    matched = [pb_id for pb_id, _ in scored]
    selected = scored[0][0] if scored else ""
    confidence = scored[0][1] if scored else 0.0

    resolution = PlaybookResolution(
        matched_playbooks=matched,
        selected_playbook=selected,
        playbook_confidence=confidence,
    )

    explanations = build_semantic_match_explanations(semantic_signals, scene, msg)
    selection_reason = (
        f"Fallback scoring: {selected!r} (conf={confidence}, "
        f"domain={intent.domain})"
        if selected else "No playbook matched"
    )
    debug = DebugEnrichment(
        playbook_rejection_reasons=rejections,
        semantic_match_explanations=explanations,
        selected_playbook_id=selected,
        selected_playbook_reason=selection_reason,
        playbook_suppressed=[r.split(" ")[0] for r in rejections if r],
        family_override_applied=False,
        playbook_bias_applied=_playbook_bias_applied,
        dominant_task_scope=_dominant_task_scope,
        continuity_resolved_topic=_continuity_resolved_topic,
        shopping_task_active=has_specific_shopping_task,
    )

    return {
        "playbook": resolution,
        "debug_enrichment": debug,
        "dominant_task_scope": _dominant_task_scope,
        "continuity_resolved_topic": _continuity_resolved_topic,
        "_trace_summary": f"Playbook: {selected or 'none'} (conf={confidence})",
    }


def _find_activity_playbook(intent, scene, mall_ctx, all_signals: list[str]):
    """
    Find a better playbook for activity/date-idea queries when a gift
    playbook incorrectly fired.
    """
    couple_companions = {"girlfriend", "boyfriend", "wife", "husband"}
    if couple_companions & set(scene.companions) or scene.visit_type == "couple":
        pb = mall_ctx.match_playbook("pb-date-plan", all_signals)
        if pb:
            return pb
    family_companions = {"family", "kids", "son", "daughter", "child"}
    if family_companions & set(scene.companions) or scene.visit_type in ("family", "family_visit"):
        for pb_id in _FAMILY_SHOPPING_PLAYBOOKS:
            pb = mall_ctx.match_playbook(pb_id, all_signals)
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
    signals.extend(scene.visit_constraints)
    if scene.visit_type:
        signals.append(scene.visit_type)
    if scene.goal:
        signals.append(scene.goal)
    if intent.domain:
        signals.append(intent.domain)
    if intent.sub_intent:
        signals.append(intent.sub_intent)
    if scene.active_topic:
        signals.append(scene.active_topic)
    # Scenario and user_role are rich context signals
    if getattr(scene, "scenario", ""):
        signals.append(scene.scenario)
    if getattr(scene, "user_role", ""):
        signals.append(scene.user_role)
    if getattr(scene, "style_intent", []):
        signals.extend(scene.style_intent)
    return signals


# Lightweight fallback for when the PlaybookEngine doesn't match
_FALLBACK_TRIGGERS: dict[str, dict] = {
    "pb-date-plan": {
        "domains": {"exploration", "dining", "entertainment", "shopping"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "date", "romantic", "couple", "couple_friendly",
        },
    },
    "pb-family-shopping": {
        "domains": {"shopping", "exploration"},
        "scene_signals": {
            "family", "kids", "children", "son", "daughter", "child",
            "kid_friendly", "family_friendly", "family_visit",
        },
    },
    "pb-family-visit": {
        "domains": {"dining", "entertainment", "shopping", "exploration"},
        "scene_signals": {
            "family", "kids", "children", "son", "daughter", "child",
            "kid_friendly", "family_friendly",
        },
    },
    "pb-gift-recommendation": {
        "domains": {"shopping"},
        "scene_signals": {
            "girlfriend", "boyfriend", "wife", "husband",
            "birthday", "anniversary", "gift", "present", "gift_friendly",
        },
    },
    "pb-before-movie": {
        "domains": {"dining", "shopping", "exploration"},
        "scene_signals": {"before_movie", "time_sensitive", "near_cinema"},
    },
    "pb-quick-bite": {
        "domains": {"dining"},
        "scene_signals": {
            "quick_visit", "before_movie", "quick", "quick_stop_preferred",
            "time_sensitive", "quick_stop",
        },
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
        "scene_signals": {"budget", "budget_sensitive", "value_shopping"},
    },
    "pb-luxury-shop": {
        "domains": {"shopping", "exploration"},
        "scene_signals": {
            "wedding_related", "bridesmaid", "bride", "groom",
            "elegant", "occasion_wear", "luxury", "premium",
            "wedding", "anniversary",
        },
    },
}


_FAMILY_FALLBACK_PLAYBOOKS: frozenset[str] = frozenset({
    "pb-family-shopping",
    "pb-family-visit",
})


def _score_fallback(
    intent,
    scene,
    semantic_signals: list[str],
    has_specific_shopping_task: bool = False,
) -> list[tuple[str, float]]:
    scene_tokens: set[str] = set()
    scene_tokens.update(scene.companions)
    if scene.occasion:
        scene_tokens.add(scene.occasion)
    if scene.budget:
        scene_tokens.add(scene.budget)
    scene_tokens.update(scene.audience)
    scene_tokens.update(scene.visit_constraints)
    scene_tokens.update(semantic_signals)
    if scene.visit_type:
        scene_tokens.add(scene.visit_type)

    scored: list[tuple[str, float]] = []
    for pb_id, triggers in _FALLBACK_TRIGGERS.items():
        score = 0.0
        if intent.domain in triggers["domains"]:
            score += 0.4
        overlap = scene_tokens & triggers["scene_signals"]
        if overlap:
            score += 0.3 * (len(overlap) / max(len(triggers["scene_signals"]), 1))

        # When a specific product task is active, heavily downweight generic
        # family/broad shopping playbooks so the task-specific path wins.
        if has_specific_shopping_task and pb_id in _FAMILY_FALLBACK_PLAYBOOKS:
            score *= 0.25

        if score >= _CONFIDENCE_THRESHOLD:
            scored.append((pb_id, round(score, 3)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
