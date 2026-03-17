"""
Interpret Turn node — classifies user intent and message kind.

Uses a lightweight rule-based + LLM hybrid classifier.  Short queries
(< 3 words) are resolved from a static lookup table with zero LLM cost.
Longer queries go through keyword pattern rules first; the LLM is only
invoked when rule confidence is too low.

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
from intent.query_classifier import (
    RULE_CONFIDENCE_THRESHOLD,
    classify_query,
    map_to_graph_intent,
)

logger = logging.getLogger(__name__)

_classifier_llm: ChatOpenAI | None = None

VALID_DOMAINS = {
    "dining", "shopping", "entertainment", "services", "navigation",
    "exploration", "mall_info", "general",
}

VALID_SUB_INTENTS = {
    "general_dining", "romantic_dining", "quick_bite", "family_dining",
    "cafe_recommendation", "dessert_recommendation",
    "general_shopping", "gift_recommendation", "fashion_shopping",
    "perfume_shopping", "jewelry_shopping", "accessories_shopping",
    "general_entertainment", "movie_showtime",
    "store_hours", "parking_info", "location_query", "service_info",
    "prayer_room", "event_schedule", "offer_details", "loyalty_info",
    "open_exploration", "activity_suggestion", "first_visit_guide",
    "overview", "facilities_summary", "opening_hours",
    "family_friendliness", "what_is_available",
    "general_inquiry",
}

CLASSIFICATION_PROMPT = """\
You are an intent classifier for a mall concierge chatbot. Classify the visitor's message.

Return ONLY valid JSON with these fields:
{
  "domain": one of: dining, shopping, entertainment, services, navigation, exploration, mall_info, general
  "sub_intent": a specific sub-intent (see list below)
  "message_kind": one of: fresh_request, correction, refinement, topic_switch, followup
  "confidence": 0.0-1.0
}

DOMAINS AND SUB-INTENTS:
- mall_info: overview ("tell me about the mall", "what is this place"), facilities_summary ("what facilities"), opening_hours ("mall opening hours"), family_friendliness ("is this mall family friendly", "can I come with kids"), what_is_available ("what shops are in the mall")
- exploration: open_exploration (vague "what can I do", "what's here"), activity_suggestion ("suggest something fun"), first_visit_guide ("first time here")
- dining: general_dining, romantic_dining, quick_bite, family_dining, cafe_recommendation, dessert_recommendation
- shopping: general_shopping, gift_recommendation, fashion_shopping, perfume_shopping, jewelry_shopping, accessories_shopping, offer_details
- entertainment: general_entertainment, movie_showtime
- services: store_hours, parking_info, service_info, prayer_room, event_schedule, loyalty_info
- navigation: location_query
- general: general_inquiry (greetings, off-topic, unclear)

IMPORTANT — offer_details:
- ANY question about offers, deals, discounts, sales, or promotions → shopping/offer_details
- "What offers are there?" → shopping/offer_details
- "What offers does Zara have?" → shopping/offer_details
- "Any sales going on?" → shopping/offer_details
- "Show me all the shopping offers" → shopping/offer_details
- "What discounts are available?" → shopping/offer_details

IMPORTANT — mall_info vs other domains:
- Broad questions ABOUT the mall itself (overview, what it has, hours, family suitability) → mall_info
- Questions about SPECIFIC activities, categories, or recommendations → exploration, dining, shopping, etc.
- "What does this mall have?" → mall_info/overview (NOT shopping)
- "Is this family friendly?" → mall_info/family_friendliness (NOT dining/family_dining)
- "What are the opening hours?" → mall_info/opening_hours (NOT services/store_hours)

CONTEXT RESOLUTION (CRITICAL):
- If a "Target person" or "Companions" context is provided, resolve short/vague queries
  IN THAT CONTEXT. Examples:
  - "food?" when target_person=child → dining/family_dining (NOT general_dining)
  - "perfume?" when target_person=girlfriend → shopping/perfume_shopping (gift context)
  - "dessert?" when companions include kids → dining/dessert_recommendation (kid-friendly)
  - "what to do?" when visit_type=family → exploration/activity_suggestion (family activities)
- Short follow-up queries (1-2 words) should ALWAYS be interpreted in the context of
  the previous conversation, not as standalone queries.

VISIT PLAN RESOLUTION (CRITICAL):
- If a "Visit plan" is provided (e.g. ["shopping", "coffee", "dessert"]) and "Completed steps"
  are listed, resolve the current query as the NEXT step in the plan.
  Example: plan=["shopping","coffee","dessert"], completed=["shopping","coffee"], query="dessert?" 
  → interpret as dessert_recommendation continuing the visit plan.
- If the query is "after that?", "what next?", "and then?", "then what?", "next?" — these are ALWAYS
  followup queries continuing the visit sequence from the last discussed topic.
  Use "Active topic" and "Completed steps" to determine what domain comes next.
- "after that?" when active_topic=dining → cafe_recommendation or dessert_recommendation
- "after that?" when active_topic=shopping → cafe_recommendation (coffee break after shopping)
- "after that?" when active_topic=entertainment → dining (meal after movie)

CONSTRAINT INHERITANCE (IMPORTANT):
- If "Visit constraints" contains "quick" → treat dining queries as quick_bite.
- If "Visit constraints" contains "light" → treat dining queries as quick_bite.
- If "Visit constraints" contains "affordable" → treat as budget-conscious shopping/dining.
- Constraints carry forward across the whole conversation.

MESSAGE KIND RULES:
- fresh_request: new question or first message
- correction: user corrects previous answer ("no", "not that", "I meant")
- refinement: user refines or adds to current topic ("also", "what about", "any other", "something affordable", "something light")
- topic_switch: user changes topic ("instead", "forget that", "something else")
- followup: short response continuing current topic OR sequential query ("after that?", "what next?", "and then?", "coffee?", "dessert?")

Examples:
- "Tell me about the mall" → mall_info/overview
- "What is this mall like?" → mall_info/overview
- "What can I find here?" → mall_info/overview
- "Is this mall family friendly?" → mall_info/family_friendliness
- "Can I come here with kids?" → mall_info/family_friendliness
- "What time does the mall open?" → mall_info/opening_hours
- "What are the opening hours?" → mall_info/opening_hours
- "What can I do here?" → exploration/open_exploration
- "I'm bored" → exploration/activity_suggestion
- "First time at this mall" → exploration/first_visit_guide
- "Hi" / "Hello" → general/general_inquiry
- "Where can I eat?" → dining/general_dining
- "I want to buy a gift" → shopping/gift_recommendation
- "Show me all the shopping offers" → shopping/offer_details
- "What offers are going on in Zara?" → shopping/offer_details
- "Any deals or discounts?" → shopping/offer_details
- "What perfume stores do you have?" → shopping/perfume_shopping
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
    has_history = bool(state.last_intent)
    history_len = 2 if has_history else 1

    scene_ctx = {
        "active_topic": state.scene.active_topic,
        "companions": state.scene.companions,
        "target_person": state.scene.target_person,
        "audience": state.scene.audience,
        "occasion": state.scene.occasion,
        "goal": state.scene.goal,
        "visit_type": state.scene.visit_type,
        "previous_need": state.scene.previous_need,
    }

    # ── 1. Rule-based fast path ───────────────────────────────────────
    rule_result = classify_query(
        msg, history_len=history_len, scene_context=scene_ctx,
    )

    if rule_result.confidence >= RULE_CONFIDENCE_THRESHOLD:
        domain, sub_intent = map_to_graph_intent(rule_result)
        message_kind = _detect_message_kind(msg.lower(), history_len, state)  # noqa: E501
        intent = InterpretedIntent(
            domain=domain,
            sub_intent=sub_intent,
            message_kind=message_kind,
            confidence=rule_result.confidence,
            raw_signals={
                "classifier_source": rule_result.source,
                "intent_class": rule_result.intent_class.value,
                "sub_tags": rule_result.sub_tags,
            },
        )
        logger.info(
            "Rule classifier matched (%s, conf=%.2f): %s/%s",
            rule_result.source, rule_result.confidence, domain, sub_intent,
        )
        return {
            "intent": intent,
            "_trace_summary": (
                f"Intent[{rule_result.source}]: {domain}/{sub_intent} "
                f"({message_kind}) conf={rule_result.confidence:.2f}"
            ),
        }

    # ── 2. LLM fallback ──────────────────────────────────────────────
    try:
        intent = await _llm_classify(msg, history_len, state)
        intent.raw_signals["classifier_source"] = "llm"
    except Exception as exc:
        logger.warning("LLM classifier failed, using heuristic: %s", exc)
        intent = _heuristic_classify(msg, history_len, state)
        intent.raw_signals["classifier_source"] = "heuristic_fallback"

    return {
        "intent": intent,
        "_trace_summary": (
            f"Intent[{intent.raw_signals.get('classifier_source', 'llm')}]: "
            f"{intent.domain}/{intent.sub_intent} ({intent.message_kind})"
        ),
    }


async def _llm_classify(
    msg: str, history_len: int, state: ConciergeState,
) -> InterpretedIntent:
    llm = _get_classifier_llm()

    context_parts = [f"Current message: {msg}"]

    if state.last_intent:
        context_parts.append(f"Previous intent domain: {state.last_intent}")
    if state.conversation_mode:
        context_parts.append(f"Conversation mode: {state.conversation_mode}")

    if state.scene.active_topic:
        context_parts.append(f"Active topic: {state.scene.active_topic}")
    if state.scene.companions:
        context_parts.append(f"Companions: {', '.join(state.scene.companions)}")
    if state.scene.target_person:
        context_parts.append(f"Target person (who the conversation is about): {state.scene.target_person}")
    if state.scene.occasion:
        context_parts.append(f"Occasion: {state.scene.occasion}")
    if state.scene.current_need:
        context_parts.append(f"Previous question: {state.scene.current_need}")
    if state.scene.previous_need:
        context_parts.append(f"Earlier question: {state.scene.previous_need}")
    if state.scene.audience:
        context_parts.append(f"Audience: {', '.join(state.scene.audience)}")
    if state.scene.visit_type:
        context_parts.append(f"Visit type: {state.scene.visit_type}")
    if state.scene.goal:
        context_parts.append(f"Current goal: {state.scene.goal}")
    if state.scene.active_shortlist:
        context_parts.append(f"Previous suggestions: {', '.join(state.scene.active_shortlist)}")

    # Visit plan context — critical for sequential follow-ups
    if state.scene.visit_plan:
        context_parts.append(
            f"Visit plan (planned sequence): {' → '.join(state.scene.visit_plan)}"
        )
    if state.scene.completed_steps:
        context_parts.append(
            f"Completed steps (already discussed): {' → '.join(state.scene.completed_steps)}"
        )
    if state.scene.current_plan_step:
        context_parts.append(
            f"Current plan step (being addressed): {state.scene.current_plan_step}"
        )
    if state.scene.visit_constraints:
        context_parts.append(
            f"Visit constraints (user preferences): {', '.join(state.scene.visit_constraints)}"
        )
    if state.scene.topic_history:
        context_parts.append(
            f"Topic journey so far: {' → '.join(state.scene.topic_history[-5:])}"
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
    "offer": "shopping", "offers": "shopping", "deal": "shopping",
    "deals": "shopping", "discount": "shopping", "discounts": "shopping",
    "sale": "shopping", "sales": "shopping", "promotion": "shopping",
    "promo": "shopping",
    "movie": "entertainment", "cinema": "entertainment",
    "film": "entertainment", "fun": "entertainment",
    "play": "entertainment", "kids zone": "entertainment",
    "park": "services", "pray": "services", "atm": "services",
    "wifi": "services", "bathroom": "services", "restroom": "services",
    "lounge": "services",
    "where": "navigation", "find": "navigation",
    "locate": "navigation", "directions": "navigation", "floor": "navigation",
}

_MALL_INFO_CUES = (
    "about the mall", "about this mall", "about this place",
    "mall like", "overview of the mall", "overview of this mall",
    "what can i find here", "what does this mall",
    "what does the mall", "what is this mall",
    "family friendly", "family-friendly", "good for families",
    "good for kids", "come here with kids", "come with kids",
    "bring kids", "bring my kids",
    "mall open", "mall hours", "opening hours",
    "mall facilities", "facilities at the mall",
    "what is available at the mall", "what's available at the mall",
)

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

    # Sequential query — treat as follow-up in active context
    sequential_cues = (
        "after that", "what next", "and then", "then what",
        "what else", "after this",
    )
    if any(cue in lower for cue in sequential_cues):
        prev_domain = state.scene.active_topic or state.last_intent or "dining"
        # Guess next domain from previous
        next_domain_map = {
            "shopping": "dining",
            "entertainment": "dining",
            "dining": "dining",
        }
        next_domain = next_domain_map.get(prev_domain, "dining")
        return InterpretedIntent(
            domain=next_domain,
            sub_intent="general_dining",
            message_kind="followup",
            confidence=0.6,
        )

    message_kind = _detect_message_kind(lower, history_len, state)

    if any(cue in lower for cue in _MALL_INFO_CUES):
        sub_intent = _extract_sub_intent(lower, "mall_info", state)
        return InterpretedIntent(
            domain="mall_info",
            sub_intent=sub_intent,
            message_kind=message_kind,
            confidence=0.7,
        )

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

    sub_intent = _extract_sub_intent(lower, domain, state)

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

    # Sequential / continuation queries — always follow-up
    sequential_cues = (
        "after that", "what next", "and then", "then what", "what else",
        "and after", "after this", "next?", "what about after",
    )
    if any(cue in msg for cue in sequential_cues):
        return "followup"

    refinement_cues = (
        "also", "and also", "what about", "how about", "any other",
        "something affordable", "something cheap", "something lighter",
        "something light", "more options", "other options",
    )
    if any(cue in msg for cue in refinement_cues):
        return "refinement"

    topic_switch_cues = ("instead", "forget that", "something else", "change")
    if any(cue in msg for cue in topic_switch_cues):
        return "topic_switch"

    # Short query in an active conversation context = follow-up
    if len(msg.split()) <= 4 and (
        state.scene.active_topic
        or state.scene.visit_plan
        or state.scene.companions
        or state.scene.target_person
    ):
        return "followup"

    return "fresh_request"


def _extract_sub_intent(
    msg: str, domain: str, state: "ConciergeState | None" = None,
) -> str:
    if domain == "mall_info":
        if any(w in msg for w in ("family", "kids", "children", "child")):
            return "family_friendliness"
        if any(w in msg for w in ("hours", "open", "time", "close")):
            return "opening_hours"
        if any(w in msg for w in ("facilities", "amenities")):
            return "facilities_summary"
        if any(w in msg for w in ("available", "shops", "stores", "restaurants")):
            return "what_is_available"
        return "overview"

    if domain == "dining":
        if any(w in msg for w in ("romantic", "date", "intimate")):
            return "romantic_dining"
        # Constraint-aware: "quick" or "light" → quick_bite
        has_quick_constraint = state and (
            "quick" in getattr(state.scene, "visit_constraints", [])
            or "light" in getattr(state.scene, "visit_constraints", [])
        )
        if any(w in msg for w in ("quick", "fast", "grab", "before movie", "light")) or has_quick_constraint:
            return "quick_bite"
        if any(w in msg for w in ("kid", "family", "children")):
            return "family_dining"
        if any(w in msg for w in ("coffee", "cafe")):
            return "cafe_recommendation"
        if any(w in msg for w in ("dessert", "sweet")):
            return "dessert_recommendation"
        return "general_dining"

    if domain == "shopping":
        if any(w in msg for w in ("offer", "offers", "deal", "deals", "discount",
                                   "discounts", "sale", "sales", "promotion", "promo")):
            return "offer_details"
        if "gift" in msg:
            return "gift_recommendation"
        if any(w in msg for w in ("fashion", "clothes", "wear")):
            return "fashion_shopping"
        if any(w in msg for w in ("perfume", "fragrance", "oud")):
            return "perfume_shopping"
        if any(w in msg for w in ("jewelry", "jewellery", "diamond", "gold", "silver")):
            return "jewelry_shopping"
        if any(w in msg for w in ("accessories", "bags", "handbag")):
            return "accessories_shopping"
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
