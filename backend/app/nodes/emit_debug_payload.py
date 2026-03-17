"""
Emit Debug Payload node — compiles the observability payload for the turn.

CONTRACT
────────
  Purpose:  Aggregate trace data, compute total latency, build the debug
            summary for the internal UI and evaluator stubs.
            Now includes rich DebugEnrichment fields from multiple nodes.
            Logs a structured turn summary for observability.
  Reads:    All state fields (read-only aggregation)
  Writes:   response_debug_summary, evaluator_stub, latency_by_node
  Failure:  Non-critical — always succeeds
  Routing:  → END
"""

from __future__ import annotations

from app.models.state import ConciergeState
from app.nodes._tracing import traced_node
from app.observability.logger import log_turn_summary


@traced_node("emit_debug_payload")
async def emit_debug_payload(state: ConciergeState) -> dict:
    latency_map: dict[str, float] = {}
    for entry in state.node_trace:
        latency_map[entry.node] = entry.latency_ms
    total_ms = sum(latency_map.values())

    de = state.debug_enrichment

    debug_lines = [
        f"turn_id: {state.turn_id}",
        f"flow_type: {state.flow_type or 'concierge'}",
        f"flow_routing_reason: {state.flow_routing_reason or 'n/a'}",
        f"intent: {state.intent.domain}/{state.intent.sub_intent} ({state.intent.message_kind})",
        f"playbook: {state.playbook.selected_playbook or 'none'} (conf={state.playbook.playbook_confidence:.3f})",
        f"strategy: {state.response_plan.chosen_strategy}",
        f"answer_mode: {state.response_plan.answer_mode}",
        f"entity_cap: {state.response_plan.entity_cap}",
        f"must_acknowledge_scene: {state.response_plan.must_acknowledge_scene}",
        f"topics: {state.context.selected_topic_blocks}",
        f"entities: {len(state.context.selected_entities)} (final)",
        f"signals: {state.context.selected_semantic_signals}",
        f"retrieval: {'yes' if state.retrieval.retrieval_needed else 'no'}",
        f"retrieval_priority: {state.retrieval_priority or 'n/a'}",
        f"response_length: {len(state.final_response_text)} chars",
        f"total_latency_ms: {round(total_ms, 2)}",
    ]

    # ── Hybrid intent + domain lock debug lines ───────────────────────
    if state.primary_intent or state.secondary_intents or state.modifiers:
        debug_lines.extend([
            f"primary_intent: {state.primary_intent or 'n/a'}",
            f"secondary_intents: {state.secondary_intents or []}",
            f"modifiers: {state.modifiers or []}",
            f"dominant_context_type: {state.dominant_context_type or 'n/a'}",
        ])
    # Domain lock + response strategy
    debug_lines.extend([
        f"domain_locked: {state.domain_locked}",
        f"filter_applied: {state.filter_applied}",
        f"response_strategy: {state.response_strategy or 'n/a'}",
    ])
    if state.scene.active_primary_intent:
        debug_lines.append(f"memory.active_primary_intent: {state.scene.active_primary_intent}")
    if state.scene.active_secondary_filters:
        debug_lines.append(f"memory.active_secondary_filters: {state.scene.active_secondary_filters}")
    if state.scene.active_modifiers:
        debug_lines.append(f"memory.active_modifiers: {state.scene.active_modifiers}")

    # ── Factual-flow debug lines ──────────────────────────────────────
    if state.flow_type == "factual":
        debug_lines.extend([
            f"fact_scope: {state.fact_scope or 'n/a'}",
            f"fact_entity_type: {state.fact_entity_type or 'n/a'}",
            f"fact_query_entity: {state.fact_query_entity or 'n/a'}",
            f"fact_response_mode: {state.fact_response_mode or 'n/a'}",
        ])
        if de.playbook_suppressed:
            debug_lines.append(f"playbook_suppressed: {de.playbook_suppressed}")

    # ── Scene summary ─────────────────────────────────────────────────
    debug_lines.append(
        f"scene: companions={state.scene.companions} "
        f"companion_details={state.scene.companion_details} "
        f"visit_type={state.scene.visit_type} "
        f"audience={state.scene.audience} "
        f"goal={state.scene.goal} "
        f"implicit_goal={state.scene.implicit_goal} "
        f"pace={state.scene.pace} "
        f"budget={state.scene.budget} "
        f"constraints={state.scene.visit_constraints}"
    )

    # ── Experience layer debug lines ──────────────────────────────────
    debug_lines.extend([
        f"experience_mode: {de.response_experience_mode or 'n/a'}",
    ])

    # ── Debug enrichment fields ───────────────────────────────────────
    if de.inferred_scene_notes:
        debug_lines.append(f"inferred_scene_notes: {de.inferred_scene_notes}")
    if de.semantic_match_explanations:
        debug_lines.append(f"semantic_match_explanations: {de.semantic_match_explanations}")
    if de.playbook_rejection_reasons:
        debug_lines.append(f"playbook_rejection_reasons: {de.playbook_rejection_reasons}")
    if de.ranking_explanations:
        debug_lines.append(f"ranking_explanations: {de.ranking_explanations}")

    debug_lines.append(f"candidate_count_before_dedupe: {de.candidate_count_before_dedupe}")
    debug_lines.append(f"deduped_entity_count: {de.deduped_entity_count}")
    debug_lines.append(f"final_entity_count: {de.final_entity_count or len(state.context.selected_entities)}")

    if de.response_strategy_reason:
        debug_lines.append(f"response_strategy_reason: {de.response_strategy_reason}")
    if de.last_refinement_applied:
        debug_lines.append(f"last_refinement_applied: {de.last_refinement_applied}")

    if state.warnings:
        debug_lines.append(f"warnings: {state.warnings}")

    # ── Structured evaluator record ───────────────────────────────────
    evaluator = {
        "turn_id": state.turn_id,
        "session_id": state.session_id,
        "mall_id": state.mall_id,
        # Dual-flow routing
        "flow_type": state.flow_type or "concierge",
        "flow_routing_reason": state.flow_routing_reason or "",
        "retrieval_priority": state.retrieval_priority or "",
        # ── Experience Layer fields ───────────────────────────────────
        "response_experience_mode": de.response_experience_mode or "",
        # Domain lock + response strategy (hybrid-intent system)
        "active_domain": state.scene.active_topic or state.intent.domain or "",
        "primary_intent": state.primary_intent or "",
        "secondary_filters": state.secondary_intents or [],
        "flow_type_resolved": state.flow_type or "concierge",
        "response_strategy": state.response_strategy or "",
        "domain_locked": state.domain_locked,
        "filter_applied": state.filter_applied,
        # Factual-flow fields
        "fact_scope": state.fact_scope or "",
        "fact_entity_type": state.fact_entity_type or "",
        "fact_query_entity": state.fact_query_entity or "",
        "fact_response_mode": state.fact_response_mode or "",
        "playbook_suppressed": de.playbook_suppressed,
        # Full hybrid intent bundle
        "secondary_intents": state.secondary_intents or [],
        "modifiers": state.modifiers or [],
        "dominant_context_type": state.dominant_context_type or "",
        # Intent
        "intent_domain": state.intent.domain,
        "intent_sub": state.intent.sub_intent,
        "intent_confidence": state.intent.confidence,
        "message_kind": state.intent.message_kind,
        "flow_type_candidate": state.intent.flow_type_candidate or "",
        # Playbook
        "playbook_used": state.playbook.selected_playbook,
        "playbook_confidence": state.playbook.playbook_confidence,
        # Strategy
        "strategy_used": state.response_plan.chosen_strategy,
        "answer_mode": state.response_plan.answer_mode,
        "entity_cap": state.response_plan.entity_cap,
        "must_acknowledge_scene": state.response_plan.must_acknowledge_scene,
        # Retrieval
        "retrieval_used": state.retrieval.retrieval_needed,
        "topics_count": len(state.context.selected_topic_blocks),
        "entities_count": len(state.context.selected_entities),
        "signals_count": len(state.context.selected_semantic_signals),
        "response_length": len(state.final_response_text),
        "total_latency_ms": round(total_ms, 2),
        "node_count": len(state.node_trace),
        "warning_count": len(state.warnings),
        # Rich scene summary
        "scene_summary": {
            "companions": state.scene.companions,
            "companion_details": state.scene.companion_details,
            "visit_type": state.scene.visit_type,
            "occasion": state.scene.occasion,
            "budget": state.scene.budget,
            "pace": state.scene.pace,
            "goal": state.scene.goal,
            "implicit_goal": state.scene.implicit_goal,
            "visit_constraints": state.scene.visit_constraints,
            "audience": state.scene.audience,
            "active_topic": state.scene.active_topic,
            "active_shortlist": state.scene.active_shortlist,
            # Hybrid follow-up memory (domain lock state)
            "active_primary_intent": state.scene.active_primary_intent,
            "active_secondary_filters": state.scene.active_secondary_filters,
            "active_modifiers": state.scene.active_modifiers,
            # Domain lock debug
            "domain_locked": state.domain_locked,
            "filter_applied": state.filter_applied,
            "response_strategy": state.response_strategy or "",
        },
        # Debug enrichment
        "inferred_scene_notes": de.inferred_scene_notes,
        "semantic_match_explanations": de.semantic_match_explanations,
        "ranking_explanations": de.ranking_explanations,
        "playbook_rejection_reasons": de.playbook_rejection_reasons,
        "candidate_count_before_dedupe": de.candidate_count_before_dedupe,
        "deduped_entity_count": de.deduped_entity_count,
        "final_entity_count": de.final_entity_count or len(state.context.selected_entities),
        "response_strategy_reason": de.response_strategy_reason,
        "last_refinement_applied": de.last_refinement_applied,
        # Session continuity
        "last_successful_playbook": state.last_successful_playbook,
        "last_response_shape": state.last_response_shape,
        "preferred_categories": state.preferred_categories,
    }

    try:
        log_turn_summary(
            session_id=state.session_id,
            turn_id=state.turn_id,
            intent_domain=state.intent.domain,
            strategy=state.response_plan.chosen_strategy,
            playbook=state.playbook.selected_playbook,
            latency_ms=total_ms,
            warning_count=len(state.warnings),
        )
    except Exception:
        pass

    return {
        "response_debug_summary": "\n".join(debug_lines),
        "latency_by_node": latency_map,
        "evaluator_stub": evaluator,
        "_trace_summary": (
            f"Debug emitted — {round(total_ms, 2)}ms total "
            f"across {len(latency_map)} nodes | "
            f"flow={state.flow_type or 'concierge'} "
            f"scene_notes={len(de.inferred_scene_notes)} "
            f"ranking_exp={len(de.ranking_explanations)}"
        ),
    }
