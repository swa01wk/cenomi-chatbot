"""
Compose Context node — assembles the LLM context payload for generation.

CONTRACT
────────
  Purpose:  Select and rank topic blocks, entities, and semantic signals
            based on intent, scene, playbook, and strategy.
            Uses the MallContextLoader to pull real mall intelligence.

            For category-based queries (e.g. "What cafes are available?",
            "What beauty stores are in the mall?"), uses deterministic
            category retrieval to return ALL matching tenants rather than
            relying on topic-block sampling.
  Reads:    intent, scene, playbook, response_plan, active_tenant_parameters
  Writes:   context (ContextComposition)
  Failure:  Missing data → degrade gracefully with whatever is available
  Routing:  Always → decide_retrieval
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.state import ConciergeState, ContextComposition, DebugEnrichment
from app.nodes._tracing import traced_node
from app.retrieval.retriever import (
    build_scene_prefix,
    detect_category_from_intent,
    detect_category_from_query,
    get_related_categories,
)
from app.runtime import get_mall_context, get_vector_store, search_brand_across_malls
from app.services.playbook_engine import PlaybookEngine
from app.services.tenant_runtime import TenantRuntime

logger = logging.getLogger(__name__)


# ── Cross-mall trigger phrase patterns ────────────────────────────────────────
# These are stripped from the user message to isolate the brand/store name.
_CROSS_MALL_STRIP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"which (?:of (?:your|the) |cenomi )?malls? (?:has|have|carries|carry|offer[s]?)\s+", re.I),
    re.compile(r"do(?:es)? (?:any|other) (?:of (?:your|the) |cenomi )?malls? (?:have|carry|offer|has)\s+", re.I),
    re.compile(r"(?:in|at) (?:both|all|other) (?:your )?malls?", re.I),
    re.compile(r"across (?:all |your |the )?malls?", re.I),
    re.compile(r"(?:at|in) any (?:cenomi |other )?mall", re.I),
    re.compile(r"(?:also|too) (?:have|carry|offer|available)", re.I),
    re.compile(r"(?:other|the other) (?:cenomi )?malls?\s*(?:also|too)?\s*(?:have|has|carry|offer)?\s*", re.I),
    re.compile(r"does (?:the other|any other|mall of arabia|al nakheel|al nakheel plaza)\s*(?:also\s*)?have\s*", re.I),
    re.compile(r"also (?:available|found|in|at) (?:other|the other|any) malls?\s*", re.I),
    re.compile(r"is (?:this|it|that) (?:brand|store|shop) (?:also |too )?(?:in|at|available)\s*", re.I),
    re.compile(r"(?:is|are)\s+\w+\s+(?:available\s+)?(?:in|at)\s+", re.I),
    re.compile(r"can i find\s+", re.I),
    re.compile(r"where can i find\s+", re.I),
]


def _extract_brand_query(message: str) -> str:
    """
    Strip cross-mall trigger phrases from the user message, returning the
    brand/store name suitable for a name-based search across all malls.
    """
    result = message
    for pattern in _CROSS_MALL_STRIP_PATTERNS:
        result = pattern.sub(" ", result)
    # Clean up punctuation and filler words
    result = re.sub(r"\b(also|too|any|both|other|all|the|your|cenomi|malls?|please|here)\b", " ", result, flags=re.I)
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip(" ?.,!")


# Sub-intents that are category-level queries and should use structured
# category retrieval rather than topic-block sampling.
_CATEGORY_LOOKUP_INTENTS: set[str] = {
    "general_dining",
    "quick_bite",
    "family_dining",
    "cafe_recommendation",
    "dessert_recommendation",
    "general_shopping",
    "fashion_shopping",
    "gift_recommendation",
    "perfume_shopping",
    "jewelry_shopping",
    "accessories_shopping",
}

# Sub-intents that require injecting offers/events data into context
_OFFER_INTENTS: set[str] = {
    "offer_details",
}


# ── Shopping-task → retrieval category mapping ────────────────────────────
# Maps product_category values (set by update_scene_memory) to entity
# category keys understood by mall_ctx.get_entities_by_category().
_PRODUCT_CATEGORY_TO_ENTITY_CATEGORY: dict[str, str] = {
    "outerwear": "clothing",
    "kids_outerwear": "kids",
    "footwear": "fashion",
    "kids_footwear": "kids",
    "womenswear": "fashion",
    "menswear": "fashion",
    "topwear": "fashion",
    "bottomwear": "fashion",
    "kids_fashion": "kids",
    "kids_womenswear": "kids",
    "kids_menswear": "kids",
    "kids_topwear": "kids",
    "kids_bottomwear": "kids",
    "modest_fashion": "fashion",
    "accessories": "accessories",
    "kids_accessories": "kids",
    "fragrance": "perfume",
    "kids_fragrance": "kids",
    "jewelry": "jewelry",
    "toys": "kids",
    "kids_toys": "kids",
    "gifts": "gift",
    "beauty": "beauty",
    "sportswear": "sportswear",
    "home": "home",
}

# Entity types that must be suppressed when a specific product shopping task is active
_SUPPRESS_ENTITY_TYPES_FOR_SHOPPING: frozenset[str] = frozenset({
    "dining", "restaurant", "cafe", "coffee", "dessert", "food", "bakery",
    "quick_service", "fast_food",
})

# Additional entity types suppressed for apparel/clothing/footwear/accessories tasks.
# Perfumes, jewelry, beauty, home-decor, electronics are irrelevant to a jacket search.
_SUPPRESS_ENTITY_TYPES_FOR_APPAREL: frozenset[str] = frozenset({
    "dining", "restaurant", "cafe", "coffee", "dessert", "food", "bakery",
    "quick_service", "fast_food",
    "perfume", "fragrance", "beauty", "cosmetics",
    "jewelry", "watches", "watch",
    "home", "home_decor", "furniture",
    "electronics", "digital", "tech",
    "gift",  # gift-only stores are not relevant for targeted apparel shopping
})

# Product categories that are apparel/clothing-oriented and warrant stricter suppression
_APPAREL_PRODUCT_CATEGORIES: frozenset[str] = frozenset({
    "outerwear", "kids_outerwear",
    "footwear", "kids_footwear",
    "womenswear", "menswear",
    "topwear", "bottomwear",
    "kids_fashion", "kids_womenswear", "kids_menswear",
    "kids_topwear", "kids_bottomwear",
    "modest_fashion", "sportswear",
    "accessories", "kids_accessories",
})

# Product categories broad enough that we DON'T apply entity suppression
# (they could include gifting contexts where anything might be relevant)
_BROAD_SHOPPING_CATEGORIES: frozenset[str] = frozenset({
    "gifts", "fashion", "", "all_stores",
})

# ── Shopping task → allowed topic blocks ──────────────────────────────────
# When a specific shopping task is active, only topic blocks relevant to that
# task should be opened.  All others are suppressed to prevent drift.
_SHOPPING_TASK_ALLOWED_TOPIC_BLOCKS: dict[str, list[str]] = {
    # Apparel / clothing / outerwear
    "outerwear":         ["shopping", "fashion", "family"],
    "kids_outerwear":    ["shopping", "family", "kidswear"],
    "footwear":          ["shopping", "fashion"],
    "kids_footwear":     ["shopping", "family", "kidswear"],
    "womenswear":        ["shopping", "fashion"],
    "menswear":          ["shopping", "fashion"],
    "topwear":           ["shopping", "fashion"],
    "bottomwear":        ["shopping", "fashion"],
    "kids_fashion":      ["shopping", "family", "kidswear"],
    "kids_womenswear":   ["shopping", "family", "kidswear"],
    "kids_menswear":     ["shopping", "family", "kidswear"],
    "kids_topwear":      ["shopping", "family", "kidswear"],
    "kids_bottomwear":   ["shopping", "family", "kidswear"],
    "modest_fashion":    ["shopping", "fashion"],
    "sportswear":        ["shopping", "fashion"],
    # Accessories / fragrance / beauty
    "accessories":       ["shopping", "fashion", "gift"],
    "kids_accessories":  ["shopping", "family", "kidswear"],
    "fragrance":         ["shopping", "perfume", "gift"],
    "kids_fragrance":    ["shopping", "family", "kidswear"],
    "jewelry":           ["shopping", "jewelry", "gift"],
    "beauty":            ["shopping", "beauty"],
    # Toys / gifts
    "toys":              ["shopping", "family", "kidswear", "gift"],
    "kids_toys":         ["shopping", "family", "kidswear", "gift"],
    "gifts":             ["shopping", "gift"],
    # Home / tech
    "home":              ["shopping", "home"],
}

# Topic blocks that are ALWAYS disallowed for specific product shopping tasks
_TOPIC_BLOCKS_ALWAYS_SUPPRESSED_FOR_SHOPPING: frozenset[str] = frozenset({
    "mall_overview",
    "services_and_facilities",
    "services",
    "cinema_and_movies",
    "movie",
    "entertainment",
})


def _resolve_shopping_task_category(scene) -> str | None:
    """
    If an active shopping_task has a specific product category, return the
    corresponding entity retrieval category key.  Returns None when the task
    is absent or too broad to constrain retrieval.
    """
    task = getattr(scene, "shopping_task", None)
    if not task or not task.product_type:
        return None
    cat = (task.product_category or "").lower()
    if cat in _BROAD_SHOPPING_CATEGORIES:
        return None
    return _PRODUCT_CATEGORY_TO_ENTITY_CATEGORY.get(cat)


async def _vector_search_fallback(
    query: str,
    mall_id: str,
    mall_ctx,
    top_k: int = 8,
) -> list[dict]:
    """
    Vector similarity search fallback for fuzzy / vibe queries.

    Called when no category rule or playbook matched (e.g. "something trendy
    for a date night", "romantic spot for anniversary").

    Returns enriched entity dicts ready for context injection, or [] when
    the vector store is unavailable (graceful degradation — no crash).
    """
    vs = get_vector_store()
    if vs is None or not vs.is_available:
        return []

    raw_results = await vs.search(query, mall_id=mall_id, top_k=top_k)
    if not raw_results:
        return []

    enriched: list[dict] = []
    canonical = mall_ctx._builder._canonical

    for result in raw_results:
        entity_id = result["entity_id"]
        entity_type = result.get("entity_type", "")

        # Hydrate entity object from in-memory canonical dict
        entity_data: dict | None = None
        search_types = [entity_type] if entity_type else ["stores", "dining", "cinemas", "services"]
        for etype in search_types:
            plural = etype if etype.endswith("s") else etype + "s"
            for entity in canonical.get(plural, []):
                if getattr(entity, "entity_id", None) == entity_id:
                    entity_data = entity.model_dump()
                    entity_type = etype
                    break
            if entity_data:
                break

        if not entity_data:
            continue

        profile = mall_ctx.get_semantic_profile(entity_id)
        name = entity_data.get("name") or entity_data.get("title", "")

        item: dict = {
            "entity_id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "category": entity_data.get("category", ""),
            "subcategory": entity_data.get("subcategory", ""),
            "description": entity_data.get("description", ""),
            "score": result["score"],
            "source": "vector",
        }

        loc = entity_data.get("location")
        if isinstance(loc, dict):
            item["floor"] = loc.get("floor", "")
            item["zone"] = loc.get("zone", "")
            item["directions_hint"] = loc.get("directions_hint", "")

        if profile:
            item["concierge_notes"] = profile.concierge_notes
            item["semantic_tags"] = profile.semantic_tags[:8]
            item["audience_fit"] = profile.audience_fit
            item["vibe"] = profile.vibe

        enriched.append(item)

    logger.info(
        "Vector fallback for query=%r mall=%s → %d entities",
        query[:40], mall_id, len(enriched),
    )
    return enriched


def _suppress_dining_for_shopping(entities: list[dict]) -> tuple[list[dict], int]:
    """Remove dining/cafe entities from a shopping task context."""
    kept: list[dict] = []
    suppressed = 0
    for e in entities:
        if e.get("entity_type", "").lower() in _SUPPRESS_ENTITY_TYPES_FOR_SHOPPING:
            suppressed += 1
        else:
            kept.append(e)
    # Never leave fewer than 2 entities after suppression
    return (kept if len(kept) >= 2 else entities), suppressed


def _suppress_off_topic_for_task(
    entities: list[dict], task,
) -> tuple[list[dict], int, list[str]]:
    """
    Remove off-topic entities for a specific product shopping task.

    For apparel/clothing tasks, also removes perfume, jewelry, beauty, home,
    electronics entities — these are irrelevant to a jacket / shoe / kids-wear search.

    Returns (kept_entities, suppressed_count, suppressed_names).
    """
    cat = (task.product_category or "").lower()
    suppress_set = (
        _SUPPRESS_ENTITY_TYPES_FOR_APPAREL
        if cat in _APPAREL_PRODUCT_CATEGORIES
        else _SUPPRESS_ENTITY_TYPES_FOR_SHOPPING
    )
    kept: list[dict] = []
    suppressed = 0
    suppressed_names: list[str] = []
    for e in entities:
        if e.get("entity_type", "").lower() in suppress_set:
            suppressed += 1
            suppressed_names.append(e.get("name", ""))
        else:
            kept.append(e)
    # Never leave fewer than 2 entities after suppression
    if len(kept) < 2:
        return entities, 0, []
    return kept, suppressed, suppressed_names


def _filter_topic_blocks_for_shopping_task(
    topic_names: list[str], task,
) -> tuple[list[str], list[str]]:
    """
    Narrow the candidate topic block list to those relevant for the active
    shopping task.  Returns (allowed_blocks, suppressed_blocks).
    """
    cat = (task.product_category or "").lower()
    allowed_for_cat = _SHOPPING_TASK_ALLOWED_TOPIC_BLOCKS.get(cat)

    suppressed: list[str] = []
    if not allowed_for_cat:
        # Unknown category — only suppress the always-disallowed blocks
        kept = [t for t in topic_names if t not in _TOPIC_BLOCKS_ALWAYS_SUPPRESSED_FOR_SHOPPING]
        suppressed = [t for t in topic_names if t in _TOPIC_BLOCKS_ALWAYS_SUPPRESSED_FOR_SHOPPING]
        return kept, suppressed

    # Keep blocks that are either in the allowed list OR not in the always-suppressed set
    kept = []
    for t in topic_names:
        if t in _TOPIC_BLOCKS_ALWAYS_SUPPRESSED_FOR_SHOPPING:
            suppressed.append(t)
        elif t in allowed_for_cat:
            kept.append(t)
        else:
            # Not in allowed list but also not always-suppressed — suppress it
            suppressed.append(t)

    # Fall back to the full list if filtering removed everything
    if not kept:
        return topic_names, []
    return kept, suppressed


def _is_mall_overview_followup(state: ConciergeState) -> bool:
    """
    True when the current turn is a follow-up within a mall_info/mall_overview context.

    Ensures that "more about the mall" / "what else" / "tell me more" stays
    locked in mall_overview and does NOT reopen shopping/dining entities.
    """
    scene = state.scene
    intent = state.intent
    msg = (state.normalized_user_message or "").lower().strip()

    was_in_overview = (
        scene.active_topic in ("mall_info", "mall_overview")
        or scene.active_primary_intent in ("mall_overview", "mall_info_lookup", "mall_info")
        or scene.topic_lock in ("mall_info", "mall_overview")
    )
    if not was_in_overview:
        return False

    is_followup_kind = intent.message_kind in (
        "followup", "refinement", "constraint_refinement",
    )
    followup_phrases = (
        "more", "what else", "tell me more", "go on", "continue",
        "more about", "more detail", "anything else",
    )
    has_followup_phrase = any(ph in msg for ph in followup_phrases)

    return is_followup_kind or has_followup_phrase


def _is_movie_context_followup(state: ConciergeState) -> bool:
    """
    True when the current turn is a follow-up within a movie/cinema lookup context.

    Ensures that "with kid", "any action?", "any other ones?" stay in the
    cinema/movie context and do NOT switch to dining or family-shopping.
    """
    scene = state.scene
    intent = state.intent

    was_in_movie = (
        scene.active_primary_intent in ("movie_lookup", "cinema_lookup")
        or scene.active_fact_scope in ("movie_schedule", "cinema_lookup")
        or scene.topic_lock in ("entertainment", "movies")
        or scene.active_topic in ("entertainment",)
    )
    if not was_in_movie:
        return False

    if intent.message_kind == "topic_switch":
        return False

    return intent.message_kind in (
        "followup", "refinement", "constraint_refinement",
    )


@traced_node("compose_context")
async def compose_context(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene
    playbook = state.playbook

    # ── Factual flow guard: this node is concierge-only ────────────────
    # Factual flow uses compose_fact_response_context instead.
    # If somehow we end up here in factual mode, return a minimal empty context
    # so downstream nodes don't get semantic pollution.
    if state.flow_type == "factual":
        logger.debug(
            "compose_context called in factual flow — returning minimal context"
        )
        return {
            "context": ContextComposition(
                selected_topic_blocks=[],
                selected_entities=[],
                selected_semantic_signals=[],
                ranking_notes=["factual_flow:compose_context_skipped"],
            ),
            "_trace_summary": "compose_context skipped (factual flow)",
        }

    # ── Cross-mall brand search fast path ─────────────────────────────────────
    # When the user asks about brand presence across Cenomi malls, bypass the
    # single-mall retrieval pipeline entirely and search all loaded contexts.
    # The home mall (state.mall_id) is always sorted first in results so the
    # LLM response naturally reads: "Yes, it's here AND at [other mall]."
    if intent.domain == "cross_mall":
        brand_query = _extract_brand_query(state.normalized_user_message or state.raw_user_message)
        logger.info("Cross-mall search — brand_query=%r home_mall=%s", brand_query, state.mall_id)

        cross_results = search_brand_across_malls(brand_query, home_mall_id=state.mall_id)

        home_count = sum(1 for r in cross_results if r["is_home_mall"])
        other_count = len(cross_results) - home_count
        note = (
            f"cross-mall search for '{brand_query}': "
            f"{home_count} match(es) at home mall ({state.mall_id}), "
            f"{other_count} match(es) at other mall(s)"
        )
        if not cross_results:
            note = f"cross-mall search for '{brand_query}': no matches found in any loaded mall"

        return {
            "context": ContextComposition(
                selected_entities=cross_results,
                ranking_notes=[note],
            ),
            "warnings": list(state.warnings),
        }

    mall_ctx = get_mall_context(state.mall_id)
    warnings: list[str] = []

    # ── Continuity-protection flags ────────────────────────────────────
    _overview_followup = _is_mall_overview_followup(state)
    _movie_followup = _is_movie_context_followup(state)

    # Dominant context debug tracking
    _dominant_context_reason = "standard"
    _candidate_scope = "full"
    _off_topic_suppressed = 0
    _off_topic_names_suppressed: list[str] = []
    _topic_blocks_suppressed: list[str] = []
    _shopping_scope_applied = False
    _overview_followup_preserved = False
    _candidate_scope_reason = ""

    # Continuity resolved topic — inherited from resolve_playbooks if set
    _continuity_resolved_topic = (
        state.continuity_resolved_topic
        or scene.active_topic
        or intent.domain
        or ""
    )
    _dominant_task_scope = state.dominant_task_scope or ""

    # ── Mall overview follow-up: lock to mall_overview only ────────────
    if _overview_followup:
        _overview_followup_preserved = True
        _dominant_context_reason = "mall_overview_followup_locked"
        _candidate_scope = "mall_overview"
        topic_names = ["mall_overview"]
        overview_block = mall_ctx.get_topic_block("mall_overview")
        if not overview_block:
            topic_names = ["services"]
            warnings.append("mall_overview block not available — falling back")
        # Return immediately with mall_overview context only — no shopping entities
        signals: list[str] = list(scene.audience)
        notes: list[str] = [
            "MALL OVERVIEW CONTINUITY: follow-up locked to mall overview context. "
            "Do NOT introduce shopping/dining entities.",
            f"Dominant context: {_dominant_context_reason}",
        ]
        return {
            "context": ContextComposition(
                selected_topic_blocks=topic_names,
                selected_entities=[],
                selected_semantic_signals=signals,
                ranking_notes=notes,
            ),
            "debug_enrichment": DebugEnrichment(
                dominant_context_reason=_dominant_context_reason,
                candidate_scope=_candidate_scope,
                overview_followup_preserved=True,
            ),
            "_trace_summary": "compose_context: mall_overview follow-up locked",
        }

    # ── Movie context follow-up: stay in entertainment/cinema ──────────
    if _movie_followup:
        _dominant_context_reason = "movie_context_followup_locked"
        _candidate_scope = "entertainment"
        topic_names = ["movie", "entertainment"]
        signals = list(scene.audience) + ["movie", "cinema"]
        notes = [
            "MOVIE CONTINUITY: follow-up locked to cinema/movie context. "
            "Do NOT switch to dining or family-shopping.",
            f"Dominant context: {_dominant_context_reason}",
        ]
        if scene.companions:
            notes.append(f"Companions (filter only): {scene.companions}")
        return {
            "context": ContextComposition(
                selected_topic_blocks=topic_names,
                selected_entities=[],
                selected_semantic_signals=signals,
                ranking_notes=notes,
            ),
            "debug_enrichment": DebugEnrichment(
                dominant_context_reason=_dominant_context_reason,
                candidate_scope=_candidate_scope,
            ),
            "_trace_summary": "compose_context: movie follow-up locked",
        }

    # ── Topic blocks (use expanded_query for richer matching) ────────
    retrieval_query = state.expanded_query or state.normalized_user_message

    # Determine if a specific shopping task should narrow topic blocks
    _task_for_topics = getattr(scene, "shopping_task", None)
    _has_specific_task_for_topics = bool(
        _task_for_topics
        and _task_for_topics.product_type
        and (_task_for_topics.product_category or "").lower() not in _BROAD_SHOPPING_CATEGORIES
    )

    if intent.domain == "exploration":
        topic_names = _exploration_topic_blocks(mall_ctx)
    elif intent.domain == "mall_info":
        topic_names = ["mall_overview"]
        overview_block = mall_ctx.get_topic_block("mall_overview")
        if not overview_block:
            warnings.append("mall_overview block not available — falling back")
            topic_names = ["services"]
    else:
        search_terms = f"{intent.domain} {intent.sub_intent} {scene.current_need} {retrieval_query}"
        topic_blocks = mall_ctx.get_topic_blocks(search_terms)
        topic_names = [b.topic for b in topic_blocks]
        if not topic_names:
            topic_names = ["dining", "services"]
            warnings.append("No topic blocks matched — using defaults")

    # ── Shopping task: filter topic blocks to task-relevant ones ──────
    # When a specific product shopping task is active, suppress off-topic
    # topic blocks (gift_shopping, mall_overview, services, etc.).
    if _has_specific_task_for_topics and intent.domain not in ("mall_info", "exploration"):
        topic_names, _topic_blocks_suppressed = _filter_topic_blocks_for_shopping_task(
            topic_names, _task_for_topics,
        )
        if _topic_blocks_suppressed:
            warnings.append(
                f"Shopping task '{_task_for_topics.product_type}': "
                f"suppressed off-topic topic blocks: {_topic_blocks_suppressed}"
            )

    # ── Semantic signals from scene + hybrid modifiers ────────────────
    signals: list[str] = list(scene.audience)
    if scene.occasion:
        signals.append(scene.occasion)
    if scene.budget:
        signals.append(scene.budget)
    if scene.companions:
        signals.extend(scene.companions)
    if scene.target_person:
        signals.append(scene.target_person)
    if scene.visit_type:
        signals.append(scene.visit_type)
    # Inject hybrid intent modifiers (kid_friendly, near_cinema, budget_sensitive…)
    for mod in (state.modifiers or []):
        if mod not in signals:
            signals.append(mod)
    # Inject scene active_modifiers from memory (carry-over filters)
    for mod in (scene.active_modifiers or []):
        if mod not in signals:
            signals.append(mod)

    # ── Category-based structured retrieval ────────────────────────────
    # For tenant-lookup queries, bypass topic-block sampling and retrieve
    # ALL entities matching the canonical category.
    # If a shopping_task is active with a specific product category, override
    # the category key to match the task scope (narrower, more precise).
    category_key = _resolve_category_key(state)
    task_category_key = _resolve_shopping_task_category(scene)
    if task_category_key and not category_key:
        # Shopping task scope takes priority over generic query-based detection
        category_key = task_category_key
        _shopping_scope_applied = True
        _dominant_context_reason = f"shopping_task_scoped:{scene.shopping_task.product_category}"
        _candidate_scope = f"shopping_task:{scene.shopping_task.product_type}"
        _candidate_scope_reason = (
            f"active shopping_task product_category="
            f"{scene.shopping_task.product_category}"
        )
        if not _dominant_task_scope:
            _dominant_task_scope = scene.shopping_task.product_category or scene.shopping_task.product_type
    category_entities: list[dict[str, Any]] = []
    discovery_expanded = False
    if category_key:
        category_entities = mall_ctx.get_entities_by_category(category_key)
        if category_entities:
            logger.info(
                "Category retrieval '%s': %d entities for sub_intent=%s",
                category_key, len(category_entities), intent.sub_intent,
            )

        # For short queries with sparse results, expand with related categories
        query_text = state.normalized_user_message or state.raw_user_message
        is_short_query = len(query_text.strip().split()) <= 2
        primary_count = len(category_entities)
        if is_short_query and 0 < primary_count < 3:
            related_keys = get_related_categories(category_key)
            existing_ids = {e["entity_id"] for e in category_entities}
            for rel_key in related_keys:
                supplement = mall_ctx.get_entities_by_category(rel_key)
                for entity in supplement:
                    if entity["entity_id"] not in existing_ids:
                        entity["source"] = f"related/{rel_key}"
                        category_entities.append(entity)
                        existing_ids.add(entity["entity_id"])
                if len(category_entities) >= 6:
                    break
            if len(category_entities) > primary_count:
                discovery_expanded = True
                logger.info(
                    "Discovery expansion for '%s': %d primary + %d related = %d total",
                    category_key, primary_count,
                    len(category_entities) - primary_count,
                    len(category_entities),
                )

    # ── Entity selection via playbook ranking ─────────────────────────
    entities: list[dict[str, Any]] = []
    playbook_obj = None

    if category_entities:
        # Category retrieval takes priority — gives complete, accurate list
        entities = category_entities
    elif playbook.selected_playbook:
        playbook_obj = mall_ctx.match_playbook(
            intent=playbook.selected_playbook,
            context_signals=signals,
        )
        if playbook_obj:
            ranked = mall_ctx.rank_for_playbook(playbook_obj)
            for item in ranked:
                entity_data = mall_ctx.get_entity_by_id(item["entity_id"])
                profile = mall_ctx.get_semantic_profile(item["entity_id"])
                if entity_data:
                    enriched = {
                        "entity_id": item["entity_id"],
                        "name": entity_data.get("name") or entity_data.get("title", ""),
                        "entity_type": item.get("entity_type", ""),
                        "score": item.get("score", 0.0),
                        "matched_tags": item.get("matched_tags", []),
                    }
                    if profile:
                        enriched["concierge_notes"] = profile.concierge_notes
                        enriched["semantic_tags"] = profile.semantic_tags[:8]
                        enriched["audience_fit"] = profile.audience_fit
                        enriched["vibe"] = profile.vibe
                        signals.extend(
                            t for t in profile.semantic_tags if t not in signals
                        )
                    _add_location_info(entity_data, enriched)
                    entities.append(enriched)

    # ── Vector search: fuzzy / vibe queries with no rule or playbook match ───
    # Fires when category retrieval AND playbook ranking both returned nothing.
    # This is the semantic layer: "trendy date night", "romantic anniversary spot",
    # "something for my mum" — queries with no hardcoded category rule.
    # Skipped for exploration and mall_info domains which have their own paths.
    if not entities and not category_key and intent.domain not in ("exploration", "mall_info"):
        vector_query = state.expanded_query or state.normalized_user_message or state.raw_user_message
        scene_prefix = build_scene_prefix(scene)
        if scene_prefix:
            vector_query = f"{scene_prefix} {vector_query}".strip()
        vector_entities = await _vector_search_fallback(
            vector_query,
            state.mall_id,
            mall_ctx,
        )
        if vector_entities:
            entities = vector_entities
            _dominant_context_reason = "vector_search"

    # ── Fallback: exploration or domain-based entity lookup ───────────
    if not entities:
        if intent.domain == "exploration":
            entities = _exploration_entity_selection(scene, mall_ctx)
        else:
            entities = _domain_entity_lookup(intent.domain, scene, mall_ctx)

    # ── Semantic audience-based enrichment ───────────────────────────
    # When the scene has audience signals (kid_friendly, couple_friendly,
    # etc.), supplement entities with semantically-matched results to
    # ensure audience-appropriate recommendations.
    if scene.audience and not category_entities:
        _semantic_audience_tags = _map_audience_to_semantic_tags(scene)
        if _semantic_audience_tags:
            semantic_results = mall_ctx.search_semantic_by_tags(
                tags=_semantic_audience_tags,
                audience_tags=scene.audience,
                top_k=10,
            )
            existing_ids = {e.get("entity_id") for e in entities}
            for sem_entity in semantic_results:
                if sem_entity["entity_id"] not in existing_ids:
                    entities.append(sem_entity)
                    existing_ids.add(sem_entity["entity_id"])
                    signals.extend(
                        t for t in sem_entity.get("semantic_tags", [])
                        if t not in signals
                    )

    # ── Intent-domain entity filtering ───────────────────────────────
    # Applied AFTER semantic enrichment so "romantic" audience tags cannot
    # re-inject store/cinema entities into dining or entertainment responses.
    # Filtering only fires when we used playbook / fallback selection — not
    # when category retrieval already gave us the right typed set.
    if entities and not category_entities:
        entities = _filter_entities_by_intent_domain(entities, intent.domain)

    # ── Shopping task: suppress off-topic entities ────────────────────
    # When a specific product shopping task is active (e.g. "kids jacket"),
    # remove irrelevant entities (dining, perfume, jewelry, beauty, etc.)
    # that were pulled in by broad retrieval OR by semantic audience enrichment.
    #
    # This suppression is ALWAYS applied when a specific task is active —
    # not gated on the retrieval path, so semantic enrichment cannot reintroduce
    # off-topic entities after category retrieval scoped the primary list.
    task = getattr(scene, "shopping_task", None)
    if (
        task
        and task.product_type
        and (task.product_category or "").lower() not in _BROAD_SHOPPING_CATEGORIES
    ):
        entities, _off_topic_suppressed, _off_topic_names_suppressed = (
            _suppress_off_topic_for_task(entities, task)
        )
        if _off_topic_suppressed:
            warnings.append(
                f"Shopping task '{task.product_type}': suppressed "
                f"{_off_topic_suppressed} off-topic entities"
                + (f": {_off_topic_names_suppressed[:5]}" if _off_topic_names_suppressed else "")
            )

    # ── Apply tenant ranking biases ───────────────────────────────────
    if state.active_tenant_parameters and entities:
        try:
            rt = TenantRuntime(state.active_tenant_parameters)
            session_signals = {
                "family": "family_friendly" in scene.audience,
                "kids": "kid_friendly" in scene.audience,
                "romantic": "couple_friendly" in scene.audience,
                "budget": scene.budget == "budget",
                "premium": scene.budget in ("premium", "luxury"),
            }
            entities = rt.apply_ranking_biases(entities, session_signals)
        except Exception:
            warnings.append("Tenant ranking bias application failed")

    # ── Re-rank entities by audience fit when scene has audience context ───
    if scene.audience and entities:
        entities = _boost_audience_fit(entities, scene.audience)

    # ── Inject offer/event data for offer_details queries ────────────
    if intent.sub_intent in _OFFER_INTENTS:
        _inject_offer_context(entities, mall_ctx, state)

    # ── Ranking notes ─────────────────────────────────────────────────
    notes: list[str] = []
    if category_key and category_entities:
        if category_key == "all_stores":
            notes.append(
                f"BROAD SHOPPING DISCOVERY: {len(category_entities)} stores across "
                f"multiple categories. Group them by category (e.g. Fashion, Beauty, "
                f"Electronics, etc.) and present 4-6 curated suggestions covering "
                f"different types. Give a brief description and location for each."
            )
        elif discovery_expanded:
            primary_count = sum(
                1 for e in category_entities
                if not e.get("source", "").startswith("related/")
            )
            notes.append(
                f"EXPANDED DISCOVERY: Primary '{category_key}' ({primary_count} "
                f"direct match(es)) supplemented with related options for a richer "
                f"response. Present the primary matches first, then naturally suggest "
                f"related options (e.g. 'You might also enjoy...'). "
                f"Aim for 4-6 suggestions total."
            )
        else:
            notes.append(
                f"CATEGORY RETRIEVAL: '{category_key}' — {len(category_entities)} entities. "
                f"List ALL of these in your response. Do NOT add entities from other categories."
            )
    if playbook_obj:
        notes.append(
            f"Playbook '{playbook_obj.playbook_id}' active — "
            f"{playbook_obj.concierge_reasoning_notes}"
        )
    if scene.rejected_options:
        notes.append(f"Exclude previously rejected: {scene.rejected_options}")
    if scene.companions:
        notes.append(f"Companions: {scene.companions} — bias audience fit")
    if scene.budget:
        notes.append(f"Budget preference: {scene.budget}")
    # Hybrid intent context — primary goal governs structure; modifiers filter
    if state.primary_intent:
        notes.append(
            f"PRIMARY INTENT: {state.primary_intent} — this is the dominant goal. "
            f"Secondary filters={state.secondary_intents}; modifiers={state.modifiers}. "
            f"Do NOT let secondary filters replace the primary answer."
        )
    if state.dominant_context_type:
        notes.append(f"Dominant context type: {state.dominant_context_type}")

    # Context-setting: signal to LLM to provide broad, multi-direction guidance
    if intent.message_kind == "context_setting":
        notes.append(
            "CONTEXT-SETTING TURN: user is providing situation context, not asking a question. "
            "Acknowledge warmly and offer a few relevant directions. "
            "Do NOT collapse immediately into one narrow recommendation."
        )

    if _shopping_scope_applied:
        notes.append(
            f"SHOPPING TASK SCOPE: product='{task.product_type}' "
            f"category='{task.product_category}'. "
            f"Retrieve only relevant stores — suppress perfumes/beauty/jewelry/"
            f"home/unrelated gifts."
        )

    if _topic_blocks_suppressed:
        notes.append(
            f"TOPIC BLOCK SUPPRESSION: removed off-topic blocks "
            f"{_topic_blocks_suppressed} for shopping task "
            f"'{task.product_type if task else '?'}'."
        )

    if _off_topic_suppressed:
        names_str = ", ".join(_off_topic_names_suppressed[:5])
        notes.append(
            f"Off-topic suppression: {_off_topic_suppressed} entities removed "
            f"from shopping task context"
            + (f" ({names_str})" if names_str else "") + "."
        )

    if _dominant_task_scope:
        notes.append(
            f"DOMINANT TASK SCOPE: {_dominant_task_scope} — "
            f"all retrieval and ranking must stay inside this scope."
        )

    if _continuity_resolved_topic:
        notes.append(f"CONTINUITY TOPIC: {_continuity_resolved_topic}")

    composition = ContextComposition(
        selected_topic_blocks=topic_names,
        selected_entities=entities,
        selected_semantic_signals=list(dict.fromkeys(signals)),
        ranking_notes=notes,
    )

    result: dict[str, Any] = {
        "context": composition,
        "debug_enrichment": DebugEnrichment(
            dominant_context_reason=_dominant_context_reason,
            candidate_scope=_candidate_scope,
            off_topic_entities_suppressed=_off_topic_suppressed,
            off_topic_entity_names_suppressed=_off_topic_names_suppressed[:10],
            shopping_scope_applied=_shopping_scope_applied,
            overview_followup_preserved=_overview_followup_preserved,
            topic_blocks_suppressed=_topic_blocks_suppressed,
            candidate_scope_reason=_candidate_scope_reason,
            dominant_task_scope=_dominant_task_scope,
            continuity_resolved_topic=_continuity_resolved_topic,
            shopping_task_active=bool(
                task and task.product_type
                and (task.product_category or "").lower() not in _BROAD_SHOPPING_CATEGORIES
            ),
        ),
        "dominant_task_scope": _dominant_task_scope,
        "continuity_resolved_topic": _continuity_resolved_topic,
        "_trace_summary": (
            f"Context: {len(topic_names)} topics, "
            f"{len(signals)} signals, {len(entities)} entities"
            + (f" [category={category_key}]" if category_key else "")
            + (f" [shopping_scope={_candidate_scope}]" if _shopping_scope_applied else "")
            + (f" [suppressed_blocks={len(_topic_blocks_suppressed)}]" if _topic_blocks_suppressed else "")
        ),
    }
    if warnings:
        result["_trace_warnings"] = warnings
    return result


def _resolve_category_key(state: ConciergeState) -> str | None:
    """
    Determine if this query should use category-based retrieval.

    Checks the sub_intent first, then falls back to query text analysis.
    Returns a category rule key or None.
    """
    sub_intent = state.intent.sub_intent
    query = state.normalized_user_message or state.raw_user_message

    # Intent-based detection
    if sub_intent in _CATEGORY_LOOKUP_INTENTS:
        # Try query first for more specific matching (e.g. "perfume" in a
        # general_shopping query), then fall back to intent mapping
        from_query = detect_category_from_query(query)
        if from_query:
            return from_query
        from_intent = detect_category_from_intent(sub_intent)
        if from_intent:
            return from_intent

    # Query-based detection for domains that commonly ask for categories
    if state.intent.domain in ("shopping", "dining"):
        return detect_category_from_query(query)

    return None


def _exploration_topic_blocks(mall_ctx) -> list[str]:
    """For exploration queries, include all major topic blocks."""
    all_blocks = ["mall_overview", "dining", "gift", "movie", "family", "services"]
    available = []
    for name in all_blocks:
        block = mall_ctx.get_topic_block(name)
        if block:
            available.append(name)
    return available or ["mall_overview", "dining", "services"]


def _exploration_entity_selection(scene, mall_ctx) -> list[dict]:
    """
    For vague/exploration queries, select a diverse mix of the mall's
    highlights across shopping, dining, entertainment, and services.
    Prioritize entities with high visitor appeal (concierge_notes, semantic tags).
    """
    entities: list[dict] = []
    seen_ids: set[str] = set()

    category_blocks = [
        ("dining", 5),
        ("gift", 4),
        ("movie", 3),
        ("family", 3),
        ("services", 2),
    ]

    for block_name, max_entities in category_blocks:
        block = mall_ctx.get_topic_block(block_name)
        if not block:
            continue

        block_entities: list[tuple[float, dict]] = []
        for entity_ref in block.entities:
            eid = entity_ref.get("entity_id", "")
            if eid in seen_ids:
                continue

            entity_data = mall_ctx.get_entity_by_id(eid)
            profile = mall_ctx.get_semantic_profile(eid)
            if not entity_data:
                continue

            score = 0.5
            if profile:
                if profile.concierge_notes:
                    score += 0.2
                if profile.semantic_tags:
                    score += 0.1
                if any(t in profile.audience_fit for t in ("family_friendly", "solo_friendly")):
                    score += 0.1
                priority_scores = getattr(profile, "priority_scores", {})
                if priority_scores:
                    score += max(priority_scores.values()) * 0.2

            enriched = {
                "entity_id": eid,
                "name": entity_data.get("name") or entity_data.get("title", ""),
                "entity_type": entity_ref.get("type", ""),
                "score": round(score, 2),
            }
            if profile:
                enriched["concierge_notes"] = profile.concierge_notes
                enriched["semantic_tags"] = profile.semantic_tags[:6]
                enriched["audience_fit"] = profile.audience_fit
            _add_location_info(entity_data, enriched)
            block_entities.append((score, enriched))

        block_entities.sort(key=lambda x: x[0], reverse=True)
        for _, enriched in block_entities[:max_entities]:
            seen_ids.add(enriched["entity_id"])
            entities.append(enriched)

    return entities[:16]


def _add_location_info(entity_data: dict, enriched: dict) -> None:
    loc = entity_data.get("location")
    if isinstance(loc, dict):
        enriched["floor"] = loc.get("floor", "")
        enriched["zone"] = loc.get("zone", "")
        enriched["directions_hint"] = loc.get("directions_hint", "")


def _inject_offer_context(
    entities: list[dict], mall_ctx, state: ConciergeState,
) -> None:
    """Pre-load offer and event data into the entity list for offer queries."""
    try:
        canonical = mall_ctx._builder._canonical
        offers = canonical.get("offers", [])
        events = canonical.get("events", [])

        existing_ids = {e.get("entity_id") for e in entities if e.get("entity_id")}

        for offer in offers:
            if offer.entity_id in existing_ids:
                continue
            store_names: list[str] = []
            tenant_ids = getattr(offer, "tenant_entity_ids", [])
            for tid in tenant_ids:
                tenant = mall_ctx.get_entity_by_id(tid)
                if tenant:
                    store_names.append(tenant.get("name", ""))
            store_str = f" at {', '.join(store_names)}" if store_names else ""

            offer_data = {
                "entity_id": offer.entity_id,
                "name": offer.title,
                "entity_type": "offer",
                "description": offer.description,
                "category": "Offer",
                "source": "offer_lookup",
                "semantic_tags": getattr(offer, "tags", []),
                "concierge_notes": (
                    f"{offer.discount_value}{store_str} — "
                    f"valid {offer.valid_from} to {offer.valid_until}. "
                    f"{offer.terms}"
                ),
            }
            existing_ids.add(offer.entity_id)
            entities.append(offer_data)

        for event in events:
            if event.entity_id in existing_ids:
                continue
            event_data = {
                "entity_id": event.entity_id,
                "name": event.title,
                "entity_type": "event",
                "description": event.description,
                "category": "Event",
                "source": "event_lookup",
                "semantic_tags": getattr(event, "tags", []),
                "concierge_notes": (
                    f"Dates: {event.start_date} to {event.end_date}. "
                    f"{'Free' if event.is_free else 'Paid'}."
                ),
            }
            existing_ids.add(event.entity_id)
            entities.append(event_data)
    except Exception as exc:
        logger.warning("Failed to inject offer context: %s", exc)


def _domain_entity_lookup(domain: str, scene, mall_ctx) -> list[dict]:
    """Fallback entity selection when no playbook matched."""
    entities: list[dict] = []
    all_topic_blocks = mall_ctx.get_topic_blocks(domain)

    for block in all_topic_blocks:
        for entity_ref in block.entities[:10]:
            eid = entity_ref.get("entity_id", "")
            entity_data = mall_ctx.get_entity_by_id(eid)
            profile = mall_ctx.get_semantic_profile(eid)
            if entity_data:
                enriched = {
                    "entity_id": eid,
                    "name": entity_data.get("name") or entity_data.get("title", ""),
                    "entity_type": entity_ref.get("type", ""),
                    "score": 0.5,
                }
                if profile:
                    enriched["concierge_notes"] = profile.concierge_notes
                    enriched["semantic_tags"] = profile.semantic_tags[:6]
                    enriched["audience_fit"] = profile.audience_fit
                _add_location_info(entity_data, enriched)
                entities.append(enriched)

    return entities[:14]


def _map_audience_to_semantic_tags(scene) -> list[str]:
    """Map scene audience signals to semantic tag queries."""
    tags: list[str] = []

    audience_tag_map: dict[str, list[str]] = {
        "kid_friendly": ["family_friendly", "kid_friendly", "has_kids_menu", "child_activity"],
        "family_friendly": ["family_friendly", "family_dining", "kids_entertainment"],
        "couple_friendly": ["romantic", "date_spot", "premium", "special_occasion"],
        "solo_friendly": ["solo_friendly", "coffee_spot", "self_treat"],
    }

    for aud_tag in scene.audience:
        if aud_tag in audience_tag_map:
            tags.extend(audience_tag_map[aud_tag])

    target_person_map: dict[str, list[str]] = {
        "child": ["kid_friendly", "has_kids_menu", "family_friendly"],
        "girlfriend": ["romantic", "gift_friendly", "premium"],
        "boyfriend": ["gift_friendly", "premium"],
        "wife": ["romantic", "gift_friendly", "premium", "special_occasion"],
        "husband": ["gift_friendly", "premium"],
    }
    if scene.target_person and scene.target_person in target_person_map:
        tags.extend(target_person_map[scene.target_person])

    return list(dict.fromkeys(tags))


# Entity types that belong to each intent domain
_DOMAIN_ENTITY_TYPES: dict[str, frozenset[str]] = {
    "dining": frozenset({
        "dining", "restaurant", "cafe", "coffee", "dessert",
        "food", "bakery", "fast_food", "quick_service",
    }),
    "entertainment": frozenset({
        "cinema", "movie", "entertainment", "activity", "attraction",
    }),
    "shopping": frozenset({
        "store", "shop", "boutique", "kiosk",
    }),
    "services": frozenset({
        "service", "facility", "amenity",
    }),
}


def _filter_entities_by_intent_domain(
    entities: list[dict], domain: str,
) -> list[dict]:
    """
    Filter the entity list to match the current intent domain.

    For `dining` queries, only dining/café/dessert entities are kept.
    For `entertainment`, only cinema/activity entities.
    For `shopping`, only store entities.

    Exploration, general, mall_info, and navigation queries are NOT
    filtered — they should include a mix of all types.

    Falls back to the full entity list if filtering leaves fewer than 2
    results, so the response is never left empty.
    """
    allowed = _DOMAIN_ENTITY_TYPES.get(domain)
    if not allowed:
        return entities  # No filtering for exploration/general/navigation

    filtered = [e for e in entities if e.get("entity_type", "").lower() in allowed]

    if len(filtered) >= 2:
        logger.debug(
            "Domain filter '%s': %d → %d entities",
            domain, len(entities), len(filtered),
        )
        return filtered

    # Fewer than 2 matches — keep full list so response isn't empty
    return entities


def _boost_audience_fit(
    entities: list[dict], audience_tags: list[str],
) -> list[dict]:
    """Re-score entities by how well they match the scene's audience."""
    audience_set = set(audience_tags)

    for entity in entities:
        entity_audience = set(entity.get("audience_fit", []))
        entity_sem_tags = set(entity.get("semantic_tags", []))

        boost = 0.0
        if entity_audience & audience_set:
            boost += 0.2
        if "kid_friendly" in audience_set and (
            "has_kids_menu" in entity_sem_tags
            or "family_friendly" in entity_sem_tags
            or "kid_friendly" in entity_sem_tags
        ):
            boost += 0.15
        if "couple_friendly" in audience_set and (
            "romantic" in entity_sem_tags
            or "date_spot" in entity_sem_tags
        ):
            boost += 0.15

        entity["score"] = round(entity.get("score", 0.5) + boost, 2)

    entities.sort(key=lambda e: e.get("score", 0), reverse=True)
    return entities
