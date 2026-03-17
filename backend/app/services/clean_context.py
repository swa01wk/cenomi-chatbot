"""
Clean context builder — assembles a contamination-free per-turn context.

Each user turn receives ONLY:
  1. The current user message
  2. Mall context (operational data, topic blocks, entities)
  3. Structured retrieval results
  4. Playbook result

Previous LLM responses are NEVER included. Session continuity is
provided through lightweight metadata (last_intent, conversation_mode)
and structured scene memory — not raw conversation history.
"""

from __future__ import annotations

from typing import Any


def clean_context_builder(
    session_state: dict[str, Any],
    mall_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a clean initial state dict for a single graph turn.

    ``session_state`` contains only structured session metadata:
        - session_id, mall_id, last_intent, conversation_mode
        - scene (SceneMemory)
        - active_tenant_parameters

    ``mall_context`` is the static mall intelligence pack (never per-turn
    LLM output).

    Returns a dict suitable for passing as ``initial_state`` to the
    LangGraph pipeline.  The returned dict explicitly excludes any
    field that could carry previous LLM output.
    """

    return {
        # Session identity
        "session_id": session_state["session_id"],
        "tenant_id": session_state.get("tenant_id", session_state.get("mall_id", "")),
        "mall_id": session_state["mall_id"],

        # Lightweight continuity signals (no raw LLM text)
        "last_intent": session_state.get("last_intent", ""),
        "conversation_mode": session_state.get("conversation_mode", ""),

        # Current turn input
        "raw_user_message": session_state["raw_user_message"],

        # Structured scene memory (persisted across turns)
        "scene": session_state.get("scene"),

        # Tenant configuration
        "active_tenant_parameters": session_state.get("active_tenant_parameters"),

        # Empty messages — only current-turn messages will be appended
        # by load_session (user) and generate_response (assistant)
        "messages": [],
    }
