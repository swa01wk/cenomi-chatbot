"""
SSE streaming chat endpoint.

POST /api/chat/stream

Streams the concierge response token-by-token using Server-Sent Events.
Session persistence and implicit feedback detection run after the stream
completes, so they never block token delivery.

SSE event format
────────────────
  event: token
  data: {"text": "<token>"}

  event: done
  data: {"session_id": "...", "session_state": {...}, "debug": {...}}

  event: error
  data: {"detail": "<message>"}

The existing blocking POST /api/chat endpoint is unaffected — existing
clients require no changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models.api import ChatRequest, DebugPayload, SessionSummary
from app.models.state import ConciergeState, SMALLTALK_KINDS
from app.runtime import (
    get_checkpointer,
    get_feedback_normalizer,
    get_feedback_service,
    get_implicit_detector,
    get_mall_context,
    get_session_store,
    get_session_tuning_engine,
)
from app.services.clean_context import clean_context_builder
from app.services.concierge import _build_debug_payload, _get_graph, _to_state
from app.services.cta_generator import get_cta_suggestions
from app.services.tenant_params import apply_session_overrides, load_tenant_config
from app.utils.ids import generate_session_id

logger = logging.getLogger(__name__)
router = APIRouter()


def _sse(event: str, data: str) -> str:
    """Format a single SSE message."""
    return f"event: {event}\ndata: {data}\n\n"


async def _generate_stream(
    request: ChatRequest,
) -> AsyncGenerator[str, None]:
    """
    Full streaming pipeline coroutine.

    Yields SSE-formatted strings:
      - ``event: token`` for each streamed LLM token
      - ``event: done``  with session_state and optional debug payload
      - ``event: error`` on failure
    """
    t0 = time.perf_counter()

    # ── Session and state setup (identical to handle_chat) ────────────────
    try:
        from app.runtime import ensure_mall_loaded

        store = get_session_store()
        await ensure_mall_loaded(request.mall_id)
        mall_ctx = get_mall_context(request.mall_id)

        session_id = request.session_id or generate_session_id()
        session = await store.get_or_create(session_id, request.mall_id)

        from functools import lru_cache
        from app.models.tenant import TenantConfig

        base_config = load_tenant_config(request.mall_id)
        session_overrides = getattr(request, "session_overrides", None) or {}
        tuning_engine = get_session_tuning_engine()
        tuning_overrides = tuning_engine.get_overrides(session_id)
        merged_overrides = {**session_overrides, **tuning_overrides}
        config = (
            apply_session_overrides(base_config, merged_overrides)
            if merged_overrides
            else base_config
        )

        # Propagate time_of_day from request into scene when provided
        scene = session.scene
        if getattr(request, "time_of_day", ""):
            scene = scene.model_copy(update={"time_of_day": request.time_of_day})

        initial_state = clean_context_builder(
            session_state={
                "session_id": session_id,
                "tenant_id": request.tenant_id,
                "mall_id": request.mall_id,
                "raw_user_message": request.message,
                "active_tenant_parameters": config,
                "scene": scene,
                "last_intent": session.last_intent,
                "conversation_mode": session.conversation_mode,
                "conversation_history": session.conversation_history,
                # Client-supplied language override ("ar"/"en"); empty = auto-detect
                "detected_language": getattr(request, "language", None) or "",
            },
            mall_context=mall_ctx.get_context_pack(),
        )
    except Exception as exc:
        logger.error("Stream setup failed: %s", exc, exc_info=True)
        yield _sse("error", json.dumps({"detail": "Failed to initialize session"}))
        return

    # ── Build invoke config ────────────────────────────────────────────────
    invoke_config: dict = {}
    checkpointer = get_checkpointer()
    if checkpointer is not None:
        turn_thread_id = f"{session_id}:{time.time_ns()}"
        invoke_config = {"configurable": {"thread_id": turn_thread_id}}

    # ── Stream graph events ────────────────────────────────────────────────
    graph = _get_graph()
    final_state_dict: dict | None = None
    tokens_emitted = 0

    try:
        async for event in graph.astream_events(
            initial_state,
            config=invoke_config or None,
            version="v2",
        ):
            event_type: str = event.get("event", "")

            # Stream tokens from the generate_response node only
            if (
                event_type == "on_chat_model_stream"
                and event.get("metadata", {}).get("langgraph_node") == "generate_response"
            ):
                chunk = event.get("data", {}).get("chunk")
                if chunk is not None:
                    content = getattr(chunk, "content", None)
                    if content:
                        yield _sse("token", json.dumps({"text": content}))
                        tokens_emitted += 1

            # Capture the final graph output (LangGraph emits this at the end)
            elif event_type == "on_chain_end" and event.get("name") == "LangGraph":
                final_state_dict = event.get("data", {}).get("output")

    except Exception as exc:
        logger.error("Stream graph execution failed: %s", exc, exc_info=True)
        yield _sse("error", json.dumps({"detail": "Graph execution error"}))
        return

    # ── Post-stream side effects ───────────────────────────────────────────
    result: ConciergeState | None = None
    if final_state_dict is not None:
        try:
            result = _to_state(final_state_dict)

            # Preserve the previous conversation_mode for transient smalltalk turns
            # (greeting, thanks, farewell, crisis, etc.) so the classifier on the
            # NEXT turn doesn't see a stale "Conversation mode: greeting" that would
            # confuse it about the real conversation state.
            saved_mode = (
                session.conversation_mode
                if result.intent.message_kind in SMALLTALK_KINDS
                else result.intent.message_kind
            )
            await store.save_turn(
                session_id=session_id,
                scene=result.scene,
                last_intent=result.intent.domain,
                conversation_mode=saved_mode,
                user_message=request.message,
                assistant_message=result.final_response_text,
            )

            # Quality evaluator (fire-and-forget)
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
                    logger.warning("Failed to schedule stream quality eval", exc_info=True)

            # Implicit feedback detection
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
                logger.warning("Stream implicit feedback detection failed", exc_info=True)

        except Exception:
            logger.warning("Stream post-processing failed", exc_info=True)

    # ── Emit static response as a token for non-LLM paths (e.g. smalltalk) ──
    # Paths like smalltalk bypass generate_response and produce no on_chat_model_stream
    # events. Emit the final_response_text as a single token so the frontend renders it.
    if tokens_emitted == 0 and result is not None and result.final_response_text:
        yield _sse("token", json.dumps({"text": result.final_response_text}))

    # ── Final done event ───────────────────────────────────────────────────
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    session_summary = None
    debug_payload = None

    if result is not None:
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
        if request.debug:
            debug_payload = _build_debug_payload(result, elapsed_ms)

    # Derive quick-reply chip suggestions from the active CTA type so the
    # frontend can render contextual pills BEFORE any closing intent question.
    suggestions: list[str] = []
    if result is not None:
        cta_type = getattr(result.debug_enrichment, "experience_cta_type", "") or ""
        suggestions = get_cta_suggestions(cta_type, language=result.detected_language or "en")

    sources: list[dict] = []
    if result is not None:
        sources = [
            {"target": r["target"], "data": r["data"]}
            for r in result.retrieval.retrieval_results
            if r.get("status") == "found" and r.get("data")
        ]

    # Enriched tenant cards from ranked/selected entities — present for concierge
    # recommendation responses where retrieval_results is empty.
    tenants: list[dict] = []
    mall_map_url = mall_ctx.get_map_url()
    if result is not None:
        for e in (result.context.selected_entities or []):
            name = e.get("name", "")
            if not name:
                continue
            image = ""
            category = e.get("category", "") or e.get("cuisine_type", "")
            entity_id = e.get("entity_id", "")
            if entity_id:
                try:
                    full = mall_ctx.get_entity_by_id(entity_id)
                    if full:
                        image = full.get("banner") or full.get("brand_logo") or ""
                        if not category:
                            category = (
                                full.get("category")
                                or full.get("cuisine_type")
                                or ""
                            )
                except Exception:
                    pass
            tenants.append({
                "name": name,
                "category": category,
                "image": image,
                "floor": e.get("floor", ""),
                "zone": e.get("zone", ""),
                "unit_number": e.get("unit_number", ""),
                "map_url": mall_map_url,
            })

    done_data = {
        "session_id": session_id,
        "session_state": session_summary.model_dump() if session_summary else {},
        "debug": debug_payload.model_dump() if debug_payload else None,
        "suggestions": suggestions,
        "sources": sources,
        "tenants": tenants,
    }
    yield _sse("done", json.dumps(done_data, default=str))


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """
    Stream a concierge response via Server-Sent Events.

    Tokens arrive as ``event: token`` messages.
    A final ``event: done`` message carries session_state and optional debug data.
    On failure an ``event: error`` message is sent.

    Example client (JavaScript):
        const es = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: 'What movies are showing?'})
        });
        const reader = es.body.getReader();
        // parse SSE chunks from reader...
    """
    return StreamingResponse(
        _generate_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
