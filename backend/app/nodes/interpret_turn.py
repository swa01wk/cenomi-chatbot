"""
Interpret Turn node — classifies user intent and message kind.

Uses a lightweight LLM call to classify the user's message into domain,
sub_intent, and message_kind.  Falls back to heuristic classification
when the LLM is unavailable.

CONTRACT
────────
  Purpose:  Analyze the normalized message in conversation context.
            Determine domain, sub_intent, and message_kind.
            Detect whether the message is a refinement of the current topic.
            Detect short follow-up refinements like "affordable", "price",
            "for my son" and flag them via is_refinement_of_current_topic.
  Reads:    normalized_user_message, messages (history), scene,
            continuity_anchor
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
  "confidence": 0.0-1.0,
  "is_refinement_of_current_topic": true/false,
  "detected_refinement_cues": ["cue1", "cue2"]
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

REFINEMENT DETECTION (critical for topic continuity):
- If the user sends a short adjective/noun follow-up (e.g. "affordable", "for my son",
  "price", "something quieter", "kid-friendly") and there is an active topic, set
  is_refinement_of_current_topic=true and list the cue words in detected_refinement_cues.
- Price questions inside an active shopping/dining thread are refinements, not topic switches.
- Child/family follow-ups inside an active thread are refinements with audience bias.
- Only classify as topic_switch when there is an *explicit* domain change
  (e.g. "forget jackets, where can I eat?" or "what movies are showing?").

CRITICAL: If the message is vague, open-ended, or asks broadly what to do/see/explore, classify as exploration/open_exploration. This is the MOST COMMON query type — do NOT default to general_inquiry for these.

Examples:
- "What can I do here?" → exploration/open_exploration
- "What's there to see?" → exploration/open_exploration
- "I'm bored" → exploration/activity_suggestion
- "First time at this mall" → exploration/first_visit_guide
- "Hi" / "Hello" → general/general_inquiry
- "Where can I eat?" → dining/general_dining
- "I want to buy a gift" → shopping/gift_recommendation
- "affordable" (active_topic=shopping) → refinement, is_refinement=true, cues=["affordable"]
- "for my son" (active_topic=shopping) → refinement, is_refinement=true, cues=["for my son"]
- "how much?" (active_topic=shopping) → followup, is_refinement=true, cues=["price"]
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
            max_tokens=300,
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

    # Post-classification refinement detection for short follow-ups:
    # If the continuity anchor is strong and the message is very short,
    # override a spurious topic_switch to refinement/followup.
    anchor = state.continuity_anchor
    if anchor.is_strong and intent.message_kind == "topic_switch":
        word_count = len(msg.split())
        if word_count <= 4 and not _has_explicit_switch_cue(msg):
            intent = intent.model_copy(update={
                "message_kind": "refinement",
                "is_refinement_of_current_topic": True,
            })

    return {
        "intent": intent,
        "_trace_summary": (
            f"Intent: {intent.domain}/{intent.sub_intent} "
            f"({intent.message_kind}) "
            f"refine={intent.is_refinement_of_current_topic}"
        ),
    }


def _has_explicit_switch_cue(msg: str) -> bool:
    """Return True only when the message contains an unambiguous topic switch."""
    lower = msg.lower()
    explicit_cues = (
        "instead", "forget that", "something else", "change topic",
        "never mind", "new question",
    )
    return any(cue in lower for cue in explicit_cues)


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

    anchor = state.continuity_anchor
    if anchor.is_strong:
        context_parts.append(
            f"Continuity anchor: domain={anchor.domain}, "
            f"topic={anchor.topic}, subtopic={anchor.subtopic}, "
            f"audience={anchor.audience}, budget={anchor.budget}"
        )

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
    is_refinement = bool(parsed.get("is_refinement_of_current_topic", False))
    refinement_cues = parsed.get("detected_refinement_cues", [])

    if domain not in VALID_DOMAINS:
        domain = "general"
    if sub_intent not in VALID_SUB_INTENTS:
        sub_intent = "general_inquiry"

    return InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=confidence,
        is_refinement_of_current_topic=is_refinement,
        detected_refinement_cues=refinement_cues,
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

_REFINEMENT_ADJECTIVES = {
    "affordable", "cheap", "expensive", "luxury", "premium", "budget",
    "quiet", "quieter", "lively", "romantic", "casual", "cozy",
    "kid-friendly", "family-friendly", "halal",
    "quick", "fast", "nearby", "closer",
}

_REFINEMENT_AUDIENCE_CUES = {
    "for my son", "for my daughter", "for kids", "for children",
    "for my wife", "for my husband", "for my girlfriend",
    "for a couple", "for family", "with kids", "with children",
    "with my son", "with my daughter",
}

_REFINEMENT_PRICE_CUES = {
    "price", "how much", "cost", "pricing", "expensive",
    "what does it cost", "budget", "affordable",
}


def _heuristic_classify(
    msg: str, history_len: int, state: ConciergeState,
) -> InterpretedIntent:
    lower = msg.lower()

    message_kind = _detect_message_kind(lower, history_len, state)
    is_refinement, cues = _detect_refinement_signals(lower, state)

    if is_refinement and message_kind in ("fresh_request", "topic_switch"):
        message_kind = "refinement"

    if any(cue in lower for cue in _EXPLORATION_CUES):
        return InterpretedIntent(
            domain="exploration",
            sub_intent="open_exploration",
            message_kind=message_kind,
            confidence=0.7,
            is_refinement_of_current_topic=is_refinement,
            detected_refinement_cues=cues,
        )

    domain = "general"
    for keyword, d in _DOMAIN_HINTS.items():
        if keyword in lower:
            domain = d
            break

    # When there is a strong continuity anchor and no explicit domain keyword,
    # inherit the anchored domain rather than defaulting to "general".
    anchor = state.continuity_anchor
    if domain == "general" and anchor.is_strong and is_refinement:
        domain = anchor.domain

    sub_intent = _extract_sub_intent(lower, domain)

    return InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=0.6,
        is_refinement_of_current_topic=is_refinement,
        detected_refinement_cues=cues,
    )


def _detect_refinement_signals(
    msg: str, state: ConciergeState,
) -> tuple[bool, list[str]]:
    """Detect short adjective/noun follow-ups that refine the active topic."""
    anchor = state.continuity_anchor
    if not anchor.is_strong:
        return False, []

    cues: list[str] = []

    for adj in _REFINEMENT_ADJECTIVES:
        if adj in msg:
            cues.append(adj)

    for phrase in _REFINEMENT_AUDIENCE_CUES:
        if phrase in msg:
            cues.append(phrase)

    for phrase in _REFINEMENT_PRICE_CUES:
        if phrase in msg:
            cues.append(phrase)

    if cues:
        return True, cues

    word_count = len(msg.split())
    if word_count <= 3 and state.scene.active_topic:
        return True, [msg.strip()]

    return False, []


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
