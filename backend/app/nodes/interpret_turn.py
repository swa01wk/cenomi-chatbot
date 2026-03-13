"""
Interpret Turn node — classifies user intent and message kind.

Uses a lightweight LLM call to classify the user's message into domain,
sub_intent, and message_kind.  Falls back to heuristic classification
when the LLM is unavailable.

CONTRACT
────────
  Purpose:  Analyze the normalized message in conversation context.
            Determine domain, sub_intent, and message_kind.
  Reads:    normalized_user_message, messages (history), scene
  Writes:   intent (InterpretedIntent)
  Failure:  Classification error → domain="general", message_kind="fresh_request"
  Routing:  Always → update_scene_memory
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, InterpretedIntent
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

_classifier_llm: ChatOpenAI | None = None

VALID_DOMAINS = {
    "dining", "shopping", "entertainment", "services", "navigation",
    "exploration", "general",
}

VALID_SUB_INTENTS = {
    "general_dining", "romantic_dining", "quick_bite", "family_dining",
    "cafe_recommendation", "dessert_recommendation",
    "general_shopping", "gift_recommendation", "fashion_shopping",
    "general_entertainment", "movie_showtime",
    "store_hours", "parking_info", "location_query", "service_info",
    "prayer_room", "event_schedule", "offer_details", "loyalty_info",
    "open_exploration", "activity_suggestion", "first_visit_guide",
    "general_inquiry",
}

CLASSIFICATION_PROMPT = """\
You are an intent classifier for a mall concierge chatbot. Classify the visitor's message.

Return ONLY valid JSON with these fields:
{
  "domain": one of: dining, shopping, entertainment, services, navigation, exploration, general
  "sub_intent": a specific sub-intent (see list below)
  "message_kind": one of: fresh_request, correction, refinement, topic_switch, followup
  "confidence": 0.0-1.0
}

DOMAINS AND SUB-INTENTS:
- exploration: open_exploration (vague "what can I do", "what's here"), activity_suggestion ("suggest something fun"), first_visit_guide ("first time here")
- dining: general_dining, romantic_dining, quick_bite, family_dining, cafe_recommendation, dessert_recommendation
- shopping: general_shopping, gift_recommendation, fashion_shopping
- entertainment: general_entertainment, movie_showtime
- services: store_hours, parking_info, service_info, prayer_room
- navigation: location_query
- general: general_inquiry (greetings, off-topic, unclear)

MESSAGE KIND RULES:
- fresh_request: new question or first message
- correction: user corrects previous answer ("no", "not that", "I meant")
- refinement: user refines or adds to current topic ("also", "what about", "any other")
- topic_switch: user changes topic ("instead", "forget that", "something else")
- followup: short response continuing current topic

CRITICAL: If the message is vague, open-ended, or asks broadly what to do/see/explore, classify as exploration/open_exploration. This is the MOST COMMON query type — do NOT default to general_inquiry for these.

Examples:
- "What can I do here?" → exploration/open_exploration
- "What's there to see?" → exploration/open_exploration
- "I'm bored" → exploration/activity_suggestion
- "First time at this mall" → exploration/first_visit_guide
- "Hi" / "Hello" → general/general_inquiry
- "Where can I eat?" → dining/general_dining
- "I want to buy a gift" → shopping/gift_recommendation
"""


def _get_classifier_llm() -> ChatOpenAI:
    global _classifier_llm
    if _classifier_llm is None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("BACKEND_OPENAI_API_KEY not set")
        _classifier_llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0.0,
            api_key=settings.openai_api_key,
            max_tokens=200,
        )
    return _classifier_llm


@traced_node("interpret_turn")
async def interpret_turn(state: ConciergeState) -> dict:
    msg = state.normalized_user_message
    history_len = len(state.messages)

    try:
        intent = await _llm_classify(msg, history_len, state)
    except Exception as exc:
        logger.warning("LLM classifier failed, using heuristic: %s", exc)
        intent = _heuristic_classify(msg, history_len, state)

    return {
        "intent": intent,
        "_trace_summary": f"Intent: {intent.domain}/{intent.sub_intent} ({intent.message_kind})",
    }


async def _llm_classify(
    msg: str, history_len: int, state: ConciergeState,
) -> InterpretedIntent:
    llm = _get_classifier_llm()

    context_parts = [f"Current message: {msg}"]

    if history_len > 1:
        recent = state.messages[-4:]
        history_text = "\n".join(
            f"  {m.role}: {m.content[:120]}" for m in recent
        )
        context_parts.append(f"Recent conversation:\n{history_text}")

    if state.scene.active_topic:
        context_parts.append(f"Active topic: {state.scene.active_topic}")
    if state.scene.companions:
        context_parts.append(f"Companions: {', '.join(state.scene.companions)}")

    user_text = "\n".join(context_parts)

    response = await llm.ainvoke([
        SystemMessage(content=CLASSIFICATION_PROMPT),
        HumanMessage(content=user_text),
    ])

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

    parsed = json.loads(raw)

    domain = parsed.get("domain", "general")
    sub_intent = parsed.get("sub_intent", "general_inquiry")
    message_kind = parsed.get("message_kind", "fresh_request")
    confidence = float(parsed.get("confidence", 0.8))

    if domain not in VALID_DOMAINS:
        domain = "general"
    if sub_intent not in VALID_SUB_INTENTS:
        sub_intent = "general_inquiry"

    return InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=confidence,
    )


# ── Heuristic fallback ─────────────────────────────────────────────

_DOMAIN_HINTS: dict[str, str] = {
    "eat": "dining", "food": "dining", "restaurant": "dining",
    "cafe": "dining", "hungry": "dining", "lunch": "dining",
    "dinner": "dining", "breakfast": "dining", "coffee": "dining",
    "snack": "dining", "dessert": "dining",
    "shop": "shopping", "buy": "shopping", "store": "shopping",
    "gift": "shopping", "fashion": "shopping", "clothes": "shopping",
    "jewelry": "shopping", "perfume": "shopping",
    "movie": "entertainment", "cinema": "entertainment",
    "film": "entertainment", "fun": "entertainment",
    "play": "entertainment", "kids zone": "entertainment",
    "park": "services", "pray": "services", "atm": "services",
    "wifi": "services", "bathroom": "services", "restroom": "services",
    "lounge": "services",
    "where": "navigation", "find": "navigation",
    "locate": "navigation", "directions": "navigation", "floor": "navigation",
}

_EXPLORATION_CUES = (
    "what can i do", "what's here", "what is here", "what to do",
    "what should i do", "suggest", "recommend", "bored", "explore",
    "show me around", "what do you have", "first time",
    "what's available", "what's around", "anything interesting",
    "what are my options", "help me decide", "tell me about",
    "what's good", "what's popular", "what's trending",
)


def _heuristic_classify(
    msg: str, history_len: int, state: ConciergeState,
) -> InterpretedIntent:
    lower = msg.lower()

    message_kind = _detect_message_kind(lower, history_len, state)

    if any(cue in lower for cue in _EXPLORATION_CUES):
        return InterpretedIntent(
            domain="exploration",
            sub_intent="open_exploration",
            message_kind=message_kind,
            confidence=0.7,
        )

    domain = "general"
    for keyword, d in _DOMAIN_HINTS.items():
        if keyword in lower:
            domain = d
            break

    sub_intent = _extract_sub_intent(lower, domain)

    return InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=0.6,
    )


def _detect_message_kind(msg: str, history_len: int, state: ConciergeState) -> str:
    if history_len <= 1:
        return "fresh_request"

    correction_cues = ("no ", "not that", "i mean", "actually", "wrong", "i said")
    if any(cue in msg for cue in correction_cues):
        return "correction"

    refinement_cues = ("also", "and also", "what about", "how about", "any other")
    if any(cue in msg for cue in refinement_cues):
        return "refinement"

    topic_switch_cues = ("instead", "forget that", "something else", "change")
    if any(cue in msg for cue in topic_switch_cues):
        return "topic_switch"

    if len(msg.split()) <= 5 and state.scene.active_topic:
        return "followup"

    return "fresh_request"


def _extract_sub_intent(msg: str, domain: str) -> str:
    if domain == "dining":
        if any(w in msg for w in ("romantic", "date", "intimate")):
            return "romantic_dining"
        if any(w in msg for w in ("quick", "fast", "grab", "before movie")):
            return "quick_bite"
        if any(w in msg for w in ("kid", "family", "children")):
            return "family_dining"
        if any(w in msg for w in ("coffee", "cafe")):
            return "cafe_recommendation"
        if any(w in msg for w in ("dessert", "sweet")):
            return "dessert_recommendation"
        return "general_dining"

    if domain == "shopping":
        if "gift" in msg:
            return "gift_recommendation"
        if any(w in msg for w in ("fashion", "clothes", "wear")):
            return "fashion_shopping"
        return "general_shopping"

    if domain == "entertainment":
        if any(w in msg for w in ("movie", "cinema", "film", "showtime")):
            return "movie_showtime"
        return "general_entertainment"

    if domain == "services":
        if any(w in msg for w in ("pray", "prayer")):
            return "prayer_room"
        if any(w in msg for w in ("park", "valet")):
            return "parking_info"
        return "service_info"

    if domain == "navigation":
        return "location_query"

    return "general_inquiry"
