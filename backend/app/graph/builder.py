"""
LangGraph concierge pipeline builder — dual-flow architecture.

Constructs the graph with explicit routing between concierge and factual paths.

Graph topology:

    START
      │
      ▼
    load_session
      │
      ▼
    interpret_turn
      │
      ├─── (small talk) ───► smalltalk ──────────────────────┐
      │                                                        │
      ▼ (normal)                                              │
    route_flow                                                │
      │                                                        │
      ├─── (factual) ──────────────────────────────────┐      │
      │                                                 │      │
      ▼ (concierge)                                     │      │
    update_scene_memory                                 │      │
      │                                                 │      │
      ▼                                                 │      │
    resolve_playbooks                                   │      │
      │                                                 │      │
      ▼                                                 │      │
    choose_strategy                                     │      │
      │                                                 │      │
      ▼                                                 │      │
    compose_context                                     │      │
      │                                                 ▼      │
      ▼                                       resolve_fact_scope
    rank_and_dedupe                                     │      │
      │                                                 ▼      │
      ▼                                       fetch_exact_facts (factual)
    decide_retrieval ──┐                               │      │
      │                │                               ▼      │
      │ (needed)       │ (not needed)    compose_fact_response_context
      ▼                │                               │      │
    fetch_exact_facts  │ ◄─────────────────────────────┘      │
    (concierge)        │                                        │
      │                │                                        │
      ├────────────────┘                                        │
      ▼                                                         │
    generate_response ◄──────────────────────────────────────  │
      │                                                         │
      ├───────────────────────────────────────────────────────  ┘
      ▼
    update_memory
      │
      ▼
    emit_debug_payload
      │
      ▼
     END

Conditional edges:
  - interpret_turn  → smalltalk               (if greeting / casual chat)
  - interpret_turn  → route_flow              (otherwise)
  - route_flow      → update_scene_memory     (concierge flow)
  - route_flow      → resolve_fact_scope      (factual flow)
  - decide_retrieval → fetch_exact_facts      (concierge, if retrieval_needed)
  - decide_retrieval → generate_response      (concierge, if not retrieval_needed)
  Both factual and concierge paths converge at generate_response.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.models.state import ConciergeState
from app.nodes import (
    choose_strategy,
    compose_context,
    decide_retrieval,
    emit_debug_payload,
    fetch_exact_facts,
    generate_response,
    interpret_turn,
    is_smalltalk,
    load_session,
    resolve_playbooks,
    smalltalk,
    update_memory,
    update_scene_memory,
)
from app.nodes.compose_fact_response_context import compose_fact_response_context
from app.nodes.rank_and_dedupe import rank_and_dedupe
from app.nodes.resolve_fact_scope import resolve_fact_scope
from app.nodes.route_flow import route_flow


def _route_after_interpret(state: ConciergeState) -> str:
    """Conditional edge after interpret_turn: fast-track small talk."""
    if is_smalltalk(state):
        return "smalltalk"
    return "route_flow"


def _route_after_flow(state: ConciergeState) -> str:
    """
    Conditional edge after route_flow.
    Routes to the concierge pipeline or the factual pipeline.
    """
    if state.flow_type == "factual":
        return "factual"
    return "concierge"


def _route_after_retrieval_decision(state: ConciergeState) -> str:
    """Conditional edge after decide_retrieval (concierge path only)."""
    if state.retrieval.retrieval_needed:
        return "fetch_exact_facts"
    return "generate_response"


def build_concierge_graph(checkpointer=None):
    """
    Build and compile the dual-flow concierge LangGraph pipeline.

    Args:
        checkpointer: Optional LangGraph checkpointer (MemorySaver,
            AsyncRedisSaver, etc.). When provided, every turn is
            snapshot-persisted under thread_id for replay and debugging.
            Pass None (default) to run without checkpointing.

    Returns a compiled graph that accepts ConciergeState and produces
    the full state including response and debug payload.

    Usage (no checkpointer):
        graph = build_concierge_graph()
        result = await graph.ainvoke({
            "session_id": "...",
            "mall_id": "al_nakheel_plaza_28",
            "raw_user_message": "What movies do you have?",
        })

    Usage (with checkpointer):
        graph = build_concierge_graph(checkpointer=MemorySaver())
        result = await graph.ainvoke(
            {"session_id": "...", "raw_user_message": "..."},
            config={"configurable": {"thread_id": "session-abc:turn-001"}},
        )
    """
    graph = StateGraph(ConciergeState)

    # ── Register all nodes ────────────────────────────────────────────
    graph.add_node("load_session", load_session)
    graph.add_node("interpret_turn", interpret_turn)
    graph.add_node("smalltalk", smalltalk)
    graph.add_node("route_flow", route_flow)

    # Concierge path nodes
    graph.add_node("update_scene_memory", update_scene_memory)
    graph.add_node("resolve_playbooks", resolve_playbooks)
    graph.add_node("choose_strategy", choose_strategy)
    graph.add_node("compose_context", compose_context)
    graph.add_node("rank_and_dedupe", rank_and_dedupe)
    graph.add_node("decide_retrieval", decide_retrieval)
    graph.add_node("fetch_exact_facts", fetch_exact_facts)

    # Factual path nodes
    graph.add_node("resolve_fact_scope", resolve_fact_scope)
    graph.add_node("compose_fact_response_context", compose_fact_response_context)

    # Shared terminal nodes
    graph.add_node("generate_response", generate_response)
    graph.add_node("update_memory", update_memory)
    graph.add_node("emit_debug_payload", emit_debug_payload)

    # ── Entry ─────────────────────────────────────────────────────────
    graph.set_entry_point("load_session")
    graph.add_edge("load_session", "interpret_turn")

    # ── Small talk branch (skips heavy pipeline) ──────────────────────
    graph.add_conditional_edges(
        "interpret_turn",
        _route_after_interpret,
        {
            "smalltalk": "smalltalk",
            "route_flow": "route_flow",
        },
    )
    graph.add_edge("smalltalk", "update_memory")

    # ── Dual-flow branch after route_flow ─────────────────────────────
    graph.add_conditional_edges(
        "route_flow",
        _route_after_flow,
        {
            "concierge": "update_scene_memory",
            "factual": "resolve_fact_scope",
        },
    )

    # ── Concierge pipeline ────────────────────────────────────────────
    graph.add_edge("update_scene_memory", "resolve_playbooks")
    graph.add_edge("resolve_playbooks", "choose_strategy")
    graph.add_edge("choose_strategy", "compose_context")
    graph.add_edge("compose_context", "rank_and_dedupe")
    graph.add_edge("rank_and_dedupe", "decide_retrieval")

    # Conditional retrieval within concierge path
    graph.add_conditional_edges(
        "decide_retrieval",
        _route_after_retrieval_decision,
        {
            "fetch_exact_facts": "fetch_exact_facts",
            "generate_response": "generate_response",
        },
    )
    graph.add_edge("fetch_exact_facts", "generate_response")

    # ── Factual pipeline ──────────────────────────────────────────────
    graph.add_edge("resolve_fact_scope", "fetch_exact_facts")

    # Note: fetch_exact_facts is shared between both paths.
    # In factual flow, it goes to compose_fact_response_context.
    # In concierge flow, it goes directly to generate_response.
    # We use a conditional edge from fetch_exact_facts to handle this.
    graph.add_conditional_edges(
        "fetch_exact_facts",
        _route_after_fetch,
        {
            "compose_fact_response_context": "compose_fact_response_context",
            "generate_response": "generate_response",
        },
    )
    graph.add_edge("compose_fact_response_context", "generate_response")

    # ── Post-generation (shared) ──────────────────────────────────────
    graph.add_edge("generate_response", "update_memory")
    graph.add_edge("update_memory", "emit_debug_payload")
    graph.add_edge("emit_debug_payload", END)

    return graph.compile(checkpointer=checkpointer)


def _route_after_fetch(state: ConciergeState) -> str:
    """
    After fetch_exact_facts, route to compose_fact_response_context for
    factual flow, or directly to generate_response for concierge flow.
    """
    if state.flow_type == "factual":
        return "compose_fact_response_context"
    return "generate_response"
