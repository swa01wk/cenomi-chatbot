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
import random

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, Message
from app.nodes._tracing import traced_node
from app.runtime import (
    build_merged_guard_canonical_for_configured_malls,
    get_all_mall_canonical_for_guard,
    get_mall_context,
)
from app.services.cta_generator import get_cta_instruction
from app.services.experience_resolver import (
    CURATED_SHORTLIST,
    DIRECT_LOOKUP,
    FACTUAL_LIST,
    FILTERED_FACTUAL_LIST,
    MICRO_ITINERARY,
    ROUTE_HINT,
    STRUCTURED_OVERVIEW,
    resolve_experience,
)
from guardrails.hallucination_guard import validate_response
from llm.prompts.concierge_prompt import (
    get_concierge_system_prompt,
    get_mall_overview_system_prompt,
)
from response.concierge_composer import ConciergeComposer

logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None

# Child companion identifiers used by the cross-domain continuity block.
_CHILD_COMPANIONS_CONTEXT: frozenset[str] = frozenset({
    "child", "kids", "son", "daughter",
})


def _build_prior_messages(
    state: "ConciergeState",
) -> list:
    """
    Return a list of HumanMessage / AIMessage objects for all prior turns
    in state.messages (i.e. everything except the last entry, which is the
    current user message appended by load_session).

    These are prepended to the LLM call so the model sees the full raw
    dialogue and is not limited to what SceneMemory managed to extract.
    """
    from app.models.state import Message  # local import to avoid circular

    prior = state.messages[:-1] if state.messages else []
    lc_msgs: list = []
    for msg in prior:
        if not isinstance(msg, Message):
            continue
        if msg.role == "user":
            lc_msgs.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            lc_msgs.append(AIMessage(content=msg.content))
    return lc_msgs


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


# ── Narrowing-clarification LLM (cached singleton, gpt-4o-mini) ──────
# A lightweight model used specifically to generate the ONE targeted
# clarifying question after the visitor confirms they want to narrow down.

_narrowing_llm: ChatOpenAI | None = None


def _get_narrowing_llm() -> ChatOpenAI:
    global _narrowing_llm
    if _narrowing_llm is None:
        settings = get_settings()
        _narrowing_llm = ChatOpenAI(
            model=settings.classifier_model,
            temperature=0.4,
            api_key=settings.openai_api_key,
            max_tokens=80,
        )
    return _narrowing_llm


async def _generate_narrowing_question(
    state: "ConciergeState",
    last_bot_text: str | None = None,
) -> str | None:
    """
    Generate the single most relevant targeting question when the visitor has
    confirmed they want to narrow down recommendations.

    When `last_bot_text` is provided (the previous bot message that contained the
    narrowing offer), the LLM reads it to discover which specific options were named
    and echoes them back as a direct choice question — e.g. if the bot offered
    "the absolute cheapest or the lightest bite", the response is
    "Which one — cheapest or lightest?"

    When no specific options were named in the prior message, the LLM falls back to
    generating the most context-aware question it can from scene data.

    Always returns a natural-language sentence. Never returns JSON or a list.
    Returns None on LLM failure (caller falls back to topic-keyed static templates).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    scene = state.scene
    task = scene.shopping_task

    context_parts: list[str] = []
    if scene.active_topic and scene.active_topic not in ("general", ""):
        context_parts.append(f"browsing topic: {scene.active_topic}")
    if scene.occasion:
        context_parts.append(f"occasion: {scene.occasion}")
    if scene.companions:
        context_parts.append(f"companions: {', '.join(scene.companions)}")
    if task.product_type:
        context_parts.append(f"product: {task.product_type}")
    if task.target_person:
        context_parts.append(f"for: {task.target_person}")
    if task.use_case:
        context_parts.append(f"use case: {task.use_case}")
    if task.style_preference:
        context_parts.append(f"style hints: {', '.join(task.style_preference)}")
    if scene.active_modifiers:
        context_parts.append(f"modifiers: {', '.join(scene.active_modifiers)}")
    if scene.active_shortlist:
        context_parts.append(
            f"options already shown: {', '.join(scene.active_shortlist[:3])}"
        )
    if scene.budget:
        context_parts.append(f"budget: {scene.budget}")

    scene_str = "; ".join(context_parts) if context_parts else "no specific context"

    system_prompt = (
        "You are a distinguished digital mall concierge. The guest has just replied "
        "with a short affirmation (e.g. 'sure', 'okay', 'yes go for it').\n\n"
        "STEP 1 — DETECTION (when a 'Last concierge message' is provided):\n"
        "Decide whether that message contained an offer to narrow, filter, shortlist, "
        "or categorise the recommendations. The offer can use ANY phrasing, for example:\n"
        "  • 'I can narrow this down to …'\n"
        "  • 'I can narrow these into the best …'\n"
        "  • 'I can turn this into a … shortlist'\n"
        "  • 'I can filter to …'\n"
        "  • 'I can focus on …'\n"
        "  • 'I can give you only …'\n"
        "  • 'Shall I narrow …?'\n"
        "If NO such offer appears in the last concierge message, output the single word PASS "
        "and nothing else. Stop here.\n\n"
        "STEP 2 — GENERATE (only if an offer was found):\n"
        "Reply with exactly ONE short, polished question that moves the "
        "conversation forward.\n"
        "  • If the offer named specific options (e.g. 'quick meal, coffee stop, or "
        "dessert spot'), echo them as a direct choice: "
        "'Which would you prefer — a quick meal, a coffee stop, or a dessert spot?'\n"
        "  • Strip filler words ('the best', 'a', 'an', 'the absolute') from labels.\n"
        "  • If no specific options were named, use the scene context to ask the most "
        "relevant, well-considered targeting question.\n\n"
        "ABSOLUTE RULES:\n"
        "- Output ONLY the question sentence or the word PASS. No preamble, no "
        "explanation, no JSON, no list.\n"
        "- Do NOT make recommendations. Do NOT ask more than one question.\n"
        "- Maintain a polished, composed tone — never casual.\n\n"
        "Examples:\n"
        "- Last concierge: 'I can narrow this to quick meal, coffee stop, or dessert stop' "
        "→ 'Which would you prefer — a quick meal, a coffee stop, or a dessert spot?'\n"
        "- Last concierge: 'I can turn this into a quick lunch or coffee shortlist' "
        "→ 'Which would suit you better — a quick lunch or coffee?'\n"
        "- Last concierge: 'I can narrow to the absolute cheapest or the lightest bite' "
        "→ 'Which appeals more — the most affordable option, or something lighter?'\n"
        "- Last concierge: 'Here are the dining options: …' (no narrowing offer) → PASS\n"
        "- Last concierge: 'My pleasure.' (no narrowing offer) → PASS\n"
        "- No last concierge message, fashion + wedding scene "
        "→ 'Is this for the bride, the groom, or another guest?'\n"
        "- No last concierge message, dining + kids → 'Would you prefer something quick or a sit-down meal?'"
    )

    human_prompt = (
        f"Scene context: {scene_str}\n"
        f"Guest confirmed: {state.normalized_user_message or 'yes'}"
    )
    if last_bot_text:
        human_prompt += f"\n\nLast bot message:\n{last_bot_text.strip()}"

    try:
        llm = _get_narrowing_llm()
        response = await llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
        )
        text = (response.content or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning(
            "Narrowing clarification LLM call failed, using static fallback: %s", exc
        )

    return None


# Explicit tone instructions keyed by playbook scenario name.
# These map the abstract scenario to concrete LLM behavioural guidance so the
# model doesn't have to infer tone from a scenario label alone.
_PLAYBOOK_TONE_MAP: dict[str, str] = {
    "family_day": (
        "TONE: Warm and reassuring, with the composure of a five-star concierge. "
        "Use clear, accessible language — a parent managing children needs confident, "
        "efficient guidance, not a lengthy exploration. Present safe, well-regarded choices "
        "and keep the cognitive load light. Polished but never stiff."
    ),
    "family_outing": (
        "TONE: Warm and reassuring, with the composure of a five-star concierge. "
        "Use clear, accessible language — a parent managing children needs confident, "
        "efficient guidance, not a lengthy exploration. Present safe, well-regarded choices "
        "and keep the cognitive load light. Polished but never stiff."
    ),
    "family_shopping": (
        "TONE: Warm and reassuring, with the composure of a five-star concierge. "
        "Use clear, accessible language — a parent managing children needs confident, "
        "efficient guidance, not a lengthy exploration. Present safe, well-regarded choices "
        "and keep the cognitive load light. Polished but never stiff."
    ),
    "date_night": (
        "TONE: Refined and warmly romantic. Speak with quiet confidence — no hesitation, "
        "no hedging. Lean toward premium, memorable, and occasion-worthy options. "
        "Frame the experience as something to look forward to, not a list of alternatives."
    ),
    "gift_hunt": (
        "TONE: Thoughtful and decisive — a trusted adviser who knows exactly what to suggest. "
        "Present one clear recommendation and one considered alternative. "
        "Explain in a single, confident line why each is right for the occasion. "
        "Never overwhelm with options; the guest wants a curated answer."
    ),
    "gift_shopping": (
        "TONE: Thoughtful and decisive — a trusted adviser who knows exactly what to suggest. "
        "Present one clear recommendation and one considered alternative. "
        "Explain in a single, confident line why each is right for the occasion. "
        "Never overwhelm with options; the guest wants a curated answer."
    ),
    "quick_visit": (
        "TONE: Composed but efficient. Respect the guest's time entirely — "
        "lead with the single best option, nearby and fast. "
        "No preamble, no padding. Polished brevity is the highest form of service here."
    ),
    "solo_explorer": (
        "TONE: Quietly curious and encouraging. This guest is here to discover. "
        "Highlight what is genuinely interesting or distinctive. "
        "Blend familiar anchors with lesser-known gems — give them a reason to explore."
    ),
    "luxury_vip": (
        "TONE: Impeccably refined. Every word should feel considered and exclusive. "
        "Lean toward the highest-end, most curated options the mall offers. "
        "Frame recommendations as bespoke selections — never generic, never rushed. "
        "The guest expects — and deserves — a personalised, world-class experience."
    ),
    "budget_conscious": (
        "TONE: Practical, reassuring, and respectful. Lead with excellent value-for-money picks — "
        "present them with the same care and confidence as any premium recommendation. "
        "Never suggest options outside the guest's stated budget. "
        "Good value is not a compromise; frame it as a smart, satisfying choice."
    ),
    "wedding_related": (
        "TONE: Elegant and occasion-aware. Every suggestion should feel worthy of the moment. "
        "Emphasise quality, craftsmanship, and style. "
        "Frame the visit as part of preparing for something truly special."
    ),
}


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
        mall_ctx = get_mall_context(state.mall_id)
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

    # Append tone guidance from _PLAYBOOK_TONE_MAP when available.
    # Check both the selected_playbook id and the scenario field for a match.
    playbook_key = pb.selected_playbook.lower().replace("-", "_").replace(" ", "_")
    tone = _PLAYBOOK_TONE_MAP.get(playbook_key, "")
    if not tone:
        # Try partial match (e.g. "family" matches "family_day")
        for key, t in _PLAYBOOK_TONE_MAP.items():
            if key in playbook_key or playbook_key in key:
                tone = t
                break
    if tone:
        parts.append(f"\n{tone}")

    return "\n".join(parts)


_ASSURANCE_STRATEGIES: frozenset[str] = frozenset({
    "guided_plan",
    "concise_shortlist",
    "gift_formula",
    "mini_itinerary",
    "movie_plus_food",
    "budget_plan",
    "route_plus_plan",
})

_LOYALTY_DOMAINS: frozenset[str] = frozenset({"shopping", "entertainment"})
_LOYALTY_SUB_INTENTS: frozenset[str] = frozenset({
    "gift_recommendation",
    "movie_showtime",
    "shopping_general",
    "offer_lookup",
    "offer_details",
})


def _build_assurance_instruction(state: ConciergeState) -> str:
    """
    Inject a prompt instruction asking the LLM to include a brief
    confidence-reinforcement sentence after its recommendations.

    Activates only for guided recommendation strategies. The assurance line
    reduces decision anxiety by grounding the visitor in facts about the
    mall layout, tenant range, or ease of access.
    """
    strategy = state.response_plan.chosen_strategy if state.response_plan else ""
    if strategy not in _ASSURANCE_STRATEGIES:
        return ""

    # Do not add assurance on category dumps or offer-only turns
    is_category_turn = any(
        e.get("source", "").startswith("category/")
        for e in (state.context.selected_entities if state.context else [])
    )
    if is_category_turn:
        return ""

    return (
        "ASSURANCE LINE: After your recommendations, add ONE brief, factual confidence "
        "line that reduces the guest's decision anxiety. Ground it in the mall — "
        "mention floor, proximity, range, or ease of access. Examples:\n"
        "  • 'Both stores are on the Ground floor — easy to reach from the main entrance.'\n"
        "  • 'Centrepoint carries a full kids' range — you'll find the right size.'\n"
        "  • 'The Food Court has plenty of family seating and is right next door.'\n"
        "Do NOT use filler like 'You won't be disappointed!' or 'A great choice awaits!'\n"
        "Keep it to one sentence. Place it after the recommendations, before the CTA.\n\n"
    )


def _build_loyalty_hint(state: ConciergeState) -> str:
    """
    Inject a conditional loyalty/rewards mention for shopping, cinema, and
    offer turns on the concierge path.

    Only fires when the visitor is in an active shopping or entertainment
    context — never for dining, navigation, or general queries.
    The LLM is instructed to include it only if loyalty data is actually
    present in the mall context to prevent hallucination.
    """
    domain = state.intent.domain or ""
    sub = state.intent.sub_intent or ""
    if domain not in _LOYALTY_DOMAINS and sub not in _LOYALTY_SUB_INTENTS:
        return ""

    return (
        "LOYALTY HINT (conditional): If the MALL CONTEXT mentions a loyalty, "
        "rewards, or points program, add ONE natural line at the end — for example: "
        "'If you have a Cenomi rewards card, you may earn points here.' "
        "Place it after the assurance line, before or after the CTA. "
        "Only include this if loyalty data is actually present in the mall context. "
        "NEVER fabricate a loyalty program that is not mentioned in the context.\n\n"
    )


def _build_dining_template_instruction(state: ConciergeState) -> str:
    """
    Inject dining-specific structure guidance for the LLM when the active
    domain is dining. Adds cuisine/mood grouping, reservation redirect, and
    time-of-day peak framing.

    Only activates on concierge dining turns — not factual lookups.
    """
    if state.intent.domain != "dining":
        return ""
    if state.flow_type == "factual":
        return ""

    # Do not add on category list dumps (e.g. "show all cafes")
    is_category_turn = any(
        e.get("source", "").startswith("category/")
        for e in (state.context.selected_entities if state.context else [])
    )
    if is_category_turn:
        return ""

    time_of_day = getattr(state.scene, "time_of_day", "")
    has_child = bool(
        any(c in {"child", "kids", "son", "daughter"} for c in state.scene.companions)
        or any(d.get("type") == "child" for d in state.scene.companion_details)
    )

    lines: list[str] = [
        "DINING RESPONSE FORMAT:",
        "  • Group suggestions by mood or cuisine type — e.g. 'Quick Service / "
        "Family Fare / Casual Bites' — rather than a flat numbered list.",
    ]

    if has_child:
        lines.append(
            "  • Family filter: max 3 sit-down restaurants. Do NOT list kiosks or "
            "snack stands as primary meal options for a family with a child."
        )

    lines.append(
        "  • If any suggested restaurant typically requires a reservation or has "
        "table service, note: 'Best to ask at the counter or book ahead.'"
    )

    if time_of_day == "evening":
        lines.append(
            "  • Evening context: note that dining areas are busiest 7–9 PM. "
            "Suggest arriving slightly early or mention a quieter alternative if available."
        )
    elif time_of_day == "morning":
        lines.append(
            "  • Morning context: lead with breakfast or brunch-friendly options if available."
        )
    elif time_of_day == "late_night":
        lines.append(
            "  • Late-night context: confirm venues are open; prioritise fast-casual "
            "or 24-hour options over sit-down restaurants that may be closing."
        )

    return "\n".join(lines) + "\n\n"


def _build_cinema_template_instruction(state: ConciergeState) -> str:
    """
    Inject cinema/entertainment-specific structure guidance.

    Adds format info (IMAX/VIP/Standard) and a booking redirect for movie
    queries, both on the factual and concierge paths.
    """
    domain = state.intent.domain or ""
    sub = state.intent.sub_intent or ""
    fact_scope = state.fact_scope or ""

    is_cinema_turn = (
        domain == "entertainment"
        or sub in ("movie_showtime", "movie_recommendation", "cinema_lookup")
        or fact_scope in ("movie_schedule", "cinema_lookup")
    )
    if not is_cinema_turn:
        return ""

    has_child = bool(
        any(c in {"child", "kids", "son", "daughter"} for c in state.scene.companions)
        or any(d.get("type") == "child" for d in state.scene.companion_details)
    )

    lines: list[str] = [
        "CINEMA RESPONSE FORMAT:",
        "  • For each movie, include available formats if present in the data "
        "(e.g. Standard / IMAX / VIP) — one line per format type.",
        "  • End with a booking redirect: 'Tickets available at the cinema counter "
        "or via the Muvi app — book ahead for peak times.'",
    ]

    if has_child:
        lines.append(
            "  • Child present: only recommend films appropriate for the child's age. "
            "Do NOT suggest sports broadcasts or adult-rated thrillers as children's options. "
            "Do NOT suggest leaving the child at a play area while parents watch a film "
            "on a separate floor — the child requires supervision."
        )

    return "\n".join(lines) + "\n\n"


def _build_scene_acknowledgment(state: ConciergeState) -> str:
    """
    Build a brief scene-acknowledgment hint for the LLM.

    This primes the LLM to open with a natural one-liner that acknowledges
    the visitor's situation before recommending.
    """
    scene = state.scene
    parts: list[str] = []

    # Child companion + shopping/dining — use gendered label when available
    _child_kinds = {"child", "kids", "son", "daughter"}
    child_label = "child"
    for _c in scene.companions:
        if _c in _child_kinds:
            child_label = _c  # e.g. "daughter", "son", "kids"
            break
    has_child = (
        any(c in _child_kinds for c in scene.companions)
        or any(d.get("type") in _child_kinds for d in scene.companion_details)
    )

    child_age = None
    if scene.companion_details:
        for d in scene.companion_details:
            if d.get("type") in _child_kinds and d.get("age"):
                child_age = d["age"]
                break

    if has_child:
        age_part = f" (age {child_age})" if child_age else ""
        parts.append(f"visiting with {child_label}{age_part}")

    partner_companions = {"girlfriend", "boyfriend", "wife", "husband"}
    for comp in scene.companions:
        if comp in partner_companions:
            parts.append(f"visiting with {comp}")
            break

    if getattr(scene, "user_role", ""):
        parts.append(f"role: {scene.user_role}")
    if getattr(scene, "scenario", "") and scene.scenario not in ("", "quick_visit", "first_visit"):
        parts.append(f"scenario: {scene.scenario.replace('_', ' ')}")
    if scene.occasion and scene.occasion not in ("casual",):
        parts.append(f"occasion: {scene.occasion}")

    if scene.implicit_goal:
        parts.append(f"implicit goal: {scene.implicit_goal}")
    elif scene.goal:
        parts.append(f"goal: {scene.goal}")

    if not parts:
        return ""

    situation = ", ".join(parts)
    # Build a gendered-child instruction when applicable.
    gendered_child_note = ""
    if child_label in ("daughter", "son"):
        gendered_child_note = (
            f"IMPORTANT: The child companion is specifically the guest's {child_label}. "
            f"ALWAYS use 'your {child_label}' in the response — do NOT say 'your child' or just 'your X-year-old'.\n"
        )
    return (
        "SCENE ACKNOWLEDGMENT REQUIRED:\n"
        f"Guest situation: {situation}.\n"
        f"{gendered_child_note}"
        "Your opening sentence MUST acknowledge this situation naturally — "
        "but NEVER use 'Since you're...', 'Given you're...', 'As you're...', or 'Because you're...'.\n"
        "Instead rotate through these polished opener styles:\n"
        "  • Lead with the destination:   'Head to Centrepoint — an excellent selection of kids' jackets on the Ground Floor.'\n"
        "  • Lead with the person/group:  'For your daughter, the finest picks are on the Ground Floor.'\n"
        "  • Lead with the need:          'For something within budget, here are your three strongest options:'\n"
        "  • Lead with an action:         'Start at Red Tag for strong value picks, then step into Max right next door.'\n"
        "  • Lead with a direct answer:   'Muvi Cinema on the Upper Level is the ideal choice for a family screening.'\n"
        "Do NOT start with 'Great!', 'Sure!', 'Of course!', 'Absolutely!', 'Hey', 'Hi there', or any casual phrase.\n\n"
    )


def _build_conversation_context(state: ConciergeState) -> str:
    """
    Build conversation context block that carries the full conversation
    frame into every turn — not just follow-ups.

    Includes visitor identity, visit constraints, visit plan/journey,
    and sequence-awareness so the LLM can behave like a true concierge.
    """
    parts: list[str] = []

    if getattr(state.scene, "user_role", ""):
        parts.append(f"Guest role: {state.scene.user_role}")
    if getattr(state.scene, "scenario", ""):
        parts.append(f"Visit scenario: {state.scene.scenario.replace('_', ' ')}")
    if getattr(state.scene, "style_intent", []):
        parts.append(f"Style/aesthetic preference: {', '.join(state.scene.style_intent)}")
    if state.scene.target_person:
        parts.append(
            f"Target person (who this query is about): {state.scene.target_person}"
        )
    if state.scene.companions:
        parts.append(f"Guest is with: {', '.join(state.scene.companions)}")
        # Explicit gender guard — prevents LLM from inverting partner gender
        partner_found = [
            c for c in state.scene.companions
            if c in {"girlfriend", "boyfriend", "wife", "husband"}
        ]
        if partner_found:
            parts.append(
                f"COMPANION GENDER: The guest's partner is '{partner_found[0]}'. "
                f"Always refer to them as '{partner_found[0]}' — "
                f"NEVER substitute with a different gendered term (e.g. do NOT say "
                f"'boyfriend' if the partner is a 'girlfriend', and vice versa)."
            )
        # After the first scene acknowledgment, companions are background context
        # for filtering — not something to call out explicitly in every response.
        if state.scene.scene_acknowledged:
            parts.append(
                "COMPANION USAGE: Companion context above is for FILTERING only. "
                "Do NOT explicitly say 'with your child', 'for you and your son', "
                "'easy for a parent with a child', or similar phrases in the response. "
                "The guest is aware of who they are with — just tailor the recommendation silently."
            )
    if state.scene.companion_details:
        detail_strs = [
            f"{d.get('type', 'companion')} age {d['age']}" if d.get("age") else d.get("type", "companion")
            for d in state.scene.companion_details
        ]
        parts.append(f"Companion details: {', '.join(detail_strs)}")
    if state.scene.visit_type:
        parts.append(f"Visit type: {state.scene.visit_type}")
    if state.scene.audience:
        parts.append(f"Audience fit needed: {', '.join(state.scene.audience)}")
    if state.scene.occasion:
        parts.append(f"Occasion: {state.scene.occasion}")
    if state.scene.budget:
        parts.append(f"Budget: {state.scene.budget}")
    if getattr(state.scene, "time_of_day", ""):
        parts.append(f"Time of day: {state.scene.time_of_day}")
    if state.scene.pace:
        parts.append(f"Pace: {state.scene.pace}")
    if state.scene.implicit_goal:
        parts.append(f"Implicit goal: {state.scene.implicit_goal}")
    elif state.scene.goal:
        parts.append(f"Current goal: {state.scene.goal}")
    if state.scene.visit_constraints:
        parts.append(
            f"Visit constraints: {', '.join(state.scene.visit_constraints)} "
            f"(tailor recommendations to honour these)"
        )

    # Follow-up context
    if state.intent.message_kind in ("followup", "refinement", "constraint_refinement"):
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
        recent = state.scene.topic_history
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
            f"CRITICAL: The guest's current focus is on '{state.scene.target_person}'. "
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

    # ── Active shopping task guard ────────────────────────────────────
    # When a specific product shopping task is in progress (e.g. jacket, shoes),
    # lock the response to that product. Prevents gift_for or other secondary
    # intents from pivoting the response to unrelated gift recommendations.
    _st = state.scene.shopping_task
    if _st and _st.product_type and state.intent.domain == "shopping":
        _task_parts = [_st.product_type]
        if _st.target_person:
            _task_parts.append(f"for {_st.target_person}")
        if _st.budget_preference:
            _task_parts.append(f"budget: {_st.budget_preference}")
        frame_block += (
            f"ACTIVE SHOPPING TASK: The guest is shopping for a "
            f"{' '.join(_task_parts)}. "
            f"ALL recommendations MUST be stores that sell {_st.product_type}. "
            f"Do NOT pivot to gift shops, accessories, jewellery, or other product "
            f"categories unless the guest explicitly changes the request. "
            f"Context refinements like gender or age ('he is a boy') narrow "
            f"the {_st.product_type} search — they do NOT change the product type.\n\n"
        )

    # ── Constraint directive ─────────────────────────────────────────
    if state.scene.visit_constraints:
        constraint_str = " and ".join(state.scene.visit_constraints)
        frame_block += (
            f"CONSTRAINT: The guest's preference is for something {constraint_str}. "
            "Keep suggestions focused, concise, and appropriate to this constraint. "
            "Do NOT recommend elaborate full-course dining for a 'quick' or 'light' query.\n\n"
        )

    # ── Hard exclusion directive ──────────────────────────────────────
    # Injected when the user has explicitly refused one or more domains.
    # This is a HARD constraint — it must be respected unconditionally and
    # carries through every turn for the remainder of the session.
    if state.scene.excluded_domains:
        domains_str = " and ".join(state.scene.excluded_domains)
        plural = "these categories" if len(state.scene.excluded_domains) > 1 else "this category"
        frame_block += (
            f"HARD CONSTRAINT — DOMAIN EXCLUSION: The guest has EXPLICITLY declined "
            f"{domains_str} recommendations. "
            f"DO NOT suggest, mention, or allude to any {domains_str} options under ANY "
            f"circumstances, even indirectly. "
            f"If the entity list below contains {plural}, ignore them entirely. "
            f"If there are no suitable alternatives available outside {plural}, respond with a "
            f"brief, composed acknowledgement and invite the guest to share what they are "
            f"looking for instead — do NOT default back to {plural}.\n\n"
        )

    # ── Follow-up / sequential directive ─────────────────────────────
    if state.intent.message_kind in ("followup", "refinement"):
        has_category_results = any(
            e.get("source", "").startswith("category/")
            for e in (state.context.selected_entities if state.context else [])
        )

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
                f"SEQUENCE CONTINUATION: The guest has already explored {last_covered}. "
                "This is a CONTINUATION — build on the visit journey already in progress. "
                "Do NOT restart the conversation or re-suggest already covered topics. "
                "Continue seamlessly from where we left off.\n\n"
            )
        else:
            frame_block += (
                "This is a FOLLOW-UP query. Interpret it in the context of the "
                "previous conversation. Do NOT treat it as a standalone question.\n\n"
            )

    # ── Cross-domain continuity block ────────────────────────────────
    # When topic_history spans more than one domain AND companions are set,
    # the visitor's audience requirements must carry into the new domain.
    # This makes the constraint explicit rather than relying on the LLM to
    # infer it from the companion list alone.
    if (
        len(state.scene.topic_history) >= 2
        and state.scene.companions
        and state.intent.domain
        and state.intent.domain != state.scene.topic_history[-2]
    ):
        current_domain = state.intent.domain
        companions_str = ", ".join(state.scene.companions)
        has_child_cross = bool(
            _CHILD_COMPANIONS_CONTEXT & set(state.scene.companions)
            or any(d.get("type") == "child" for d in state.scene.companion_details)
        )

        cross_domain_constraints: list[str] = []

        if has_child_cross:
            if current_domain == "dining":
                cross_domain_constraints.append(
                    "food options MUST be kid-friendly (kids menus, family seating, "
                    "casual pace) — NOT fine-dining or adult-only venues. "
                    "Recommend no more than 3 dining venues; prioritise sit-down "
                    "restaurants suitable for families over kiosks or snack stands."
                )
            elif current_domain == "entertainment":
                cross_domain_constraints.append(
                    "entertainment MUST be suitable for children — family-rated movies "
                    "or kid-friendly activities. Do NOT suggest parents watch a movie at "
                    "the cinema while the child plays at a separate activity area on a "
                    "different floor — a young child requires adult supervision at all times. "
                    "Recommend activities the whole family can enjoy together, or if "
                    "suggesting a play area, make clear a parent must accompany the child."
                )
            elif current_domain == "shopping":
                cross_domain_constraints.append(
                    "shopping suggestions should include child-appropriate options "
                    "or stores the family can browse together"
                )

        budget = state.scene.budget or (
            state.scene.shopping_task.budget_preference
            if state.scene.shopping_task else ""
        )
        if budget in ("budget", "affordable") and current_domain == "dining":
            cross_domain_constraints.append(
                "dining options MUST be budget-friendly / casual — "
                "NOT premium restaurants or fine-dining"
            )

        if cross_domain_constraints:
            frame_block += (
                f"CROSS-DOMAIN CONTINUITY: You are now helping with {current_domain}. "
                f"This means: {'; '.join(cross_domain_constraints)}. "
                "Apply these constraints to ALL recommendations in this response.\n\n"
            )

    # ── Context-setting directive ─────────────────────────────────────
    # When the user has declared their role, companions, or occasion without
    # requesting a specific action, the response MUST:
    #   1. Acknowledge the context naturally (1 sentence, no filler)
    #   2. Offer concrete next-step options relevant to their situation
    # It must NOT produce a narrow single recommendation or force a category.
    if state.intent.message_kind == "context_setting":
        scene = state.scene
        role_part = f" as a {scene.user_role}" if scene.user_role else ""
        scenario_part = (
            f" for a {scene.scenario.replace('_', ' ')}"
            if scene.scenario and scene.scenario not in ("", "quick_visit")
            else ""
        )
        companion_part = (
            f" with {', '.join(scene.companions)}"
            if scene.companions
            else ""
        )
        style_part = (
            f" looking for {', '.join(scene.style_intent)} options"
            if scene.style_intent
            else ""
        )
        context_summary = f"{role_part}{scenario_part}{companion_part}{style_part}".strip()
        frame_block += (
            "CONTEXT-SETTING RESPONSE REQUIRED:\n"
            f"The guest is sharing their situation{': ' + context_summary if context_summary else ''}.\n"
            "Your response MUST:\n"
            "  1. Acknowledge their context in ONE composed, natural sentence "
            "(no 'Great!', 'Sure!', 'Absolutely!', 'Hey', or casual filler).\n"
            "  2. Offer 2-3 relevant next-step options they can choose from "
            "(e.g. 'I can assist with elegant accessories, gifts, or beauty — which would you like to explore first?').\n"
            "  3. Do NOT immediately present a specific recommendation list.\n"
            "  4. Do NOT treat this as a product search or force a narrow category.\n"
            "  5. Match the tone and register to their stated role/occasion.\n\n"
        )

    # ── Constraint refinement directive ──────────────────────────────
    if state.intent.message_kind == "constraint_refinement":
        new_constraints = state.scene.visit_constraints
        if state.scene.active_shortlist:
            frame_block += (
                "CONSTRAINT REFINEMENT: The guest is refining a previous suggestion, NOT starting fresh.\n"
                f"Previous suggestions: {', '.join(state.scene.active_shortlist)}\n"
                f"New constraint to apply: {', '.join(new_constraints) if new_constraints else 'unstated'}\n"
                "DO NOT discard the previous suggestions entirely. Instead, filter or replace them "
                "with options that better satisfy the new constraint. Acknowledge the refinement naturally — "
                "e.g. 'For something a little quicker, I would suggest...'\n\n"
            )
        else:
            frame_block += (
                "CONSTRAINT REFINEMENT: The guest is adding a constraint to their current request. "
                f"Constraints: {', '.join(new_constraints) if new_constraints else 'unstated'}\n"
                "Adjust recommendations accordingly.\n\n"
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

    # Use entity_cap from response_plan for contextual queries
    strategy = state.response_plan.chosen_strategy
    entity_cap = state.response_plan.entity_cap if state.response_plan.entity_cap > 0 else 10

    if is_category or is_discovery:
        display_limit = len(entities)
    elif is_offer:
        display_limit = len(entities)
    elif strategy in ("guided_plan", "concise_shortlist", "quick_answer", "route_plus_plan"):
        display_limit = entity_cap
    else:
        display_limit = min(10, len(entities))

    if is_discovery:
        parts.append(
            "[CURATED OPTIONS — present your TOP 5-6 recommendations with a brief "
            "reason why each is the right choice for this guest. Group by type if helpful.]"
        )
    elif is_category:
        cat_key = entities[0].get("source", "").replace("category/", "") if entities else ""
        parts.append(
            f"[COMPLETE {cat_key.upper()} LIST — mention ALL of these "
            f"in your response. Do NOT add stores from other categories.]"
        )
    elif strategy in ("guided_plan",) and len(entities) > 0:
        parts.append(
            f"[GUIDED PLAN ENTITIES — use at most {display_limit} of these in your plan. "
            f"Pick the most relevant. Do NOT list all of them — weave them into a narrative plan.]"
        )
    elif not is_offer and len(entities) > 5:
        parts.append(
            f"[CURATED RECOMMENDATIONS — present your TOP {min(display_limit, 5)} picks with a brief "
            "reason why each one is right for this guest. Lead with your best 3, "
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

    # Add retrieval facts
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
    text: str,
    mall_ctx,
    warnings: list[str],
    canonical_override: dict | None = None,
) -> str:
    try:
        canonical = canonical_override if canonical_override is not None else mall_ctx.get_canonical_for_guard()
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


async def _build_factual_response(
    state: ConciergeState,
) -> tuple[str, list[str], str]:
    """
    Dedicated response path for factual-flow queries.

    Produces direct, compact, structured answers grounded in exact retrieved facts.
    Does NOT use concierge playbook reasoning or semantic padding.

    Returns (response_text, warnings, experience_mode).
    """
    warnings: list[str] = []
    mall_ctx = get_mall_context(state.mall_id)
    mall_context = mall_ctx.get_canonical_for_prompt()
    system_prompt = get_concierge_system_prompt(mall_context)

    scope = state.fact_scope or "store_lookup"

    # For cross-mall availability queries, append a suppression directive to the
    # system prompt so the LLM does not infer brand presence from zone description
    # text (e.g. "Food Court: McDonald's, Kudu, Popeyes...") — it must rely solely
    # on the structured CROSS-MALL BRAND AVAILABILITY data in the user prompt.
    if scope == "cross_mall_availability" or state.intent.domain == "cross_mall":
        system_prompt = (
            system_prompt
            + "\n\nCROSS-MALL AVAILABILITY MODE: The mall zone descriptions above "
            "are for tone and persona only. To answer which malls carry something, "
            "use ONLY the structured CROSS-MALL BRAND AVAILABILITY section in the "
            "user message. Do NOT infer availability from zone description text. "
            "When 'AT OTHER CENOMI MALLS' contains results, those ARE the answer — "
            "present them clearly and do NOT add an apologetic opening paragraph. "
            "Only say something is unavailable at the current mall as a brief "
            "one-sentence note before listing where it IS found."
        )
    response_mode = state.fact_response_mode or "direct_lookup"
    query_entity = state.fact_query_entity or ""
    fact_ctx = getattr(state, "fact_context", {}) or {}
    strategy = state.response_plan.chosen_strategy or "direct_lookup"
    query = state.normalized_user_message or state.raw_user_message

    secondary_intents = list(state.secondary_intents or [])
    modifiers = list(state.modifiers or [])

    # ── Experience layer resolution ───────────────────────────────────
    has_child = (
        "child" in state.scene.companions
        or any(d.get("type") == "child" for d in state.scene.companion_details)
    )
    exp = resolve_experience(
        flow_type="factual",
        primary_intent=state.primary_intent or "",
        secondary_intents=secondary_intents,
        modifiers=modifiers,
        response_strategy=state.response_strategy or "",
        chosen_strategy=strategy,
        message_kind=state.intent.message_kind,
        domain=state.intent.domain,
        sub_intent=state.intent.sub_intent,
        fact_scope=scope,
        has_companions=bool(state.scene.companions),
        has_occasion=bool(state.scene.occasion),
        has_child=has_child,
        visit_constraints=list(state.scene.visit_constraints or []),
        active_shortlist=list(state.scene.active_shortlist or []),
    )

    # ── Build the factual context block ──────────────────────────────
    fact_block = _format_fact_context(fact_ctx, state)

    # ── Build response mode instruction (experience-aware) ────────────
    mode_instruction = _build_factual_mode_instruction(
        scope, response_mode, strategy, query_entity, fact_ctx,
        secondary_intents=secondary_intents,
        modifiers=modifiers,
        experience_mode=exp.response_experience_mode,
    )

    # ── Refinement acknowledgement hint ──────────────────────────────
    refinement_hint = ""
    if exp.refinement_acknowledgement:
        refinement_hint = (
            f"REFINEMENT CONTEXT: {exp.refinement_acknowledgement}. "
            "Acknowledge the refinement naturally in your opening — "
            "e.g. 'For a child...' or 'If you're looking for something quicker...'\n\n"
        )

    retrieval_ok = fact_ctx.get("retrieval_succeeded", False)
    if not retrieval_ok:
        no_data_note = (
            "\nNOTE: No exact data was retrieved for this query. "
            "Be honest — say you don't have that specific information available. "
            "Do NOT invent data. If you can direct the guest to ask a member of staff, do so.\n"
        )
    else:
        no_data_note = ""

    # ── Hybrid modifier instructions ──────────────────────────────────
    hybrid_instruction = _build_hybrid_filter_instruction(fact_ctx, state)

    # ── CTA instruction ───────────────────────────────────────────────
    cta_instruction = get_cta_instruction(exp.cta_type)

    user_prompt = (
        f"Guest question: {query}\n\n"
        f"Fact scope: {scope}\n"
        f"Expected response mode: {response_mode}\n\n"
        f"Retrieved facts:\n{fact_block}\n\n"
        f"{no_data_note}"
        f"{refinement_hint}"
        f"{mode_instruction}"
        f"{hybrid_instruction}"
        f"{cta_instruction}"
        "IMPORTANT: Use ONLY the facts above. "
        "Do NOT add semantic suggestions, dining recommendations, "
        "or 'while you're here' padding unless the scope explicitly allows it. "
        "Be direct, compact, and truthful."
    )

    try:
        llm = _get_llm()
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        final_text = response.content
        if scope == "cross_mall_availability":
            merged = await build_merged_guard_canonical_for_configured_malls()
            final_text = _run_hallucination_guard(
                final_text, mall_ctx, warnings, canonical_override=merged
            )
        else:
            final_text = _run_hallucination_guard(final_text, mall_ctx, warnings)
    except Exception as exc:
        logger.error("LLM call failed for factual response: %s", exc)
        warnings.append(f"LLM call failed: {exc}")
        final_text = _build_factual_fallback(state, fact_ctx)

    return final_text, warnings, exp.response_experience_mode


def _genre_str(genre) -> str:
    """Normalize genre to a plain string whether it arrives as list or str."""
    if isinstance(genre, list):
        return " ".join(str(g) for g in genre)
    return str(genre) if genre else ""


def _classify_movie_genre(genre, title: str = "") -> str:
    """Classify a movie into a broad category for grouping."""
    g = _genre_str(genre).lower()
    t = (title or "").lower()
    if any(kw in g for kw in ("animation", "animated", "family", "kids")):
        return "Family / Animation"
    if any(kw in t for kw in ("panda", "kung fu", "moana", "inside out", "toy story", "shrek")):
        return "Family / Animation"
    if any(kw in g for kw in ("horror", "thriller")):
        return "Horror / Thriller"
    if any(kw in g for kw in ("action", "adventure", "superhero")):
        return "Action / Adventure"
    if any(kw in g for kw in ("comedy",)):
        return "Comedy"
    if any(kw in g for kw in ("romance", "romantic", "drama")):
        return "Drama / Romance"
    if any(kw in g for kw in ("documentary",)):
        return "Documentary"
    return "Other"


def _format_fact_context(fact_ctx: dict, state) -> str:
    """Serialize the fact_context into a structured text block for the LLM."""
    import json as _json
    parts: list[str] = []

    if fact_ctx.get("scope") == "cross_mall_availability" or (
        "cross_mall_at_home" in fact_ctx or "cross_mall_other" in fact_ctx
    ):
        brand = fact_ctx.get("cross_mall_brand_query") or fact_ctx.get("query_entity") or ""
        at_home = fact_ctx.get("cross_mall_at_home") or []
        other = fact_ctx.get("cross_mall_other") or []

        # Summary line — makes the hit/miss split unambiguous for the LLM
        if at_home and other:
            summary = f"FOUND at current mall ({len(at_home)}) AND at {len(other)} other Cenomi mall(s)"
        elif at_home:
            summary = f"FOUND at current mall ({len(at_home)} result(s)) — NOT at other malls"
        elif other:
            summary = f"NOT at current mall — FOUND at {len(other)} other Cenomi mall(s)"
        else:
            summary = "NOT found at any configured Cenomi mall"

        parts.append("=== CROSS-MALL AVAILABILITY ===")
        parts.append(f"Search query: \"{brand}\"")
        parts.append(f"Summary: {summary}")
        parts.append("")

        parts.append("AT YOUR CURRENT MALL:")
        if at_home:
            for e in at_home:
                floor = e.get("floor", "")
                floor_label = f"{floor} Floor" if floor and not floor.lower().endswith(("floor", "level")) else floor
                loc_parts = [p for p in [floor_label, e.get("zone"), e.get("unit_number")] if p]
                loc_str = f" — {', '.join(loc_parts)}" if loc_parts else ""
                parts.append(f"  • {e.get('name', '')}{loc_str}")
        else:
            parts.append(f"  Not found — \"{brand}\" is not at your current mall.")

        parts.append("")
        if other:
            parts.append(
                "AT OTHER CENOMI MALLS "
                "(these are the primary answer when current mall has no result):"
            )
            for e in other:
                floor = e.get("floor", "")
                floor_label = f"{floor} Floor" if floor and not floor.lower().endswith(("floor", "level")) else floor
                loc_parts = [p for p in [floor_label, e.get("zone"), e.get("unit_number")] if p]
                loc_str = f" — {', '.join(loc_parts)}" if loc_parts else ""
                parts.append(
                    f"  • {e.get('name', '')} at {e.get('mall_name', '')}{loc_str}"
                )
        else:
            parts.append("AT OTHER CENOMI MALLS:")
            parts.append("  No matches at other configured malls.")

        return "\n".join(parts)

    # Detect if family/kid filter is active — determines whether to group by genre
    secondary_intents = getattr(state, "secondary_intents", []) or []
    modifiers = getattr(state, "modifiers", []) or []
    is_filtered = bool(
        set(secondary_intents) & {"family_filter", "kid_friendly", "budget_filter",
                                   "romantic_filter", "proximity_filter"}
        or set(modifiers) & {"kid_friendly", "family_friendly", "budget_sensitive", "romantic"}
    )
    strategy = getattr(state, "response_strategy", "") or (
        (getattr(state, "response_plan", None) or type("", (), {"chosen_strategy": ""})()).chosen_strategy
        if hasattr(state, "response_plan") else ""
    )
    is_filtered_factual = is_filtered or strategy == "filtered_factual_list"

    # Movies
    if "movies" in fact_ctx:
        movies = fact_ctx["movies"]
        if is_filtered_factual and movies:
            # Group movies by genre category for filtered/hybrid queries
            from collections import defaultdict
            grouped: dict[str, list] = defaultdict(list)
            for movie in movies:
                genre = movie.get("genre", "")
                title = movie.get("title", "Unknown")
                cat = _classify_movie_genre(genre, title)
                grouped[cat].append(movie)

            parts.append("=== MOVIE SCHEDULE (grouped by category) ===")
            # Put family-friendly first when kid filter is active
            ordered_cats = sorted(
                grouped.keys(),
                key=lambda c: (0 if c == "Family / Animation" else 1),
            )
            for cat in ordered_cats:
                parts.append(f"\n[{cat}]")
                for movie in grouped[cat]:
                    title = movie.get("title", "Unknown")
                    genre = _genre_str(movie.get("genre", ""))
                    duration = movie.get("duration_minutes", "")
                    showtimes = movie.get("showtimes", [])
                    short_desc = movie.get("short_description", "") or movie.get("description", "")
                    line = f"• {title}"
                    if genre:
                        line += f" ({genre})"
                    if duration:
                        line += f" — {duration} min"
                    if short_desc:
                        line += f"\n  {short_desc[:100]}"
                    if showtimes:
                        if isinstance(showtimes, list) and showtimes:
                            if isinstance(showtimes[0], dict):
                                times = [f"{s.get('time', '')} {s.get('format', '')}".strip() for s in showtimes[:6]]
                            else:
                                times = [str(s) for s in showtimes[:6]]
                            line += f"\n  Times: {', '.join(times)}"
                    parts.append(line)
        else:
            parts.append("=== MOVIE SCHEDULE ===")
            for movie in fact_ctx["movies"]:
                title = movie.get("title", "Unknown")
                genre = _genre_str(movie.get("genre", ""))
                duration = movie.get("duration_minutes", "")
                showtimes = movie.get("showtimes", [])
                short_desc = movie.get("short_description", "") or movie.get("description", "")
                line = f"• {title}"
                if genre:
                    line += f" ({genre})"
                if duration:
                    line += f" — {duration} min"
                if short_desc:
                    line += f"\n  {short_desc[:100]}"
                if showtimes:
                    if isinstance(showtimes, list) and showtimes:
                        if isinstance(showtimes[0], dict):
                            times = [f"{s.get('time', '')} {s.get('format', '')}".strip() for s in showtimes[:6]]
                        else:
                            times = [str(s) for s in showtimes[:6]]
                        line += f"\n  Times: {', '.join(times)}"
                parts.append(line)

    elif "movie" in fact_ctx:
        movie = fact_ctx["movie"]
        parts.append("=== SPECIFIC MOVIE ===")
        parts.append(f"Title: {movie.get('title', '')}")
        parts.append(f"Genre: {_genre_str(movie.get('genre', ''))}")
        parts.append(f"Duration: {movie.get('duration_minutes', '')} min")
        parts.append(f"Rating: {movie.get('rating', '')}")
        showtimes = movie.get("showtimes", [])
        if showtimes:
            parts.append(f"Showtimes: {_json.dumps(showtimes, default=str)[:300]}")
        formats = movie.get("formats_available", [])
        if formats:
            parts.append(f"Formats: {', '.join(formats)}")

    # Location
    if "location" in fact_ctx:
        loc = fact_ctx["location"]
        parts.append("=== LOCATION ===")
        parts.append(f"Name: {loc.get('name', '')}")
        if loc.get("floor"):
            parts.append(f"Floor: {loc['floor']}")
        if loc.get("zone"):
            parts.append(f"Zone: {loc['zone']}")
        if loc.get("unit_number"):
            parts.append(f"Unit: {loc['unit_number']}")
        if loc.get("nearby_landmarks"):
            parts.append(f"Near: {', '.join(loc['nearby_landmarks'][:3])}")
        if loc.get("directions_hint"):
            parts.append(f"Directions: {loc['directions_hint']}")

    # Facility
    if "facility" in fact_ctx:
        fac = fact_ctx["facility"]
        parts.append("=== FACILITY ===")
        parts.append(f"Name: {fac.get('name', '')}")
        parts.append(f"Type: {fac.get('facility_type', '')}")
        if fac.get("floor"):
            parts.append(f"Floor: {fac['floor']}")
        if fac.get("zone"):
            parts.append(f"Zone: {fac['zone']}")
        if fac.get("directions_hint"):
            parts.append(f"Directions: {fac['directions_hint']}")

    # Service
    if "service" in fact_ctx:
        svc = fact_ctx["service"]
        parts.append("=== SERVICE ===")
        parts.append(f"Name: {svc.get('name', '')}")
        parts.append(f"Category: {svc.get('service_category', '')}")
        if svc.get("description"):
            parts.append(f"Description: {svc['description'][:150]}")
        if svc.get("floor"):
            parts.append(f"Floor: {svc['floor']}")

    # Mall hours
    if "mall_hours" in fact_ctx:
        parts.append("=== MALL HOURS ===")
        hours = fact_ctx["mall_hours"]
        if isinstance(hours, dict):
            for day, hrs in hours.items():
                parts.append(f"  {day}: {hrs}")
        else:
            parts.append(str(hours))

    if "entity_hours" in fact_ctx:
        eh = fact_ctx["entity_hours"]
        parts.append(f"=== HOURS FOR {eh.get('name', '').upper()} ===")
        oh = eh.get("operating_hours", {})
        if oh:
            parts.append(_json.dumps(oh, default=str)[:200])

    # Parking
    if "parking" in fact_ctx:
        parts.append("=== PARKING ===")
        parts.append(_json.dumps(fact_ctx["parking"], default=str)[:300])

    # Loyalty
    if "loyalty" in fact_ctx:
        loy = fact_ctx["loyalty"]
        parts.append("=== LOYALTY PROGRAM ===")
        parts.append(f"Program: {loy.get('program_name', '')}")
        if loy.get("description"):
            parts.append(f"Details: {loy['description'][:200]}")

    # Offers
    if "offers" in fact_ctx:
        parts.append("=== ACTIVE OFFERS ===")
        for offer in fact_ctx["offers"][:10]:
            parts.append(
                f"• {offer.get('title', '')} — {offer.get('description', '')[:100]}"
            )

    # Events
    if "events" in fact_ctx:
        parts.append("=== EVENTS ===")
        for event in fact_ctx["events"][:8]:
            parts.append(
                f"• {event.get('title', '')} — {event.get('description', '')[:100]}"
            )

    # Also include raw retrieval results for entities found in context
    retrieval_entities = state.context.selected_entities if state.context else []
    for entity in retrieval_entities[:10]:
        src = entity.get("source", "")
        if src.startswith("factual/"):
            name = entity.get("name", "")
            floor = entity.get("floor", "")
            zone = entity.get("zone", "")
            etype = entity.get("entity_type", "")
            loc_parts = [p for p in [floor, zone] if p]
            loc = f" — {', '.join(loc_parts)}" if loc_parts else ""
            parts.append(f"• {name} ({etype}){loc}")

    return "\n".join(parts) if parts else "No exact data retrieved."


def _build_factual_mode_instruction(
    scope: str,
    response_mode: str,
    strategy: str,
    query_entity: str,
    fact_ctx: dict,
    secondary_intents: list[str] | None = None,
    modifiers: list[str] | None = None,
    experience_mode: str = "",
) -> str:
    """
    Build the response mode instruction for factual queries.

    Uses experience_mode (from the Experience Layer resolver) to select
    the most appropriate polish template.
    """
    entity_ref = f" for '{query_entity}'" if query_entity else ""
    secondary_intents = secondary_intents or []
    modifiers = modifiers or []

    _family_filters = {"family_filter", "kid_friendly", "family_friendly"}
    _budget_filters = {"budget_filter", "budget_sensitive"}
    _romantic_filters = {"romantic_filter", "romantic"}
    has_family_filter = bool(_family_filters & (set(secondary_intents) | set(modifiers)))
    has_budget_filter = bool(_budget_filters & (set(secondary_intents) | set(modifiers)))
    has_romantic_filter = bool(_romantic_filters & (set(secondary_intents) | set(modifiers)))

    # ── Filtered factual list template (experience layer: filtered_factual_list) ─
    if (
        experience_mode == FILTERED_FACTUAL_LIST
        or strategy == "filtered_factual_list"
    ) and scope == "movie_schedule":
        filter_notes: list[str] = []
        if has_family_filter:
            filter_notes.append(
                "• FAMILY/KID FILTER ACTIVE: The guest has a child with them.\n"
                "  - Open with: 'For a young guest, the most suitable family-friendly options right now are:'\n"
                "  - Lead with the Family / Animation category if present.\n"
                "  - For each film, briefly note its suitability for children "
                "(e.g. '✓ Ideal for children' or '⚠ Adult content').\n"
                "  - SPORTS BROADCASTS: Any entry flagged 'is_sports_broadcast: true' "
                "or with genre 'Sport' is a live sports screening — "
                "do NOT present these as family-friendly or children's films.\n"
                "  - If NO film in the schedule belongs to the Animation/Family/Kids genre, "
                "acknowledge it with grace: 'There is no children's film showing at present.' "
                "Then suggest a suitable kids' activity available at the mall "
                "(e.g. the kids' play area) as an alternative.\n"
                "  - Do NOT redirect away from movies — this remains a cinema query.\n"
                "  - Close with one sentence noting the safest family choice, "
                "or direct the guest to the kids' play area if no suitable film is available."
            )
        if has_budget_filter:
            filter_notes.append(
                "• BUDGET FILTER ACTIVE: Note affordable screening formats (e.g. standard "
                "over premium/IMAX) where relevant."
            )
        if has_romantic_filter:
            filter_notes.append(
                "• ROMANTIC FILTER ACTIVE: Note any premium or special-format screenings "
                "that would make for a nicer date experience."
            )
        filter_block = "\n".join(filter_notes) if filter_notes else "• General filter active — highlight the most relevant options."
        return (
            "RESPONSE MODE — FILTERED MOVIE LIST:\n"
            "Present movies from the schedule, grouped by category as shown.\n"
            "For each movie include: title, genre, duration, and showtimes.\n"
            "ACTIVE FILTERS:\n"
            f"{filter_block}\n\n"
            "RULES:\n"
            "  1. Acknowledge the filter naturally in your opening line "
            "(e.g. 'For a child...' or 'If you're looking for something budget-friendly...').\n"
            "  2. Show all films from the schedule; for each entry flag its suitability — "
            "clearly mark Sport/Broadcast entries as '⚠ Not suitable for children'.\n"
            "  3. Add ONE brief filter-note per movie where relevant.\n"
            "  4. Do NOT add dining or entertainment suggestions in the main answer — "
            "UNLESS no family-appropriate film exists, in which case redirect to a kids' "
            "activity alternative as a closing suggestion.\n"
            "  5. Do NOT recommend sports broadcasts as children's entertainment.\n\n"
        )

    # ── Filtered factual list for non-movie scopes ────────────────────────────
    if experience_mode == FILTERED_FACTUAL_LIST or strategy == "filtered_factual_list":
        filter_label = (
            "family-friendly" if has_family_filter
            else "budget-friendly" if has_budget_filter
            else "filtered"
        )
        return (
            f"RESPONSE MODE — FILTERED {scope.upper().replace('_', ' ')}:\n"
            f"Acknowledge the filter naturally (e.g. 'For {filter_label} options...').\n"
            "Lead with the most relevant items for the active filter.\n"
            "Keep it concise and scannable.\n\n"
        )

    # ── Factual list template (experience layer: factual_list) ────────────────
    if (
        experience_mode == FACTUAL_LIST
        and (scope == "movie_schedule" or strategy == "structured_fact_list")
    ) or (scope == "movie_schedule" or strategy == "structured_fact_list"):
        return (
            "RESPONSE MODE — MOVIE SCHEDULE:\n"
            "Present the movies now showing. For each include:\n"
            "  1. Title — Genre — Duration\n"
            "  2. Short description (if available)\n"
            "  3. All available showtimes\n"
            "Use a clean, scan-friendly format. "
            "Open with 'Here are the movies showing now:' or similar. "
            "Do NOT pad with dining suggestions or entertainment recommendations. "
            "If the guest asked about a specific film, lead with that film.\n\n"
        )

    # ── Route hint template (experience layer: route_hint) ────────────────────
    if experience_mode == ROUTE_HINT or response_mode == "route_hint" or scope == "route_hint":
        return (
            f"RESPONSE MODE — LOCATION GUIDE{entity_ref}:\n"
            "Answer directly with floor, zone, and a direction hint. "
            "Format: '[Name] is on [Floor], [Zone]. [Direction hint if available].'\n"
            "Be conversational — like a friend pointing the way. Keep it short.\n\n"
        )

    # ── Direct lookup template (experience layer: direct_lookup) ─────────────
    if (
        experience_mode == DIRECT_LOOKUP
        or response_mode == "direct_lookup"
        or scope == "brand_availability"
    ):
        return (
            f"RESPONSE MODE — AVAILABILITY CHECK{entity_ref}:\n"
            "Answer yes/no first, then give the location if yes. "
            "Style: 'Yes, [Name] is here — [Floor], [Zone].' or "
            "'I don't see [Name] in our current listing.'\n"
            "Do NOT suggest alternatives unless retrieval returned none.\n\n"
        )

    if response_mode == "quick_answer" or scope == "mall_fact":
        return (
            "RESPONSE MODE — QUICK ANSWER:\n"
            f"Give a direct, concise answer{entity_ref}. "
            "Lead with the fact. Keep it to 1–3 sentences. No padding.\n\n"
        )
    if scope == "service_lookup":
        return (
            f"RESPONSE MODE — SERVICE LOOKUP{entity_ref}:\n"
            "Provide the location and any relevant details directly. "
            "Lead with the floor/zone. Keep it short.\n\n"
        )
    if scope == "cross_mall_availability":
        return (
            "RESPONSE MODE — CROSS-MALL AVAILABILITY:\n"
            "Use ONLY the structured data above. Follow this format strictly:\n"
            "1) If AT OTHER CENOMI MALLS has results AND current mall does not:\n"
            "   → One brief sentence: '[Query] isn't at your current mall, but I found [entity name(s)] at [N] other Cenomi mall(s):'\n"
            "   → Then a clean bulleted list: '• [Entity name] — [Mall name], [Floor], [Zone] (Unit [unit])'\n"
            "   → NEVER start with 'I do not have information' or an apology when results exist.\n"
            "2) If current mall has results: confirm it first, then mention other malls.\n"
            "3) If nothing found anywhere: acknowledge honestly in one sentence.\n"
            "Be specific — always include mall name, floor, zone, and unit number when available.\n"
            "Be conversational — write like a helpful concierge, not a database readout.\n\n"
        )
    return (
        "RESPONSE MODE — DIRECT ANSWER:\n"
        f"Give a direct, factual answer{entity_ref}. "
        "Answer first, elaborate briefly, no padding.\n\n"
    )


def _build_factual_fallback(state, fact_ctx: dict) -> str:
    """Graceful fallback when LLM fails during factual response."""
    scope = getattr(state, "fact_scope", "") or ""
    query_entity = getattr(state, "fact_query_entity", "") or ""

    if scope == "movie_schedule":
        movies = fact_ctx.get("movies", [])
        if movies:
            names = [m.get("title", "") for m in movies[:5] if m.get("title")]
            return f"Currently showing: {', '.join(names)}."
        return "I couldn't retrieve the current movie schedule. Please check with the cinema directly."

    if scope in ("route_hint", "service_lookup"):
        loc = fact_ctx.get("location") or fact_ctx.get("facility", {})
        if loc:
            name = loc.get("name", query_entity or "that")
            floor = loc.get("floor", "")
            return f"{name} is{f' on {floor}' if floor else ' in the mall'}. Please ask a staff member for exact directions."
        return "I couldn't find the exact location. Please ask our information desk for assistance."

    if scope == "brand_availability":
        loc = fact_ctx.get("location", {})
        if loc:
            name = loc.get("name", query_entity or "that brand")
            floor = loc.get("floor", "")
            return f"Yes, {name} is available here{f' on {floor}' if floor else ''}."
        if query_entity:
            return f"I couldn't confirm whether {query_entity} is in the mall. Please check with the information desk."

    if scope == "mall_fact":
        hours = fact_ctx.get("mall_hours")
        if hours:
            return f"Mall hours: {hours}"

    if scope == "cross_mall_availability":
        at_home = fact_ctx.get("cross_mall_at_home") or []
        other = fact_ctx.get("cross_mall_other") or []
        brand = fact_ctx.get("cross_mall_brand_query") or fact_ctx.get("query_entity") or "that brand"
        if not fact_ctx.get("cross_mall_brand_query") and not fact_ctx.get("query_entity"):
            return "Which store or brand should I look up across our malls?"
        if at_home and other:
            o0 = other[0]
            return (
                f"Yes, {brand} is at your current mall and also at {o0.get('mall_name', 'another mall')}."
            )
        if at_home:
            return f"Yes, {brand} is listed at your current mall."
        if other:
            o0 = other[0]
            return f"{brand} is not listed at your current mall in our data, but it is at {o0.get('mall_name', 'another mall')}."
        return f"I couldn't find {brand} at the malls I have data for."

    return (
        "I'm having trouble retrieving that information right now. "
        "Please ask at our information desk — they'll be happy to help."
    )


async def _build_mall_info_response(
    state: ConciergeState,
) -> tuple[str, list[str], str]:
    """
    Dedicated response path for ALL ``mall_info`` domain queries.

    Returns (response_text, warnings, experience_mode).
    """
    warnings: list[str] = []
    mall_ctx = get_mall_context(state.mall_id)

    composer = ConciergeComposer(mall_ctx)
    overview = composer.compose_mall_overview()

    system_prompt = get_mall_overview_system_prompt(overview.to_prompt_block())
    query = state.normalized_user_message or state.raw_user_message

    sub = state.intent.sub_intent
    focus_hint = ""
    if sub == "opening_hours":
        focus_hint = (
            "The guest is asking about opening hours. "
            "Lead with the hours in a clear, well-formatted response. Keep it concise.\n"
        )
    elif sub == "facilities_summary":
        focus_hint = (
            "The guest is asking about facilities and services. "
            "Focus on services and facilities. Use a brief, grouped format.\n"
        )
    elif sub == "family_friendliness":
        focus_hint = (
            "The guest is asking whether this mall is family-friendly. "
            "Highlight family-friendly zones, play areas, and facilities.\n"
        )
    elif sub == "what_is_available":
        focus_hint = (
            "The guest would like to know what is available here. "
            "Mention key zones, categories, and 3–4 notable anchor tenants. "
            "Keep it warm and inviting — paint a picture of a wonderful visit.\n"
        )

    # ── Structured overview template (experience layer) ───────────────
    overview_template = (
        "RESPONSE MODE — MALL OVERVIEW:\n"
        "Structure your answer as a concise, polished overview:\n"
        "  1. One composed opening line about the mall\n"
        "  2. Key highlights: major zones or categories (3–4 max)\n"
        "  3. Practical information: hours and key services (parking, prayer rooms, etc.)\n"
        "Keep it scan-friendly and engaging. "
        "Do NOT present every detail — leave room for the guest to ask further.\n\n"
    )

    # ── CTA for mall overview ─────────────────────────────────────────
    # When response_mode=direct_factual, the guest asked for information — answer and stop.
    # Suppress exploration prompts, CTAs, and "would you like to..." follow-ups.
    is_direct_factual = state.response_mode == "direct_factual"
    if is_direct_factual:
        cta_instruction = (
            "IMPORTANT: The guest asked for factual information. "
            "Answer the question directly and completely. "
            "Do NOT append exploration prompts, CTAs, or 'would you like to...' follow-ups. "
            "Give the information and stop.\n\n"
        )
    else:
        cta_instruction = get_cta_instruction("overview_continue")

    user_prompt = (
        f"Guest question:\n{query}\n\n"
        f"{focus_hint}"
        f"{overview_template}"
        f"{cta_instruction}"
        "Respond using ONLY the overview data above. "
        "Do NOT invent any store names, services, floors, or details."
    )

    try:
        llm = _get_llm()
        lc_messages = [SystemMessage(content=system_prompt)]
        lc_messages.extend(_build_prior_messages(state))
        lc_messages.append(HumanMessage(content=user_prompt))
        response = await llm.ainvoke(lc_messages)
        final_text = response.content
        final_text = _run_hallucination_guard(final_text, mall_ctx, warnings)
        return final_text, warnings, STRUCTURED_OVERVIEW
    except Exception as exc:
        logger.error("LLM call failed for mall info: %s", exc)
        warnings.append(f"LLM call failed: {exc}")
        return overview.render_text(), warnings, STRUCTURED_OVERVIEW


async def _build_cross_mall_response(
    state: ConciergeState,
) -> tuple[str, list[str], str]:
    """
    Dedicated response path for cross-mall brand search queries.

    Returns (response_text, warnings, experience_mode).
    """
    warnings: list[str] = []
    mall_ctx = get_mall_context(state.mall_id)
    mall_context = mall_ctx.get_canonical_for_prompt()
    system_prompt = get_concierge_system_prompt(mall_context)

    entities = state.context.selected_entities
    home_entities = [e for e in entities if e.get("is_home_mall")]
    other_entities = [e for e in entities if not e.get("is_home_mall")]

    entity_lines: list[str] = []
    if home_entities:
        entity_lines.append("AT YOUR CURRENT MALL:")
        for e in home_entities:
            loc_parts = [p for p in [e.get("floor"), e.get("zone"), e.get("unit_number")] if p]
            floor_info = f" — {', '.join(loc_parts)}" if loc_parts else ""
            entity_lines.append(f"  • {e['name']} ({e.get('entity_type', 'store')}){floor_info}")
    if other_entities:
        entity_lines.append("AT OTHER CENOMI MALLS:")
        for e in other_entities:
            loc_parts = [p for p in [e.get("floor"), e.get("zone"), e.get("unit_number")] if p]
            floor_info = f" — {', '.join(loc_parts)}" if loc_parts else ""
            entity_lines.append(f"  • {e['name']} at {e['mall_name']}{floor_info}")
    if not entities:
        entity_lines.append("NO MATCHES FOUND in any loaded Cenomi mall.")

    entity_block = "\n".join(entity_lines)
    query = state.normalized_user_message or state.raw_user_message

    cross_mall_instruction = (
        "RESPONSE MODE — CROSS-MALL AVAILABILITY:\n"
        "Use ONLY the structured data above. Follow this format:\n"
        "- If AT OTHER CENOMI MALLS has results AND current mall does not:\n"
        "  One sentence: '[Query] isn't at your current mall, but I found [entity name(s)] at [N] other Cenomi mall(s):'\n"
        "  Then a clean bulleted list: '• [Entity name] — [Mall name], [Floor], [Zone] (Unit [unit])'\n"
        "  Do NOT start with 'I do not have information' when results exist.\n"
        "- If current mall has results: confirm first, then mention other malls.\n"
        "- If nowhere: one honest sentence.\n"
        "Be specific (include mall name, floor, zone, unit). Be conversational.\n\n"
    )

    cta_instruction = get_cta_instruction("cross_mall")

    user_prompt = (
        f"Guest query: {query}\n\n"
        f"Brand/store availability across Cenomi malls:\n{entity_block}\n\n"
        f"{cross_mall_instruction}"
        f"{cta_instruction}"
        "Generate a helpful, conversational concierge response."
    )

    langchain_messages = [SystemMessage(content=system_prompt)]
    langchain_messages.extend(_build_prior_messages(state))
    langchain_messages.append(HumanMessage(content=user_prompt))

    try:
        llm = _get_llm()
        response = await llm.ainvoke(langchain_messages)
        final_text = response.content
        merged_canonical = get_all_mall_canonical_for_guard()
        final_text = _run_hallucination_guard(
            final_text, mall_ctx, warnings,
            canonical_override=merged_canonical,
        )
    except Exception as exc:
        logger.error("LLM call failed for cross-mall response: %s", exc)
        warnings.append(f"LLM call failed: {exc}")
        if home_entities:
            names = ", ".join(e["name"] for e in home_entities[:3])
            final_text = f"Yes, {names} is available at your current mall."
        elif other_entities:
            other_mall = other_entities[0]["mall_name"]
            names = ", ".join(e["name"] for e in other_entities[:3])
            final_text = f"{names} is not at your current mall, but is available at {other_mall}."
        else:
            final_text = "I couldn't find that brand at any of the Cenomi malls I have data for."

    return final_text, warnings, DIRECT_LOOKUP


def _build_unsupported_recovery_response(state: ConciergeState) -> str:
    """
    Build a graceful recovery response for unsupported, random, or misspelled input.

    Rules:
    - Do NOT hallucinate
    - Ask a SHORT clarifying question OR offer 3-4 supported directions
    - If a brand correction hint is available, use it as a suggestion
    - Never assert brand presence based solely on a name match
    """
    raw = state.raw_user_message.strip()

    # Check for brand correction hint
    brand_hint = state.intent.raw_signals.get("brand_correction_hint", "")
    if brand_hint:
        return (
            f"Did you mean **{brand_hint}**? I can check if it's available at this mall — "
            f"just confirm and I'll look it up for you."
        )

    # Build a domain-aware alternative list (exclude the domain that just failed).
    current_domain = (state.intent.domain or "").lower()
    _ALL_ALTERNATIVES: list[tuple[str, str, str]] = [
        ("dining",        "dining",        "Dining — restaurants, cafes, quick bites"),
        ("shopping",      "shopping",      "Shopping — stores, brands, offers"),
        ("entertainment", "entertainment", "Movies & Entertainment — now showing, showtimes"),
        ("services",      "services",      "Directions & Services — floors, parking, prayer rooms"),
        ("navigation",    "navigation",    "Navigation — finding specific stores or facilities"),
    ]
    alternatives = [
        label
        for domain, _, label in _ALL_ALTERNATIVES
        if domain != current_domain
    ][:4]  # cap at 4

    bullet_list = "\n".join(f"• **{alt}**" for alt in alternatives)

    # Very short / random input
    if len(raw) <= 3:
        alt_str = ", ".join(a.split(" —")[0] for a in alternatives)
        return (
            "I didn't quite catch that — I'm here to help with the mall. "
            f"You can ask me about {alt_str}. "
            "What would you like to know?"
        )

    # Longer gibberish / off-topic
    return (
        f"I'm not sure I can help with \"{raw[:40]}{'...' if len(raw) > 40 else ''}\" — "
        "but I'm here to make your mall visit excellent. "
        "I can assist with:\n"
        f"{bullet_list}\n\n"
        "What would you like help with?"
    )


@traced_node("generate_response")
async def generate_response(state: ConciergeState) -> dict:
    mall_ctx = get_mall_context(state.mall_id)
    warnings: list[str] = []
    experience_mode = ""

    # ── Early exit: unsupported / gibberish / clarification-needed inputs ──
    # Handles graceful_recovery (out-of-scope, low-confidence) and
    # clarification_request (unsupported capability, unintelligible input).
    is_unsupported = (
        state.intent.primary_intent == "unsupported"
        or state.intent.raw_signals.get("unsupported", False)
        or state.response_plan.response_mode == "graceful_recovery"
        or state.response_plan.response_mode == "clarification_request"
    )
    if is_unsupported:
        final_text = _build_unsupported_recovery_response(state)
        assistant_msg = Message(
            role="assistant",
            content=final_text,
            turn_id=state.turn_id,
            metadata={"strategy": "unsupported_recovery", "experience_mode": "unsupported"},
        )
        return {
            "final_response_text": final_text,
            "response_debug_summary": "strategy=unsupported_recovery | unsupported_input",
            "messages": [assistant_msg],
            "debug_enrichment": state.debug_enrichment.model_copy(
                update={"response_experience_mode": "unsupported_recovery"}
            ),
            "_trace_summary": "Generated unsupported-input recovery response",
        }

    # ── Early exit: acknowledgement — non-actionable filler, no intent ──────
    # The visitor said something vague ("okay", "wait", "hmm", "not sure") that
    # carries no new topic or request. The bot must ask a clarifying question
    # instead of generating unsolicited recommendations.
    #
    # SAFETY CHECK: If the previous assistant message ended with a direct question
    # or offer (e.g. "Want me to...?", "Shall I...?"), an affirmative reply
    # ("sure", "yes", "ok", "yeah") is a CONFIRMATION — not an acknowledgement.
    # Bypass the clarification path and let the full pipeline handle it.
    if state.intent.message_kind == "acknowledgement":
        _affirm_starts = frozenset({
            "sure", "yes", "yeah", "yep", "ok", "okay", "please", "go ahead",
            "do it", "sounds good", "perfect", "of course", "absolutely",
            "why not", "love that", "great idea", "go for it",
        })
        _msg_lower = (state.normalized_user_message or "").lower().strip().rstrip("!.,")
        # Exact match OR starts-with any affirmative token (catches "yes that will be good" etc.)
        _first_word = _msg_lower.split()[0] if _msg_lower.split() else ""
        _is_affirmative = _msg_lower in _affirm_starts or _first_word in _affirm_starts

        if _is_affirmative:
            # Check if the last assistant message ended with a question or offer
            prior_assistant_msgs = [
                m for m in state.messages if m.role == "assistant"
            ]
            if prior_assistant_msgs:
                _last_bot = prior_assistant_msgs[-1].content.strip()
                # Look for question markers or common offer patterns near end of message
                _last_sentence = _last_bot.split(".")[-2] if "." in _last_bot else _last_bot
                _last_sentence = _last_sentence.strip()
                _is_bot_question = (
                    _last_bot.endswith("?")
                    or _last_sentence.endswith("?")
                    or any(
                        phrase in _last_bot.lower()
                        for phrase in (
                            "want me to", "shall i", "would you like", "want to", "should i",
                            "if you want", "i can help", "i can also", "i can next",
                            "shall we", "want a", "would you want", "i can plan",
                            "i can line up", "i can point", "i can turn",
                        )
                    )
                )
                if _is_bot_question:
                    # LLM decides whether the last bot message contained a narrowing/
                    # filtering offer (any phrasing) and returns the appropriate
                    # choice question, or "PASS" when no such offer was found.
                    # Keyword list removed — the LLM handles all phrasing variants.
                    llm_question = await _generate_narrowing_question(
                        state, last_bot_text=_last_bot
                    )
                    if llm_question and llm_question.strip().upper() != "PASS":
                        final_text = llm_question
                    elif not llm_question:
                        # LLM call failed — use static topic-keyed fallback.
                        _task = state.scene.shopping_task
                        _active_topic = state.scene.active_topic or ""
                        if _task and _task.product_type:
                            _product = _task.product_type
                            final_text = (
                                f"Sure! To find the best {_product}, tell me: "
                                f"who is it for, what's your budget, or any style preference? "
                                f"Any one of those helps me point you to the right store."
                            )
                        elif _active_topic in ("dining", "food"):
                            final_text = (
                                "Of course! Quick question — do you prefer a sit-down meal, "
                                "something fast, or a light snack? And any cuisine in mind?"
                            )
                        elif _active_topic == "shopping":
                            final_text = (
                                "Happy to narrow it down! Is this for you or someone else, "
                                "and do you have a budget or style in mind?"
                            )
                        elif _active_topic == "entertainment":
                            final_text = (
                                "Sure! Any preference — a specific genre, age group, "
                                "or something to do right now vs. later?"
                            )
                        else:
                            final_text = (
                                "Of course! Tell me a bit more — who is it for, "
                                "what's your budget, or any preference you have in mind?"
                            )
                    else:
                        # LLM returned PASS — no narrowing offer in last bot message.
                        # Fall through: rewrite to followup and let main pipeline run.
                        final_text = None

                    if final_text is not None:
                        assistant_msg = Message(
                            role="assistant",
                            content=final_text,
                            turn_id=state.turn_id,
                            metadata={
                                "strategy": "narrowing_clarification",
                                "experience_mode": "clarification",
                            },
                        )
                        return {
                            "final_response_text": final_text,
                            "response_debug_summary": "strategy=narrowing_clarification",
                            "messages": [assistant_msg],
                            "debug_enrichment": state.debug_enrichment.model_copy(
                                update={"response_experience_mode": "narrowing_clarification"}
                            ),
                            "_trace_summary": "Confirmed narrowing offer: returned clarifying question",
                        }

                    # Not a narrowing offer — treat as standard followup confirmation
                    logger.info(
                        "Acknowledgement override: prior bot message was a question, "
                        "treating affirmative '%s' as followup", state.normalized_user_message
                    )
                    state = state.model_copy(
                        update={"intent": state.intent.model_copy(update={"message_kind": "followup"})}
                    )
                    # Fall through to main pipeline below
                else:
                    pass  # genuine acknowledgement — proceed to clarification

        if state.intent.message_kind == "acknowledgement":
            # Build a context-aware clarifying question. If there's an active topic
            # from a prior turn, gently anchor to it; otherwise ask openly.
            active = state.scene.active_topic
            prior_need = state.scene.previous_need
            if active and active not in ("general",):
                topic_label = active.replace("_", " ")
                final_text = random.choice([
                    f"Still thinking about {topic_label}? Just let me know what you're "
                    f"looking for and I'll help narrow it down.",

                    f"Happy to help with {topic_label} — what specifically are you after?",

                    f"Still on {topic_label}? Tell me what you need and I'll narrow it right down.",
                ])
            elif prior_need:
                final_text = random.choice([
                    "Take your time — what would you like to find or do today? "
                    "Shopping, dining, a movie, or something else?",

                    "No rush. What are you in the mood for — shopping, food, a film, or exploring?",

                    "Whenever you're ready — what can I help you find?",
                ])
            else:
                final_text = random.choice([
                    "What are you looking for today? "
                    "Tell me anything — a store, a meal, a movie, or even just a vibe — "
                    "and I'll point you in the right direction.",

                    "What brings you here today? Shopping, dining, a movie, or something else?",

                    "How can I help? Just name it — store, food, film, or any service — "
                    "and I'll take it from there.",
                ])
            assistant_msg = Message(
                role="assistant",
                content=final_text,
                turn_id=state.turn_id,
                metadata={"strategy": "acknowledgement_clarification", "experience_mode": "clarification"},
            )
            return {
                "final_response_text": final_text,
                "response_debug_summary": "strategy=acknowledgement_clarification",
                "messages": [assistant_msg],
                "debug_enrichment": state.debug_enrichment.model_copy(
                    update={"response_experience_mode": "acknowledgement_clarification"}
                ),
                "_trace_summary": "Acknowledgement: returned clarifying question",
            }

    # ── Early exit: companion_correction — user denied a false assumption ───
    # The LLM already cleared the incorrect companions via scene_corrections.
    # The bot must acknowledge the correction naturally and ask what they want.
    if state.intent.message_kind == "companion_correction":
        # Determine what was corrected to phrase the acknowledgement naturally.
        corrections = state.intent.scene_corrections
        is_solo_correction = any(
            t in corrections for t in ("all_family_context", "visit_type:solo")
        ) or any(
            sig in (state.normalized_user_message or "").lower()
            for sig in ("alone", "solo", "by myself", "just me", "no kids", "dont have kids",
                        "don't have kids")
        )
        if is_solo_correction:
            final_text = random.choice([
                "Got it — solo visit, noted. "
                "What are you looking to do today? Shopping, grab a bite, catch a film, "
                "or just exploring?",

                "Noted, just you — my apologies for the mix-up. "
                "What can I help you find today?",

                "Solo it is — got it. What are you after? "
                "A meal, some shopping, a film, or something else?",
            ])
        else:
            final_text = random.choice([
                "Understood, my mistake for the wrong assumption. "
                "What are you actually looking for today? "
                "Shopping, dining, a movie, or something else?",

                "My apologies — I got that wrong. Let's start fresh. "
                "What would you like help with?",

                "Thanks for the correction. What are you looking to do? "
                "I can help with shopping, food, entertainment, or services.",
            ])
        assistant_msg = Message(
            role="assistant",
            content=final_text,
            turn_id=state.turn_id,
            metadata={"strategy": "companion_correction_ack", "experience_mode": "correction"},
        )
        return {
            "final_response_text": final_text,
            "response_debug_summary": "strategy=companion_correction_ack",
            "messages": [assistant_msg],
            "debug_enrichment": state.debug_enrichment.model_copy(
                update={"response_experience_mode": "companion_correction_ack"}
            ),
            "_trace_summary": "Companion correction: acknowledged + asked what visitor wants",
        }

    # ── Early exit: disengagement — visitor is frustrated or giving up ───
    # Returns a brief empathetic recovery message instead of recycling prior
    # recommendations. Does NOT call the full LLM pipeline.
    if state.intent.message_kind == "disengagement":
        excluded = state.scene.excluded_domains
        if excluded:
            domains_str = " or ".join(excluded)
            final_text = random.choice([
                f"Apologies for not getting that right — I kept suggesting "
                f"{domains_str} options even after you said no. "
                f"What kind of activity are you looking for? I can help with "
                f"shopping, entertainment, services, or directions.",

                f"Sorry about that — I should have moved on from {domains_str} sooner. "
                f"What would you like to do instead?",

                f"My mistake for not catching that earlier. Let's move away from {domains_str}. "
                f"Shopping, entertainment, or something else?",
            ])
        else:
            final_text = random.choice([
                "I'm sorry if I wasn't being helpful. "
                "What would you like to do? I can help with shopping, dining, "
                "movies, entertainment, services, or directions at the mall.",

                "Apologies for the confusion. Let's try again — "
                "what are you looking for right now?",

                "My bad — let me do better. Tell me what you need "
                "and I'll point you straight to it.",
            ])
        assistant_msg = Message(
            role="assistant",
            content=final_text,
            turn_id=state.turn_id,
            metadata={"strategy": "disengagement_recovery", "experience_mode": "disengagement"},
        )
        return {
            "final_response_text": final_text,
            "response_debug_summary": "strategy=disengagement_recovery | visitor_disengaged",
            "messages": [assistant_msg],
            "debug_enrichment": state.debug_enrichment.model_copy(
                update={"response_experience_mode": "disengagement_recovery"}
            ),
            "_trace_summary": "Generated disengagement recovery response",
        }

    # ── Followup: intercept simple affirmations confirming a bot offer ──────
    # When the visitor replies "sure", "ok", "okay", "sure do it" etc. to a
    # bot offer, the LLM correctly classifies it as `followup` (per the
    # "affirmative after a bot offer → followup" rule).  The main concierge
    # pipeline downstream doesn't know to ask a narrowing question — it just
    # regenerates the same recommendation list.  This block intercepts BEFORE
    # the main pipeline to return the right response.
    #
    # Two sub-cases:
    #   A) Bot offered to NARROW recommendations → ask the narrowing question.
    #   B) Bot offered a TOPIC SWITCH (e.g. "want me to point you to stores?")
    #      after discussing a different domain → return a clarifying prompt
    #      for the offered topic (retrieval has already run for the old topic
    #      so we cannot regenerate; a clarifying question is the safe path).
    if state.intent.message_kind == "followup":
        _SIMPLE_AFFIRM_F = frozenset({
            "sure", "yes", "yeah", "yep", "ok", "okay", "please", "go ahead",
            "do it", "sounds good", "perfect", "of course", "absolutely",
            "why not", "love that", "great idea", "go for it",
        })
        _msg_lower_f = (state.normalized_user_message or "").lower().strip().rstrip("!.,")
        _first_word_f = _msg_lower_f.split()[0] if _msg_lower_f.split() else ""
        _is_short_affirm_f = (
            _msg_lower_f in _SIMPLE_AFFIRM_F or _first_word_f in _SIMPLE_AFFIRM_F
        ) and len(_msg_lower_f.split()) <= 4

        if _is_short_affirm_f:
            _prior_asst = [m for m in state.messages if m.role == "assistant"]
            if _prior_asst:
                _last_bot_f = _prior_asst[-1].content.strip()

                # ── Case A: Bot offered to narrow recommendations ──────
                # LLM decides whether the last bot message contained any
                # narrowing/filtering offer (any phrasing) and returns either:
                #   • a natural-language question to ask the visitor, OR
                #   • "PASS" meaning no narrowing offer was found — fall through.
                # Keyword lists removed; the LLM handles all phrasing variants.
                _nq = await _generate_narrowing_question(
                    state, last_bot_text=_last_bot_f
                )
                if _nq and _nq.strip().upper() == "PASS":
                    # No narrowing offer found — continue to Case B and beyond.
                    pass
                else:
                    # Either LLM returned a question, or the call failed (None).
                    if _nq:
                        _nq_text = _nq
                    else:
                        # Static topic-keyed fallback on LLM failure.
                        _task_f = state.scene.shopping_task
                        _atopic_f = state.scene.active_topic or ""
                        if _task_f and _task_f.product_type:
                            _nq_text = (
                                f"Sure! To find the best {_task_f.product_type}, "
                                f"tell me: who is it for, what's your budget, "
                                f"or any style preference?"
                            )
                        elif _atopic_f in ("dining", "food"):
                            _nq_text = (
                                "Of course! Quick question — do you prefer a sit-down "
                                "meal, something fast, or a light snack? Any cuisine in mind?"
                            )
                        elif _atopic_f == "shopping":
                            _nq_text = (
                                "Happy to narrow it down! Is this for you or someone else, "
                                "and do you have a budget or style in mind?"
                            )
                        elif _atopic_f == "entertainment":
                            _nq_text = (
                                "Sure! Any preference — a specific genre, age group, "
                                "or something to do right now vs. later?"
                            )
                        else:
                            _nq_text = (
                                "Of course! Tell me a bit more — who is it for, "
                                "what's your budget, or any preference you have in mind?"
                            )
                    _nq_msg = Message(
                        role="assistant",
                        content=_nq_text,
                        turn_id=state.turn_id,
                        metadata={
                            "strategy": "narrowing_clarification",
                            "experience_mode": "clarification",
                        },
                    )
                    return {
                        "final_response_text": _nq_text,
                        "response_debug_summary": "strategy=narrowing_clarification (followup affirmation)",
                        "messages": [_nq_msg],
                        "debug_enrichment": state.debug_enrichment.model_copy(
                            update={"response_experience_mode": "narrowing_clarification"}
                        ),
                        "_trace_summary": "Followup affirmation confirmed narrowing offer: returned clarifying question",
                    }

                # ── Case B: Bot offered a different topic ──────────────
                # Maps signal phrases in last bot message → (topic, clarifying text)
                _last_bot_f_lower = _last_bot_f.lower()
                _TOPIC_OFFER_MAP: list[tuple[tuple[str, ...], str]] = [
                    (
                        (
                            "point you to any stores", "point you to stores",
                            "suggest what's worth checking", "explore the shops",
                            "i can suggest what", "feel like browsing",
                        ),
                        "Of course! What kind of stores are you looking for? "
                        "Fashion, beauty, accessories, or something else?",
                    ),
                    (
                        (
                            "what's showing", "what is showing", "muvi cinema",
                            "cinema here if", "up for a film",
                            "in the mood for entertainment",
                        ),
                        "Sure! Muvi Cinema is here. Want me to list what's showing, "
                        "or are you looking for something else in entertainment?",
                    ),
                    (
                        (
                            "grab a bite", "point you to a good spot to eat",
                            "suggest something quick to eat", "getting hungry",
                        ),
                        "Happy to! What are you in the mood for — something quick, "
                        "a sit-down meal, coffee, or dessert?",
                    ),
                ]
                for _offer_signals, _offer_text in _TOPIC_OFFER_MAP:
                    if any(sig in _last_bot_f_lower for sig in _offer_signals):
                        _offer_msg = Message(
                            role="assistant",
                            content=_offer_text,
                            turn_id=state.turn_id,
                            metadata={
                                "strategy": "topic_offer_clarification",
                                "experience_mode": "clarification",
                            },
                        )
                        return {
                            "final_response_text": _offer_text,
                            "response_debug_summary": "strategy=topic_offer_clarification",
                            "messages": [_offer_msg],
                            "debug_enrichment": state.debug_enrichment.model_copy(
                                update={"response_experience_mode": "topic_offer_clarification"}
                            ),
                            "_trace_summary": "Followup affirmation confirmed topic-switch offer: returned clarifying prompt",
                        }

    is_mall_info = (
        state.response_plan.chosen_strategy == "mall_overview"
        or state.intent.domain == "mall_info"
    )

    # ── Factual flow: use dedicated factual response path ─────────────
    if state.flow_type == "factual":
        final_text, fact_warnings, experience_mode = await _build_factual_response(state)
        warnings.extend(fact_warnings)
    elif is_mall_info:
        final_text, info_warnings, experience_mode = await _build_mall_info_response(state)
        warnings.extend(info_warnings)
    elif state.intent.domain == "cross_mall":
        final_text, cross_warnings, experience_mode = await _build_cross_mall_response(state)
        warnings.extend(cross_warnings)
    else:
        mall_context = mall_ctx.get_canonical_for_prompt()
        system_prompt = get_concierge_system_prompt(mall_context)

        # ── Emotional recovery tone modifier ──────────────────────────
        # When the guest was recently frustrated or disengaged, append a
        # tone instruction so the LLM opens with brief, genuine empathy
        # before moving into recommendations.  This keeps the first sentence
        # warm without turning the entire response into an apology.
        if state.scene.recent_mood in ("emotional", "disengagement"):
            system_prompt += (
                "\n\nTONE OVERRIDE — EMOTIONAL RECOVERY: The guest was recently "
                "frustrated or disengaged. Open your response with ONE brief, "
                "sincere sentence that acknowledges their experience with warmth and "
                "composure (e.g. 'Allow me to make this a little easier for you.' or "
                "'Let me point you in the right direction.'). "
                "Do NOT over-apologise or dwell on the difficulty — pivot smoothly and "
                "confidently into your recommendation. "
                "Maintain the polished, reassuring register of a five-star concierge throughout."
            )

        query = state.normalized_user_message or state.raw_user_message
        playbook = _format_playbook(state)
        retrieval_results = _format_retrieval_results(state)
        strategy = state.response_plan.chosen_strategy

        is_category_turn = any(
            e.get("source", "").startswith("category/")
            for e in state.context.selected_entities
        )
        is_broad_discovery = any(
            "BROAD SHOPPING DISCOVERY" in n for n in state.context.ranking_notes
        )
        is_expanded_discovery = any(
            "EXPANDED DISCOVERY" in n for n in state.context.ranking_notes
        )
        is_offer_query = state.intent.sub_intent == "offer_details"

        # ── Experience layer: resolve mode for concierge path ─────────
        has_child = (
            "child" in state.scene.companions
            or any(d.get("type") == "child" for d in state.scene.companion_details)
        )
        exp = resolve_experience(
            flow_type="concierge",
            primary_intent=state.primary_intent or "",
            secondary_intents=list(state.secondary_intents or []),
            modifiers=list(state.modifiers or []),
            response_strategy=state.response_strategy or "",
            chosen_strategy=strategy,
            message_kind=state.intent.message_kind,
            domain=state.intent.domain,
            sub_intent=state.intent.sub_intent,
            fact_scope=state.fact_scope or "",
            has_companions=bool(state.scene.companions),
            has_occasion=bool(state.scene.occasion),
            has_child=has_child,
            visit_constraints=list(state.scene.visit_constraints or []),
            active_shortlist=list(state.scene.active_shortlist or []),
        )
        experience_mode = exp.response_experience_mode

        # ── Category / offer instruction ──────────────────────────────
        category_instruction = ""
        if is_offer_query:
            category_instruction = (
                "IMPORTANT: The guest is asking about offers, deals, or discounts. "
                "The 'Relevant tenants' section contains active offers and events. "
                "Present ALL offers with the store name, discount details, validity "
                "dates, and terms. If no offers matched for a specific store, "
                "acknowledge it with grace (e.g. 'I do not have a current offer on file "
                "for that store') and then present any other active offers from the context — "
                "do NOT leave the guest without options. "
                "If genuinely no offers exist in the context, say: 'There are no "
                "active promotions listed at present, though it is worth asking individual "
                "stores directly — many run in-store deals not published centrally.' "
                "NEVER say you don't have offer information if the context contains offers.\n\n"
            )
        elif is_broad_discovery:
            category_instruction = (
                "IMPORTANT: This is a broad shopping query. The 'Relevant tenants' "
                "section contains stores across multiple categories. Group them by "
                "category (e.g. Fashion, Beauty, Electronics, Jewellery) and present "
                "4-6 curated suggestions with a brief description and location. "
                "Present the mall as a rich, diverse destination worth exploring.\n\n"
            )
        elif is_expanded_discovery:
            category_instruction = (
                "IMPORTANT: The 'Relevant tenants' section includes both direct "
                "matches and related suggestions. Present the direct matches first, "
                "then gracefully introduce the related options "
                "(e.g. 'You may also enjoy...' or 'To complement that...'). "
                "Aim for 4-6 total suggestions to give the guest a considered selection.\n\n"
            )
        elif is_category_turn:
            # When a specific target person is set (e.g. "men", "son", "girlfriend"),
            # this is a targeted recommendation, not a directory lookup.
            # Override to shortlist mode so the LLM presents 2–3 best fits,
            # not a full category dump.
            _task = getattr(state.scene, "shopping_task", None)
            _target_person = (
                getattr(_task, "target_person", "") if _task else ""
            ) or ""
            _has_specific_target = _target_person not in ("", "self")

            if _has_specific_target:
                _product = getattr(_task, "product_type", "") or "items"
                category_instruction = (
                    f"IMPORTANT: The guest is shopping for {_product} for "
                    f"{_target_person}. The 'Relevant tenants' section contains "
                    f"stores that carry {_target_person}'s options. "
                    f"Present your 2–3 best recommendations — the stores most "
                    f"suited to this specific request. Do NOT list every store. "
                    "Choose the strongest fits, include the floor/zone for each, "
                    "and briefly explain in one line why each is the right choice. "
                    "Do NOT pad with stores from unrelated categories.\n\n"
                )
            else:
                category_instruction = (
                    "IMPORTANT: This is a category lookup. The 'Relevant tenants' "
                    "section contains ALL matching tenants for the requested category. "
                    "You MUST list ALL of them with a brief description and location. "
                    "Do NOT add stores from other categories. Do NOT pad with "
                    "unrelated suggestions like 'grab a coffee' or 'enjoy dessert'. "
                    "Keep your answer focused on exactly what was asked.\n\n"
                )

        conversation_context = _build_conversation_context(state)
        scene_ack = ""
        if state.response_plan.must_acknowledge_scene:
            scene_ack = _build_scene_acknowledgment(state)

        # ── Experience mode instruction (experience layer) ────────────
        experience_instruction = _build_concierge_experience_instruction(
            state=state,
            exp=exp,
            strategy=strategy,
            is_category_turn=is_category_turn,
            is_offer_query=is_offer_query,
        )

        # ── Hybrid intent instruction for concierge path ──────────────
        hybrid_concierge_instruction = _build_hybrid_concierge_instruction(state)

        # ── Response mode instruction (behaviour layer) ───────────────
        response_mode_instruction = _build_response_mode_instruction(state)

        # ── Decision response directive (adaptive concierge engine) ───
        decision_directive = _build_decision_response_directive(state)

        # ── CTA instruction ───────────────────────────────────────────
        cta_instruction = ""
        if not is_category_turn and not is_offer_query:
            cta_instruction = get_cta_instruction(exp.cta_type, strategy=strategy)

        # ── Assurance line instruction ────────────────────────────────
        assurance_instruction = _build_assurance_instruction(state)

        # ── Loyalty hint instruction ──────────────────────────────────
        loyalty_instruction = _build_loyalty_hint(state)

        # ── Domain-specific template instructions ─────────────────────
        dining_template_instruction = _build_dining_template_instruction(state)
        cinema_template_instruction = _build_cinema_template_instruction(state)

        user_prompt = (
            f"{conversation_context}"
            f"{scene_ack}"
            f"User query:\n{query}\n\n"
            f"Playbook plan:\n{playbook}\n\n"
            f"Relevant tenants:\n{retrieval_results}\n\n"
            f"{category_instruction}"
            f"{dining_template_instruction}"
            f"{cinema_template_instruction}"
            f"{hybrid_concierge_instruction}"
            f"{response_mode_instruction}"
            f"{decision_directive}"
            f"{experience_instruction}"
            f"{assurance_instruction}"
            f"{loyalty_instruction}"
            f"{cta_instruction}"
            "Generate a helpful concierge response. "
            "Only mention stores, restaurants, and services from the "
            "mall context. Never invent names or details."
        )

        langchain_messages = [SystemMessage(content=system_prompt)]
        langchain_messages.extend(_build_prior_messages(state))
        langchain_messages.append(HumanMessage(content=user_prompt))

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

    # ── Merge experience fields into debug_enrichment ─────────────────
    # Preserve all existing debug_enrichment fields set by earlier nodes
    # (rank_and_dedupe, resolve_playbooks, update_scene_memory), then add
    # experience layer fields on top.
    existing_de = state.debug_enrichment
    updated_de = existing_de.model_copy(update={
        "response_experience_mode": experience_mode,
    })

    debug_summary = (
        f"strategy={state.response_plan.chosen_strategy} | "
        f"response_mode={state.response_plan.response_mode or 'n/a'} | "
        f"confidence_level={state.response_plan.confidence_level or 'n/a'} | "
        f"experience_mode={experience_mode} | "
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
            "experience_mode": experience_mode,
        },
    )

    result: dict = {
        "final_response_text": final_text,
        "response_debug_summary": debug_summary,
        "messages": [assistant_msg],
        "debug_enrichment": updated_de,
        "_trace_summary": (
            f"Generated {len(final_text)} chars via "
            f"{state.response_plan.chosen_strategy} [{experience_mode}]"
        ),
    }
    if warnings:
        result["_trace_warnings"] = warnings
    return result


def _build_concierge_experience_instruction(
    *,
    state: ConciergeState,
    exp,
    strategy: str,
    is_category_turn: bool,
    is_offer_query: bool,
) -> str:
    """
    Build the experience-mode instruction block for the concierge response path.

    Uses the resolved ExperienceResolution to select the right template,
    replacing the previous ad-hoc if/elif chain with a clean mode-driven dispatch.
    The existing per-strategy fallbacks are preserved for backward compatibility.
    """
    if is_category_turn or is_offer_query:
        return ""

    entity_cap = state.response_plan.entity_cap or 5
    mode = exp.response_experience_mode

    # ── Refinement acknowledgement prefix ────────────────────────────
    ack_prefix = ""
    if exp.refinement_acknowledgement:
        ack_prefix = (
            f"REFINEMENT NOTE: {exp.refinement_acknowledgement}. "
            "Open with a natural acknowledgement of this refinement "
            "(e.g. 'For something quicker...' / 'If you're with a child...').\n"
        )

    # ── Mode: category negation (highest priority after disengagement) ───
    if state.intent.message_kind == "category_negation":
        excluded = state.scene.excluded_domains
        domains_str = " and ".join(excluded) if excluded else "the excluded category"
        return (
            f"{ack_prefix}"
            "CATEGORY EXCLUSION MODE: The guest has explicitly declined a category.\n"
            f"Excluded domains: {domains_str}\n"
            "Rules:\n"
            "  1. Do NOT suggest anything from the excluded domains — not even as a passing mention\n"
            "  2. Acknowledge the exclusion briefly and graciously (e.g. 'Of course — I will keep that in mind.')\n"
            "  3. Pivot to alternatives: invite the guest to share what they are looking for, "
            "or suggest non-excluded options (shopping, entertainment, services)\n"
            "  4. Keep the response concise — 2-3 sentences maximum\n\n"
        )

    # ── Mode: constraint refinement (highest priority) ────────────────
    if state.intent.message_kind == "constraint_refinement":
        constraints = state.scene.visit_constraints
        return (
            f"{ack_prefix}"
            "CONSTRAINT REFINEMENT MODE: The guest is refining a prior recommendation.\n"
            f"Applied constraints: {', '.join(constraints) if constraints else 'see previous context'}\n"
            "Do NOT restart from scratch. Instead:\n"
            "  1. Acknowledge the constraint naturally and composedly (e.g. 'For something a little quicker...')\n"
            "  2. Suggest 2-3 options that satisfy the new constraint\n"
            "  3. Be direct and concise — no lengthy lists\n\n"
        )

    # ── Mode: micro-itinerary ─────────────────────────────────────────
    if mode == MICRO_ITINERARY or (
        exp.itinerary_allowed and strategy in (
            "guided_plan", "mini_itinerary", "movie_plus_food",
            "family_plan", "route_plus_plan",
        )
    ):
        plan_str = (
            " → ".join(state.scene.visit_plan)
            if state.scene.visit_plan
            else "their planned activities"
        )
        visit_plan_note = (
            f"Planned sequence: {plan_str}. Honour this order.\n"
            if state.scene.visit_plan
            else ""
        )
        return (
            f"{ack_prefix}"
            "MICRO-ITINERARY MODE: Generate a short, step-by-step visit plan "
            f"(2–4 steps max, using at most {entity_cap} places).\n"
            f"{visit_plan_note}"
            "Rules:\n"
            "  1. Open with ONE composed sentence acknowledging the guest's situation\n"
            "     — Do NOT start with 'Great!', 'Sure!', 'Of course!', 'Hey', or any casual filler\n"
            "  2. Present each step as a natural, purposeful transition: "
            "'Begin at [X]...', 'From there, head to [Y]...', 'Close the visit at [Z]...'\n"
            "  3. Include store name, floor/zone, and ONE reason why it fits\n"
            "  4. Weave it into a narrative — NOT a bullet dump\n"
            "Speak like a knowledgeable concierge guiding a guest through a curated experience.\n\n"
        )

    # ── Mode: guided plan ─────────────────────────────────────────────
    if strategy == "guided_plan":
        return (
            f"{ack_prefix}"
            "GUIDED PLAN MODE: The guest has shared their situation — give them a curated concierge plan.\n"
            "Your response MUST:\n"
            f"  1. Open with ONE composed sentence acknowledging their situation\n"
            "     — Do NOT start with 'Great!', 'Sure!', 'Of course!', 'Hey', or any casual filler\n"
            f"  2. Deliver a compact 3–4 step plan (max {entity_cap} stores)\n"
            "  3. Weave store names into a natural narrative — NOT a bullet dump\n"
            "  4. Close with ONE brief, gracious offer to extend or refine the plan\n"
            "Example openers (vary each time — NEVER repeat the same pattern):\n"
            "  'Head to [X] first — the strongest fit for your visit. Then [Y] for [reason].'\n"
            "  'For [situation], [X] is your best option. [Y] is an excellent alternative.'\n"
            "  'Here is a plan for [situation]: begin at [X], then [Y], and finish at [Z].'\n\n"
        )

    # ── Mode: curated shortlist ───────────────────────────────────────
    if mode == CURATED_SHORTLIST or strategy in (
        "concise_shortlist", "shortlist_recommendation", "gift_formula",
    ):
        return (
            f"{ack_prefix}"
            "CURATED SHORTLIST MODE: Present a focused, curated list.\n"
            f"  • 3–4 best options max (out of {entity_cap} available)\n"
            "  • Lead with your top pick and briefly say why it fits\n"
            "  • Include floor/zone for each option\n"
            "  • One sentence of context per option — no lengthy descriptions\n"
            "  • End with an offer to narrow further if needed\n\n"
        )

    # ── Mode: quick answer ────────────────────────────────────────────
    if strategy == "quick_answer":
        return (
            f"{ack_prefix}"
            "QUICK ANSWER MODE: Brief and direct.\n"
            "  • Maximum 3 options\n"
            "  • The guest has a constraint — honour it immediately\n"
            "  • Acknowledge the constraint composedly, give the shortlist, done\n\n"
        )

    # ── Sequential continuation ───────────────────────────────────────
    is_sequential = bool(
        state.scene.completed_steps or state.scene.visit_plan
    ) and state.intent.message_kind in ("followup", "refinement")
    if is_sequential:
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
                    f" Remaining plan: {' → '.join(remaining)}."
                )
        return (
            f"{ack_prefix}"
            f"SEQUENCE CONTINUATION: The guest has already explored {last_covered}. "
            "Continue their visit journey seamlessly — do NOT re-suggest anything "
            f"from previous steps.{next_steps} "
            "Respond as a concierge guiding them gracefully to the next part of the visit.\n\n"
        )

    # ── Playbook itinerary mode ───────────────────────────────────────
    if bool(state.playbook.selected_playbook):
        return (
            f"{ack_prefix}"
            "ITINERARY MODE: A scenario playbook is active. Generate a MINI-ITINERARY.\n"
            "  1. Start with [first activity/place]\n"
            "  2. Then [second activity/place]\n"
            "  3. Finish with [third activity/place]\n"
            "Use natural transitions: 'Start at...', 'Then head to...', 'Finish with...'.\n"
            "Include specific store names, floor locations, and brief reasons.\n\n"
        )

    # ── Multi-activity visit plan mode ────────────────────────────────
    if state.scene.multi_activity_mode:
        plan_str = (
            " → ".join(state.scene.visit_plan)
            if state.scene.visit_plan
            else "their planned activities"
        )
        return (
            f"{ack_prefix}"
            f"VISIT PLAN MODE: The guest has a multi-step plan: {plan_str}.\n"
            "Structure your response as a curated mini-itinerary that honours this sequence. "
            "Use natural, flowing transitions. Do NOT reorder the planned steps.\n\n"
        )

    # ── Default: experience guidance when context is present ─────────
    has_context = bool(
        state.scene.companions
        or state.scene.occasion
        or state.scene.audience
        or state.scene.target_person
        or state.scene.visit_type
    )
    if has_context:
        return (
            f"{ack_prefix}"
            "EXPERIENCE GUIDANCE: The guest has shared context about their visit. "
            "Frame your response as a guided, curated experience — not a flat list. "
            "Lead with what fits them best, explain why in one line, "
            "and close with one gracious follow-up offer.\n\n"
        )

    return ack_prefix  # May still carry a refinement note


def _build_hybrid_filter_instruction(fact_ctx: dict, state: ConciergeState) -> str:
    """
    Build an instruction block for the LLM that explains which secondary
    filters / modifiers are active for a hybrid factual query.

    For example: "any movies with the kid?" → fact flow + family_filter active.
    The LLM should lead with facts and add a brief family-friendly note.
    """
    secondary_intents = state.secondary_intents or []
    modifiers = state.modifiers or []

    if not secondary_intents and not modifiers:
        return ""

    lines: list[str] = ["\nHYBRID QUERY FILTERS ACTIVE:"]

    if fact_ctx.get("family_filter_active"):
        lines.append(
            "• FAMILY FILTER: The guest has a child with them. "
            "Lead with the factual answer (e.g. film schedule). "
            "You MAY add ONE brief sentence about which options are most "
            "family-friendly or suited for children — but do NOT replace the facts."
        )
    if fact_ctx.get("budget_filter_active"):
        lines.append(
            "• BUDGET FILTER: Prefer affordable options. "
            "Note any budget-friendly options in your answer."
        )
    if fact_ctx.get("proximity_filter_active"):
        lines.append(
            "• PROXIMITY/TIMING FILTER: Options near the cinema or time-efficient "
            "are preferred. Note proximity where possible."
        )
    if fact_ctx.get("romantic_filter_active"):
        lines.append(
            "• ROMANTIC FILTER: The guest is with a partner. "
            "Note any couple-friendly or occasion-worthy aspects where relevant."
        )

    if len(lines) == 1:
        return ""

    lines.append(
        "\nRULE: The primary answer (factual data) MUST come first. "
        "Secondary filter hints are modifiers only — they do NOT replace the answer.\n"
    )
    return "\n".join(lines) + "\n"


def _build_decision_response_directive(state: ConciergeState) -> str:
    """
    Inject a 4-part decision structure when the response is a guided recommendation.

    Activates for guided recommendation queries that are NOT:
    - category dumps (full tenant list)
    - offer queries
    - factual lookups
    - context-acknowledgement turns (user is just setting scene, not requesting action)
    - clarification / graceful_recovery modes

    The 4-part structure is:
      1. PRIMARY RECOMMENDATION — best single option + 1-line why
      2. SECONDARY OPTION       — one solid fallback + 1-line why
      3. ACTION PLAN            — where to go first, what to do next
      4. OPTIONAL FOLLOW-UP     — only if genuinely helpful

    Also injects the scene_sufficient no-clarification directive when the scene
    already has enough context (companions / budget / target_person / etc).
    """
    mode = state.response_plan.response_mode
    message_kind = state.intent.message_kind

    # Only activate for guided recommendation responses
    is_guided = mode in ("guided_recommendation", "hybrid_plan", "")
    is_action_needed = message_kind not in (
        "context_setting", "greeting", "smalltalk",
    )
    is_category_turn = any(
        e.get("source", "").startswith("category/")
        for e in state.context.selected_entities
    )
    is_offer_query = state.intent.sub_intent == "offer_details"
    is_recovery = mode in ("graceful_recovery", "clarification_request")

    if not is_guided or not is_action_needed or is_category_turn or is_offer_query or is_recovery:
        return ""

    parts: list[str] = []

    # ── Investigative mode: vague gift/shopping with no target context ─
    needs_clarification = getattr(state.scene, "needs_clarification", False)
    clarification_topic = getattr(state.scene, "clarification_topic", "")
    if needs_clarification and clarification_topic == "gift_target":
        parts.append(
            "INVESTIGATIVE MODE — VAGUE GIFT QUERY:\n"
            "The guest is asking about a gift but has not indicated who it is for.\n"
            "Follow this approach:\n"
            "  1. Offer 2 broad gift suggestions as an immediate opener — demonstrate you are ready to help.\n"
            "  2. Ask exactly ONE targeting question, e.g.: 'Is this for a partner, "
            "a child, or a friend? That will help me narrow things down for you.'\n"
            "Do NOT ask about budget yet. Do NOT ask more than one question.\n"
            "Keep the opener concise — 2 options at most before the question.\n"
        )
        return "\n".join(parts) + "\n"
    elif needs_clarification and clarification_topic == "shopping_target":
        parts.append(
            "INVESTIGATIVE MODE — VAGUE SHOPPING QUERY:\n"
            "The guest's shopping query is open-ended without enough context.\n"
            "Offer 2–3 broad suggestions across different categories, then ask:\n"
            "'Are you shopping for someone in particular, or simply browsing?'\n"
            "One question only.\n"
        )
        return "\n".join(parts) + "\n"

    # ── Scene sufficient → block clarification questions ──────────────
    scene_sufficient = getattr(state.debug_enrichment, "scene_sufficient", False)
    if scene_sufficient:
        parts.append(
            "SCENE CONTEXT IS SUFFICIENT: The guest's context (companions, budget, "
            "target person, or occasion) is already known. "
            "DO NOT ask a clarifying question. Infer any remaining details and "
            "respond directly with a well-considered recommendation.\n"
        )

    # ── 4-part decision structure ──────────────────────────────────────
    # Activated for guided/concierge recommendation turns only.
    # Category list turns and factual lookups keep their own structure.
    if state.response_plan.chosen_strategy not in (
        "mall_overview", "exploration_overview", "direct_fact",
        "structured_fact_list", "direct_lookup", "schedule_answer",
    ):
        parts.append(
            "DECISION STRUCTURE — shape your response as follows:\n"
            "  1. PRIMARY RECOMMENDATION: Your single best selection. "
            "One sentence explaining precisely why it is the right choice for this guest.\n"
            "  2. SECONDARY OPTION: One considered alternative / fallback. "
            "One sentence on how it differs from the primary and when it suits better.\n"
            "  3. ACTION PLAN: Tell the guest exactly what to do next — "
            "where to go first, what to look for.\n"
            "  4. OPTIONAL FOLLOW-UP: Add ONE brief question ONLY if it would "
            "meaningfully improve the next recommendation. Omit entirely if the "
            "plan is already clear and complete.\n"
            "HARD RULES: Max 2–3 stores total. Max 2 lines per item. "
            "No generic brand descriptions. No catalogue-style lists.\n"
        )

    return "\n".join(parts) + "\n" if parts else ""


def _build_response_mode_instruction(state: ConciergeState) -> str:
    """
    Translate the resolved response_mode into concrete LLM-prompt guidance.

    This is a LIGHT-TOUCH overlay — it reinforces the existing experience/
    strategy instructions; it never silences them.  Modes already fully
    handled upstream (context_setting, unsupported) return an empty string.
    """
    mode = state.response_plan.response_mode
    confidence = state.response_plan.confidence_level

    if not mode:
        return ""

    # ── clarification_request ─────────────────────────────────────────
    # Handled by is_unsupported early-exit; this fallback catches any that
    # slip through to the overlay stage.
    if mode == "clarification_request":
        return (
            "RESPONSE MODE — CLARIFICATION REQUEST:\n"
            "The guest has asked for something outside our current capabilities, "
            "or the input was unclear. Rules:\n"
            "  1. Acknowledge the limitation graciously — briefly and without dwelling on it.\n"
            "  2. Redirect to what the concierge CAN assist with.\n"
            "  3. Do NOT attempt the unsupported action.\n"
            "  4. Keep it composed and brief — one or two sentences at most.\n\n"
        )

    # ── graceful_recovery (low-confidence, non-unsupported) ───────────
    # The `is_unsupported` early-exit in generate_response already catches
    # intent.primary_intent == "unsupported".  This branch handles low-
    # confidence queries that still reached the concierge path.
    if mode == "graceful_recovery":
        # Exclude the domain that just failed so we never re-suggest it.
        current_domain = (state.intent.domain or "").lower() if state else ""
        _RECOVERY_OPTIONS: list[tuple[str, str]] = [
            ("dining",        "dining (restaurants, cafes, quick bites)"),
            ("shopping",      "shopping (stores, brands, offers)"),
            ("entertainment", "cinema and entertainment (movies, showtimes)"),
            ("services",      "services and directions (parking, prayer rooms, ATM)"),
            ("navigation",    "navigation (finding a specific store or facility)"),
        ]
        alternatives = ", ".join(
            label
            for domain, label in _RECOVERY_OPTIONS
            if domain != current_domain
        )
        return (
            "RESPONSE MODE — GRACEFUL RECOVERY:\n"
            "The guest's query is unclear or outside supported topics. "
            "Rules:\n"
            "  1. Do NOT hallucinate stores, services, or details.\n"
            f"  2. Offer 3–4 supported directions graciously from: {alternatives}.\n"
            "  3. Ask ONE short, focused clarifying question if it would genuinely help.\n"
            "  4. Never claim information you do not have.\n\n"
        )

    # ── context_acknowledgement ───────────────────────────────────────
    # Already handled in detail by _build_conversation_context(); just echo.
    if mode == "context_acknowledgement":
        return ""

    # ── hybrid_plan ───────────────────────────────────────────────────
    if mode == "hybrid_plan":
        return (
            "RESPONSE MODE — HYBRID PLAN:\n"
            "The guest's query covers more than one goal (e.g. dining AND cinema). "
            "Rules:\n"
            "  1. Produce ONE unified, seamless answer — NOT two disconnected lists.\n"
            "  2. Lead with the dominant intent; weave the secondary goal in naturally.\n"
            "  3. Use a structured mini-plan format (2–3 steps at most).\n"
            "  4. Example: 'Here is a plan: enjoy a film at [Cinema], "
            "then a wonderful dinner at [Restaurant] just nearby.'\n\n"
        )

    # ── best_effort_shortlist ─────────────────────────────────────────
    if mode == "best_effort_shortlist":
        return (
            "RESPONSE MODE — BEST EFFORT SHORTLIST:\n"
            "The query is broad or partially clear. Rules:\n"
            "  1. Offer 3–5 strong, relevant options.\n"
            "  2. Lightly acknowledge any ambiguity "
            "(e.g. 'Without a specific preference to go on...' or 'Here's a good starting point:').\n"
            "  3. Do NOT claim constraints or specifics you cannot verify.\n"
            "  4. End with ONE short, focused follow-up question if it would help "
            "narrow the choice.\n\n"
        )

    # ── direct_factual ────────────────────────────────────────────────
    if mode == "direct_factual":
        return (
            "RESPONSE MODE — DIRECT FACTUAL:\n"
            "Give a concise, structured answer grounded in the provided data. "
            "Lead with the key fact. No padding or speculation.\n\n"
        )

    # ── guided_recommendation ─────────────────────────────────────────
    if mode == "guided_recommendation":
        if confidence == "medium":
            return (
                "RESPONSE MODE — GUIDED RECOMMENDATION:\n"
                "Present a focused, curated shortlist (3–5 options) with a brief reason "
                "why each one is the right choice for this guest. "
                "Acknowledge any scenario or companion context naturally and graciously.\n\n"
            )
        # high confidence — minimal overlay; experience layer already handles it
        return ""

    return ""


def _build_hybrid_concierge_instruction(state: ConciergeState) -> str:
    """
    Build an instruction block for the concierge path when hybrid intent is present.

    Ensures the primary goal governs the response structure and secondary
    filters are modifiers, not intent replacements.
    """
    primary_intent = state.primary_intent or ""
    secondary_intents = state.secondary_intents or []
    modifiers = state.modifiers or []

    if not primary_intent and not secondary_intents and not modifiers:
        return ""

    lines: list[str] = []

    if primary_intent or secondary_intents:
        lines.append("\nHYBRID INTENT CONTEXT:")
        if primary_intent:
            lines.append(
                f"• PRIMARY GOAL: {primary_intent} — this is what the guest fundamentally wants. "
                "Shape the primary answer around this."
            )
        if secondary_intents:
            lines.append(
                f"• SECONDARY FILTERS: {', '.join(secondary_intents)} — "
                "these modify the primary answer. They should filter options and "
                "adjust the tone, but NEVER replace the primary goal."
            )
        if modifiers:
            lines.append(
                f"• ACTIVE MODIFIERS: {', '.join(modifiers)} — "
                "use these to bias entity selection and response wording."
            )

    # Specific guidance for common patterns
    if "family_filter" in secondary_intents or "kid_friendly" in modifiers:
        lines.append(
            "• Since a child is with the guest, prioritise family-friendly options "
            "and briefly note why each is suited for children. "
            "Do NOT redirect the entire response to 'kids entertainment' unless that "
            "is what was explicitly requested."
        )
    if "before_movie_constraint" in secondary_intents or "near_cinema" in modifiers:
        lines.append(
            "• TIMING CONTEXT: The guest has a time/location constraint relative to the cinema. "
            "Prioritise options that are efficient and close to the cinema area."
        )
    if "gift_for" in secondary_intents or "gift_friendly" in modifiers:
        lines.append(
            "• GIFTING CONTEXT: This is a gift-buying scenario. "
            "Focus on gift-suitable options for the target person."
        )
    if "romantic_filter" in secondary_intents or "romantic" in modifiers:
        lines.append(
            "• ROMANTIC CONTEXT: Couple/partner is present. "
            "Bias toward couple-friendly, premium, or special-occasion options."
        )

    if not lines:
        return ""

    lines.append("")
    return "\n".join(lines) + "\n"


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
