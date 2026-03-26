"""
Concierge service — high-level orchestration of a single chat turn.

Glue between the API layer and the LangGraph pipeline.
Handles session hydration, graph invocation, state persistence,
and response extraction.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache

from app.graph.builder import build_concierge_graph
from app.models.api import (
    ChatRequest,
    ChatResponse,
    DebugPayload,
    SessionSummary,
)
from app.models.state import ConciergeState
from app.models.tenant import TenantConfig
from app.runtime import (
    ensure_mall_loaded,
    get_checkpointer,
    get_feedback_normalizer,
    get_feedback_service,
    get_implicit_detector,
    get_mall_context,
    get_session_store,
    get_session_tuning_engine,
)
from app.services.clean_context import clean_context_builder
from app.services.tenant_params import apply_session_overrides, load_tenant_config
from app.utils.ids import generate_session_id

logger = logging.getLogger(__name__)

_graph = None


def _get_graph():
    """Return the compiled graph, building it once and caching it.

    The graph is rebuilt whenever the checkpointer state changes (i.e. on
    first call after startup). In practice the graph is built exactly once
    per process because the checkpointer is set during runtime.initialize().
    """
    global _graph
    if _graph is None:
        checkpointer = get_checkpointer()
        _graph = build_concierge_graph(checkpointer=checkpointer)
        if checkpointer is not None:
            logger.info("Graph compiled with checkpointer: %s", type(checkpointer).__name__)
        else:
            logger.info("Graph compiled without checkpointer")
    return _graph


@lru_cache(maxsize=8)
def _cached_tenant_config(mall_id: str) -> TenantConfig:
    return load_tenant_config(mall_id)


async def handle_chat(request: ChatRequest) -> ChatResponse:
    """
    Process a single chat turn through the full LangGraph pipeline.

    1. Resolve session (load or create)
    2. Load tenant config
    3. Build initial graph state from session + request
    4. Run the compiled LangGraph
    5. Persist updated session state
    6. Extract and return response
    """
    t0 = time.perf_counter()
    store = get_session_store()
    # Ensure the mall is in the LRU RAM cache before any synchronous lookups.
    # For hot malls this is a no-op (O(1)); for cold malls it triggers disk load here,
    # not inside graph nodes where latency would be unpredictable.
    await ensure_mall_loaded(request.mall_id)
    mall_ctx = get_mall_context(request.mall_id)

    session_id = request.session_id or generate_session_id()
    session = await store.get_or_create(session_id, request.mall_id)

    base_config = _cached_tenant_config(request.mall_id)
    session_overrides = getattr(request, "session_overrides", None) or {}

    tuning_engine = get_session_tuning_engine()
    tuning_overrides = tuning_engine.get_overrides(session_id)
    merged_overrides = {**session_overrides, **tuning_overrides}

    config = (
        apply_session_overrides(base_config, merged_overrides)
        if merged_overrides
        else base_config
    )

    initial_state = clean_context_builder(
        session_state={
            "session_id": session_id,
            "tenant_id": request.tenant_id,
            "mall_id": request.mall_id,
            "raw_user_message": request.message,
            "active_tenant_parameters": config,
            "scene": session.scene,
            "last_intent": session.last_intent,
            "conversation_mode": session.conversation_mode,
        },
        mall_context=mall_ctx.get_context_pack(),
    )

    graph = _get_graph()

    # ── Build invoke config (adds thread_id when checkpointer is active) ──
    invoke_config: dict = {}
    checkpointer = get_checkpointer()
    if checkpointer is not None:
        # turn_id is generated inside load_session, so we snapshot under a
        # unique per-turn key derived from session_id + wall-clock nanoseconds.
        # This keeps every turn as an independent replay snapshot.
        turn_thread_id = f"{session_id}:{time.time_ns()}"
        invoke_config = {"configurable": {"thread_id": turn_thread_id}}

    raw_result = await graph.ainvoke(initial_state, config=invoke_config or None)

    result = _to_state(raw_result)

    await store.save_turn(
        session_id=session_id,
        scene=result.scene,
        last_intent=result.intent.domain,
        conversation_mode=result.intent.message_kind,
    )

    # ── Async quality evaluator (fire-and-forget, never blocks response) ──
    from app.config.settings import get_settings
    if get_settings().enable_evaluator:
        try:
            from app.services.quality_evaluator import evaluate_turn
            asyncio.create_task(
                evaluate_turn(
                    evaluator_stub=result.evaluator_stub,
                    response_text=result.final_response_text,
                    session_id=session_id,
                    turn_id=result.turn_id,
                )
            )
        except Exception:
            logger.warning("Failed to schedule quality evaluation task", exc_info=True)

    # ── implicit feedback detection (non-blocking) ────────────────────────
    try:
        detector = get_implicit_detector()
        implicit_event = detector.detect(
            user_message=request.message,
            session_id=session_id,
            tenant_id=request.tenant_id,
            mall_id=request.mall_id,
            turn_id=result.turn_id,
            message_kind=result.intent.message_kind,
        )
        if implicit_event:
            fb_svc = get_feedback_service()
            normalizer = get_feedback_normalizer()
            await fb_svc.record_implicit(implicit_event)
            signals = normalizer.normalize_implicit(implicit_event)
            if signals:
                await fb_svc.record_normalized(signals)
                tuning_engine.apply_signals(session_id, request.mall_id, signals)
    except Exception:
        logger.warning("Implicit feedback detection failed", exc_info=True)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    session_summary = SessionSummary(
        session_id=session_id,
        turn_count=session.turn_count,
        active_topic=result.scene.active_topic,
        companions=result.scene.companions,
        occasion=result.scene.occasion,
        budget=result.scene.budget,
        active_shortlist=result.scene.active_shortlist,
        target_person=result.scene.target_person,
        visit_type=result.scene.visit_type,
        goal=result.scene.goal,
    )

    debug_payload = None
    if request.debug:
        debug_payload = _build_debug_payload(result, elapsed_ms)

    return ChatResponse(
        session_id=session_id,
        message=result.final_response_text,
        session_state=session_summary,
        debug=debug_payload,
    )


def _to_state(raw: dict) -> ConciergeState:
    """Convert the LangGraph dict-like result back to a typed ConciergeState."""
    if isinstance(raw, ConciergeState):
        return raw
    return ConciergeState(**{k: v for k, v in raw.items() if k in ConciergeState.model_fields})


def _build_debug_payload(state: ConciergeState, total_elapsed_ms: float) -> DebugPayload:
    latency_map = {}
    trace_list = []
    for entry in state.node_trace:
        latency_map[entry.node] = entry.latency_ms
        trace_list.append(entry.model_dump())

    return DebugPayload(
        turn_id=state.turn_id,
        session_id=state.session_id,
        mall_id=state.mall_id,
        intent_domain=state.intent.domain,
        intent_sub=state.intent.sub_intent,
        intent_confidence=state.intent.confidence,
        message_kind=state.intent.message_kind,
        scene_summary=state.scene.model_dump(),
        selected_playbook=state.playbook.selected_playbook,
        playbook_confidence=state.playbook.playbook_confidence,
        matched_playbooks=state.playbook.matched_playbooks,
        chosen_strategy=state.response_plan.chosen_strategy,
        response_shape=state.response_plan.response_shape_hint,
        selected_topic_blocks=state.context.selected_topic_blocks,
        selected_entities=state.context.selected_entities,
        selected_semantic_signals=state.context.selected_semantic_signals,
        ranking_notes=state.context.ranking_notes,
        retrieval_needed=state.retrieval.retrieval_needed,
        retrieval_reason=state.retrieval.retrieval_reason,
        retrieval_targets=state.retrieval.retrieval_targets,
        retrieval_results_count=len(
            [r for r in state.retrieval.retrieval_results if r.get("data")]
        ),
        latency_by_node=latency_map,
        total_latency_ms=round(total_elapsed_ms, 2),
        node_count=len(state.node_trace),
        response_mode=state.evaluator_stub.get("response_mode", ""),
        confidence_level=state.evaluator_stub.get("confidence_level", ""),
        response_mode_reason=state.evaluator_stub.get("response_mode_reason", ""),
        fallback_applied=bool(state.evaluator_stub.get("fallback_applied", False)),
        flow_type=state.evaluator_stub.get("flow_type", state.flow_type or ""),
        warnings=list(state.warnings),
        node_trace=trace_list,
    )
