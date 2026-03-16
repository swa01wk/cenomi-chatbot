"""
Generate Response node — builds the LLM prompt and produces the response.

CONTRACT
────────
  Purpose:  Assemble the full prompt from context, scene, playbook, strategy,
            retrieval results, response_contract, candidate_reason_map, and
            narrowing_followup.  Call the LLM and produce the final text.
            Consume response_contract strictly — all intro style, shortlist
            size, explanation style, location visibility, follow-up style,
            continuity, brochure-tone, and price-mode rules are binding.
  Reads:    messages, intent, scene, playbook, context, response_plan,
            response_contract, candidate_reason_map, narrowing_followup,
            continuity_anchor, retrieval, active_tenant_parameters
  Writes:   final_response_text, response_debug_summary,
            continuity_anchor (update last_strategy, last_playbook,
            last_successful_shortlist),
            messages (appends assistant Message)
  Failure:  LLM error → short fallback response + warning
  Routing:  Always → update_memory
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, Message
from app.nodes._tracing import traced_node
from app.prompts.builder import PromptBuilder

logger = logging.getLogger(__name__)

_prompt_builder = PromptBuilder()
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


@traced_node("generate_response")
async def generate_response(state: ConciergeState) -> dict:
    prompt_messages = _prompt_builder.build_messages(state)

    langchain_messages = []
    for msg in prompt_messages:
        if msg["role"] == "system":
            langchain_messages.append(SystemMessage(content=msg["content"]))
        elif msg["role"] == "user":
            langchain_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            langchain_messages.append(AIMessage(content=msg["content"]))

    warnings: list[str] = []

    try:
        llm = _get_llm()
        response = await llm.ainvoke(langchain_messages)
        final_text = response.content
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        final_text = _build_fallback_response(state)
        warnings.append(f"LLM call failed: {exc}")

    debug_summary = (
        f"strategy={state.response_plan.chosen_strategy} | "
        f"playbook={state.playbook.selected_playbook or 'none'} | "
        f"topics={state.context.selected_topic_blocks} | "
        f"entities={len(state.context.selected_entities)} | "
        f"retrieval={'yes' if state.retrieval.retrieval_needed else 'no'} | "
        f"contract_intro={state.response_contract.intro_style} | "
        f"contract_shortlist={state.response_contract.shortlist_size} | "
        f"reasons={len(state.candidate_reason_map)} | "
        f"narrowing={'yes' if state.narrowing_followup.is_useful else 'no'}"
    )

    assistant_msg = Message(
        role="assistant",
        content=final_text,
        turn_id=state.turn_id,
        metadata={
            "strategy": state.response_plan.chosen_strategy,
            "playbook": state.playbook.selected_playbook,
            "response_contract": state.response_contract.model_dump(),
        },
    )

    # Update continuity anchor with this turn's outputs
    anchor = state.continuity_anchor.model_copy(deep=True)
    anchor.last_strategy = state.response_plan.chosen_strategy
    anchor.last_playbook = state.playbook.selected_playbook
    entity_names = [
        e.get("name", "") for e in state.context.selected_entities if e.get("name")
    ]
    anchor.last_successful_shortlist = entity_names

    result: dict = {
        "final_response_text": final_text,
        "response_debug_summary": debug_summary,
        "continuity_anchor": anchor,
        "messages": [assistant_msg],
        "_trace_summary": (
            f"Generated {len(final_text)} chars via "
            f"{state.response_plan.chosen_strategy} | "
            f"contract enforced"
        ),
    }
    if warnings:
        result["_trace_warnings"] = warnings
    return result


def _build_fallback_response(state: ConciergeState) -> str:
    """Produce a graceful fallback when the LLM is unavailable."""
    domain = state.intent.domain
    entities = state.context.selected_entities
    reasons = {r.entity_name: r for r in state.candidate_reason_map}

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
        parts_list: list[str] = []
        for e in entities[:state.response_contract.shortlist_size]:
            name = e.get("name", "")
            reason_entry = reasons.get(name)
            if reason_entry and reason_entry.one_line_reason:
                parts_list.append(f"**{name}** — {reason_entry.one_line_reason}")
            elif name:
                parts_list.append(f"**{name}**")

        if parts_list:
            numbered = [f"{i+1}. {p}" for i, p in enumerate(parts_list)]
            body = "\n".join(numbered)

            followup = ""
            if state.narrowing_followup.is_useful:
                followup = f"\n\n{state.narrowing_followup.suggested_question}"
            elif state.response_contract.followup_style == "open_ended":
                followup = "\n\nWould you like more details on any of these?"

            return f"Here are my picks:\n\n{body}{followup}"

    return (
        "I'd love to help! Could you tell me a bit more about "
        "what you're looking for — shopping, dining, entertainment, or something else?"
    )
