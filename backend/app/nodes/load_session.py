"""
Load Session node — initializes turn state and loads runtime configuration.

CONTRACT
────────
  Purpose:  Hydrate the session with tenant config, mall data references,
            and a fresh turn_id.  Normalize the raw user message.
  Reads:    session_id, mall_id, raw_user_message
  Writes:   turn_id, tenant_id, active_mall_id, active_tenant_parameters,
            normalized_user_message, messages (appends user Message)
  Failure:  Config not found → uses TenantConfig defaults + warning
  Routing:  Always → interpret_turn
"""

from __future__ import annotations

from app.models.state import ConciergeState, Message
from app.models.tenant import TenantConfig
from app.nodes._tracing import traced_node
from app.services.tenant_params import load_tenant_config
from app.utils.ids import generate_message_id
from llm.prompts.query_expander import expand_short_query


@traced_node("load_session")
async def load_session(state: ConciergeState) -> dict:
    mall_id = state.mall_id or "al_nakheel_plaza_28"
    turn_id = generate_message_id()

    tenant_config = state.active_tenant_parameters
    warnings: list[str] = []

    if tenant_config is None:
        try:
            tenant_config = load_tenant_config(mall_id)
        except Exception:
            tenant_config = TenantConfig(mall_id=mall_id)
            warnings.append("Tenant config not found — using defaults")

    normalized = state.raw_user_message.strip()

    scene_ctx = None
    if state.scene and (
        state.scene.target_person
        or state.scene.companions
        or state.scene.audience
        or state.scene.occasion
        or state.scene.active_topic
        or state.scene.visit_plan
        or state.scene.visit_constraints
    ):
        scene_ctx = {
            "target_person": state.scene.target_person,
            "companions": state.scene.companions,
            "audience": state.scene.audience,
            "occasion": state.scene.occasion,
            "goal": state.scene.goal,
            "previous_need": state.scene.previous_need,
            "active_topic": state.scene.active_topic,
            "visit_plan": state.scene.visit_plan,
            "completed_steps": state.scene.completed_steps,
            "visit_constraints": state.scene.visit_constraints,
            "current_plan_step": state.scene.current_plan_step,
        }

    expansion = expand_short_query(normalized, scene_context=scene_ctx)

    user_message = Message(
        role="user",
        content=normalized,
        turn_id=turn_id,
    )

    result: dict = {
        "turn_id": turn_id,
        "tenant_id": mall_id,
        "active_mall_id": mall_id,
        "active_tenant_parameters": tenant_config,
        "normalized_user_message": normalized,
        "expanded_query": expansion.expanded,
        "messages": [user_message],
        "_trace_summary": (
            f"Session loaded for {mall_id}, turn {turn_id}"
            + (f" | expanded: {expansion.expanded!r}" if expansion.was_expanded else "")
        ),
    }

    if warnings:
        result["_trace_warnings"] = warnings

    return result
