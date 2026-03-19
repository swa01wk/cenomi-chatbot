"""
Interpret Turn node — classifies user intent and message kind.

Uses a lightweight rule-based + LLM hybrid classifier.  Short queries
(< 3 words) are resolved from a static lookup table with zero LLM cost.
Longer queries go through keyword pattern rules first; the LLM is only
invoked when rule confidence is too low.

CONTRACT
────────
  Purpose:  Analyze the normalized message in conversation context.
            Determine domain, sub_intent, message_kind, and flow routing hints.
  Reads:    normalized_user_message, messages (history), scene
  Writes:   intent (InterpretedIntent) including flow_type_candidate,
            fact_scope_candidate, fact_entity_type_candidate
  Failure:  Classification error → domain="general", message_kind="fresh_request"
  Routing:  → route_flow (new) — which branches to concierge or factual path
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, DebugEnrichment, InterpretedIntent
from app.nodes._tracing import traced_node
from intent.query_classifier import (
    RULE_CONFIDENCE_THRESHOLD,
    classify_query,
    is_likely_unsupported,
    map_to_graph_intent,
    maybe_correct_brand,
    normalize_query_with_pattern,
)

logger = logging.getLogger(__name__)

_classifier_llm: ChatOpenAI | None = None

VALID_DOMAINS = {
    "dining", "shopping", "entertainment", "services", "navigation",
    "exploration", "mall_info", "general",
    "cross_mall",
}

# ── Flow routing hint tables ────────────────────────────────────────────────
# Sub-intents that strongly signal factual flow
_FACTUAL_SUB_INTENTS: frozenset[str] = frozenset({
    "movie_showtime",
    "opening_hours",
    "store_hours",
    "location_query",
    "service_info",
    "prayer_room",
    "parking_info",
    "cross_mall_search",
    "brand_availability",    # "do you have H&M?" / "is Nike here?" → exact presence check
    "overview",              # "tell me about the mall" → factual mall data
    "facilities_summary",    # "what facilities does this mall have?"
    "family_friendliness",   # "is this mall family friendly?"
    "what_is_available",     # "what does this mall have?"
})

# Keywords that signal a direct factual lookup regardless of domain
_FACTUAL_KEYWORD_SIGNALS: tuple[str, ...] = (
    "what movies", "which movies", "movies do we", "movies can i",
    "now showing", "what's playing", "what is playing",
    "all movies", "movie list", "show times", "showtimes",
    "where is", "where's the", "where are the",
    "what time do you", "when do you open", "when do you close",
    "opening hours", "closing time", "what are your hours",
    "do you have", "is there a", "is there an", "do you carry",
    "is starbucks", "is zara", "is nike", "is h&m",
    "where is the atm", "atm location", "prayer room", "restroom",
    "how do i get to", "directions to",
)

# Keywords that strongly indicate concierge / planning flow
_CONCIERGE_KEYWORD_SIGNALS: tuple[str, ...] = (
    "suggest", "recommend", "what can we do", "what should we",
    "something quick", "something fun", "something for",
    "before the movie", "after the movie",
    "gift for", "present for",
    "date plan", "date night",
    "family plan", "with my kids", "with my child", "with my daughter", "with my son",
    "with my girlfriend", "with my boyfriend", "with my wife", "with my husband",
)


def _detect_flow_type_candidate(
    msg: str,
    domain: str,
    sub_intent: str,
    scene_context: dict,
) -> tuple[str, str, str]:
    """
    Emit a (flow_type_candidate, fact_scope_candidate, fact_entity_type_candidate)
    hint for the route_flow node.

    Returns strings — the route_flow node makes the final decision.
    """
    lower = msg.lower()

    # Cross-mall is always factual
    if domain == "cross_mall" or sub_intent == "cross_mall_search":
        return "factual", "cross_mall_availability", "brand"

    # Concierge signals override if strong planning language is present
    has_concierge_signal = any(cue in lower for cue in _CONCIERGE_KEYWORD_SIGNALS)
    # Also treat companion/occasion/visit context as concierge signal
    has_scene_context = bool(
        scene_context.get("companions")
        or scene_context.get("occasion")
        or scene_context.get("visit_type")
        or scene_context.get("goal")
    )

    # Factual sub-intent check
    if sub_intent in _FACTUAL_SUB_INTENTS:
        # Edge case: "something quick before the movie" is concierge even if
        # movie_showtime appears as sub-intent — let concierge signals win
        if has_concierge_signal:
            return "concierge", "", ""
        # Map sub_intent → fact_scope
        _SUB_INTENT_SCOPE: dict[str, tuple[str, str]] = {
            "movie_showtime": ("movie_schedule", "movie"),
            "opening_hours": ("mall_fact", "mall"),
            "store_hours": ("store_lookup", "store"),
            "location_query": ("route_hint", "entity"),
            "service_info": ("service_lookup", "service"),
            "prayer_room": ("service_lookup", "facility"),
            "parking_info": ("service_lookup", "parking"),
            "cross_mall_search": ("cross_mall_availability", "brand"),
            "brand_availability": ("brand_availability", "store"),
            "overview": ("mall_fact", "mall"),
            "facilities_summary": ("mall_fact", "mall"),
            "family_friendliness": ("mall_fact", "mall"),
            "what_is_available": ("mall_fact", "mall"),
        }
        scope_info = _SUB_INTENT_SCOPE.get(sub_intent, ("", ""))
        return "factual", scope_info[0], scope_info[1]

    # Explicit factual keyword signals
    has_factual_signal = any(cue in lower for cue in _FACTUAL_KEYWORD_SIGNALS)
    if has_factual_signal and not has_concierge_signal:
        # Try to resolve scope from keywords
        if any(k in lower for k in ("movie", "film", "cinema", "showtime", "playing")):
            return "factual", "movie_schedule", "movie"
        if any(k in lower for k in ("hours", "open", "close", "timing")):
            return "factual", "mall_fact", "mall"
        if any(k in lower for k in ("where is", "where's", "location", "floor", "directions")):
            return "factual", "route_hint", "entity"
        if any(k in lower for k in ("do you have", "is there", "do you carry", "is starbucks", "is zara")):
            return "factual", "brand_availability", "store"
        if any(k in lower for k in ("atm", "prayer", "restroom", "parking", "stroller", "wheelchair")):
            return "factual", "service_lookup", "facility"
        return "factual", "store_lookup", "entity"

    # Exploration with strong scene context → concierge
    if has_scene_context or has_concierge_signal:
        return "concierge", "", ""

    # Default — route_flow will make the final call
    return "", "", ""


# ── Hybrid intent extraction ────────────────────────────────────────────────

# Domain/sub-intent → canonical primary intent label
_PRIMARY_INTENT_MAP: dict[str, str] = {
    "entertainment/movie_showtime": "movie_lookup",
    "entertainment/general_entertainment": "entertainment_discovery",
    "shopping/gift_recommendation": "gift_shopping",
    "shopping/general_shopping": "shopping_recommendation",
    "shopping/fashion_shopping": "shopping_recommendation",
    "shopping/perfume_shopping": "shopping_recommendation",
    "shopping/jewelry_shopping": "shopping_recommendation",
    "shopping/accessories_shopping": "shopping_recommendation",
    "shopping/offer_details": "offer_lookup",
    "shopping/brand_availability": "brand_availability",
    "dining/general_dining": "dining_recommendation",
    "dining/family_dining": "dining_recommendation",
    "dining/romantic_dining": "dining_recommendation",
    "dining/quick_bite": "dining_recommendation",
    "dining/cafe_recommendation": "dining_recommendation",
    "dining/dessert_recommendation": "dining_recommendation",
    "navigation/location_query": "location_lookup",
    "services/service_info": "service_lookup",
    "services/prayer_room": "service_lookup",
    "services/parking_info": "service_lookup",
    "services/store_hours": "mall_fact_lookup",
    "mall_info/opening_hours": "mall_fact_lookup",
    "mall_info/overview": "mall_overview",
    "mall_info/facilities_summary": "mall_overview",
    "mall_info/family_friendliness": "mall_overview",
    "mall_info/what_is_available": "mall_overview",
    "exploration/open_exploration": "discovery",
    "exploration/activity_suggestion": "discovery",
    "exploration/first_visit_guide": "discovery",
    "cross_mall/cross_mall_search": "cross_mall_lookup",
}

# Keyword patterns → secondary intent labels
_SECONDARY_INTENT_SIGNALS: list[tuple[tuple[str, ...], str]] = [
    (("with the kid", "with my kid", "with kids", "with my kids",
      "with child", "with my child", "with the children", "for the kid",
      "any movies with", "movies with kid", "movies for kids",
      "movies for children"), "family_filter"),
    (("before the movie", "before movie"), "before_movie_constraint"),
    (("after the movie", "after movie"), "after_movie_constraint"),
    (("and coffee", "coffee after", "coffee before"), "add_coffee_step"),
    (("and dinner", "dinner after", "dinner before",
      "and food", "and eat", "and then eat", "grab food",
      "grab a bite", "grab dinner", "and lunch",
      "food and", "movies and food", "movies and eat"), "add_dining_step"),
    (("near cinema", "near the cinema", "closer to cinema",
      "close to cinema", "next to cinema"), "proximity_filter"),
    (("not expensive", "not too expensive", "affordable",
      "budget", "cheaper"), "budget_filter"),
    (("gift for", "present for", "buying for", "shopping for"), "gift_for"),
    (("romantic", "for my girlfriend", "for my boyfriend",
      "for wife", "for husband"), "romantic_filter"),
    (("quick", "something quick", "fast", "hurry"), "quick_filter"),
]

# Keyword patterns → semantic modifier tags
_MODIFIER_SIGNALS: list[tuple[tuple[str, ...], list[str]]] = [
    (("with the kid", "with my kid", "with kids", "with my kids",
      "with child", "with my child", "with the children",
      "any movies with", "for kids", "for children",
      "kid friendly", "kid-friendly", "child friendly"),
     ["kid_friendly", "family_friendly", "parent_with_child"]),
    (("before the movie", "before movie"),
     ["before_movie", "time_sensitive", "near_cinema"]),
    (("after the movie", "after movie"),
     ["after_movie", "time_sensitive"]),
    (("near cinema", "near the cinema", "closer to cinema",
      "close to cinema", "next to cinema"),
     ["near_cinema"]),
    (("not expensive", "not too expensive", "something affordable",
      "affordable", "budget friendly", "budget-friendly",
      "not too pricey", "cheaper"),
     ["budget_sensitive"]),
    (("quick", "something quick", "in a hurry", "short visit",
      "fast", "not much time"),
     ["quick_stop", "time_sensitive"]),
    (("girlfriend", "boyfriend", "wife", "husband", "romantic",
      "date", "anniversary"),
     ["romantic", "couple_friendly"]),
    (("family", "families"),
     ["family_friendly"]),
    (("gift", "present", "buying for", "shopping for"),
     ["gift_friendly"]),
    (("healthy", "light meal", "light snack", "something light"),
     ["healthy", "light"]),
    (("solo", "alone", "by myself"),
     ["solo_friendly"]),
    (("group", "friends", "with friends"),
     ["group_friendly"]),
]

# Domain-level dominant context type
_DOMAIN_CONTEXT_TYPE: dict[str, str] = {
    "entertainment": "cinema_and_movies",
    "shopping": "retail_stores",
    "dining": "restaurants_and_cafes",
    "navigation": "mall_navigation",
    "services": "mall_services",
    "mall_info": "mall_overview",
    "exploration": "discovery",
    "cross_mall": "cross_mall",
    "general": "general",
}


def _extract_hybrid_intent_bundle(
    msg: str,
    domain: str,
    sub_intent: str,
) -> tuple[str, list[str], list[str], str]:
    """
    Extract (primary_intent, secondary_intents, modifiers, dominant_context_type)
    from the message + classified domain/sub-intent.

    This is a pure keyword-based extractor. It never overrides the primary intent —
    it only ADDS secondary intents and modifiers.
    """
    lower = msg.lower()

    # Primary intent from domain/sub_intent map
    key = f"{domain}/{sub_intent}"
    primary_intent = _PRIMARY_INTENT_MAP.get(key, "")
    if not primary_intent:
        # Fallback: domain-level primary
        primary_intent = _PRIMARY_INTENT_MAP.get(f"{domain}/", domain or "general")

    # Dominant context type from domain
    dominant_context_type = _DOMAIN_CONTEXT_TYPE.get(domain, "general")

    # Secondary intents — add any that match, never replace primary
    secondary_intents: list[str] = []
    for keywords, secondary in _SECONDARY_INTENT_SIGNALS:
        if any(kw in lower for kw in keywords):
            if secondary not in secondary_intents:
                secondary_intents.append(secondary)

    # Modifiers — collect all matching tags
    seen_modifiers: set[str] = set()
    modifiers: list[str] = []
    for keywords, tags in _MODIFIER_SIGNALS:
        if any(kw in lower for kw in keywords):
            for tag in tags:
                if tag not in seen_modifiers:
                    seen_modifiers.add(tag)
                    modifiers.append(tag)

    return primary_intent, secondary_intents, modifiers, dominant_context_type

VALID_SUB_INTENTS = {
    "general_dining", "romantic_dining", "quick_bite", "family_dining",
    "cafe_recommendation", "dessert_recommendation",
    "general_shopping", "gift_recommendation", "fashion_shopping",
    "perfume_shopping", "jewelry_shopping", "accessories_shopping",
    "brand_availability",
    "general_entertainment", "movie_showtime",
    "store_hours", "parking_info", "location_query", "service_info",
    "prayer_room", "event_schedule", "offer_details", "loyalty_info",
    "open_exploration", "activity_suggestion", "first_visit_guide",
    "overview", "facilities_summary", "opening_hours",
    "family_friendliness", "what_is_available",
    "general_inquiry",
    "cross_mall_search",
}

CLASSIFICATION_PROMPT = """\
You are an intent classifier for a mall concierge chatbot. Classify the visitor's message.

Return ONLY valid JSON with these fields:
{
  "domain": one of: dining, shopping, entertainment, services, navigation, exploration, mall_info, general, cross_mall
  "sub_intent": a specific sub-intent (see list below)
  "message_kind": one of: fresh_request, correction, refinement, constraint_refinement, topic_switch, followup
  "confidence": 0.0-1.0
}

DOMAINS AND SUB-INTENTS:
- cross_mall: cross_mall_search — visitor asks about brand/store availability across multiple Cenomi malls.
  Use ONLY when the question explicitly references other malls, "both malls", "any of your malls", "Mall of Arabia",
  "across malls", or asks "does [other mall] also have X?". Examples:
  "Which of your malls has H&M?", "Is Nike at Mall of Arabia too?", "Does any Cenomi mall carry Starbucks?"
  DO NOT use cross_mall for single-mall questions like "Do you have Nike?" or "Is H&M here?".
- mall_info: overview ("tell me about the mall", "what is this place"), facilities_summary ("what facilities"), opening_hours ("mall opening hours"), family_friendliness ("is this mall family friendly", "can I come with kids"), what_is_available ("what shops are in the mall")
- exploration: open_exploration (vague "what can I do", "what's here"), activity_suggestion ("suggest something fun"), first_visit_guide ("first time here")
- dining: general_dining, romantic_dining, quick_bite, family_dining, cafe_recommendation, dessert_recommendation
- shopping: general_shopping, gift_recommendation, fashion_shopping, perfume_shopping, jewelry_shopping, accessories_shopping, offer_details, brand_availability ("do you have H&M?", "is Nike here?", "do you carry Zara?")
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
- refinement: user refines or adds to current topic ("also", "what about", "any other")
- constraint_refinement: visitor REFINES a previous recommendation with a CONSTRAINT or QUALIFIER.
  This is NOT a new request — it tightens the existing recommendation.
  Examples: "something quicker", "not too expensive", "closer to the cinema",
  "make it cheaper", "something faster", "not expensive", "kid friendly but not crowded",
  "more affordable option", "nearer to the entrance", "something lighter".
  Key signal: the visitor is reacting to a suggestion they already received.
  Use constraint_refinement when the message is a short qualifier/adjective phrase that constrains
  the previous result rather than asking a new question.
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
    raw_msg = state.normalized_user_message

    # ── Pre-flight: normalize semantically equivalent queries ─────────
    msg, canonical_pattern = normalize_query_with_pattern(raw_msg)
    # Use normalized form for all downstream classification
    # (the raw form is still in state.normalized_user_message)

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
        "topic_lock": state.scene.topic_lock,
    }

    # ── Pre-flight: detect unsupported / random inputs ────────────────
    # Context-setting messages always win — never flag as unsupported
    # Gibberish (e.g. "asdf") detected on any turn → clarification_request
    is_context_setting_msg = _is_context_setting(raw_msg, history_len)
    if not is_context_setting_msg and is_likely_unsupported(raw_msg):
        # Don't lock into a false domain — return low-confidence general inquiry
        intent = InterpretedIntent(
            domain="general",
            sub_intent="general_inquiry",
            message_kind="fresh_request",
            confidence=0.1,
            raw_signals={"classifier_source": "unsupported_detector", "unsupported": True},
            primary_intent="unsupported",
        )
        interp_contract = {
            "primary_intent": "unsupported",
            "scenario": "",
            "modifiers": [],
            "message_kind": "fresh_request",
            "flow_type": "concierge",
            "active_topic": "",
            "normalized_query": msg,
            "canonical_query_pattern": "unsupported_input",
            "topic_lock": "",
            "topic_lock_confidence": 0.0,
            "fact_scope": "",
        }
        # Check if it might be a misspelled brand
        brand_hint, brand_conf = maybe_correct_brand(raw_msg)
        if brand_hint:
            interp_contract["brand_correction_hint"] = brand_hint
            interp_contract["brand_correction_confidence"] = brand_conf
            intent.primary_intent = "brand_availability"
            intent.domain = "services"
            intent.sub_intent = "general_inquiry"
            intent.confidence = brand_conf
            intent.raw_signals["brand_correction_hint"] = brand_hint
        return {
            "intent": intent,
            "dominant_context_type": "general",
            "debug_enrichment": DebugEnrichment(
                interpretation_contract=interp_contract,
                primary_intent=intent.primary_intent,
                normalized_query=msg,
                canonical_query_pattern="unsupported_input",
            ),
            "_trace_summary": (
                f"Intent[unsupported]: random/gibberish input detected. "
                f"brand_hint={brand_hint or 'none'}"
            ),
        }

    # ── 1. Rule-based fast path ───────────────────────────────────────
    rule_result = classify_query(
        msg, history_len=history_len, scene_context=scene_ctx,
    )

    if rule_result.confidence >= RULE_CONFIDENCE_THRESHOLD:
        domain, sub_intent = map_to_graph_intent(rule_result)
        message_kind = _detect_message_kind(msg.lower(), history_len, state)
        flow_candidate, fact_scope, fact_entity_type = _detect_flow_type_candidate(
            msg, domain, sub_intent, scene_ctx,
        )
        primary_intent, secondary_intents, modifiers, dominant_ctx = (
            _extract_hybrid_intent_bundle(msg, domain, sub_intent)
        )
        scenario = _extract_scenario_from_message(msg, scene_ctx)
        # For context_setting turns, force concierge hint so routing doesn't push factual
        if message_kind == "context_setting":
            flow_candidate = "concierge"
            fact_scope = ""
            fact_entity_type = ""

        # Topic lock confidence: if we have an active topic_lock and this turn
        # is a short follow-up or refinement, the lock remains fully valid (1.0).
        # If the turn introduces a new primary intent that differs from the lock,
        # confidence drops to signal a potential switch.
        topic_lock = state.scene.topic_lock
        topic_lock_confidence = state.scene.topic_lock_confidence
        if topic_lock:
            if message_kind in ("followup", "refinement", "constraint_refinement"):
                topic_lock_confidence = min(1.0, topic_lock_confidence + 0.1)
            elif message_kind in ("topic_switch", "fresh_request") and primary_intent:
                # New intent differs from locked topic → reduce confidence
                topic_lock_confidence = max(0.0, topic_lock_confidence - 0.3)

        interpretation_contract = {
            "primary_intent": primary_intent,
            "scenario": scenario,
            "modifiers": modifiers,
            "message_kind": message_kind,
            "flow_type": flow_candidate or "tbd",
            "active_topic": scene_ctx.get("active_topic", ""),
            "normalized_query": msg,
            "canonical_query_pattern": canonical_pattern,
            "topic_lock": topic_lock,
            "topic_lock_confidence": round(topic_lock_confidence, 2),
            "fact_scope": fact_scope,
        }
        intent = InterpretedIntent(
            domain=domain,
            sub_intent=sub_intent,
            message_kind=message_kind,
            confidence=rule_result.confidence,
            raw_signals={
                "classifier_source": rule_result.source,
                "intent_class": rule_result.intent_class.value,
                "sub_tags": rule_result.sub_tags,
                "interpretation_contract": interpretation_contract,
            },
            flow_type_candidate=flow_candidate,
            fact_scope_candidate=fact_scope,
            fact_entity_type_candidate=fact_entity_type,
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            modifiers=modifiers,
        )
        logger.info(
            "Rule classifier matched (%s, conf=%.2f): %s/%s "
            "primary=%s secondary=%s flow_hint=%s message_kind=%s scenario=%s "
            "norm_query=%r pattern=%s",
            rule_result.source, rule_result.confidence, domain, sub_intent,
            primary_intent, secondary_intents, flow_candidate, message_kind, scenario,
            msg, canonical_pattern or "none",
        )
        return {
            "intent": intent,
            "dominant_context_type": dominant_ctx,
            "debug_enrichment": DebugEnrichment(
                interpretation_contract=interpretation_contract,
                primary_intent=primary_intent,
                secondary_intents=secondary_intents,
                modifiers=modifiers,
                dominant_context_type=dominant_ctx,
                normalized_query=msg,
                canonical_query_pattern=canonical_pattern,
                topic_lock=topic_lock,
                topic_lock_confidence=round(topic_lock_confidence, 2),
            ),
            "_trace_summary": (
                f"Intent[{rule_result.source}]: {domain}/{sub_intent} "
                f"({message_kind}) conf={rule_result.confidence:.2f} "
                f"primary={primary_intent} secondary={secondary_intents} "
                f"flow_hint={flow_candidate or 'tbd'} scenario={scenario or 'none'} "
                f"norm={msg!r} pattern={canonical_pattern or 'none'}"
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

    # Enrich with flow hints and hybrid bundle regardless of classifier source
    flow_candidate, fact_scope, fact_entity_type = _detect_flow_type_candidate(
        msg, intent.domain, intent.sub_intent, scene_ctx,
    )
    primary_intent, secondary_intents, modifiers, dominant_ctx = (
        _extract_hybrid_intent_bundle(msg, intent.domain, intent.sub_intent)
    )
    scenario = _extract_scenario_from_message(msg, scene_ctx)

    # For context_setting turns, force concierge hint
    if intent.message_kind == "context_setting":
        flow_candidate = "concierge"
        fact_scope = ""
        fact_entity_type = ""

    # Re-check message_kind with our enhanced detector (LLM may not know context_setting)
    enhanced_kind = _detect_message_kind(msg.lower(), history_len, state)
    if enhanced_kind == "context_setting":
        intent.message_kind = "context_setting"
        flow_candidate = "concierge"

    # Topic lock handling (same as rule path)
    topic_lock = state.scene.topic_lock
    topic_lock_confidence = state.scene.topic_lock_confidence
    if topic_lock:
        if intent.message_kind in ("followup", "refinement", "constraint_refinement"):
            topic_lock_confidence = min(1.0, topic_lock_confidence + 0.1)
        elif intent.message_kind in ("topic_switch", "fresh_request") and primary_intent:
            topic_lock_confidence = max(0.0, topic_lock_confidence - 0.3)

    interpretation_contract = {
        "primary_intent": primary_intent,
        "scenario": scenario,
        "modifiers": modifiers,
        "message_kind": intent.message_kind,
        "flow_type": flow_candidate or "tbd",
        "active_topic": scene_ctx.get("active_topic", ""),
        "normalized_query": msg,
        "canonical_query_pattern": canonical_pattern,
        "topic_lock": topic_lock,
        "topic_lock_confidence": round(topic_lock_confidence, 2),
        "fact_scope": fact_scope,
    }
    intent.flow_type_candidate = flow_candidate
    intent.fact_scope_candidate = fact_scope
    intent.fact_entity_type_candidate = fact_entity_type
    intent.primary_intent = primary_intent
    intent.secondary_intents = secondary_intents
    intent.modifiers = modifiers
    intent.raw_signals["interpretation_contract"] = interpretation_contract

    classifier_source = intent.raw_signals.get("classifier_source", "llm")
    return {
        "intent": intent,
        "dominant_context_type": dominant_ctx,
        "debug_enrichment": DebugEnrichment(
            interpretation_contract=interpretation_contract,
            primary_intent=primary_intent,
            secondary_intents=secondary_intents,
            modifiers=modifiers,
            dominant_context_type=dominant_ctx,
            normalized_query=msg,
            canonical_query_pattern=canonical_pattern,
            topic_lock=topic_lock,
            topic_lock_confidence=round(topic_lock_confidence, 2),
        ),
        "_trace_summary": (
            f"Intent[{classifier_source}]: "
            f"{intent.domain}/{intent.sub_intent} ({intent.message_kind}) "
            f"primary={primary_intent} secondary={secondary_intents} "
            f"flow_hint={flow_candidate or 'tbd'} scenario={scenario or 'none'} "
            f"norm={msg!r} pattern={canonical_pattern or 'none'}"
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

    valid_kinds = {
        "fresh_request", "correction", "refinement", "constraint_refinement",
        "topic_switch", "followup", "greeting", "smalltalk", "context_setting",
    }
    if message_kind not in valid_kinds:
        message_kind = "fresh_request"

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


# ── Context-setting patterns ────────────────────────────────────────────────
# These indicate the user is providing scene context rather than seeking action.
# They MUST be classified as context_setting so the bot acknowledges them
# and offers next steps — NOT collapses into a narrow answer.

_CONTEXT_SETTING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Role declarations: "i am bridesmaid", "i'm the groom", "i am a parent"
    re.compile(
        r"^i\s+(am|'m)\s+(a\s+|the\s+)?(bridesmaid|bride|groom|maid\s+of\s+honor|"
        r"best\s+man|parent|mom|dad|mother|father|tourist|first.time\s+visitor)s?\b",
        re.I,
    ),
    # Companion declarations: "i am here with the kid", "i'm here with my girlfriend"
    re.compile(
        r"^i\s+(am|'m)\s+here\s+(with|and)\s+",
        re.I,
    ),
    # Companion declarations without "here": "i am with my kid"
    re.compile(
        r"^i\s+(am|'m)\s+with\s+(my\s+|the\s+)?",
        re.I,
    ),
    # Situation declarations: "we are in a hurry", "we are a family of 4"
    re.compile(
        r"^we\s+(are|'re)\s+(in\s+a\s+hurry|a\s+family|with\s+kids?|celebrating|shopping)",
        re.I,
    ),
    # Shopping context: "i am here for shopping" (statement, not request)
    re.compile(
        r"^i\s+(am|'m)\s+here\s+for\s+(shopping|dining|a\s+movie|some\s+food|gifts?)",
        re.I,
    ),
    # "with friends", "with family" standalone context statements
    re.compile(
        r"^(with\s+(my\s+)?(friends?|family|girlfriend|boyfriend|wife|husband|kid|child|son|daughter))\s*$",
        re.I,
    ),
    # Semi-colon compound with role: "im here for shopping ; i am bridesmaid"
    re.compile(
        r"\bi\s+(am|'m)\s+(a\s+|the\s+)?(bridesmaid|bride|groom|maid\s+of\s+honor|"
        r"best\s+man|parent)\b",
        re.I,
    ),
    # ── NEW: Group / family / occasion openers ─────────────────────────
    # "we're a big family", "we are a family", "we're a family of 8"
    re.compile(r"^we\s*('re|are)\s+(a\s+)?(big\s+|large\s+)?family\b", re.I),
    # "we're a bridal party", "we're a wedding party", "we're a corporate team"
    # NOTE: "group of N" intentionally excluded — too broad; captures casual groups
    # like "5 teens hanging out" which should stay as exploration queries.
    re.compile(
        r"^we\s*('re|are)\s+a\s+(bridal\s+party|wedding\s+party|corporate\s+team)\b",
        re.I,
    ),
    # ── NEW: Planning openers ──────────────────────────────────────────
    # "i'm planning a team outing", "i'm planning my daughter's birthday"
    re.compile(
        r"^i\s*('m|am)\s+planning\s+(a\s+|my\s+)?"
        r"(wedding|birthday|anniversary|team\s+outing|corporate|bridal|"
        r"daughter'?s?|son'?s?)",
        re.I,
    ),
    # ── NEW: Luxury / VIP profile ─────────────────────────────────────
    # "money is not a concern", "budget isn't a concern"
    re.compile(r"money\s+(is|isn'?t|is\s+not)\s+(a\s+)?concern", re.I),
    # "looking for a premium experience", "i'm looking for a luxury day"
    re.compile(
        r"(looking|searching)\s+for\s+a\s+(premium|luxury|vip|high.end)\s+(experience|day|visit)",
        re.I,
    ),
    # ── NEW: Wellness / fitness profile ───────────────────────────────
    # "i'm really into fitness", "i'm into healthy eating"
    re.compile(
        r"^i\s*('m|am)\s+(really\s+)?into\s+(fitness|healthy\s+eating|health\s+and\s+wellness|working\s+out)",
        re.I,
    ),
    # ── NEW: Back-to-school context ────────────────────────────────────
    # "school is starting next week", "school starts this week"
    re.compile(r"school\s+(is\s+)?(starting|starts)\s+(next\s+|this\s+)?week", re.I),
    # "i need to shop for three kids" / "shopping for three kids"
    re.compile(
        r"(i\s+need\s+to\s+)?shop(ping)?\s+for\s+(three|two|four|five|all\s+(three|four|five))\s+kids?",
        re.I,
    ),
    # ── NEW: Tourist with apostrophe ──────────────────────────────────
    # "i'm a tourist visiting", "i'm visiting Saudi Arabia for the first time"
    re.compile(r"^i\s*('m|am)\s+a\s+tourist\b", re.I),
    re.compile(r"^i\s*('m|am)\s+visiting\s+\w+\s+for\s+the\s+first\s+time", re.I),
    # ── NEW: Anniversary / celebration openers ────────────────────────
    # "it's our wedding anniversary tonight", "our anniversary is today"
    re.compile(r"\b(wedding|golden|silver|diamond)\s+anniversary\b", re.I),
    re.compile(r"\bit'?s\s+our\s+anniversary\b", re.I),
    # ── NEW: Event / occasion announcements ───────────────────────────
    # "i have a dinner event tonight", "i have a formal event today"
    re.compile(r"^i\s+have\s+a\s+\w+\s+event\b", re.I),
    re.compile(
        r"^i\s+have\s+an?\s+(formal|important|work|corporate|dinner|lunch|wedding|gala)\s+",
        re.I,
    ),
    # ── NEW: Vague gift/recipient openers ─────────────────────────────
    # "something nice not too much maybe for kid"
    re.compile(
        r"\b(something\s+(nice|good|cute|fun)|get\s+something)\s+(not\s+too\s+much\s+)?maybe\s+for\b",
        re.I,
    ),
)

_CONTEXT_SETTING_SUBSTRINGS: tuple[str, ...] = (
    "i am bridesmaid",
    "i'm bridesmaid",
    "i am a bridesmaid",
    "i'm a bridesmaid",
    "i am the bridesmaid",
    "i am here with the kid",
    "i am here with my kid",
    "i'm here with the kid",
    "i'm here with my kid",
    "i am here with kid",
    "i am here with a kid",
    "i am here with my child",
    "i am here with my son",
    "i am here with my daughter",
    "i am here with friends",
    "i am here with my friends",
    "i am here with my girlfriend",
    "i am here with my boyfriend",
    "i am here with my wife",
    "i am here with my husband",
    "i am here with family",
    "i am here with my family",
    "i am here with my toddler",
    "i am here with my baby",
    "i am here with the children",
    "i am the groom",
    "i am the bride",
    "i am a tourist",
    "i am first time",
    "we are in a hurry",
    "we are a family",
    "we are here with kids",
    "we are here with children",
    "here with the kid",
    "here with my kid",
    "visiting with my family",
    "visiting with my kids",
    "i came with my",
    "i came here with my",
    "shopping for my",
    "looking for something for my",
    "it's my first time",
    "this is my first visit",
    # ── Apostrophe variants ───────────────────────────────────────────────
    "i'm a tourist",
    "i'm a first time visitor",
    "i'm the groom",
    "i'm the bride",
    "i'm a bridesmaid",
    # ── Group / family / occasion openers ─────────────────────────────────
    "we're a bridal party",
    "we are a bridal party",
    "we're a big family",
    "we're a family of",
    "we are a big family",
    "we are a family of",
    "we're a team",
    "we are a team",
    # Group visit openers (re-added to support best_effort_shortlist routing via
    # embedded-exploration check in resolve_response_mode)
    "we're a group of",
    "we are a group of",
    # ── Planning openers ──────────────────────────────────────────────────
    "i'm planning a team outing",
    "i am planning a team outing",
    "planning a team outing",
    "i'm planning my daughter's",
    "i'm planning my son's",
    "i'm planning a birthday",
    "i am planning a birthday",
    "planning my daughter's",
    "planning my son's",
    "planning a birthday",
    # ── Luxury / VIP profile ──────────────────────────────────────────────
    "money is not a concern",
    "budget is not a concern",
    "money isn't a concern",
    "looking for a premium experience",
    "i'm looking for a premium",
    "i am looking for a premium",
    # ── Wellness / fitness profile ────────────────────────────────────────
    "i'm really into fitness",
    "i am really into fitness",
    "i'm into fitness",
    "i am into fitness",
    "i'm into healthy",
    "i am into healthy",
    # ── Back-to-school context ────────────────────────────────────────────
    "school is starting",
    "school starts next week",
    "school starts",
    "back to school shopping",
    "shopping for school",
    "shop for three kids",
    "shop for my kids",
    # ── Group outing context ──────────────────────────────────────────────
    "team outing for",
    "planning an outing for",
    "planning a corporate",
    # ── Anniversary / celebration openers ─────────────────────────────────
    "our wedding anniversary",
    "it's our anniversary",
    "wedding anniversary tonight",
    "it's our wedding anniversary",
    "wedding anniversary",
    "our anniversary",
    # ── Informal role declarations ─────────────────────────────────────────
    "im bridesmaid",
    "im a bridesmaid",
    "im the bridesmaid",
    "im here as bridesmaid",
    # ── Gift / recipient context openers ──────────────────────────────────
    "i want to get something for my",
    "i want to buy something for my",
    "i want to find something for my",
    "looking for something for a",
    "looking for something for my",
    "i'm looking for something for my",
    "i am looking for something for my",
    "i'm looking for something for a",
    "i am looking for something for a",
    "need to get something for my",
    "need to buy something for my",
    # ── Event / occasion openers ───────────────────────────────────────────
    "i have a dinner event",
    "i have a lunch event",
    "i have a work event",
    "i have an event tonight",
    "i have an event today",
    "i have a wedding event",
    "i have a formal event",
    "i have an important event",
    # ── Vague multi-fragment gift/recipient openers ────────────────────────
    "something nice not too much maybe for",
    "not too much maybe for kid",
    "maybe for kid",
    "something for the kids maybe",
)


def _is_context_setting(msg: str, history_len: int) -> bool:
    """
    Return True when the message is primarily providing scene context
    rather than requesting an action.

    Context-setting messages should be acknowledged + offered next steps,
    NOT interpreted as a narrow action request.
    """
    lower = msg.lower().strip()
    if any(sub in lower for sub in _CONTEXT_SETTING_SUBSTRINGS):
        return True
    for pattern in _CONTEXT_SETTING_PATTERNS:
        if pattern.search(lower):
            return True
    return False


# ── Scenario extractor ──────────────────────────────────────────────────────
# Extract the real-world scenario (occasion/role/situation) from the message.

_SCENARIO_SIGNALS: list[tuple[tuple[str, ...], str]] = [
    # Wedding-related (highest priority — explicit role declarations)
    (("bridesmaid", "bride", "groom", "wedding", "maid of honor", "brides maid",
      "bridesmaids", "bridal", "engagement", "hen night", "bachelorette"), "wedding_related"),
    # Family outing
    (("with my kid", "with my kids", "with the kid", "with my child",
      "with my son", "with my daughter", "with my family", "family outing",
      "i am here with kid", "here with the kid", "with the children",
      "with my toddler", "with my baby"), "family_outing"),
    # Date / couple
    (("date night", "date plan", "with my girlfriend", "with my boyfriend",
      "with my wife", "with my husband", "anniversary", "romantic",
      "for my girlfriend", "for my boyfriend", "for my wife", "for my husband"), "date"),
    # Gift shopping
    (("gift for", "present for", "buying for", "shopping for",
      "looking for a gift", "looking for something for"), "gift_shopping"),
    # Quick visit / before movie
    (("before the movie", "before movie", "before our movie",
      "quick bite before", "something quick before"), "before_movie"),
    # Quick visit (standalone)
    (("quick visit", "in a hurry", "not much time", "short visit",
      "quickly", "just passing", "we are in a hurry", "short on time"), "quick_visit"),
    # Birthday celebration
    (("birthday", "celebrating", "celebrate"), "birthday"),
    # First visit
    (("first time", "first visit", "never been", "never visited"), "first_visit"),
    # Group outing
    (("with friends", "with my friends", "group of friends",
      "with colleagues", "with my colleagues", "with a group"), "group_outing"),
    # Solo
    (("alone", "by myself", "solo", "just me"), "solo_visit"),
]


def _extract_scenario_from_message(msg: str, scene_ctx: dict) -> str:
    """
    Extract the real-world scenario from the current message + scene context.
    Returns a scenario label or empty string.
    """
    lower = msg.lower()
    for keywords, scenario in _SCENARIO_SIGNALS:
        if any(kw in lower for kw in keywords):
            return scenario
    # Fallback: use existing scene occasion
    occasion = scene_ctx.get("occasion", "")
    if occasion == "anniversary":
        return "date"
    if occasion == "birthday":
        return "birthday"
    companions = scene_ctx.get("companions", [])
    if any(c in ("son", "daughter", "child", "kids") for c in companions):
        return "family_outing"
    if any(c in ("girlfriend", "boyfriend", "wife", "husband") for c in companions):
        return "date"
    return ""


_CONSTRAINT_REFINEMENT_CUES: tuple[str, ...] = (
    "something quicker",
    "something faster",
    "something cheaper",
    "something more affordable",
    "something lighter",
    "something smaller",
    "something closer",
    "something nearer",
    "not too expensive",
    "not expensive",
    "not too pricey",
    "more affordable",
    "closer to",
    "nearer to",
    "near the cinema",
    "near cinema",
    "quicker option",
    "faster option",
    "cheaper option",
    "kid friendly",
    "kid-friendly",
    "child friendly",
    "less crowded",
    "not crowded",
    "quieter option",
    "make it quicker",
    "make it cheaper",
    "something budget",
    "budget option",
)


def _detect_message_kind(msg: str, history_len: int, state: ConciergeState) -> str:
    # "With kid" / "with kids" as filter in movie/entertainment context → followup, NOT context_setting
    # (user is adding audience filter to movie list, not declaring companions for general planning)
    _KID_FILTER_IN_ENTERTAINMENT: tuple[str, ...] = ("with kid", "with kids")
    if any(p in msg for p in _KID_FILTER_IN_ENTERTAINMENT) and history_len >= 2:
        topic = (state.scene.active_topic or state.scene.topic_lock or "").lower()
        if any(t in topic for t in ("movie", "movies", "entertainment", "cinema")):
            return "followup"

    # Context-setting always takes priority regardless of history length
    if _is_context_setting(msg, history_len):
        return "context_setting"

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

    # Constraint refinement — tightens a previous recommendation
    # Only fires when there IS prior conversation context
    has_prior_context = bool(
        state.scene.active_shortlist
        or state.scene.current_need
        or state.scene.active_topic
    )
    if has_prior_context and any(cue in msg for cue in _CONSTRAINT_REFINEMENT_CUES):
        return "constraint_refinement"

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
