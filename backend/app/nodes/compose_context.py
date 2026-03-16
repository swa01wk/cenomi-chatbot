"""
Compose Context node — assembles the LLM context payload for generation.

CONTRACT
────────
  Purpose:  Select and rank topic blocks, entities, and semantic signals
            based on intent, scene, playbook, and strategy.
            Delegate final shortlist ranking to ShortlistRanker which:
            - scores entities on audience/budget/scenario/continuity fit
            - generates candidate_reason_map (one-line reasons per entity)
            - generates narrowing_followup_opportunity
            - applies cross-domain penalty
            Inject price_expectation metadata for price-aware responses.
            Uses the MallContextLoader to pull real mall intelligence.
  Reads:    intent, scene, playbook, response_plan, response_contract,
            continuity_anchor, continuity_resolution, active_tenant_parameters
  Writes:   context (ContextComposition), candidate_reason_map,
            narrowing_followup
  Failure:  Missing data → degrade gracefully with whatever is available
  Routing:  Always → decide_retrieval
"""

from __future__ import annotations

from typing import Any

from app.models.state import (
    ConciergeState,
    ContextComposition,
)
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context
from app.services.shortlist_ranker import ShortlistRanker
from app.services.tenant_runtime import TenantRuntime

_shortlist_ranker = ShortlistRanker()


@traced_node("compose_context")
async def compose_context(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene
    playbook = state.playbook
    anchor = state.continuity_anchor
    continuity_res = state.continuity_resolution

    mall_ctx = get_mall_context()
    warnings: list[str] = []

    # ── Topic blocks ──────────────────────────────────────────────────
    if intent.domain == "exploration":
        topic_names = _exploration_topic_blocks(mall_ctx)
    else:
        topic_blocks = mall_ctx.get_topic_blocks(
            f"{intent.domain} {intent.sub_intent} {scene.current_need}"
        )
        topic_names = [b.topic for b in topic_blocks]
        if not topic_names:
            topic_names = ["dining", "services"]
            warnings.append("No topic blocks matched — using defaults")

    # ── Semantic signals from scene ───────────────────────────────────
    signals: list[str] = list(scene.audience)
    if scene.occasion:
        signals.append(scene.occasion)
    if scene.budget:
        signals.append(scene.budget)
    if scene.companions:
        signals.extend(scene.companions)
    if anchor.audience:
        if anchor.audience not in signals:
            signals.append(anchor.audience)

    # ── Entity selection via playbook ranking ─────────────────────────
    entities: list[dict[str, Any]] = []
    playbook_obj = None

    if playbook.selected_playbook:
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
                        enriched["price_band"] = profile.price_band
                        enriched["concierge_reasons"] = profile.concierge_reasons.model_dump()
                        enriched["price_expectation"] = profile.price_expectation.model_dump()
                        signals.extend(
                            t for t in profile.semantic_tags if t not in signals
                        )
                    _add_location_info(entity_data, enriched)
                    entities.append(enriched)

    # ── Fallback: exploration or domain-based entity lookup ───────────
    if not entities:
        if intent.domain == "exploration":
            entities = _exploration_entity_selection(scene, mall_ctx)
        else:
            entities = _domain_entity_lookup(intent.domain, scene, mall_ctx)

    # ── Apply tenant ranking biases (pre-shortlist) ───────────────────
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

    # ── Detect if this is a price question ────────────────────────────
    is_price_question = _is_price_question(state.normalized_user_message)
    if is_price_question:
        entities = _enrich_price_context(entities, anchor)

    # ── Shortlist Ranker: final selection with reasons + narrowing ────
    shortlist_entities, reason_map, narrowing = _shortlist_ranker.rank(
        entities, state,
    )

    # ── Ranking notes ─────────────────────────────────────────────────
    notes: list[str] = []
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
    if anchor.is_strong:
        notes.append(
            f"Continuity anchor: {anchor.domain}/{anchor.topic} "
            f"(audience={anchor.audience}, budget={anchor.budget})"
        )
    if continuity_res.continuity_type == "refinement":
        notes.append(
            f"Refinement active — dimensions: {continuity_res.refinement_dimensions}"
        )
    if is_price_question:
        notes.append("Price question detected — price expectation metadata injected")

    composition = ContextComposition(
        selected_topic_blocks=topic_names,
        selected_entities=shortlist_entities,
        selected_semantic_signals=list(dict.fromkeys(signals)),
        ranking_notes=notes,
    )

    result: dict[str, Any] = {
        "context": composition,
        "candidate_reason_map": reason_map,
        "narrowing_followup": narrowing,
        "_trace_summary": (
            f"Context: {len(topic_names)} topics, "
            f"{len(signals)} signals, {len(shortlist_entities)} entities, "
            f"{len(reason_map)} reasons, "
            f"narrowing={'yes' if narrowing.is_useful else 'no'}, "
            f"price_q={'yes' if is_price_question else 'no'}"
        ),
    }
    if warnings:
        result["_trace_warnings"] = warnings
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Price question detection and enrichment (Part 5)
# ═══════════════════════════════════════════════════════════════════════════

_PRICE_QUESTION_CUES = (
    "price", "how much", "cost", "pricing", "expensive", "cheap",
    "afford", "budget", "under", "less than", "what does it cost",
    "how expensive", "price range", "is it expensive",
)


def _is_price_question(message: str) -> bool:
    lower = message.lower()
    return any(cue in lower for cue in _PRICE_QUESTION_CUES)


def _enrich_price_context(
    entities: list[dict[str, Any]],
    anchor: Any,
) -> list[dict[str, Any]]:
    """
    When the user asks about price, enrich entities with prominent
    price positioning data so the LLM can use it in the response.

    Rules:
    - Do not ask for clarification if the continuity anchor is strong.
    - Use rough price positioning from mall intelligence.
    - Mark that exact prices are unavailable.
    """
    for entity in entities:
        price_exp = entity.get("price_expectation", {})
        if not isinstance(price_exp, dict):
            price_exp = {}

        # Ensure the price expectation has the marker for non-live data
        if not price_exp.get("price_confidence_mode"):
            price_exp["price_confidence_mode"] = "range_only"

        # If no rough positioning, infer from tags
        if not price_exp.get("rough_price_positioning"):
            tags = entity.get("semantic_tags", [])
            price_band = entity.get("price_band", "")
            name = entity.get("name", "")
            if "budget" in tags or price_band == "budget":
                price_exp["rough_price_positioning"] = f"{name} is generally budget-friendly."
            elif "premium" in tags or price_band in ("premium", "luxury"):
                price_exp["rough_price_positioning"] = f"{name} is on the higher end."
            elif price_band == "mid_range":
                price_exp["rough_price_positioning"] = f"{name} is in the mid-range."
            else:
                price_exp["rough_price_positioning"] = (
                    f"{name} varies — check in-store for current prices."
                )

        entity["price_expectation"] = price_exp
        entity["_price_enriched"] = True

    return entities


# ═══════════════════════════════════════════════════════════════════════════
# Topic block helpers
# ═══════════════════════════════════════════════════════════════════════════


def _exploration_topic_blocks(mall_ctx) -> list[str]:
    """For exploration queries, include all major topic blocks."""
    all_blocks = ["dining", "gift", "movie", "family", "services"]
    available = []
    for name in all_blocks:
        block = mall_ctx.get_topic_block(name)
        if block:
            available.append(name)
    return available or ["dining", "services"]


def _exploration_entity_selection(scene, mall_ctx) -> list[dict]:
    """
    For vague/exploration queries, select a diverse mix of the mall's
    highlights across shopping, dining, entertainment, and services.
    """
    entities: list[dict] = []
    seen_ids: set[str] = set()

    category_blocks = [
        ("dining", 3),
        ("gift", 2),
        ("movie", 2),
        ("family", 1),
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
                enriched["price_band"] = profile.price_band
                enriched["concierge_reasons"] = profile.concierge_reasons.model_dump()
                enriched["price_expectation"] = profile.price_expectation.model_dump()
            _add_location_info(entity_data, enriched)
            block_entities.append((score, enriched))

        block_entities.sort(key=lambda x: x[0], reverse=True)
        for _, enriched in block_entities[:max_entities]:
            seen_ids.add(enriched["entity_id"])
            entities.append(enriched)

    return entities[:10]


def _add_location_info(entity_data: dict, enriched: dict) -> None:
    loc = entity_data.get("location")
    if isinstance(loc, dict):
        enriched["floor"] = loc.get("floor", "")
        enriched["zone"] = loc.get("zone", "")
        enriched["directions_hint"] = loc.get("directions_hint", "")


def _domain_entity_lookup(domain: str, scene, mall_ctx) -> list[dict]:
    """Fallback entity selection when no playbook matched."""
    entities: list[dict] = []
    all_topic_blocks = mall_ctx.get_topic_blocks(domain)

    for block in all_topic_blocks:
        for entity_ref in block.entities[:5]:
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
                    enriched["price_band"] = profile.price_band
                    enriched["concierge_reasons"] = profile.concierge_reasons.model_dump()
                    enriched["price_expectation"] = profile.price_expectation.model_dump()
                _add_location_info(entity_data, enriched)
                entities.append(enriched)

    return entities[:8]
