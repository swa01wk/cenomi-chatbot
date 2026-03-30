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
from app.models.state import ConciergeState, Message, MessageKind, SMALLTALK_KINDS
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
        "You are a warm mall concierge saying goodbye to a departing visitor. "
        "Write exactly ONE short farewell — 2 sentences maximum. "
        "If scene context is available, personalise the first sentence naturally: "
        "reference who they are with or what they were looking for. "
        "The final sentence must tell them you are still available if they need "
        "anything else during their visit. "
        "Do NOT mention specific store names. Do NOT make new recommendations. "
        "Sound warm and natural, like a real person — not corporate."
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
    "Hey, welcome! I'm your mall guide — ask me anything about shops, dining, "
    "movies, or services like parking and prayer rooms. What brings you in today?",

    "Hi there! Good to have you here. Whether you're after a meal, some shopping, "
    "a film, or you're just exploring — I can point you in the right direction. "
    "What are you in the mood for?",

    "Hello! I'm here to help you make the most of your visit. "
    "Shops, restaurants, cinema, kids' spots, or practical things like ATMs and parking — "
    "just ask. What's on your mind?",

    "Ahlan! Welcome to the mall. I can help you discover great stores, find a place to eat, "
    "check movie times, or sort out any services. What would you like to do today?",
]

_GREETING_RETURNING: list[str] = [
    "Hey again! Still finding your footing? No worries — it's a big place. "
    "The ground floor has most of the shops and food spots, the cinema is upstairs, "
    "and I'm here to make it easy. What are you actually looking for?",

    "Back again — that's fine, take your time! Quick heads-up: fashion and accessories "
    "are mostly in the Main Gallery, dining is near the central atrium, and entertainment "
    "is on the upper level. Anything specific catch your interest?",

    "Still here, ready when you are! The mall has a lot going on today — "
    "if you're not sure where to start, tell me one thing you enjoy "
    "(shopping, food, a film, something for the kids?) and I'll build from there.",
]

_GREETING_PERSISTENT: list[str] = [
    "I'm with you — no rush at all. What's one thing you'd like to do or find today? "
    "Even something vague like 'food' or 'gifts' works and I'll take it from there.",

    "Still here! Sometimes the easiest way is to just tell me the first thing "
    "that comes to mind — a store you're looking for, something to eat, or even "
    "just 'I don't know, suggest something'. What do you feel like?",

    "Whenever you're ready. If it helps to narrow it down: "
    "are you here mainly to shop, eat, catch a film, or is this more of a browse? "
    "That's enough for me to give you a decent starting point.",
]

# ── Other response pools ───────────────────────────────────────────────

_IDENTITY_RESPONSES: list[str] = [
    "I'm your mall concierge assistant — here to help you get the most out of your visit. "
    "Ask me about shops, dining, movies, entertainment, or any services like parking and prayer rooms.",

    "I'm an AI assistant for this mall. I can help you find stores, restaurants, movie times, "
    "kids' activities, or practical things like ATMs and directions. What are you looking for?",

    "Think of me as your personal mall guide — powered by AI. I know every shop, restaurant, "
    "and service here. Just tell me what you need and I'll point you in the right direction.",
]

_CRISIS_RESPONSES: list[str] = [
    "That doesn't sound like a good place to be right now, and I want you to know that matters. "
    "Please talk to someone you trust — or reach out to a crisis helpline for immediate support. "
    "You don't have to figure this out alone.",

    "I hear you, and I'm genuinely concerned. Please reach out to someone who can help — "
    "a friend, a family member, or a crisis support line. Your wellbeing is what matters most right now.",

    "That sounds really hard. Please don't go through this alone — reach out to a crisis helpline "
    "or someone close to you. I'm just a mall assistant, but I want you to get the real support you deserve.",
]

_RESPONSES: dict[str, list[str]] = {
    "howru": [
        "Doing well, thanks for asking! What are you looking for today?",
        "All good over here — ready to help. Shopping, food, a film, or something else?",
        "Great, and happy to assist! What can I point you to?",
    ],
    "thanks": [
        "Of course! Anything else I can help with?",
        "Happy to help — let me know if you need anything else.",
        "Anytime! Got more questions? Just ask.",
    ],
    # Context-aware thanks variants — used when scene has meaningful context.
    # These append a proactive nudge toward something the visitor hasn't explored yet.
    "thanks_with_dining_suggestion": [
        "Glad that helped! If you haven't eaten yet, there are some great options in the Food Court — just say the word.",
        "Happy to help! And if you're getting hungry, I can point you to a good spot to eat.",
        "Of course! Still time to grab a bite? Just ask and I'll suggest something quick.",
    ],
    "thanks_with_shopping_suggestion": [
        "Glad that helped! There's plenty of shopping here too — want me to point you to any stores?",
        "Happy to help! If you feel like browsing, I can suggest what's worth checking out.",
        "Of course! And if you want to explore the shops while you're here, just say the word.",
    ],
    "thanks_with_entertainment_suggestion": [
        "Glad that helped! Muvi Cinema is here too if you're up for a film — want to know what's showing?",
        "Happy to help! And if you're in the mood for entertainment, there's plenty on offer — just ask.",
        "Of course! There's a cinema here if you want to wind down with a film — let me know.",
    ],
    "thanks_with_generic_suggestion": [
        "Happy to help! Still exploring? I can suggest dining, shopping, movies, or any services you need.",
        "Of course! There's plenty more to discover here — just tell me what sounds good next.",
        "Anytime! If there's anything else on your list — food, shops, a film, services — just ask.",
    ],
    "farewell": [
        "Enjoy the rest of your visit! I'll be right here if you need directions, "
        "recommendations, or anything else — just send a message.",

        "Have a great time! If you change your mind or need help finding anything, "
        "I'm always here — no need to start over.",

        "See you around! And if something comes up while you're still in the mall — "
        "a store you can't find, a bite to eat, anything — just ask.",
    ],
    "emotional": [
        "That's fair — let's make this simpler. Tell me one thing you came here for, "
        "even something vague, and I'll take it from there. "
        "No need to figure it all out at once.",

        "Totally get it. Big malls can feel like a lot. "
        "If it helps, just pick one thing — grab a bite, browse a store, catch a film — "
        "and I'll guide you straight to it. What sounds appealing?",

        "I hear you. Let me help cut through the noise — what did you originally come here to do? "
        "Even a rough idea works and I'll narrow it down for you.",

        "Sorry to hear that. I'm here to make the visit easier, not harder. "
        "Tell me what's on your mind — whether it's finding something specific "
        "or just needing a quiet spot to regroup — and we'll sort it.",
    ],
    "emotional_mood_plan": [
        "Let's fix that. Quick mood-booster plan: grab something delicious from the Food Court, "
        "browse a few shops to unwind, and if you want to switch off completely — there's a cinema here too. "
        "Want me to build a proper feel-good route for you?",

        "Sounds like you need a good visit, not just directions. "
        "Here's a simple pick-me-up loop: a treat from the Food Court, "
        "a browse through the shops, and a film if you fancy it. "
        "Tell me what sounds most appealing and I'll plan it out.",

        "I've got you. Mall therapy: start with something tasty to eat, "
        "do a bit of browsing at the shops, and end with a film or a good coffee. "
        "What's the one thing that sounds most appealing right now?",
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
    }
