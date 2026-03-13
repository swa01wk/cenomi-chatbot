"""
Update Memory node — persists turn-level memory changes.

CONTRACT
────────
  Purpose:  After response generation, update the scene with any entities
            mentioned in the response, refresh shortlists, and prepare
            for the next turn.
  Reads:    scene, final_response_text, intent, context
  Writes:   scene (updated with shortlist, topic confirmation)
  Failure:  Memory update error → warning, scene unchanged
  Routing:  Always → emit_debug_payload
"""

from __future__ import annotations

from app.models.state import ConciergeState
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context


@traced_node("update_memory")
async def update_memory(state: ConciergeState) -> dict:
    scene = state.scene.model_copy(deep=True)
    changes: list[str] = []

    if state.intent.domain and state.intent.domain != "general":
        scene.active_topic = state.intent.domain
        changes.append(f"active_topic confirmed: {state.intent.domain}")

    if state.intent.message_kind == "correction" and scene.active_shortlist:
        scene.rejected_options.extend(scene.active_shortlist)
        scene.active_shortlist = []
        changes.append("moved shortlist to rejected (correction)")

    mentioned = _extract_mentioned_entities(state)
    if mentioned:
        scene.active_shortlist = mentioned
        changes.append(f"shortlist: {mentioned}")

    return {
        "scene": scene,
        "_trace_summary": f"Memory: {', '.join(changes) if changes else 'no changes'}",
    }


def _extract_mentioned_entities(state: ConciergeState) -> list[str]:
    """Identify entity names from the response by checking against context entities."""
    response_lower = state.final_response_text.lower()
    mentioned: list[str] = []

    for entity in state.context.selected_entities:
        name = entity.get("name", "")
        if name and name.lower() in response_lower:
            mentioned.append(name)

    if not mentioned:
        try:
            mall_ctx = get_mall_context()
            for etype in ("stores", "dining", "cinemas"):
                canonical = mall_ctx._builder._canonical.get(etype, [])
                for e in canonical:
                    if hasattr(e, "name") and e.name.lower() in response_lower:
                        mentioned.append(e.name)
        except RuntimeError:
            pass

    return mentioned[:5]
