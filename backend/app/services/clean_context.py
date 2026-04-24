"""
Clean context builder — assembles a contamination-free per-turn context.

Each user turn receives ONLY:
  1. The current user message
  2. Mall context (operational data, topic blocks, entities)
  3. Structured retrieval results
  4. Playbook result
  5. Prior conversation history (raw user+assistant pairs from the session log)

Session continuity is provided through structured SceneMemory AND the full
raw conversation log so the LLM can see every prior exchange — making it
resilient to cases where SceneMemory extraction misses a nuance (e.g. a subtle
negation or a mid-conversation topic reversal).
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
        - conversation_history (list of {"role", "content"} dicts)

    ``mall_context`` is the static mall intelligence pack (never per-turn
    LLM output).

    Returns a dict suitable for passing as ``initial_state`` to the
    LangGraph pipeline.
    """
    from app.models.state import Message

    # Convert the persisted raw dialogue log into Message objects so they are
    # accessible inside the graph as state.messages.  load_session will then
    # append the new user message via the operator.add reducer, and
    # generate_response will append the assistant reply — the full growing
    # log is always present in state.messages.
    history: list[dict] = session_state.get("conversation_history") or []
    prior_messages: list[Message] = [
        Message(role=h["role"], content=h["content"])
        for h in history
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]

    return {
        # Session identity
        "session_id": session_state["session_id"],
        "tenant_id": session_state.get("tenant_id", session_state.get("mall_id", "")),
        "mall_id": session_state["mall_id"],

        # Lightweight continuity signals
        "last_intent": session_state.get("last_intent", ""),
        "conversation_mode": session_state.get("conversation_mode", ""),

        # Current turn input
        "raw_user_message": session_state["raw_user_message"],

        # Language — client override when provided; empty string means
        # load_session will auto-detect from the message text.
        "detected_language": session_state.get("detected_language", ""),

        # Structured scene memory (persisted across turns)
        "scene": session_state.get("scene"),

        # Tenant configuration
        "active_tenant_parameters": session_state.get("active_tenant_parameters"),

        # Prior conversation messages pre-populated from the session log.
        # The operator.add reducer in ConciergeState will append the new
        # user message (load_session) and assistant reply (generate_response)
        # to this list during the current turn.
        "messages": prior_messages,
    }
