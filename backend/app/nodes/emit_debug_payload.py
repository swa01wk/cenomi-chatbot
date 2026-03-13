"""
Emit Debug Payload node — compiles the observability payload for the turn.

CONTRACT
────────
  Purpose:  Aggregate trace data, compute total latency, build the debug
            summary for the internal UI and evaluator stubs.
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

    debug_lines = [
        f"turn_id: {state.turn_id}",
        f"intent: {state.intent.domain}/{state.intent.sub_intent} ({state.intent.message_kind})",
        f"playbook: {state.playbook.selected_playbook or 'none'} (conf={state.playbook.playbook_confidence})",
        f"strategy: {state.response_plan.chosen_strategy}",
        f"topics: {state.context.selected_topic_blocks}",
        f"entities: {len(state.context.selected_entities)}",
        f"signals: {state.context.selected_semantic_signals}",
        f"retrieval: {'yes' if state.retrieval.retrieval_needed else 'no'}",
        f"response_length: {len(state.final_response_text)} chars",
        f"total_latency_ms: {round(total_ms, 2)}",
    ]

    if state.warnings:
        debug_lines.append(f"warnings: {state.warnings}")

    evaluator = {
        "turn_id": state.turn_id,
        "session_id": state.session_id,
        "mall_id": state.mall_id,
        "intent_domain": state.intent.domain,
        "intent_sub": state.intent.sub_intent,
        "intent_confidence": state.intent.confidence,
        "message_kind": state.intent.message_kind,
        "playbook_used": state.playbook.selected_playbook,
        "playbook_confidence": state.playbook.playbook_confidence,
        "strategy_used": state.response_plan.chosen_strategy,
        "retrieval_used": state.retrieval.retrieval_needed,
        "topics_count": len(state.context.selected_topic_blocks),
        "entities_count": len(state.context.selected_entities),
        "signals_count": len(state.context.selected_semantic_signals),
        "response_length": len(state.final_response_text),
        "total_latency_ms": round(total_ms, 2),
        "node_count": len(state.node_trace),
        "warning_count": len(state.warnings),
        "scene_summary": {
            "companions": state.scene.companions,
            "occasion": state.scene.occasion,
            "budget": state.scene.budget,
            "active_topic": state.scene.active_topic,
            "active_shortlist": state.scene.active_shortlist,
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
            f"across {len(latency_map)} nodes"
        ),
    }
