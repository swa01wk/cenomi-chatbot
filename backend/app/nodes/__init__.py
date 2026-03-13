"""
Concierge graph node functions.

All nodes follow the same contract:
  - Receive ConciergeState (Pydantic model)
  - Return dict with partial state updates
  - Automatically traced via @traced_node decorator

Graph order:
  load_session → interpret_turn → update_scene_memory → resolve_playbooks
  → choose_strategy → compose_context → decide_retrieval
  → [fetch_exact_facts] → generate_response → update_memory
  → emit_debug_payload
"""

from app.nodes.choose_strategy import choose_strategy
from app.nodes.compose_context import compose_context
from app.nodes.decide_retrieval import decide_retrieval
from app.nodes.emit_debug_payload import emit_debug_payload
from app.nodes.fetch_exact_facts import fetch_exact_facts
from app.nodes.generate_response import generate_response
from app.nodes.interpret_turn import interpret_turn
from app.nodes.load_session import load_session
from app.nodes.resolve_playbooks import resolve_playbooks
from app.nodes.update_memory import update_memory
from app.nodes.update_scene_memory import update_scene_memory

__all__ = [
    "load_session",
    "interpret_turn",
    "update_scene_memory",
    "resolve_playbooks",
    "choose_strategy",
    "compose_context",
    "decide_retrieval",
    "fetch_exact_facts",
    "generate_response",
    "update_memory",
    "emit_debug_payload",
]
