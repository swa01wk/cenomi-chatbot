"""
LangGraph concierge pipeline builder.

Constructs the graph with conditional routing for single-mall
AI Findr-style concierge behavior.

Graph topology:

    START
      │
      ▼
    load_session
      │
      ▼
    interpret_turn
      │
      ├─── (small talk) ───► smalltalk ──┐
      │                                   │
      ▼ (normal)                          │
    update_scene_memory                   │
      │                                   │
      ▼                                   │
    resolve_playbooks                     │
      │                                   │
      ▼                                   │
    choose_strategy                       │
      │                                   │
      ▼                                   │
    compose_context                       │
      │                                   │
      ▼                                   │
    decide_retrieval ──┐                  │
      │                │                  │
      │ (needed)       │ (not needed)     │
      ▼                │                  │
    fetch_exact_facts  │                  │
      │                │                  │
      ├────────────────┘                  │
      ▼                                   │
    generate_response                     │
      │                                   │
      ├───────────────────────────────────┘
      ▼
    update_memory
      │
      ▼
    emit_debug_payload
      │
      ▼
     END

Conditional edges:
  - interpret_turn → smalltalk           (if greeting / casual chat)
  - interpret_turn → update_scene_memory (otherwise)
  - decide_retrieval → fetch_exact_facts (if retrieval_needed)
  - decide_retrieval → generate_response (if not retrieval_needed)
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


def _route_after_interpret(state: ConciergeState) -> str:
    """Conditional edge after interpret_turn: fast-track small talk."""
    if is_smalltalk(state):
        return "smalltalk"
    return "update_scene_memory"


def _route_after_retrieval_decision(state: ConciergeState) -> str:
    """Conditional edge after decide_retrieval."""
    if state.retrieval.retrieval_needed:
        return "fetch_exact_facts"
    return "generate_response"


def build_concierge_graph():
    """
    Build and compile the concierge LangGraph pipeline.

    Returns a compiled graph that accepts ConciergeState and produces
    the full state including response and debug payload.

    Usage:
        graph = build_concierge_graph()
        result = await graph.ainvoke({
            "session_id": "...",
            "mall_id": "al_nakheel_plaza_28",
            "raw_user_message": "Where should I eat?",
        })
    """
    graph = StateGraph(ConciergeState)

    # ── Register all nodes ────────────────────────────────────────────
    graph.add_node("load_session", load_session)
    graph.add_node("interpret_turn", interpret_turn)
    graph.add_node("smalltalk", smalltalk)
    graph.add_node("update_scene_memory", update_scene_memory)
    graph.add_node("resolve_playbooks", resolve_playbooks)
    graph.add_node("choose_strategy", choose_strategy)
    graph.add_node("compose_context", compose_context)
    graph.add_node("decide_retrieval", decide_retrieval)
    graph.add_node("fetch_exact_facts", fetch_exact_facts)
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
            "update_scene_memory": "update_scene_memory",
        },
    )
    graph.add_edge("smalltalk", "update_memory")

    # ── Normal pipeline ───────────────────────────────────────────────
    graph.add_edge("update_scene_memory", "resolve_playbooks")
    graph.add_edge("resolve_playbooks", "choose_strategy")
    graph.add_edge("choose_strategy", "compose_context")
    graph.add_edge("compose_context", "decide_retrieval")

    # ── Conditional retrieval branch ──────────────────────────────────
    graph.add_conditional_edges(
        "decide_retrieval",
        _route_after_retrieval_decision,
        {
            "fetch_exact_facts": "fetch_exact_facts",
            "generate_response": "generate_response",
        },
    )
    graph.add_edge("fetch_exact_facts", "generate_response")

    # ── Post-generation ───────────────────────────────────────────────
    graph.add_edge("generate_response", "update_memory")
    graph.add_edge("update_memory", "emit_debug_payload")
    graph.add_edge("emit_debug_payload", END)

    return graph.compile()
