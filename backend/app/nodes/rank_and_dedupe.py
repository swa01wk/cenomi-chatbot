"""
Rank and Deduplicate node — post-processes the entity shortlist before retrieval.

CONTRACT
────────
  Purpose:  Take the candidate entity list from compose_context, deduplicate it,
            apply a multi-factor scoring formula, enforce entity caps, inject
            required anchors (e.g. child-relief for family visits), and produce a
            clean, ranked shortlist ready for the LLM.

  Reads:    context (selected_entities, selected_semantic_signals),
            intent, scene, playbook, response_plan
  Writes:   context (selected_entities updated), debug_enrichment (partial)
  Failure:  Scoring error → preserve original entity order, emit warning
  Routing:  Always → decide_retrieval

Scoring formula:
  final_score =
    intent_fit       * 0.20
    + semantic_score * 0.20
    + audience_fit   * 0.15
    + playbook_score * 0.20
    + constraint_fit * 0.15
    + diversity_pen  * 0.10
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from app.models.state import (
    ConciergeState,
    ContextComposition,
    DebugEnrichment,
)
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# ── Canonical name normalizer for deduplication ─────────────────────────────

_PUNCT_STRIP = re.compile(r"[^\w\s]")
_WHITESPACE_COLLAPSE = re.compile(r"\s+")
_ARTICLES = frozenset({"the", "a", "an", "al", "el"})


def _dedupe_key(name: str) -> str:
    """
    Produce a normalization key for deduplication that is:
    - Case-insensitive
    - Punctuation-insensitive (removes &, -, ', ., etc.)
    - Whitespace-collapsed
    - Article-stripped (leading "The", "Al", etc.)

    Examples:
        "Season Accessorize" → "season accessorize"
        "season accessorize" → "season accessorize"   ← same key → deduped
        "H&M"               → "hm"
        "H & M"             → "h m"                   ← different; keep both
        "The Body Shop"     → "body shop"
        "Al Nakheel"        → "nakheel"
    """
    # Normalize unicode (é → e, etc.)
    normalized = unicodedata.normalize("NFKD", name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    # Lowercase
    normalized = normalized.lower()
    # Strip punctuation (keep spaces and word chars)
    normalized = _PUNCT_STRIP.sub(" ", normalized)
    # Collapse whitespace
    normalized = _WHITESPACE_COLLAPSE.sub(" ", normalized).strip()
    # Strip leading article words
    tokens = normalized.split()
    if tokens and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    return " ".join(tokens)

# Weights for the scoring formula.
# Audience fit is raised to 0.30 (was 0.15) so that target_person / companion
# context dominates the ranking — a kid context will reliably surface
# family-friendly stores above premium adult-only options.
# Semantic and playbook weights are trimmed to compensate; total still sums to 1.0.
_WEIGHT_INTENT = 0.20
_WEIGHT_SEMANTIC = 0.15   # was 0.20
_WEIGHT_AUDIENCE = 0.30   # was 0.15
_WEIGHT_PLAYBOOK = 0.10   # was 0.20
_WEIGHT_CONSTRAINT = 0.15
_WEIGHT_DIVERSITY = 0.10

# Default entity caps by strategy
_STRATEGY_CAPS: dict[str, int] = {
    "guided_plan": 5,
    "concise_shortlist": 5,
    "quick_answer": 3,
    "mini_itinerary": 6,
    "gift_formula": 4,
    "family_plan": 5,
    "route_plus_plan": 4,
    "shortlist_recommendation": 8,
    "exploration_overview": 12,
    "mall_overview": 20,
    "direct_fact": 5,
    "fallback_guided_response": 8,
}

# Entity types that count as child-relief anchors
_CHILD_RELIEF_TYPES: frozenset[str] = frozenset({
    "entertainment", "activity", "cinema", "kids_zone", "play_area",
})

# Semantic tags that indicate child-relief suitability
_CHILD_RELIEF_TAGS: frozenset[str] = frozenset({
    "child_activity", "kids_entertainment", "family_entertainment",
    "interactive_experience", "kid_friendly", "child_gift",
})

# Entity types that count as anchor for a given playbook use-case
_ENTERTAINMENT_TYPES: frozenset[str] = frozenset({
    "entertainment", "activity", "cinema",
})

# Child companion signals
_CHILD_COMPANIONS: frozenset[str] = frozenset({
    "child", "kids", "son", "daughter",
})


@traced_node("rank_and_dedupe")
async def rank_and_dedupe(state: ConciergeState) -> dict:
    entities = list(state.context.selected_entities)

    # ── Factual flow: skip semantic ranking ───────────────────────────
    # Exact retrieval results should not be re-ranked with concierge heuristics.
    # Minimal deduplication only when multiple exact results exist.
    if state.flow_type == "factual":
        seen_keys: set[str] = set()
        deduped: list[dict] = []
        collapsed_count = 0
        factual_notes: list[str] = []
        for entity in entities:
            eid = entity.get("entity_id", "")
            name = entity.get("name", "")
            # Use entity_id first; fall back to normalized name key
            key = eid if eid else _dedupe_key(name)
            if not key:
                deduped.append(entity)
                continue
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(entity)
            else:
                collapsed_count += 1
                factual_notes.append(f"Collapsed duplicate (factual) name={name!r} key={key!r}")
        return {
            "context": state.context.model_copy(
                update={"selected_entities": deduped}
            ),
            "debug_enrichment": DebugEnrichment(
                candidate_count_before_dedupe=len(entities),
                deduped_entity_count=len(deduped),
                final_entity_count=len(deduped),
                ranking_explanations=["factual_flow:ranking_skipped"],
                dedupe_key_used="entity_id|normalized_name",
                duplicate_entities_collapsed=collapsed_count,
                canonical_name_normalization_notes=factual_notes[:5],
                retrieval_discipline_reason="factual_flow:exact_results_preserved",
            ),
            "_trace_summary": (
                f"Rank/Dedupe[factual]: minimal dedup only "
                f"{len(entities)} → {len(deduped)}"
                + (f" ({collapsed_count} collapsed)" if collapsed_count else "")
            ),
        }

    semantic_signals = set(state.context.selected_semantic_signals)
    intent = state.intent
    scene = state.scene
    response_plan = state.response_plan

    candidate_count = len(entities)

    try:
        # ── 1. Deduplicate by entity_id then normalized canonical name ─
        # Uses a two-level key: entity_id (exact) first, then normalized
        # canonical name (case/punct/article insensitive) as fallback.
        # This collapses "Season Accessorize" vs "season accessorize", etc.
        seen_ids: set[str] = set()
        seen_name_keys: set[str] = set()
        deduped: list[dict[str, Any]] = []
        collapsed_count = 0
        normalization_notes: list[str] = []

        for entity in entities:
            eid = entity.get("entity_id", "")
            name = entity.get("name", "")
            name_key = _dedupe_key(name) if name else ""
            canonical = entity.get("canonical_name", "") or name
            canonical_key = _dedupe_key(canonical) if canonical else ""

            if eid:
                if eid in seen_ids:
                    collapsed_count += 1
                    normalization_notes.append(
                        f"Collapsed duplicate id={eid!r} name={name!r}"
                    )
                    continue
                # Strict name-clash check: collapse variants with different entity_ids
                # but the same normalized name (e.g. "Season Accessorize" vs
                # "season accessorize" registered under two IDs).
                if (name_key and name_key in seen_name_keys) or (
                    canonical_key and canonical_key in seen_name_keys
                ):
                    collapsed_count += 1
                    normalization_notes.append(
                        f"Collapsed name-clash id={eid!r} name={name!r} "
                        f"key={name_key!r} (different id, same normalized name)"
                    )
                    continue
                seen_ids.add(eid)
                # Register name keys so further aliases are also caught
                if name_key:
                    seen_name_keys.add(name_key)
                if canonical_key:
                    seen_name_keys.add(canonical_key)
                deduped.append(entity)
            else:
                # No entity_id — use normalized name as unique key
                key = canonical_key or name_key
                if not key:
                    deduped.append(entity)
                    continue
                if key in seen_name_keys:
                    collapsed_count += 1
                    normalization_notes.append(
                        f"Collapsed duplicate name={name!r} key={key!r}"
                    )
                    continue
                seen_name_keys.add(key)
                if name_key:
                    seen_name_keys.add(name_key)
                if name_key != canonical_key and canonical_key:
                    normalization_notes.append(
                        f"name={name!r} → canonical_key={canonical_key!r}"
                    )
                deduped.append(entity)

        deduped_count = len(deduped)

        # ── 2. Determine entity cap ───────────────────────────────────
        strategy = response_plan.chosen_strategy or "fallback_guided_response"
        cap_from_plan = response_plan.entity_cap if response_plan.entity_cap > 0 else 0
        cap_from_strategy = _STRATEGY_CAPS.get(strategy, 8)
        entity_cap = cap_from_plan if cap_from_plan > 0 else cap_from_strategy

        # For category-level retrieval, preserve the full list (don't cap)
        is_category_retrieval = any(
            e.get("source", "").startswith("category/") for e in deduped
        )
        if is_category_retrieval:
            entity_cap = len(deduped)

        # ── 3. Collect scoring context ────────────────────────────────
        has_child = bool(_CHILD_COMPANIONS & set(scene.companions)) or any(
            d.get("type") == "child" for d in scene.companion_details
        )
        audience_set = set(scene.audience)
        constraint_set = set(scene.visit_constraints)
        shopping_task = getattr(scene, "shopping_task", None)
        is_context_setting = state.intent.message_kind == "context_setting"

        # Determine whether an active audience requirement is in force.
        # When True, entities that do not match audience_fit receive a hard
        # score multiplier (0.2×) to push them below appropriate alternatives.
        _active_audience_requirement = bool(
            audience_set
            or has_child
            or (shopping_task and getattr(shopping_task, "target_person", "") not in ("", "self"))
            or (shopping_task and (getattr(shopping_task, "budget_preference", "") == "affordable"))
        )

        # Playbook biases: tag → weight
        playbook_biases: dict[str, float] = {}
        try:
            from app.runtime import get_mall_context
            mall_ctx = get_mall_context(state.mall_id)
            if state.playbook.selected_playbook:
                pb_obj = mall_ctx.match_playbook(
                    intent=state.playbook.selected_playbook,
                    context_signals=[],
                )
                if pb_obj and hasattr(pb_obj, "ranking_biases"):
                    playbook_biases = pb_obj.ranking_biases or {}
        except Exception:
            pass

        # ── 4. Score each entity ──────────────────────────────────────
        type_counts: dict[str, int] = {}
        scored_entities: list[tuple[float, dict[str, Any]]] = []
        suppressed_off_topic = 0

        for entity in deduped:
            score = _score_entity(
                entity=entity,
                semantic_signals=semantic_signals,
                audience_set=audience_set,
                constraint_set=constraint_set,
                playbook_biases=playbook_biases,
                intent_domain=intent.domain,
                intent_sub=intent.sub_intent,
            )
            # Apply shopping task boost / suppression if a specific product task is active
            if shopping_task and shopping_task.product_type and (
                shopping_task.product_category or ""
            ).lower() not in ("", "fashion", "gifts", "all_stores"):
                score, was_suppressed = _apply_shopping_task_score_adjustment(
                    score, entity, shopping_task
                )
                if was_suppressed:
                    suppressed_off_topic += 1

            # Hard audience mismatch penalty: when an active audience requirement
            # exists and this entity's audience_fit score is very low (< 0.2),
            # multiply the score by 0.2 to push it far below appropriate alternatives.
            # This prevents premium/adult-only stores from outranking family-friendly
            # options purely on semantic tag strength.
            if _active_audience_requirement:
                entity_audience = set(entity.get("audience_fit", []))
                if audience_set and entity_audience:
                    aud_overlap = len(entity_audience & audience_set)
                    raw_aud_fit = aud_overlap / max(len(audience_set), 1)
                else:
                    raw_aud_fit = 0.4  # same neutral baseline as _score_entity

                # Apply penalty for clear audience mismatch
                if raw_aud_fit < 0.2:
                    score = score * 0.2

            scored_entities.append((score, entity))

        # Sort by score descending
        scored_entities.sort(key=lambda x: x[0], reverse=True)

        # ── 5. Apply diversity penalty ────────────────────────────────
        # Penalize entities of the same type after 3 occurrences
        type_counts_seen: dict[str, int] = {}
        diversified: list[tuple[float, dict[str, Any]]] = []
        for score, entity in scored_entities:
            etype = entity.get("entity_type", "unknown")
            count = type_counts_seen.get(etype, 0)
            if count >= 3:
                score = max(0.0, score - 0.15 * (count - 2))
            type_counts_seen[etype] = count + 1
            diversified.append((score, entity))

        # Re-sort after diversity
        diversified.sort(key=lambda x: x[0], reverse=True)

        # ── 6. Apply entity cap (pre-anchor injection) ────────────────
        capped = [e for _, e in diversified[:entity_cap]]

        # ── 7. Child-relief anchor injection ─────────────────────────
        # Skip child-relief injection when a specific product shopping task is
        # active — the task scope already governs what should be included, and
        # injecting an entertainment/activity entity would break the shopping
        # task scope. (e.g. "buy jackets for my 5 yr old" should NOT get a
        # cinema injected just because a child is present.)
        must_include_anchor = response_plan.must_include_anchor_type
        _skip_child_relief = (
            shopping_task is not None
            and bool(shopping_task.product_type)
            and (shopping_task.product_category or "").lower() not in _BROAD_TASK_CATEGORIES
        )
        if (has_child or "kid_friendly" in audience_set or "kid_friendly_required" in constraint_set) and not _skip_child_relief:
            capped = _ensure_child_relief_anchor(
                capped, deduped, entity_cap,
            )
        elif must_include_anchor:
            capped = _ensure_anchor_type(capped, deduped, must_include_anchor, entity_cap)

        # ── 8. Build ranking explanations ─────────────────────────────
        ranking_explanations = _build_ranking_explanations(
            capped[:5], semantic_signals, audience_set,
            playbook_biases, has_child,
        )

        # ── 9. Update context ─────────────────────────────────────────
        updated_context = state.context.model_copy(deep=True)
        updated_context.selected_entities = capped
        updated_context.candidate_count_before_dedupe = candidate_count

        final_count = len(capped)

        # Determine ranking scope label for debug
        _dominant_task_scope_rank = state.dominant_task_scope or ""
        _continuity_topic_rank = state.continuity_resolved_topic or ""
        if shopping_task and shopping_task.product_type:
            ranking_scope = f"shopping_task:{shopping_task.product_type}"
            if not _dominant_task_scope_rank:
                _dominant_task_scope_rank = (
                    shopping_task.product_category or shopping_task.product_type
                )
        elif is_context_setting:
            ranking_scope = "context_setting:broad"
        else:
            ranking_scope = f"standard:{intent.domain}"

        # Best entity reason
        strongest_reason = ""
        if capped:
            top = capped[0]
            top_tags = set(top.get("semantic_tags", []))
            if has_child and top_tags & _CHILD_RELIEF_TAGS:
                strongest_reason = f"{top.get('name', '')} selected as child-relief anchor"
            elif top.get("score", 0) >= 0.8:
                strongest_reason = f"{top.get('name', '')} highest score={top.get('score', 0):.2f}"
            else:
                strongest_reason = f"{top.get('name', '')} top-ranked by {ranking_scope}"

        debug = DebugEnrichment(
            candidate_count_before_dedupe=candidate_count,
            deduped_entity_count=deduped_count,
            final_entity_count=final_count,
            ranking_explanations=ranking_explanations,
            dedupe_key_used="entity_id|canonical_name(normalized)|name_clash_check",
            duplicate_entities_collapsed=collapsed_count,
            canonical_name_normalization_notes=normalization_notes[:10],
            ranking_scope=ranking_scope,
            suppressed_off_topic_count=suppressed_off_topic,
            strongest_surviving_entity_reason=strongest_reason,
            dominant_task_scope=_dominant_task_scope_rank,
            continuity_resolved_topic=_continuity_topic_rank,
            shopping_task_active=not _skip_child_relief and bool(
                shopping_task and shopping_task.product_type
            ),
        )

        return {
            "context": updated_context,
            "debug_enrichment": debug,
            "_trace_summary": (
                f"Rank/Dedupe: {candidate_count} candidates → "
                f"{deduped_count} deduped ({collapsed_count} collapsed) → "
                f"{final_count} final [cap={entity_cap}, strategy={strategy}]"
            ),
        }

    except Exception as exc:
        logger.warning("rank_and_dedupe failed, preserving original order: %s", exc)
        return {
            "debug_enrichment": DebugEnrichment(
                candidate_count_before_dedupe=candidate_count,
                final_entity_count=len(entities),
                ranking_explanations=[f"Ranking skipped due to error: {exc}"],
            ),
            "_trace_warnings": [f"rank_and_dedupe error: {exc}"],
        }


# Entity types to suppress / boost for shopping tasks
_SUPPRESS_ENTITY_TYPES_SHOPPING: frozenset[str] = frozenset({
    "dining", "restaurant", "cafe", "coffee", "dessert", "food",
    "bakery", "quick_service", "fast_food",
})

# Expanded suppression for apparel / clothing / outerwear tasks.
# Perfumes, jewelry, beauty, home, electronics are irrelevant to a jacket search.
_SUPPRESS_ENTITY_TYPES_APPAREL: frozenset[str] = frozenset({
    "dining", "restaurant", "cafe", "coffee", "dessert", "food",
    "bakery", "quick_service", "fast_food",
    "perfume", "fragrance", "beauty", "cosmetics",
    "jewelry", "watches", "watch",
    "home", "home_decor", "furniture",
    "electronics", "digital", "tech",
    "gift",
})

# Product categories that are apparel-oriented (warrant stricter ranking suppression)
_APPAREL_RANK_CATEGORIES: frozenset[str] = frozenset({
    "outerwear", "kids_outerwear",
    "footwear", "kids_footwear",
    "womenswear", "menswear",
    "topwear", "bottomwear",
    "kids_fashion", "kids_womenswear", "kids_menswear",
    "kids_topwear", "kids_bottomwear",
    "modest_fashion", "sportswear",
    "accessories", "kids_accessories",
})

# Product categories that are too broad for strict task-scoped ranking
_BROAD_TASK_CATEGORIES: frozenset[str] = frozenset({
    "gifts", "fashion", "", "all_stores",
})

_KIDS_BOOST_TAGS: frozenset[str] = frozenset({
    "kid_friendly", "kids", "children", "family_friendly",
    "has_kids_menu", "family_dining", "kids_entertainment",
})


def _apply_shopping_task_score_adjustment(
    score: float,
    entity: dict[str, Any],
    task,  # ShoppingTask
) -> tuple[float, bool]:
    """
    Adjust score based on active shopping task relevance.

    Returns (adjusted_score, was_suppressed).
    Suppression is a strong negative adjustment, not removal — the entity
    can still survive if there are very few alternatives.

    For apparel/clothing categories, suppression extends to perfume, jewelry,
    beauty, home, electronics — not just dining.
    """
    entity_type = entity.get("entity_type", "").lower()
    entity_tags = set(entity.get("semantic_tags", []))
    cat = (task.product_category or "").lower()

    # Determine which suppression set to use based on task category
    suppress_set = (
        _SUPPRESS_ENTITY_TYPES_APPAREL
        if cat in _APPAREL_RANK_CATEGORIES
        else _SUPPRESS_ENTITY_TYPES_SHOPPING
    )

    # Suppress off-topic entities from product shopping shortlists
    if entity_type in suppress_set:
        return max(0.0, score - 0.40), True

    # Boost entities that match kids category
    if cat.startswith("kids_") or (task.target_age is not None and task.target_age < 14):
        if entity_type in ("store", "shop") and entity_tags & _KIDS_BOOST_TAGS:
            score = min(1.0, score + 0.20)
        elif entity_type in ("store", "shop") and (
            "fashion" in entity_tags
            or "clothing" in entity_tags
            or "kids" in entity_tags
        ):
            score = min(1.0, score + 0.10)

    # Boost entities that match budget preference
    budget = (task.budget_preference or "").lower()
    if budget in ("affordable", "budget"):
        if "value_shopping" in entity_tags or "budget" in entity_tags or "mid_range" in entity_tags:
            score = min(1.0, score + 0.10)
        if "luxury" in entity_tags or "premium" in entity_tags:
            score = max(0.0, score - 0.10)
    elif budget == "premium":
        if "luxury" in entity_tags or "premium" in entity_tags:
            score = min(1.0, score + 0.10)

    return score, False


def _score_entity(
    entity: dict[str, Any],
    semantic_signals: set[str],
    audience_set: set[str],
    constraint_set: set[str],
    playbook_biases: dict[str, float],
    intent_domain: str,
    intent_sub: str,
) -> float:
    """
    Score a single entity using the multi-factor formula.

    Returns a float in [0, 1].
    """
    entity_tags = set(entity.get("semantic_tags", []))
    entity_audience = set(entity.get("audience_fit", []))
    entity_vibe = set(entity.get("vibe", []) if isinstance(entity.get("vibe"), list) else [entity.get("vibe", "")])
    existing_score = float(entity.get("score", 0.5))

    # ── Intent fit ────────────────────────────────────────────────────
    intent_fit = 0.5  # base
    entity_type = entity.get("entity_type", "").lower()
    _DOMAIN_TYPE_FIT: dict[str, frozenset] = {
        "dining": frozenset({"dining", "restaurant", "cafe", "coffee", "dessert", "food", "bakery"}),
        "entertainment": frozenset({"cinema", "movie", "entertainment", "activity", "attraction"}),
        "shopping": frozenset({"store", "shop", "boutique", "kiosk"}),
        "services": frozenset({"service", "facility", "amenity"}),
    }
    type_set = _DOMAIN_TYPE_FIT.get(intent_domain, frozenset())
    if entity_type in type_set:
        intent_fit = 1.0
    elif intent_domain in ("exploration", "mall_info", "general"):
        intent_fit = 0.7

    # ── Semantic overlap ─────────────────────────────────────────────
    if semantic_signals and entity_tags:
        overlap_count = len(entity_tags & semantic_signals)
        semantic_score = min(1.0, overlap_count / max(len(semantic_signals) * 0.5, 1))
    else:
        semantic_score = 0.3

    # ── Audience fit ─────────────────────────────────────────────────
    if audience_set and entity_audience:
        aud_overlap = len(entity_audience & audience_set)
        audience_fit = min(1.0, aud_overlap / max(len(audience_set), 1))
    else:
        audience_fit = 0.4

    # ── Playbook score ────────────────────────────────────────────────
    playbook_score = 0.4  # default
    if playbook_biases and entity_tags:
        matched_biases = [
            weight for tag, weight in playbook_biases.items()
            if tag in entity_tags
        ]
        if matched_biases:
            playbook_score = min(1.0, sum(matched_biases) / len(matched_biases))

    # ── Constraint fit ────────────────────────────────────────────────
    constraint_fit = 0.5  # neutral
    if constraint_set:
        _CONSTRAINT_TAG_MAP: dict[str, frozenset] = {
            "quick": frozenset({"quick_stop", "quick_bite", "low_commitment", "fast"}),
            "quick_stop_preferred": frozenset({"quick_stop", "quick_bite", "low_commitment"}),
            "near_cinema_preferred": frozenset({"near_cinema", "entertainment_anchor"}),
            "kid_friendly_required": frozenset({"kid_friendly", "family_friendly", "child_activity"}),
            "budget_sensitive": frozenset({"budget", "value_shopping", "mid_range"}),
            "time_sensitive": frozenset({"quick_stop", "time_sensitive", "low_commitment"}),
            "light": frozenset({"quick_bite", "light"}),
        }
        constraint_scores: list[float] = []
        for constraint in constraint_set:
            desired_tags = _CONSTRAINT_TAG_MAP.get(constraint, frozenset())
            if desired_tags & entity_tags:
                constraint_scores.append(1.0)
            elif desired_tags:
                constraint_scores.append(0.2)
        if constraint_scores:
            constraint_fit = sum(constraint_scores) / len(constraint_scores)

    # ── Diversity score (base — will be penalized later) ─────────────
    diversity_score = 1.0

    # ── Compute weighted final score ─────────────────────────────────
    final = (
        intent_fit * _WEIGHT_INTENT
        + semantic_score * _WEIGHT_SEMANTIC
        + audience_fit * _WEIGHT_AUDIENCE
        + playbook_score * _WEIGHT_PLAYBOOK
        + constraint_fit * _WEIGHT_CONSTRAINT
        + diversity_score * _WEIGHT_DIVERSITY
    )

    # Blend with existing score from upstream (playbook ranking, audience boost)
    final = 0.7 * final + 0.3 * min(existing_score, 1.0)

    return round(final, 4)


def _ensure_child_relief_anchor(
    capped: list[dict[str, Any]],
    all_entities: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """
    Ensure at least one child-relief entity (entertainment/activity) is included.
    If none present in capped, pull the best one from all_entities and inject it.
    """
    has_relief = any(
        e.get("entity_type", "").lower() in _CHILD_RELIEF_TYPES
        or bool(set(e.get("semantic_tags", [])) & _CHILD_RELIEF_TAGS)
        for e in capped
    )
    if has_relief:
        return capped

    # Find best child-relief entity not already in capped
    capped_ids = {e.get("entity_id", "") for e in capped}
    candidates = [
        e for e in all_entities
        if e.get("entity_id", "") not in capped_ids
        and (
            e.get("entity_type", "").lower() in _CHILD_RELIEF_TYPES
            or bool(set(e.get("semantic_tags", [])) & _CHILD_RELIEF_TAGS)
        )
    ]
    if not candidates:
        return capped

    # Pick highest-scored child-relief candidate
    best = max(candidates, key=lambda e: e.get("score", 0))

    # If already at cap, replace the lowest-scoring non-anchor entity
    if len(capped) >= cap:
        # Find lowest scorer that isn't shopping or dining (replace peripheral item)
        replaceable = [
            i for i, e in enumerate(capped)
            if e.get("entity_type", "").lower() not in {"dining", "restaurant"}
        ]
        if replaceable:
            worst_idx = min(
                replaceable,
                key=lambda i: capped[i].get("score", 0),
            )
            result = list(capped)
            result[worst_idx] = best
            return result
        return capped  # Can't replace, keep as is

    return capped + [best]


def _ensure_anchor_type(
    capped: list[dict[str, Any]],
    all_entities: list[dict[str, Any]],
    anchor_type: str,
    cap: int,
) -> list[dict[str, Any]]:
    """Ensure at least one entity of the required anchor type is included."""
    has_anchor = any(
        e.get("entity_type", "").lower() == anchor_type.lower()
        for e in capped
    )
    if has_anchor:
        return capped

    capped_ids = {e.get("entity_id", "") for e in capped}
    candidates = [
        e for e in all_entities
        if e.get("entity_id", "") not in capped_ids
        and e.get("entity_type", "").lower() == anchor_type.lower()
    ]
    if not candidates:
        return capped

    best = max(candidates, key=lambda e: e.get("score", 0))
    if len(capped) >= cap and capped:
        result = list(capped)
        result[-1] = best
        return result
    return capped + [best]


def _build_ranking_explanations(
    top_entities: list[dict[str, Any]],
    semantic_signals: set[str],
    audience_set: set[str],
    playbook_biases: dict[str, float],
    has_child: bool,
) -> list[str]:
    """Generate human-readable explanations for top ranked entities."""
    explanations: list[str] = []

    for entity in top_entities:
        name = entity.get("name", "Unknown")
        etype = entity.get("entity_type", "")
        tags = set(entity.get("semantic_tags", []))
        audience = set(entity.get("audience_fit", []))

        reasons: list[str] = []

        if has_child and (
            etype in _CHILD_RELIEF_TYPES
            or tags & _CHILD_RELIEF_TAGS
        ):
            reasons.append("child-relief anchor")

        matching_signals = tags & semantic_signals
        if matching_signals:
            reasons.append(f"matches signals: {', '.join(list(matching_signals)[:3])}")

        matching_audience = audience & audience_set
        if matching_audience:
            reasons.append(f"audience fit: {', '.join(list(matching_audience)[:2])}")

        matching_biases = [
            tag for tag in playbook_biases if tag in tags
        ]
        if matching_biases:
            reasons.append(f"playbook boost: {', '.join(matching_biases[:2])}")

        if reasons:
            explanations.append(f"{name} ({etype}): {'; '.join(reasons)}")
        else:
            score = entity.get("score", 0)
            explanations.append(f"{name} ({etype}): score={score:.2f}")

    return explanations
