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

    # ── Visit plan progression — mark current step as completed ───────
    _advance_completed_steps(state, scene, changes)

    return {
        "scene": scene,
        "_trace_summary": f"Memory: {', '.join(changes) if changes else 'no changes'}",
    }


def _advance_completed_steps(
    state: ConciergeState,
    scene,
    changes: list[str],
) -> None:
    """
    Record a completed visit-plan step only when the user has genuinely moved
    to a NEW domain — not when they re-ask about the same topic.

    Rules:
    - Only marks a domain complete when it is NOT already the last item in
      completed_steps (prevents "Dinner?" + "any restaurants?" from double-
      advancing to the movie step).
    - Does not advance for pure followup/refinement turns that stay in the
      same domain (same-domain retry detection).
    - Always advances for explicitly sequential turns (message_kind signals
      the user has moved on).
    """
    domain = state.intent.domain
    if not domain or domain in ("general",):
        return

    # Map domain → activity label used in visit_plan
    domain_to_activity: dict[str, str] = {
        "dining": "dining",
        "shopping": "shopping",
        "entertainment": "movie",
        "services": "services",
        "navigation": "navigation",
        "exploration": "exploration",
        "mall_info": "mall_info",
    }
    activity = domain_to_activity.get(domain, domain)

    # Don't re-mark as complete if this domain was the LAST completed step
    # (the user is still exploring the same domain, e.g. "any restaurants?"
    # after "Dinner?").  Only mark complete when it's genuinely new.
    last_completed = scene.completed_steps[-1] if scene.completed_steps else None
    is_same_domain_retry = (last_completed == activity)

    if activity not in scene.completed_steps:
        scene.completed_steps = (scene.completed_steps + [activity])[-8:]
        changes.append(f"completed_step:{activity}")
    elif is_same_domain_retry:
        # User is still asking about the same domain — do NOT advance the plan
        return

    # If we have a visit_plan, advance current_plan_step to next unfinished step
    if scene.visit_plan:
        for step in scene.visit_plan:
            step_activity = domain_to_activity.get(step, step)
            if step_activity not in scene.completed_steps and step not in scene.completed_steps:
                if scene.current_plan_step != step:
                    scene.current_plan_step = step
                    changes.append(f"next_plan_step:{step}")
                break


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

    return mentioned[:10]
