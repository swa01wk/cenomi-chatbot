"""
Small Talk node — handles greetings, casual messages, and emotional expressions
with fast, mall-anchored responses that skip the full pipeline.

CONTRACT
────────
  Purpose:  Respond to greetings, pleasantries, emotional expressions, crisis
            statements, and identity questions with warm, on-brand responses.
            Greetings adapt based on how many times the user has greeted before
            in the same session (progressive: welcome → mall insight → direct ask).
            Emotional / crisis expressions receive empathy-first responses.
            FAREWELL: LLM-generated using scene context for personalisation;
            static pool used as fallback on error.
  Reads:    intent.message_kind (set by interpret_turn), turn_id,
            scene.greeting_streak, scene (for farewell personalisation)
  Writes:   final_response_text, response_debug_summary,
            messages (appends assistant Message)
  Failure:  LLM farewell error → falls back to static pool (no crash)
  Routing:  Always → update_memory

  Classification is performed entirely by the LLM in interpret_turn.py.
  Routing predicate is_smalltalk() reads state.intent.message_kind
  against SMALLTALK_KINDS — no regex matching in this module.
"""

from __future__ import annotations

import logging
import random

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, Message, MessageKind, ResponsePlan, SMALLTALK_KINDS
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# ── Farewell LLM (cached singleton, gpt-4o-mini) ─────────────────────
# Used only for farewell turns to personalise the goodbye with scene context.
# All other smalltalk kinds remain static (no LLM cost).

_farewell_llm: ChatOpenAI | None = None


def _get_farewell_llm() -> ChatOpenAI:
    global _farewell_llm
    if _farewell_llm is None:
        settings = get_settings()
        _farewell_llm = ChatOpenAI(
            model=settings.classifier_model,
            temperature=0.7,
            api_key=settings.openai_api_key,
            max_tokens=80,
        )
    return _farewell_llm


async def _generate_farewell(state: ConciergeState) -> str:
    """
    Generate a personalised farewell using scene context.

    Falls back to the static pool on any LLM error so the node never fails.
    """
    scene = state.scene

    context_parts: list[str] = []
    if scene.companions:
        context_parts.append(f"companions: {', '.join(scene.companions)}")
    if scene.occasion:
        context_parts.append(f"occasion: {scene.occasion}")
    if scene.active_topic and scene.active_topic not in ("general", ""):
        context_parts.append(f"browsing topic: {scene.active_topic}")
    if scene.active_shortlist:
        context_parts.append(
            f"stores/options discussed: {', '.join(scene.active_shortlist[:3])}"
        )
    if scene.visit_type:
        context_parts.append(f"visit type: {scene.visit_type}")

    scene_str = (
        "; ".join(context_parts) if context_parts else "no specific context shared"
    )

    system_prompt = (
        "You are a distinguished digital mall concierge bidding farewell to a departing guest. "
        "Write exactly ONE gracious farewell — 2 sentences maximum. "
        "If scene context is available, personalise the first sentence naturally: "
        "acknowledge who they were with or what they came to do, without repeating details verbatim. "
        "The closing sentence must warmly assure them that you remain available should they "
        "need anything further during their visit. "
        "Do NOT mention specific store names. Do NOT make new recommendations. "
        "Maintain a polished, warm, and composed tone — the register of a five-star concierge. "
        "Never use casual language: no 'See you!', 'Take care!', 'Bye!', or informal phrases."
    )

    human_prompt = (
        f"Scene context: {scene_str}\n"
        f"Visitor said: {state.raw_user_message or 'goodbye'}"
    )

    try:
        llm = _get_farewell_llm()
        response = await llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
        )
        text = (response.content or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("Farewell LLM call failed, using static fallback: %s", exc)

    return random.choice(_RESPONSES["farewell"])

# ── Progressive greeting response pools ──────────────────────────────
#
# Three tiers keyed to scene.greeting_streak (how many consecutive greeetings
# have already been answered in this session):
#   streak == 0: first greeting — warm welcome, brief capability hint
#   streak == 1: second greeting — acknowledge, give one concrete mall pointer
#   streak >= 2: third+ greeting — gently ask what they're actually looking for

_GREETING_FIRST: list[str] = [
    "Welcome. I am your personal mall concierge — here to help you make the very most "
    "of your visit. Whether you are looking for a restaurant, a particular store, "
    "cinema times, or any of our facilities, I am at your service. "
    "What can I arrange for you today?",

    "Welcome to the mall. I am your dedicated concierge for today's visit — "
    "shopping, dining, entertainment, or any services you may need. "
    "How may I assist you?",

    "A warm welcome to you. I am here to guide your visit from start to finish — "
    "whether that means finding the right store, the perfect place to dine, "
    "a film for the family, or simply getting your bearings. "
    "What would you like to explore first?",

    "Welcome. Your concierge is here. I can help you discover stores, "
    "plan a meal, check what's on at the cinema, or assist with any of "
    "our facilities and services. What brings you in today?",
]

_GREETING_RETURNING: list[str] = [
    "Still with you — take all the time you need. It is a generous space. "
    "The ground floor is home to most of the retail and dining, the cinema is on the "
    "upper level, and I am here to guide you effortlessly. What are you looking for?",

    "Still here whenever you are ready. A brief orientation: fashion and accessories "
    "are on the Ground Floor, dining is near the central atrium, and entertainment "
    "is on the Upper Level. Is there something specific I can help you with?",

    "At your service. If you are not quite sure where to begin, simply tell me one thing "
    "you have in mind — shopping, a meal, a film, something for the family — "
    "and I will put together a plan from there.",
]

_GREETING_PERSISTENT: list[str] = [
    "I am right here — there is absolutely no rush. "
    "What is one thing you would like to do or find during your visit? "
    "Even something as simple as 'food' or 'a gift' is all I need to get started.",

    "Whenever you are ready. Sometimes the easiest place to begin is the first "
    "thing that comes to mind — a store, something to eat, or simply "
    "'I am not sure, suggest something'. I will take it from there.",

    "I am entirely at your disposal. If it helps to frame it: "
    "are you here primarily to shop, to dine, to catch a film, or simply to explore? "
    "Any of those is a fine starting point and I will take care of the rest.",
]

# ── Other response pools ───────────────────────────────────────────────

_IDENTITY_RESPONSES: list[str] = [
    "I am your personal digital concierge for this mall — here to ensure your visit is "
    "as smooth and enjoyable as possible. Stores, dining, cinema, family activities, "
    "parking, facilities — simply ask and I will take care of the rest.",

    "Think of me as your dedicated mall concierge, powered by the latest technology. "
    "I have complete knowledge of every store, restaurant, and service here. "
    "What would you like help with today?",

    "I am a digital concierge — designed to make your visit exceptional. "
    "Whether you need directions, a dining recommendation, the latest offers, "
    "or help planning your time, I am here for you. How may I assist?",
]

_CRISIS_RESPONSES: list[str] = [
    "What you have shared matters, and I want you to know that. "
    "Please reach out to someone you trust, or contact a crisis support line — "
    "you do not need to face this alone, and real help is available to you.",

    "I hear you, and your wellbeing is what matters most right now. "
    "Please speak to a trusted person in your life or reach out to a crisis helpline. "
    "You deserve proper support, and I genuinely hope you find it.",

    "That sounds very difficult, and I am sorry. Please do not carry this alone — "
    "a crisis helpline or someone close to you can offer the care and support you deserve. "
    "I am only a mall concierge, but I care that you get the help you need.",
]

_RESPONSES: dict[str, list[str]] = {
    "howru": [
        "Very well, thank you for asking. What can I help you with today?",
        "All is well — and I am fully at your service. Shopping, dining, a film, or something else?",
        "Doing wonderfully, thank you. What may I assist you with?",
    ],
    "thanks": [
        "It is my pleasure. Is there anything else I can help you with?",
        "My pleasure entirely — please do not hesitate to ask if anything else comes to mind.",
        "You are most welcome. I am here whenever you need me.",
    ],
    # Context-aware thanks variants — used when scene has meaningful context.
    # These append a proactive nudge toward something the guest hasn't explored yet.
    "thanks_with_dining_suggestion": [
        "My pleasure. If you have not yet had a chance to dine, there are some wonderful options "
        "at the Food Court — I would be happy to recommend something.",
        "It was my pleasure to assist. Should you be looking for somewhere to eat, "
        "I can point you to an excellent spot — just say the word.",
        "Of course. If you find yourself wanting a bite to eat, I can suggest something "
        "that suits your taste perfectly.",
    ],
    "thanks_with_shopping_suggestion": [
        "My pleasure. There is a great deal of wonderful shopping here as well — "
        "shall I point you to any particular stores?",
        "It was a pleasure. If you feel like exploring the retail offering, "
        "I can suggest what is well worth your time.",
        "Of course. And if you would like to browse the shops while you are here, "
        "I am happy to guide you.",
    ],
    "thanks_with_entertainment_suggestion": [
        "My pleasure. Should you be in the mood for a film, the cinema is right here — "
        "would you like to know what is currently showing?",
        "It was a pleasure to assist. If you are looking for entertainment, "
        "there is a great deal on offer — just ask.",
        "Of course. There is a cinema here if you would like to round off your visit "
        "with a film — I can pull up the listings for you.",
    ],
    "thanks_with_generic_suggestion": [
        "My pleasure. There is still much to discover here — "
        "dining, shopping, entertainment, or any services you may need. Just ask.",
        "Of course. Whenever you are ready to explore further — "
        "a meal, a store, a film, or anything else — I am right here.",
        "You are most welcome. If there is anything else on your list today, "
        "I am delighted to help.",
    ],
    "farewell": [
        "It has been a pleasure assisting you. Enjoy the rest of your visit — "
        "I am right here should you need directions, a recommendation, or anything at all.",

        "Wishing you a wonderful time. Should anything come up during your visit, "
        "do not hesitate to reach out — I am always available.",

        "Until next time. And if you need anything further while you are still here — "
        "a store to find, somewhere to eat, any service at all — just send a message.",
    ],
    "emotional": [
        "I understand — let us make this simpler. Tell me one thing you came here for, "
        "even something general, and I will take it from there. "
        "There is no need to have it all figured out.",

        "Large spaces can sometimes feel a little overwhelming — that is perfectly natural. "
        "If it helps, simply choose one thing: a bite to eat, a particular store, a film — "
        "and I will guide you there directly. What appeals to you most?",

        "I hear you. Let me help bring some clarity — what was the one thing you originally "
        "came here to do? Even a general idea is all I need to point you in the right direction.",

        "I am sorry to hear that. I am here to make your visit easier, not more complicated. "
        "Tell me what is on your mind — whether it is finding something specific "
        "or simply needing a moment to regroup — and we will sort it together.",
    ],
    "emotional_mood_plan": [
        "Allow me to put something together for you. A restorative visit might look like this: "
        "begin with something delicious at the Food Court, take a leisurely browse through the stores, "
        "and if you want to switch off completely, the cinema is here too. "
        "Shall I build a proper feel-good route for you?",

        "It sounds as though you could do with a truly enjoyable visit today. "
        "Here is a simple plan: a treat from the Food Court, a relaxed browse through the shops, "
        "and a film to close the evening if you fancy it. "
        "Which part of that sounds most appealing to start with?",

        "Consider it handled. Start with something wonderful to eat, "
        "take your time with the shops, and end with a good film or a quiet coffee. "
        "What sounds most appealing to you right now?",
    ],
}


_KIND_TO_POOL: dict[MessageKind, str] = {
    MessageKind.HOWRU:     "howru",
    MessageKind.FAREWELL:  "farewell",
    MessageKind.EMOTIONAL: "emotional",
}

# Domains that count as "completed" for proactive follow-up logic.
# Ordered by priority of suggestion (most universally useful first).
_SUGGESTION_PRIORITY: list[tuple[str, str]] = [
    ("dining",         "thanks_with_dining_suggestion"),
    ("shopping",       "thanks_with_shopping_suggestion"),
    ("entertainment",  "thanks_with_entertainment_suggestion"),
]


def _pick_emotional_response(state: ConciergeState) -> str:
    """
    Return a context-aware emotional response.

    When the visitor expresses a mood + asks for a plan/activity to feel better,
    use the uplifting mood-plan pool. Otherwise use the standard empathetic pool.
    """
    msg = (state.normalized_user_message or state.raw_user_message or "").lower()
    _MOOD_PLAN_SIGNALS = (
        "make me happy", "cheer me up", "feel better", "give me a plan",
        "make it better", "something fun", "fun plan", "good plan",
        "pick me up", "lift my mood",
    )
    if any(sig in msg for sig in _MOOD_PLAN_SIGNALS):
        return random.choice(_RESPONSES["emotional_mood_plan"])
    return random.choice(_RESPONSES["emotional"])


def _pick_thanks_response(state: ConciergeState) -> str:
    """
    Return a context-aware thanks response.

    If the visitor has covered some topics already, proactively suggest
    a domain they haven't explored yet — so the conversation stays alive
    rather than ending with a dead-end "you're welcome".

    Falls back to the generic thanks pool when no context is available.
    """
    scene = state.scene
    completed = set(scene.completed_steps or [])
    active = scene.active_topic or ""

    # Build the set of domains the visitor has NOT yet explored.
    unexplored = [
        pool_key
        for domain, pool_key in _SUGGESTION_PRIORITY
        if domain != active and domain not in completed
    ]

    if unexplored and (completed or active):
        # Suggest the first unexplored domain that makes sense given context.
        pool_key = unexplored[0]
        return random.choice(_RESPONSES[pool_key])

    # No meaningful context — use the generic pool.
    return random.choice(_RESPONSES["thanks_with_generic_suggestion"])


def is_smalltalk(state: ConciergeState) -> bool:
    """
    Graph-routing predicate: ``True`` when the LLM classified this turn as
    one of the SMALLTALK_KINDS message kinds.

    Reads the enum field set by interpret_turn — no regex here.
    """
    return state.intent.message_kind in SMALLTALK_KINDS


@traced_node("smalltalk")
async def smalltalk(state: ConciergeState) -> dict:
    kind: MessageKind = state.intent.message_kind

    if kind == MessageKind.CRISIS:
        response_text = random.choice(_CRISIS_RESPONSES)
        experience_mode = "crisis_support"
    elif kind == MessageKind.IDENTITY:
        response_text = random.choice(_IDENTITY_RESPONSES)
        experience_mode = "identity_response"
    elif kind == MessageKind.GREETING:
        # greeting_streak is how many consecutive greetings have already been
        # answered in this session (persisted in SceneMemory via update_memory).
        # It hasn't been incremented for the CURRENT turn yet, so streak=0 means
        # this is the very first greeting.
        streak = state.scene.greeting_streak
        if streak == 0:
            response_text = random.choice(_GREETING_FIRST)
        elif streak == 1:
            response_text = random.choice(_GREETING_RETURNING)
        else:
            response_text = random.choice(_GREETING_PERSISTENT)
        experience_mode = "greeting_scaffold"
    elif kind == MessageKind.FAREWELL:
        # LLM-generated: personalises the goodbye using scene context
        # (companions, occasion, active_topic, active_shortlist).
        # Falls back to the static pool automatically on any LLM error.
        response_text = await _generate_farewell(state)
        experience_mode = "farewell_personalised"
    elif kind == MessageKind.THANKS:
        response_text = _pick_thanks_response(state)
        experience_mode = "thanks_response"
    elif kind == MessageKind.EMOTIONAL:
        response_text = _pick_emotional_response(state)
        experience_mode = "emotional_response"
    elif kind in _KIND_TO_POOL:
        response_text = random.choice(_RESPONSES[_KIND_TO_POOL[kind]])
        experience_mode = "smalltalk"
    else:
        # Unexpected SMALLTALK_KINDS member — fall back to greeting
        response_text = random.choice(_GREETING_FIRST)
        experience_mode = "greeting_scaffold"

    category = kind.value  # string value for logging / metadata

    assistant_msg = Message(
        role="assistant",
        content=response_text,
        turn_id=state.turn_id,
        metadata={
            "strategy": "smalltalk",
            "category": category,
            "experience_mode": experience_mode,
        },
    )

    return {
        "final_response_text": response_text,
        "response_debug_summary": f"smalltalk/{category} [{experience_mode}]",
        "messages": [assistant_msg],
        "_trace_summary": f"Small talk ({category}): {len(response_text)} chars",
        # Propagate response_mode so emit_debug_payload reflects context_acknowledgement
        # for all smalltalk turns (greetings, thanks, farewell, identity, crisis, etc.)
        "response_plan": state.response_plan.model_copy(
            update={"response_mode": "context_acknowledgement", "confidence_level": "high"}
        ),
    }
