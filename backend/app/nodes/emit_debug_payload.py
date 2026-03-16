"""
Emit Debug Payload node — compiles the observability payload for the turn.

CONTRACT
────────
  Purpose:  Aggregate trace data, compute total latency, build the debug
            summary for the internal UI and evaluator stubs.
            Include new debug fields:
            - continuity_anchor
            - thread_preservation_decision
            - continuity_resolution
            - expected_playbook_candidates
            - shortlist_reason_map
            - response_contract_used
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

    cr = state.continuity_resolution
    debug_lines = [
        f"turn_id: {state.turn_id}",
        f"intent: {state.intent.domain}/{state.intent.sub_intent} ({state.intent.message_kind})",
        f"continuity: {cr.continuity_type} → {cr.thread_action} "
        f"(conf={cr.confidence:.2f}, {cr.reason})",
        f"playbook: {state.playbook.selected_playbook or 'none'} "
        f"(conf={state.playbook.playbook_confidence})",
        f"strategy: {state.response_plan.chosen_strategy}",
        f"topics: {state.context.selected_topic_blocks}",
        f"entities: {len(state.context.selected_entities)}",
        f"signals: {state.context.selected_semantic_signals}",
        f"retrieval: {'yes' if state.retrieval.retrieval_needed else 'no'}",
        f"response_length: {len(state.final_response_text)} chars",
        f"total_latency_ms: {round(total_ms, 2)}",
    ]

    # Shortlist reasons summary
    if state.candidate_reason_map:
        reason_names = [r.entity_name for r in state.candidate_reason_map if r.entity_name]
        debug_lines.append(f"shortlist_reasons: {reason_names}")

    # Playbook candidates summary
    if state.playbook.expected_playbook_candidates:
        top_candidates = [
            f"{c['playbook_id']}({c['score']:.2f})"
            for c in state.playbook.expected_playbook_candidates[:3]
        ]
        debug_lines.append(f"playbook_candidates: {', '.join(top_candidates)}")

    if state.warnings:
        debug_lines.append(f"warnings: {state.warnings}")

    # Continuity anchor snapshot
    anchor = state.continuity_anchor
    anchor_snapshot = {
        "domain": anchor.domain,
        "topic": anchor.topic,
        "subtopic": anchor.subtopic,
        "audience": anchor.audience,
        "budget": anchor.budget,
        "is_strong": anchor.is_strong,
        "last_playbook": anchor.last_playbook,
        "last_strategy": anchor.last_strategy,
        "last_successful_shortlist": anchor.last_successful_shortlist[:5],
    }

    evaluator = {
        "turn_id": state.turn_id,
        "session_id": state.session_id,
        "mall_id": state.mall_id,
        "intent_domain": state.intent.domain,
        "intent_sub": state.intent.sub_intent,
        "intent_confidence": state.intent.confidence,
        "message_kind": state.intent.message_kind,
        "continuity_type": cr.continuity_type,
        "continuity_action": cr.thread_action,
        "continuity_confidence": cr.confidence,
        "continuity_reason": cr.reason,
        "playbook_used": state.playbook.selected_playbook,
        "playbook_confidence": state.playbook.playbook_confidence,
        "expected_playbook_candidates": state.playbook.expected_playbook_candidates[:5],
        "strategy_used": state.response_plan.chosen_strategy,
        "response_contract_used": state.response_policy_profile.style_family,
        "retrieval_used": state.retrieval.retrieval_needed,
        "topics_count": len(state.context.selected_topic_blocks),
        "entities_count": len(state.context.selected_entities),
        "signals_count": len(state.context.selected_semantic_signals),
        "response_length": len(state.final_response_text),
        "total_latency_ms": round(total_ms, 2),
        "node_count": len(state.node_trace),
        "warning_count": len(state.warnings),
        "continuity_anchor": anchor_snapshot,
        "thread_preservation_decision": {
            "preserve": state.thread_preservation.preserve_current_topic,
            "reason": state.thread_preservation.reason,
        },
        "shortlist_reason_map": [
            {
                "entity_name": r.entity_name,
                "reason_type": r.chosen_reason_type,
                "reason": r.one_line_reason,
            }
            for r in state.candidate_reason_map
        ],
        "scene_summary": {
            "companions": state.scene.companions,
            "occasion": state.scene.occasion,
            "budget": state.scene.budget,
            "active_topic": state.scene.active_topic,
            "active_shortlist": state.scene.active_shortlist,
            "audience": state.scene.audience,
        },
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
            f"continuity={cr.continuity_type}"
        ),
    }
