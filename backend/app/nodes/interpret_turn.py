"""
Interpret Turn node — classifies user intent and message kind.

Uses a single LLM classifier (gpt-4o-mini) that receives the full
conversation context and returns structured JSON with domain, sub_intent,
and message_kind.  No rule-based or regex path — the LLM is always
the decision-maker.

CONTRACT
────────
  Purpose:  Analyze the normalized message in conversation context.
            Determine domain, sub_intent, message_kind, and flow routing hints.
  Reads:    normalized_user_message, messages (history), scene
  Writes:   intent (InterpretedIntent) including flow_type_candidate,
            fact_scope_candidate, fact_entity_type_candidate
  Failure:  Classification error → domain="general", message_kind="fresh_request"
  Routing:  → smalltalk (when message_kind in SMALLTALK_KINDS)
            → route_flow (all other turns)
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, DebugEnrichment, InterpretedIntent
from app.nodes._tracing import traced_node
from intent.query_classifier import (
    is_likely_unsupported,
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
    # Personal pronoun references — always concierge/recommendation, never entity lookup
    "anything she", "anything he", "anything they",
    "something she", "something he", "something they",
    "she would", "he would", "she likes", "he likes",
    "she wants", "he wants", "she'd", "he'd",
    "for her", "for him", "for them",
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
  "message_kind": one of: fresh_request, correction, refinement, constraint_refinement, topic_switch, followup, context_setting, disengagement, category_negation, emotional, acknowledgement, companion_correction, greeting, howru, thanks, farewell, identity, crisis
  "confidence": 0.0-1.0,
  "is_gibberish": true/false  (true if the message is random characters, keyboard mashing, or completely meaningless — e.g. "sadasdas", "qwerty", "asdfgh"; false for any real word, phrase, or intent)
  "scene_corrections": []  (list — REQUIRED when message_kind is "companion_correction", empty otherwise)
  "secondary_intents": []  (list — contextual filters active for this turn, chosen from the list below)
  "modifiers": []          (list — semantic modifier tags active for this turn, chosen from the list below)
}

GIBBERISH DETECTION RULES:
- Set is_gibberish=true ONLY when the input has NO semantic content: random letter sequences (e.g. "sadasdas", "asdfgh", "qwerty", "zxcvb"), keyboard mashing, or strings that form no recognisable word in any language.
- Set is_gibberish=false for: real words (even misspelled), short queries ("food?", "hi", "ok"), numbers, punctuation-only, or anything that could be a genuine communication attempt.
- When is_gibberish=true, set domain="general", sub_intent="general_inquiry", message_kind="fresh_request", confidence=0.1.

SECONDARY INTENTS (contextual filters — include all that apply given scene context):
- family_filter       — companions include children/family; results should be family-appropriate
- kid_friendly        — specifically for a child or young person
- romantic_filter     — couple/date context; prefer intimate/romantic settings
- gift_for            — user is shopping/looking for someone else as a gift
- budget_filter       — cost sensitivity expressed or inherited
- before_movie_constraint — must fit before an upcoming movie
- after_movie_constraint  — planning something after a movie
- proximity_filter    — user wants options near a specific location (e.g. near cinema)
- quick_filter        — short on time, needs fast options
- group_filter        — visiting as a larger group

MODIFIERS (semantic tags — include all that apply):
- family_friendly, kid_friendly, parent_with_child — family/child context
- romantic, couple_friendly — romantic/date context
- budget_sensitive, affordable — cost-conscious
- premium, luxury — high-end preference
- gift_friendly — gift-buying context
- quick_stop, time_sensitive — time-constrained
- healthy, light — dietary/preference signal
- solo_friendly — visiting alone
- group_friendly — visiting as a group
- near_cinema — proximity to cinema constraint
- fresh_start — user is re-engaging after disengagement; treat as new conversation

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

ACTIVE SHOPPING TASK (CRITICAL — overrides gift_for inference):
- If an "Active shopping task" is provided in context (e.g. product_type=jacket), ALL
  follow-up messages that refine the target, gender, age, or style are REFINEMENTS of
  that shopping task — NOT new gift-shopping requests.
  - "he is a boy" when shopping for jacket → followup refining jacket search for a boy
  - "she prefers blue" when shopping for shoes → followup refining shoe search
  - "for my daughter" when shopping for a dress → followup, target_person=daughter
  - NEVER classify these as gift_shopping or pivot the domain when a product task is active.
- Only switch to gift_shopping when the user EXPLICITLY says "gift" or "present".

PRONOUN DISAMBIGUATION (CRITICAL — apply before resolving target_person):
- "for him" / "him" must be resolved using the companions list and their genders:
  - companions = [child/son, girlfriend/wife] → "him" = child (the partner is FEMALE, not male)
  - companions = [boyfriend/husband] → "him" = boyfriend/husband
  - companions = [child/son, boyfriend/husband] → "him" = child OR partner; use context to decide
- "for her" / "her" similarly:
  - companions = [daughter, boyfriend] → "her" = daughter
  - companions = [girlfriend/wife] → "her" = girlfriend/wife
- NEVER assign "boyfriend" or "husband" when the companion is explicitly "girlfriend" or "wife".
- Example: "I want the jacket for him" + companions=[child, girlfriend] → target_person=child,
  sub_intent=shopping for a male child (NOT shopping for boyfriend).

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
- category_negation: visitor EXPLICITLY REFUSES an entire category or domain.
  Examples: "no food", "no dining", "strictly no food", "without food", "skip dining",
  "no restaurants", "no shopping", "avoid food", "no coffee", "no cinema".
  Key signal: the message contains a direct "no [category]" or "without [category]" pattern.
  This is STRONGER than constraint_refinement — it completely excludes the domain for all future turns.
  Do NOT classify "no food" as correction; it is category_negation.
- disengagement: visitor is frustrated, dismissing the bot, or giving up.
  Examples: "nevermind", "never mind", "forget it", "fine", "doesn't matter",
  "don't bother", "nvm", "nevermind dude", "forget about it".
  Key signal: the message conveys resignation or frustration, NOT a request for new content.
  Respond with a brief empathetic acknowledgement and an open-ended question — do NOT recycle prior recommendations.
- topic_switch: user changes topic ("instead", "forget that", "something else")
- followup: short response continuing current topic OR sequential query ("after that?", "what next?", "and then?", "coffee?", "dessert?")
  ALSO use followup when the visitor is CONFIRMING an action the bot offered or asked about.
  If the bot's most recent message contained a direct question or offer (e.g. "Want me to...?",
  "Shall I...?", "Would you like...?", "Want me to map out...?", "Want me to narrow...?",
  "If you want, I can...", "I can help with...") AND
  the visitor replies with ANY affirmative — even a multi-word one — classify as followup, NOT acknowledgement.
  Affirmatives include (but are NOT limited to): "sure", "yes", "ok", "yeah", "please", "go ahead",
  "yes please", "yep", "do it", "sounds good", "perfect", "yes that will be good", "that will be great",
  "yes that sounds good", "that would be nice", "great idea", "yes do that", "go for it",
  "yes that would help", "that's good", "sounds great", "that sounds great", "please do",
  "i'd like that", "yes i would", "love that", "that's helpful", "why not".
  CRITICAL: Any message that STARTS with "yes", "yeah", "sure", "ok", "okay", "yep", "please"
  followed by ANY words (e.g. "yes that will be good", "yes please", "yeah that would work")
  AND follows a bot offer/question → ALWAYS classify as followup.
  The visitor is confirming the proposed action, so the bot should execute it.
- acknowledgement: the message is a NON-ACTIONABLE filler with no new intent, topic, or request.
  The user is just reacting or lingering — the bot must respond with a gentle clarifying question,
  NOT repeat or generate unsolicited recommendations.
  Examples: "okay", "ok", "alright", "wait", "hmm", "not sure", "anyways", "anyway",
  "i see", "got it", "i don't know", "not really", "i told you", "to you chatbot",
  "whatever", "moving on", "right", "interesting", "i guess".
  EXCEPTION: Any affirmative response (starting with "yes", "sure", "yeah", "ok", "please", etc.)
  after a bot offer or question is ALWAYS followup, NOT acknowledgement — even if it has extra words.
  Key test: if the message gives no actionable information about what the visitor wants AND the
  bot did not just ask a yes/no question or make an offer, it is acknowledgement.
- companion_correction: the user is explicitly correcting a FALSE assumption about their companions
  or personal situation that the bot has been making.
  Examples: "I don't have kids", "I'm alone", "I am by myself", "no kids", "I came alone",
  "I'm not with family", "why do you think I have kids?", "I told you I'm solo",
  "I don't have a family with me", "just me", "it's just me".
  Key test: the message DENIES companions or declares solo status, or expresses frustration at
  the bot incorrectly assuming companions/family/children.
  IMPORTANT: When message_kind is companion_correction, you MUST populate "scene_corrections"
  with the list of scene fields to clear. Use these token formats:
    "all_family_context"     — clears ALL family-related scene memory (companions: family/child/kids/son/daughter,
                               visit_type: family_visit, target_person if child-related, scenario: family_outing,
                               audience tags: family_friendly/kid_friendly/parent_with_child/family, implicit_goal if family-related)
    "companion:<name>"       — removes a specific companion (e.g. "companion:kids", "companion:family")
    "visit_type:solo"        — sets visit_type to solo
    "target_person"          — clears the target_person field
    "scenario"               — clears the scenario field
  For "I am alone", "I don't have kids", "no kids", "just me", etc. → always include "all_family_context" and "visit_type:solo".

META-QUESTION HANDLING (questions about what the bot itself said):
When the visitor is questioning or correcting something the BOT said — not making a new request —
classify it as companion_correction (if about companions/people) or correction (for other bot errors).
Examples:
- "who is the other friend?" (bot mentioned 'friend' but visitor didn't say so) → companion_correction
- "what friend are you talking about?" → companion_correction
- "I didn't mention a friend" → companion_correction
- "I never said I had kids" → companion_correction
- "you said X but I didn't say that" → correction
- "why did you mention X?" → correction
Key test: the visitor is reacting to something the bot stated, not making a fresh request.
For companion_correction, populate "scene_corrections" to clear the invented companion.

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
SMALLTALK AND SAFETY MESSAGE KINDS (new — use these when the message is purely conversational or requires special handling):
- "greeting"  — pure greeting with no information request: "hi", "hello", "hey", "good morning", "marhaba", "ahlan"
- "howru"     — asking how the bot is: "how are you", "how's it going", "what's up"
- "thanks"    — expressing gratitude: "thanks", "thank you", "thx", "shukran", "cheers"
- "farewell"  — saying goodbye: "bye", "goodbye", "see you", "take care", "ma'a salama"
- "identity"  — asking what/who the bot is: "who are you", "what are you", "are you a bot",
                "are you AI", "what is your name", "what can you do", "how do you work"
- "crisis"    — self-harm, suicidal, or extreme distress language:
                "shall I jump off the roof", "kill myself", "end my life", "want to die",
                "hurt myself", "life is not worth living", "I want to jump", "suicidal"
                IMPORTANT: classify as "crisis" even if framed as hypothetical. Safety first.
For all of the above, set domain="general" and sub_intent="general_inquiry".
These bypass the full pipeline and receive compassionate, context-appropriate responses.

- "Hi" / "Hello" → general/general_inquiry with message_kind="greeting"
- "How are you?" → general/general_inquiry with message_kind="howru"
- "Thanks" / "Thank you" → general/general_inquiry with message_kind="thanks"
- "Bye" → general/general_inquiry with message_kind="farewell"
- "Who are you?" → general/general_inquiry with message_kind="identity"
- "shall I jump off the roof" → general/general_inquiry with message_kind="crisis"
- "Where can I eat?" → dining/general_dining
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
            model=settings.classifier_model,
            temperature=0.0,
            api_key=settings.openai_api_key,
            max_tokens=300,
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

    # ── Pre-flight: fast exit for trivially empty / single-char inputs ──
    # Only the most obvious non-inputs bypass the LLM to save tokens.
    # All other gibberish detection is handled by the LLM via is_gibberish.
    if is_likely_unsupported(raw_msg):
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
                f"Intent[unsupported]: trivially empty/single-char input. "
                f"brand_hint={brand_hint or 'none'}"
            ),
        }

    # ── LLM classification ────────────────────────────────────────────
    try:
        intent = await _llm_classify(msg, history_len, state)
        intent.raw_signals["classifier_source"] = "llm"
    except Exception as exc:
        logger.warning("LLM classifier failed, using safe default: %s", exc)
        intent = InterpretedIntent(
            domain="general",
            sub_intent="general_inquiry",
            message_kind="fresh_request",
            confidence=0.3,
        )
        intent.raw_signals["classifier_source"] = "error_fallback"

    # ── LLM-driven gibberish detection ────────────────────────────────
    # If the LLM flagged the input as gibberish (random chars, keyboard mash)
    # mark it as unsupported so downstream nodes route to graceful_recovery.
    if intent.raw_signals.get("is_gibberish"):
        intent.primary_intent = "unsupported"
        intent.confidence = 0.1
        intent.raw_signals["unsupported"] = True
        logger.info("LLM flagged input as gibberish: %r", raw_msg)

    # Flow hints: still derived from domain/sub_intent (deterministic, not LLM)
    flow_candidate, fact_scope, fact_entity_type = _detect_flow_type_candidate(
        msg, intent.domain, intent.sub_intent, scene_ctx,
    )
    scenario = _extract_scenario_from_message(msg, scene_ctx)

    # On the LLM path, use LLM-returned secondary_intents and modifiers directly.
    # _extract_hybrid_intent_bundle is NOT called here — it is keyword-only and
    # would overwrite the LLM's contextual understanding of the scene.
    secondary_intents: list[str] = list(intent.secondary_intents)
    modifiers: list[str] = list(intent.modifiers)

    # Primary intent and dominant context type are still derived deterministically
    # from the LLM-classified domain/sub_intent.
    primary_intent_from_map, _, _, dominant_ctx = _extract_hybrid_intent_bundle(
        msg, intent.domain, intent.sub_intent,
    )
    # Use only the primary_intent from the map; discard the keyword secondary_intents/modifiers.
    primary_intent = primary_intent_from_map

    # For context_setting / companion_correction / acknowledgement turns, force concierge hint.
    if intent.message_kind in ("context_setting", "companion_correction", "acknowledgement"):
        flow_candidate = "concierge"
        fact_scope = ""
        fact_entity_type = ""

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
        "classifier_source": intent.raw_signals.get("classifier_source", "llm"),
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

    # Active shopping task — CRITICAL for follow-up refinements like "for my kid"
    # Without this the LLM cannot tell that "for my kid" refines an in-progress
    # jacket search rather than opening a new gift query.
    st = state.scene.shopping_task
    if st.product_type:
        task_parts = [f"product_type={st.product_type}"]
        if st.product_category:
            task_parts.append(f"category={st.product_category}")
        if st.target_person:
            task_parts.append(f"target={st.target_person}")
        if st.shopping_stage:
            task_parts.append(f"stage={st.shopping_stage}")
        context_parts.append(
            f"Active shopping task ({', '.join(task_parts)}): "
            "interpret follow-up messages as REFINEMENTS of this task, "
            "NOT as new gift or general queries."
        )

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

    # ── Inject recent conversation history ───────────────────────────
    # Pass the last 3 user/assistant exchanges so the LLM can resolve
    # ambiguous queries ("what can I do?", "show me more") in the correct
    # conversational context rather than treating them as fresh requests.
    # Each message is capped at 150 chars to keep the prompt lean.
    recent_msgs = [m for m in state.messages if m.role in ("user", "assistant")][-6:]
    if recent_msgs:
        dialogue_lines = [
            f"  {m.role.capitalize()}: {m.content[:150]}"
            for m in recent_msgs
        ]
        context_parts.append("Recent conversation (most recent last):\n" + "\n".join(dialogue_lines))

    # ── Inject recent mood state ─────────────────────────────────────
    # If the visitor was recently frustrated or disengaged, tell the LLM
    # so it can correctly classify the current query in that emotional context
    # (e.g. a vague follow-up query after frustration is likely a fresh
    # attempt, not a refinement of the prior recommendation).
    if state.scene.recent_mood:
        context_parts.append(
            f"Visitor's recent emotional state: {state.scene.recent_mood} "
            f"(factor this into message_kind classification)"
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
    is_gibberish = bool(parsed.get("is_gibberish", False))

    if domain not in VALID_DOMAINS:
        domain = "general"
    if sub_intent not in VALID_SUB_INTENTS:
        sub_intent = "general_inquiry"

    # All message_kinds that interpret_turn can legitimately set.
    valid_kinds = {
        "fresh_request", "correction", "refinement", "constraint_refinement",
        "topic_switch", "followup", "context_setting",
        "disengagement", "category_negation", "emotional",
        "acknowledgement", "companion_correction",
        # Smalltalk / safety kinds (previously handled by regex in smalltalk.py)
        "greeting", "howru", "thanks", "farewell", "identity", "crisis",
    }
    if message_kind not in valid_kinds:
        message_kind = "fresh_request"

    # Parse scene_corrections — only meaningful for companion_correction turns.
    raw_corrections = parsed.get("scene_corrections", [])
    scene_corrections: list[str] = (
        [str(c) for c in raw_corrections if isinstance(c, str)]
        if isinstance(raw_corrections, list)
        else []
    )

    # Parse secondary_intents and modifiers — LLM-reasoned from full context.
    # These replace the keyword-only _extract_hybrid_intent_bundle on the LLM path.
    raw_secondary = parsed.get("secondary_intents", [])
    llm_secondary_intents: list[str] = (
        [str(s) for s in raw_secondary if isinstance(s, str)]
        if isinstance(raw_secondary, list)
        else []
    )

    raw_modifiers = parsed.get("modifiers", [])
    llm_modifiers: list[str] = (
        [str(m) for m in raw_modifiers if isinstance(m, str)]
        if isinstance(raw_modifiers, list)
        else []
    )

    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=confidence,
        scene_corrections=scene_corrections,
        secondary_intents=llm_secondary_intents,
        modifiers=llm_modifiers,
    )
    if is_gibberish:
        intent.raw_signals["is_gibberish"] = True
    return intent


# ── Scenario extractor ──────────────────────────────────────────────────────
# Derives the real-world situation/occasion from the message + scene context.
# Used to populate the interpretation_contract "scenario" field consumed by
# resolve_playbooks for situation-aware recommendations.

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


