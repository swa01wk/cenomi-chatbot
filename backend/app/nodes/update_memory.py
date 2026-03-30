"""
Update Memory node — persists turn-level memory changes.

CONTRACT
────────
  Purpose:  After response generation, update the scene with any entities
            mentioned in the response, refresh shortlists, and prepare
            for the next turn.  Also persists session-level continuity
            fields (last_successful_playbook, preferred_categories, etc.)
  Reads:    scene, final_response_text, intent, context, response_plan, playbook
  Writes:   scene (updated with shortlist, topic confirmation),
            last_successful_playbook, last_response_shape, preferred_categories
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
    result: dict = {}

    # ── Flow-type memory ──────────────────────────────────────────────
    current_flow = state.flow_type or "concierge"
    scene.last_flow_type = current_flow
    changes.append(f"last_flow_type={current_flow}")

    turn_index = len(state.messages)  # proxy for turn counter

    # ── Greeting streak tracking ──────────────────────────────────────
    # Increment when this turn was a greeting (smalltalk node), reset otherwise.
    # response_debug_summary is "smalltalk/greeting [greeting_scaffold]" on
    # greeting turns regardless of which tier was selected.
    if state.response_debug_summary.startswith("smalltalk/greeting"):
        scene.greeting_streak = (scene.greeting_streak or 0) + 1
        changes.append(f"greeting_streak={scene.greeting_streak}")
    elif scene.greeting_streak:
        scene.greeting_streak = 0
        changes.append("greeting_streak reset")

    # ── Mood / emotional state persistence ───────────────────────────
    # When the visitor was recently frustrated or disengaged, record it in
    # SceneMemory so the next turn's classifier and response generator can
    # adapt tone and routing accordingly.
    # Mood expires automatically after 2 subsequent non-emotional turns so it
    # doesn't bleed into unrelated queries later in the session.
    _EMOTIONAL_KINDS = frozenset({"disengagement", "emotional"})
    current_kind = state.intent.message_kind
    if current_kind in _EMOTIONAL_KINDS:
        scene.recent_mood = current_kind
        scene.mood_turn_index = turn_index
        changes.append(f"recent_mood={current_kind} at turn_index={turn_index}")
    elif scene.recent_mood and (turn_index - scene.mood_turn_index) >= 2:
        changes.append(f"recent_mood cleared (was '{scene.recent_mood}', expired after 2 turns)")
        scene.recent_mood = ""
        scene.mood_turn_index = 0

    # ── Hybrid intent memory — persist across turns for follow-up ─────
    # primary_intent and secondary_filters are stored so the NEXT turn
    # can inherit them (e.g. "anything with the kid?" follow-up to movies).
    primary_intent = state.primary_intent or ""
    secondary_intents = list(state.secondary_intents or [])
    modifiers = list(state.modifiers or [])
    is_topic_switch = state.intent.message_kind == "topic_switch"

    if primary_intent:
        if is_topic_switch and scene.active_primary_intent:
            # On explicit topic switch, replace (don't lock to old domain)
            changes.append(
                f"active_primary_intent: {scene.active_primary_intent} → {primary_intent} (topic_switch)"
            )
        scene.active_primary_intent = primary_intent
        changes.append(f"active_primary_intent={primary_intent}")

    elif is_topic_switch:
        # Explicit switch without a new primary intent → clear the lock
        # so the next turn doesn't inherit a stale factual domain.
        old = scene.active_primary_intent
        scene.active_primary_intent = ""
        changes.append(f"active_primary_intent cleared (topic_switch from {old})")

    # Secondary filters: audience/companion filters persist across topics;
    # domain-specific constraints are cleared on topic switch.
    _PERSISTENT_FILTERS = frozenset({
        "family_filter", "kid_friendly", "family_friendly",
        "budget_filter", "budget_sensitive",
        "romantic_filter", "romantic",
    })
    _DOMAIN_SPECIFIC_FILTERS = frozenset({
        "before_movie_constraint", "near_cinema", "near_cinema_preferred",
        "time_sensitive", "quick_stop_preferred",
    })

    if is_topic_switch:
        # Clear domain-specific filters; keep audience/companion filters
        surviving = [f for f in (scene.active_secondary_filters or [])
                     if f in _PERSISTENT_FILTERS]
        if surviving != scene.active_secondary_filters:
            changes.append(
                f"active_secondary_filters pruned on topic_switch: "
                f"{scene.active_secondary_filters} → {surviving}"
            )
        scene.active_secondary_filters = surviving

    if secondary_intents:
        # Merge, don't replace, so cumulative filters survive across turns
        existing = set(scene.active_secondary_filters or [])
        for f in secondary_intents:
            existing.add(f)
        scene.active_secondary_filters = list(existing)
        changes.append(f"active_secondary_filters={scene.active_secondary_filters}")

    if modifiers:
        existing_mods = set(scene.active_modifiers or [])
        for m in modifiers:
            existing_mods.add(m)
        scene.active_modifiers = list(existing_mods)
        changes.append(f"active_modifiers={scene.active_modifiers}")

    # ── Topic lock — maintain active topic coherence across turns ─────
    # The topic_lock is derived from the active_primary_intent and active_topic.
    # Rules:
    #   - New factual intent → lock to that intent (high confidence)
    #   - Follow-up / refinement → strengthen existing lock
    #   - Topic switch → clear the lock or reset to new topic
    #   - Context-setting → preserve existing lock (it's just scene enrichment)
    #   - Concierge intent → lock if no existing lock or same domain
    _LOCKABLE_INTENTS = frozenset({
        "movie_lookup", "location_lookup", "mall_fact_lookup",
        "service_lookup", "cross_mall_lookup", "offer_lookup",
        "dining_recommendation", "shopping_recommendation",
        "gift_shopping", "mall_overview", "discovery",
    })
    message_kind = state.intent.message_kind
    new_primary = state.primary_intent or ""

    if message_kind == "topic_switch":
        # Clear topic lock on explicit topic switch
        old_lock = scene.topic_lock
        scene.topic_lock = new_primary if new_primary in _LOCKABLE_INTENTS else ""
        scene.topic_lock_confidence = 0.7 if scene.topic_lock else 0.0
        changes.append(f"topic_lock reset: {old_lock} → {scene.topic_lock}")
    elif message_kind == "context_setting":
        # Context setting doesn't change the topic lock — it enriches the scene
        pass
    elif message_kind in ("followup", "refinement", "constraint_refinement"):
        # Follow-up strengthens existing lock
        if scene.topic_lock:
            scene.topic_lock_confidence = min(1.0, scene.topic_lock_confidence + 0.1)
            changes.append(f"topic_lock reinforced: {scene.topic_lock} conf={scene.topic_lock_confidence:.2f}")
    elif new_primary and new_primary in _LOCKABLE_INTENTS:
        # New lockable intent — establish or update lock
        if scene.topic_lock != new_primary:
            scene.topic_lock = new_primary
            scene.topic_lock_confidence = 0.8
            changes.append(f"topic_lock set: {new_primary} conf=0.8")
        else:
            # Same topic repeated → reinforce
            scene.topic_lock_confidence = min(1.0, scene.topic_lock_confidence + 0.1)
            changes.append(f"topic_lock reinforced: {scene.topic_lock}")

    # ── Scene acknowledgment — mark as acknowledged after first ack turn ─
    # Once the bot has opened with a scene-aware line, stop forcing it to
    # re-acknowledge companions on every subsequent turn.
    if state.response_plan.must_acknowledge_scene and not scene.scene_acknowledged:
        scene.scene_acknowledged = True
        changes.append("scene_acknowledged=True")

    # ── Persist selected playbook ─────────────────────────────────────
    if state.playbook.selected_playbook:
        scene.last_selected_playbook = state.playbook.selected_playbook
        changes.append(f"last_selected_playbook={scene.last_selected_playbook}")

    # ── Factual-flow memory updates ───────────────────────────────────
    if current_flow == "factual":
        # Update factual follow-up tracking fields
        if state.fact_scope:
            scene.active_fact_scope = state.fact_scope
            changes.append(f"active_fact_scope={state.fact_scope}")

        if state.fact_query_entity:
            scene.last_resolved_entity = state.fact_query_entity
            changes.append(f"last_resolved_entity={state.fact_query_entity!r}")

        if state.fact_entity_type:
            scene.last_resolved_entity_type = state.fact_entity_type
            changes.append(f"last_resolved_entity_type={state.fact_entity_type}")

        # Lightly update active_topic for follow-up coherence
        if state.intent.domain and state.intent.domain != "general":
            scene.active_topic = state.intent.domain
            changes.append(f"active_topic (factual): {state.intent.domain}")

        # Do NOT advance visit plan or update shortlists in factual flow
        result["scene"] = scene
        if state.response_plan.response_shape_hint:
            result["last_response_shape"] = state.response_plan.response_shape_hint

        result["_trace_summary"] = (
            f"Memory[factual]: {', '.join(changes) if changes else 'no changes'}"
        )
        return result

    # ── Domain exclusions — merge and persist across all turns ──────────
    # excluded_domains are durable: they accumulate and are NEVER cleared,
    # not even on topic_switch. Only an explicit reversal ("actually food is
    # fine") should remove them, which is not yet implemented.
    if state.scene.excluded_domains:
        existing_excluded = set(scene.excluded_domains or [])
        existing_excluded.update(state.scene.excluded_domains)
        scene.excluded_domains = list(existing_excluded)
        changes.append(f"excluded_domains={scene.excluded_domains}")

    # ── Concierge-flow memory updates (existing logic) ────────────────
    if state.intent.domain and state.intent.domain != "general":
        scene.active_topic = state.intent.domain
        changes.append(f"active_topic confirmed: {state.intent.domain}")

    if state.intent.message_kind == "correction" and scene.active_shortlist:
        scene.rejected_options.extend(scene.active_shortlist)
        scene.active_shortlist = []
        changes.append("moved shortlist to rejected (correction)")

    # Extract shortlist from response (top-5 entities from context)
    mentioned = _extract_mentioned_entities(state)
    if mentioned:
        scene.active_shortlist = mentioned[:5]
        changes.append(f"shortlist: {mentioned[:5]}")

    # ── Visit plan progression — mark current step as completed ───────
    _advance_completed_steps(state, scene, changes)

    result["scene"] = scene

    # ── Session continuity fields ─────────────────────────────────────
    if state.playbook.selected_playbook and state.playbook.playbook_confidence >= 0.3:
        result["last_successful_playbook"] = state.playbook.selected_playbook
        changes.append(f"last_successful_playbook={state.playbook.selected_playbook}")

    if state.response_plan.response_shape_hint:
        result["last_response_shape"] = state.response_plan.response_shape_hint
        changes.append(f"last_response_shape={state.response_plan.response_shape_hint}")

    # Update preferred_categories from successful category retrievals
    preferred = list(state.preferred_categories)
    is_category_retrieval = any(
        e.get("source", "").startswith("category/")
        for e in state.context.selected_entities
    )
    if is_category_retrieval:
        for entity in state.context.selected_entities[:3]:
            cat = entity.get("category", "")
            if cat and cat not in preferred:
                preferred.append(cat)
        preferred = preferred[-10:]  # keep last 10
        result["preferred_categories"] = preferred
        changes.append(f"preferred_categories updated: {preferred[-3:]}")

    result["_trace_summary"] = f"Memory: {', '.join(changes) if changes else 'no changes'}"
    return result


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
            mall_ctx = get_mall_context(state.mall_id)
            for etype in ("stores", "dining", "cinemas"):
                canonical = mall_ctx._builder._canonical.get(etype, [])
                for e in canonical:
                    if hasattr(e, "name") and e.name.lower() in response_lower:
                        mentioned.append(e.name)
        except RuntimeError:
            pass

    return mentioned[:10]
