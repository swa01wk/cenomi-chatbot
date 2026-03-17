"""
Generate Response node — builds the LLM prompt and produces the response.

CONTRACT
────────
  Purpose:  Assemble the full prompt from mall context, playbook resolution,
            retrieval results, and the user query.  Uses the concierge system
            prompt to ground the LLM in real mall data.
  Reads:    raw_user_message / normalized_user_message, playbook, retrieval,
            intent, context, response_plan, active_tenant_parameters
  Writes:   final_response_text, response_debug_summary,
            messages (appends assistant Message)
  Failure:  LLM error → short fallback response + warning
  Routing:  Always → update_memory
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, Message
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context
from guardrails.hallucination_guard import validate_response
from llm.prompts.concierge_prompt import (
    get_concierge_system_prompt,
    get_mall_overview_system_prompt,
)
from response.concierge_composer import ConciergeComposer

logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError(
                "BACKEND_OPENAI_API_KEY is not set. "
                "Check your .env file uses the BACKEND_ prefix."
            )
        _llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=settings.openai_temperature,
            api_key=settings.openai_api_key,
            max_tokens=1024,
        )
    return _llm


def _format_playbook(state: ConciergeState) -> str:
    """Summarize playbook resolution for the user prompt, including reasoning notes."""
    pb = state.playbook
    if not pb.selected_playbook:
        return "No specific playbook matched."

    parts = [
        f"Selected: {pb.selected_playbook} "
        f"(confidence {pb.playbook_confidence:.2f})",
        f"Matched: {', '.join(pb.matched_playbooks)}",
    ]

    try:
        mall_ctx = get_mall_context()
        playbook_obj = mall_ctx.match_playbook(
            intent=pb.selected_playbook,
            context_signals=[],
        )
        if playbook_obj:
            parts.append(f"Scenario: {playbook_obj.scenario}")
            if playbook_obj.response_shape_hint:
                parts.append(f"Response shape: {playbook_obj.response_shape_hint}")
            if playbook_obj.concierge_reasoning_notes:
                parts.append(
                    f"Concierge guidance: {playbook_obj.concierge_reasoning_notes}"
                )
            if playbook_obj.next_step_hint:
                parts.append(f"Next step hint: {playbook_obj.next_step_hint}")
    except Exception:
        pass

    return "\n".join(parts)


def _build_conversation_context(state: ConciergeState) -> str:
    """
    Build conversation context block that carries the full conversation
    frame into every turn — not just follow-ups.

    Includes visitor identity, visit constraints, visit plan/journey,
    and sequence-awareness so the LLM can behave like a true concierge.
    """
    parts: list[str] = []

    if state.scene.target_person:
        parts.append(
            f"Target person (who this query is about): {state.scene.target_person}"
        )
    if state.scene.companions:
        parts.append(f"Visitor is with: {', '.join(state.scene.companions)}")
    if state.scene.visit_type:
        parts.append(f"Visit type: {state.scene.visit_type}")
    if state.scene.audience:
        parts.append(f"Audience fit needed: {', '.join(state.scene.audience)}")
    if state.scene.occasion:
        parts.append(f"Occasion: {state.scene.occasion}")
    if state.scene.budget:
        parts.append(f"Budget: {state.scene.budget}")
    if state.scene.goal:
        parts.append(f"Current goal: {state.scene.goal}")
    if state.scene.visit_constraints:
        parts.append(
            f"Visit constraints: {', '.join(state.scene.visit_constraints)} "
            f"(tailor recommendations to honour these)"
        )

    # Follow-up context
    if state.intent.message_kind in ("followup", "refinement"):
        prev_need = state.scene.previous_need
        if prev_need:
            parts.append(f"Previous question: {prev_need}")
        if state.scene.active_shortlist:
            parts.append(
                f"Previously suggested: {', '.join(state.scene.active_shortlist)}"
            )
        if state.last_intent:
            parts.append(f"Previous topic: {state.last_intent}")

    if not parts:
        return ""

    frame_block = (
        "VISITOR CONTEXT (conversation frame — persists across the conversation):\n"
        + "\n".join(f"  • {p}" for p in parts)
        + "\n\n"
    )

    # ── Visit journey block ──────────────────────────────────────────
    journey_lines: list[str] = []
    if state.scene.visit_plan:
        journey_lines.append(
            f"Planned visit sequence: {' → '.join(state.scene.visit_plan)}"
        )
    if state.scene.completed_steps:
        journey_lines.append(
            f"Already covered in this conversation: {' → '.join(state.scene.completed_steps)}"
        )
    if state.scene.topic_history and len(state.scene.topic_history) > 1:
        recent = state.scene.topic_history[-4:]
        journey_lines.append(
            f"Conversation journey so far: {' → '.join(recent)}"
        )
    if state.scene.current_plan_step:
        journey_lines.append(
            f"Currently addressing: {state.scene.current_plan_step}"
        )

    if journey_lines:
        frame_block += (
            "VISIT JOURNEY (use this to maintain sequence coherence):\n"
            + "\n".join(f"  • {j}" for j in journey_lines)
            + "\n\n"
        )

    # ── Audience / target person directive ───────────────────────────
    if state.scene.target_person:
        frame_block += (
            f"CRITICAL: The visitor's current focus is on '{state.scene.target_person}'. "
            f"All recommendations MUST be appropriate for {state.scene.target_person}. "
        )
        if state.scene.target_person in ("child", "son", "daughter", "kids"):
            frame_block += (
                "This means kid-friendly options: places with kids menus, "
                "child-appropriate activities, and family-friendly environments. "
            )
        elif state.scene.target_person in ("girlfriend", "boyfriend", "wife", "husband"):
            frame_block += (
                "This means romantic/couple-friendly options or gift-appropriate "
                "suggestions depending on the query. "
            )
        frame_block += "\n\n"

    # ── Constraint directive ─────────────────────────────────────────
    if state.scene.visit_constraints:
        constraint_str = " and ".join(state.scene.visit_constraints)
        frame_block += (
            f"CONSTRAINT: The visitor wants something {constraint_str}. "
            "Keep suggestions focused, concise, and appropriate to this constraint. "
            "Do NOT recommend elaborate full-course dining for a 'quick' or 'light' query.\n\n"
        )

    # ── Follow-up / sequential directive ─────────────────────────────
    if state.intent.message_kind in ("followup", "refinement"):
        # If the current turn used structured category retrieval (e.g. "any
        # restaurants?", "kudu restaurant", "coffee?"), the user is asking a
        # DIRECT question — answer it.  Do NOT push the sequence forward.
        has_category_results = any(
            e.get("source", "").startswith("category/")
            for e in (state.context.selected_entities if state.context else [])
        )

        # Detect same-domain retry: user asks about a domain they already got
        # recommendations for (e.g. "any restaurants?" after "Dinner?").
        # Guard against treating this as a sequence-advance.
        _d2a = {"dining": "dining", "shopping": "shopping", "entertainment": "movie"}
        current_activity = _d2a.get(state.intent.domain, state.intent.domain)
        last_completed = (
            state.scene.completed_steps[-1] if state.scene.completed_steps else None
        )
        same_domain_retry = bool(last_completed and current_activity == last_completed)

        is_sequential = bool(state.scene.completed_steps or state.scene.visit_plan)

        if is_sequential and not has_category_results and not same_domain_retry:
            last_covered = last_completed or state.scene.active_topic or "previous topic"
            frame_block += (
                f"SEQUENCE CONTINUATION: The visitor has already explored {last_covered}. "
                "This is a CONTINUATION — build on the visit journey already in progress. "
                "Do NOT restart the conversation or re-suggest already covered topics. "
                "Pick up exactly where we left off.\n\n"
            )
        else:
            frame_block += (
                "This is a FOLLOW-UP query. Interpret it in the context of the "
                "previous conversation. Do NOT treat it as a standalone question.\n\n"
            )

    return frame_block


def _format_retrieval_results(state: ConciergeState) -> str:
    """Serialize retrieval results and selected entities into a text block."""
    parts: list[str] = []

    entities = state.context.selected_entities
    is_category = any(e.get("source", "").startswith("category/") for e in entities)
    is_discovery = any(
        e.get("source", "").startswith("related/") for e in entities
    ) or any(
        e.get("source", "") == "category/all_stores" for e in entities
    )
    is_offer = state.intent.sub_intent == "offer_details"

    # Category lookups: show all. Recommendations: cap at 10 for richer suggestions.
    if is_category or is_discovery:
        display_limit = len(entities)
    elif is_offer:
        display_limit = len(entities)
    else:
        display_limit = 10

    if is_discovery:
        parts.append(
            "[CURATED OPTIONS — present your TOP 5-6 recommendations with a brief "
            "reason why each fits this visitor. Group by type if helpful.]"
        )
    elif is_category:
        cat_key = entities[0].get("source", "").replace("category/", "") if entities else ""
        parts.append(
            f"[COMPLETE {cat_key.upper()} LIST — mention ALL of these "
            f"in your response. Do NOT add stores from other categories.]"
        )
    elif not is_offer and len(entities) > 5:
        parts.append(
            "[CURATED RECOMMENDATIONS — present your TOP 5-6 picks with a brief "
            "reason why each one fits the visitor's situation. Lead with your best 3, "
            "then naturally mention the remaining as further options.]"
        )

    for entity in entities[:display_limit]:
        name = entity.get("name", "Unknown")
        etype = entity.get("entity_type", "")
        category = entity.get("category", "")
        subcategory = entity.get("subcategory", "")
        description = entity.get("description", "")
        floor = entity.get("floor", "")
        zone = entity.get("zone", "")
        tags = entity.get("semantic_tags", [])
        notes = entity.get("concierge_notes", "")

        loc = f" — {floor}" if floor else ""
        if zone:
            loc += f", {zone}"

        cat_str = ""
        if category:
            cat_str = f" | {category}"
            if subcategory:
                cat_str += f" > {subcategory}"

        tag_str = f" [{', '.join(tags[:4])}]" if tags else ""
        line = f"- {name} ({etype}{cat_str}){loc}{tag_str}"
        if description:
            line += f"\n  {description[:150]}"
        elif notes:
            line += f"\n  Note: {notes[:150]}"
        parts.append(line)

    # Add retrieval facts, but skip offers if already present as entities
    # to avoid duplication
    offer_entity_ids = {
        e.get("entity_id") for e in entities
        if e.get("entity_type") in ("offer", "event")
    }
    for result in state.retrieval.retrieval_results:
        data = result.get("data")
        if not data:
            continue
        data_type = data.get("type", "")
        if data_type in ("active_offers", "events") and offer_entity_ids:
            continue
        parts.append(f"- [Fact] {json.dumps(data, default=str)[:300]}")

    return "\n".join(parts) if parts else "No specific tenants matched."


def _run_hallucination_guard(
    text: str, mall_ctx, warnings: list[str],
) -> str:
    """Validate LLM output against canonical data and return corrected text."""
    try:
        canonical = mall_ctx.get_canonical_for_guard()
        result = validate_response(text, canonical)
        if result.was_modified:
            warnings.append(
                f"Hallucination guard corrected {len(result.violations)} "
                f"violation(s): "
                + "; ".join(v.reason for v in result.violations[:3])
            )
            return result.corrected_text
    except Exception as exc:
        logger.warning("Hallucination guard failed: %s", exc)
        warnings.append(f"Hallucination guard error: {exc}")
    return text


async def _build_mall_info_response(
    state: ConciergeState,
) -> tuple[str, list[str]]:
    """
    Dedicated response path for ALL ``mall_info`` domain queries.

    Uses the mall profile, mall_overview topic block, and full canonical
    entity data to prevent hallucination.  Covers: overview, opening
    hours, facilities, family friendliness, what-is-available, etc.
    Returns (final_text, warnings).
    """
    warnings: list[str] = []
    mall_ctx = get_mall_context()

    composer = ConciergeComposer(mall_ctx)
    overview = composer.compose_mall_overview()

    system_prompt = get_mall_overview_system_prompt(overview.to_prompt_block())
    query = state.normalized_user_message or state.raw_user_message

    sub = state.intent.sub_intent
    focus_hint = ""
    if sub == "opening_hours":
        focus_hint = (
            "The visitor is asking about opening hours. "
            "Focus your answer on the hours data. "
        )
    elif sub == "facilities_summary":
        focus_hint = (
            "The visitor is asking about facilities and services. "
            "Focus your answer on the services & facilities data. "
        )
    elif sub == "family_friendliness":
        focus_hint = (
            "The visitor is asking if this mall is family-friendly. "
            "Focus your answer on family-friendly notes and relevant facilities. "
        )
    elif sub == "what_is_available":
        focus_hint = (
            "The visitor wants to know what they can find in this mall. "
            "Mention key zones, categories, and notable anchor tenants from the data. "
        )

    user_prompt = (
        f"Visitor question:\n{query}\n\n"
        f"{focus_hint}"
        "Respond using ONLY the overview data above. "
        "Do NOT invent any store names, services, floors, or details."
    )

    try:
        llm = _get_llm()
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        final_text = response.content
        final_text = _run_hallucination_guard(final_text, mall_ctx, warnings)
        return final_text, warnings
    except Exception as exc:
        logger.error("LLM call failed for mall info: %s", exc)
        warnings.append(f"LLM call failed: {exc}")
        return overview.render_text(), warnings


@traced_node("generate_response")
async def generate_response(state: ConciergeState) -> dict:
    mall_ctx = get_mall_context()
    warnings: list[str] = []

    is_mall_info = (
        state.response_plan.chosen_strategy == "mall_overview"
        or state.intent.domain == "mall_info"
    )

    if is_mall_info:
        final_text, info_warnings = await _build_mall_info_response(state)
        warnings.extend(info_warnings)
    else:
        mall_context = mall_ctx.get_canonical_for_prompt()
        system_prompt = get_concierge_system_prompt(mall_context)

        query = state.normalized_user_message or state.raw_user_message
        playbook = _format_playbook(state)
        retrieval_results = _format_retrieval_results(state)

        # Detect if this is a category-based retrieval turn
        is_category_turn = any(
            e.get("source", "").startswith("category/")
            for e in state.context.selected_entities
        )

        category_instruction = ""
        is_broad_discovery = any(
            "BROAD SHOPPING DISCOVERY" in n for n in state.context.ranking_notes
        )
        is_expanded_discovery = any(
            "EXPANDED DISCOVERY" in n for n in state.context.ranking_notes
        )

        is_offer_query = state.intent.sub_intent == "offer_details"

        if is_offer_query:
            category_instruction = (
                "IMPORTANT: The visitor is asking about offers, deals, or discounts. "
                "The 'Relevant tenants' section contains active offers and events. "
                "Present ALL offers with the store name, discount details, validity "
                "dates, and terms. If no offers matched for a specific store, "
                "acknowledge that gracefully (e.g. 'I don't have a current offer for "
                "that store') and then list any other active offers from the context — "
                "do NOT leave the visitor empty-handed. "
                "If genuinely no offers exist in the context, say: 'There are no "
                "active promotions listed right now, but it's worth asking individual "
                "stores directly — many run in-store deals not listed centrally.' "
                "NEVER say you don't have offer information if the context contains offers.\n\n"
            )
        elif is_broad_discovery:
            category_instruction = (
                "IMPORTANT: This is a broad shopping query. The 'Relevant tenants' "
                "section contains stores across multiple categories. Group them by "
                "category (e.g. Fashion, Beauty, Electronics, Jewelry) and present "
                "4-6 curated suggestions with a brief description and location. "
                "Make the mall feel rich and diverse.\n\n"
            )
        elif is_expanded_discovery:
            category_instruction = (
                "IMPORTANT: The 'Relevant tenants' section includes both direct "
                "matches and related suggestions. Present the direct matches first, "
                "then naturally suggest the related options (e.g. 'You might also "
                "enjoy...' or 'To pair with that...'). Aim for 4-6 total suggestions "
                "so the visitor has plenty of choice.\n\n"
            )
        elif is_category_turn:
            category_instruction = (
                "IMPORTANT: This is a category lookup. The 'Relevant tenants' "
                "section contains ALL matching tenants for the requested category. "
                "You MUST list ALL of them with a brief description and location. "
                "Do NOT add stores from other categories. Do NOT pad with "
                "unrelated suggestions like 'grab a coffee' or 'enjoy dessert'. "
                "Keep your answer focused on exactly what was asked.\n\n"
            )

        conversation_context = _build_conversation_context(state)

        experience_instruction = ""
        has_playbook = bool(state.playbook.selected_playbook)
        has_experience_context = bool(
            state.scene.companions
            or state.scene.occasion
            or state.scene.audience
            or state.scene.target_person
            or state.scene.visit_type
        )
        is_sequential_continuation = bool(
            state.scene.completed_steps or state.scene.visit_plan
        ) and state.intent.message_kind in ("followup", "refinement")
        is_multi_activity = state.scene.multi_activity_mode

        if is_sequential_continuation and not is_category_turn and not is_offer_query:
            last_covered = (
                state.scene.completed_steps[-1]
                if state.scene.completed_steps
                else state.scene.active_topic or "previous activity"
            )
            next_steps = ""
            if state.scene.visit_plan and state.scene.completed_steps:
                remaining = [
                    s for s in state.scene.visit_plan
                    if s not in state.scene.completed_steps
                ]
                if remaining:
                    next_steps = (
                        f" After this, the visitor's plan continues with: "
                        f"{' → '.join(remaining)}."
                    )
            experience_instruction = (
                f"SEQUENCE CONTINUATION: The visitor has already been guided through "
                f"{last_covered}. This current query continues their visit journey. "
                f"Do NOT re-suggest anything from previous steps. "
                f"Respond naturally as if you are continuing to walk them through the mall.{next_steps} "
                f"Keep the response focused and concierge-like.\n\n"
            )
        elif has_playbook and not is_category_turn and not is_offer_query:
            experience_instruction = (
                "ITINERARY MODE: A scenario playbook is active. You MUST "
                "generate a MINI-ITINERARY, not a flat list. Structure your "
                "response as a step-by-step experience flow:\n"
                "  1. Start with [first activity/place]\n"
                "  2. Then [second activity/place]\n"
                "  3. Finish with [third activity/place]\n"
                "Use natural transitions like 'Start at...', 'Then head to...', "
                "'Finish up with...'. Think like a concierge friend showing "
                "them around the mall. Include specific store names, floor "
                "locations, and brief reasons why each stop fits their situation.\n\n"
            )
        elif is_multi_activity and not is_category_turn and not is_offer_query:
            plan_str = " → ".join(state.scene.visit_plan) if state.scene.visit_plan else "their planned activities"
            experience_instruction = (
                f"VISIT PLAN MODE: The visitor has a multi-step plan: {plan_str}. "
                "Structure your response as a mini-itinerary that honours this sequence. "
                "Use natural transitions and be specific with store names, floors, and brief reasons. "
                "Do NOT reorder or contradict the planned sequence.\n\n"
            )
        elif has_experience_context and not is_category_turn and not is_offer_query:
            experience_instruction = (
                "EXPERIENCE GUIDANCE: The visitor has shared context about "
                "their situation. Frame your response as a guided experience "
                "flow, not a flat list. For example: 'Start with X → then Y "
                "→ finish with Z'. Think like a friend showing them around.\n\n"
            )

        user_prompt = (
            f"{conversation_context}"
            f"User query:\n{query}\n\n"
            f"Playbook plan:\n{playbook}\n\n"
            f"Relevant tenants:\n{retrieval_results}\n\n"
            f"{category_instruction}"
            f"{experience_instruction}"
            "Generate a helpful concierge response. "
            "Only mention stores, restaurants, and services from the "
            "mall context. Never invent names or details."
        )

        langchain_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        try:
            llm = _get_llm()
            response = await llm.ainvoke(langchain_messages)
            final_text = response.content
            final_text = _run_hallucination_guard(
                final_text, mall_ctx, warnings,
            )
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            final_text = _build_fallback_response(state)
            warnings.append(f"LLM call failed: {exc}")

    debug_summary = (
        f"strategy={state.response_plan.chosen_strategy} | "
        f"playbook={state.playbook.selected_playbook or 'none'} | "
        f"topics={state.context.selected_topic_blocks} | "
        f"entities={len(state.context.selected_entities)} | "
        f"retrieval={'yes' if state.retrieval.retrieval_needed else 'no'}"
    )

    assistant_msg = Message(
        role="assistant",
        content=final_text,
        turn_id=state.turn_id,
        metadata={
            "strategy": state.response_plan.chosen_strategy,
            "playbook": state.playbook.selected_playbook,
        },
    )

    result: dict = {
        "final_response_text": final_text,
        "response_debug_summary": debug_summary,
        "messages": [assistant_msg],
        "_trace_summary": (
            f"Generated {len(final_text)} chars via "
            f"{state.response_plan.chosen_strategy}"
        ),
    }
    if warnings:
        result["_trace_warnings"] = warnings
    return result


def _build_fallback_response(state: ConciergeState) -> str:
    """Produce a graceful fallback when the LLM is unavailable."""
    domain = state.intent.domain
    entities = state.context.selected_entities

    if domain == "exploration" and entities:
        dining = [e for e in entities if e.get("entity_type") in ("dining", "restaurant", "cafe")]
        shopping = [e for e in entities if e.get("entity_type") in ("store",)]
        entertainment = [e for e in entities if e.get("entity_type") in ("cinema", "entertainment")]

        parts = ["There's plenty to do here!"]
        if shopping:
            names = [e["name"] for e in shopping[:2]]
            parts.append(f"You can start with some shopping — {' and '.join(names)} are popular picks.")
        if dining:
            names = [e["name"] for e in dining[:2]]
            parts.append(f"For food, check out {' or '.join(names)}.")
        if entertainment:
            names = [e["name"] for e in entertainment[:1]]
            parts.append(f"And if you're in the mood for entertainment, {names[0]} is on the Third floor.")
        parts.append("What sounds interesting to you?")
        return "\n\n".join(parts)

    if entities:
        by_type: dict[str, list[str]] = {}
        for e in entities[:6]:
            etype = e.get("entity_type", "option")
            name = e.get("name", "")
            if name:
                by_type.setdefault(etype, []).append(name)

        parts = []
        for etype, names in by_type.items():
            parts.append(f"{', '.join(names[:3])}")
        if parts:
            return (
                f"Here are some suggestions: {'; '.join(parts)}. "
                f"Would you like more details on any of these?"
            )

    return (
        "I'd love to help! Could you tell me a bit more about "
        "what you're looking for — shopping, dining, entertainment, or something else?"
    )
