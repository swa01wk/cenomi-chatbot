"""
Compose Context node — assembles the LLM context payload for generation.

CONTRACT
────────
  Purpose:  Select and rank topic blocks, entities, and semantic signals
            based on intent, scene, playbook, and strategy.
            Uses the MallContextLoader to pull real mall intelligence.
  Reads:    intent, scene, playbook, response_plan, active_tenant_parameters
  Writes:   context (ContextComposition)
  Failure:  Missing data → degrade gracefully with whatever is available
  Routing:  Always → decide_retrieval
"""

from __future__ import annotations

from typing import Any

from app.models.state import ConciergeState, ContextComposition
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context
from app.services.playbook_engine import PlaybookEngine
from app.services.tenant_runtime import TenantRuntime


@traced_node("compose_context")
async def compose_context(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene
    playbook = state.playbook

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

    composition = ContextComposition(
        selected_topic_blocks=topic_names,
        selected_entities=entities,
        selected_semantic_signals=list(dict.fromkeys(signals)),
        ranking_notes=notes,
    )

    result: dict[str, Any] = {
        "context": composition,
        "_trace_summary": (
            f"Context: {len(topic_names)} topics, "
            f"{len(signals)} signals, {len(entities)} entities"
        ),
    }
    if warnings:
        result["_trace_warnings"] = warnings
    return result


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
    Prioritize entities with high visitor appeal (concierge_notes, semantic tags).
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
                _add_location_info(entity_data, enriched)
                entities.append(enriched)

    return entities[:8]
