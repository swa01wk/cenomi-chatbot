"""
Interpret Turn node — classifies user intent and message kind.

Uses a single LLM classifier (gpt-4o-mini) that receives the full
conversation context and returns structured JSON with domain, sub_intent,
message_kind, flow_type, scenario, and related signals.

The LLM is the sole decision-maker for intent, routing hints, and scenario.
No keyword lists, regex tables, or post-LLM rule overrides are used.

CONTRACT
────────
  Purpose:  Analyze the normalized message in conversation context.
            Determine domain, sub_intent, message_kind, flow_type, scenario,
            and flow routing hints.
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
from intent.query_classifier import is_likely_unsupported

logger = logging.getLogger(__name__)

_classifier_llm: ChatOpenAI | None = None

VALID_DOMAINS = {
    "dining", "shopping", "entertainment", "services", "navigation",
    "exploration", "mall_info", "general",
    "cross_mall",
}

# ── Deterministic lookup tables (domain/sub_intent → canonical labels) ───────
# These do not match against raw message text — they map structured LLM output.

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

# Domain → dominant context type label
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

# Sub-intent → (fact_scope, fact_entity_type) for factual flow routing hints
_SUB_INTENT_TO_FACT_SCOPE: dict[str, tuple[str, str]] = {
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
  "flow_type": "factual" | "concierge"  (routing decision — see FLOW TYPE RULES below)
  "scenario": ""  (real-world context — see SCENARIO VALUES below)
  "response_mode": ""  (how to respond — see RESPONSE MODE RULES below)
  "scene_corrections": []  (list — REQUIRED when message_kind is "companion_correction", empty otherwise)
  "secondary_intents": []  (list — contextual filters active for this turn, chosen from the list below)
  "modifiers": []          (list — semantic modifier tags active for this turn, chosen from the list below)
  "entity_query": ""       (the specific entity/brand/item being searched — REQUIRED for factual flow and cross_mall; empty string otherwise)
  "retrieval_needed": true/false  (see RETRIEVAL DECISION RULES below)
  "companion_context": []  (companions detected in THIS turn's message — see COMPANION CONTEXT RULES below)
  "releases_topic_lock": true/false  (see TOPIC LOCK RELEASE RULES below)
}

ENTITY_QUERY EXTRACTION RULES:
- Populate "entity_query" whenever flow_type="factual" OR domain="cross_mall".
- Extract only the core entity/brand/item — strip all question scaffolding.
  Examples:
  • "which all malls does shrimp"          → "shrimp"
  • "which malls have Starbucks"           → "Starbucks"
  • "do you have H&M"                      → "H&M"
  • "where is the prayer room"             → "prayer room"
  • "what time does Zara close"            → "Zara"
  • "is there a Muvi Cinema here"          → "Muvi Cinema"
  • "which malls sell sushi"               → "sushi"
  • "where can I find something like Zara" → "Zara"
  • "where else can I find it" (scene has last entity "Nike") → "Nike"
- For follow-up cross-mall turns ("where else", "other malls too?"), use the last mentioned entity from conversation context.
- Leave empty ("") for concierge/recommendation turns with no specific entity target.

GIBBERISH DETECTION RULES:
- Set is_gibberish=true ONLY when the input has NO semantic content: random letter sequences (e.g. "sadasdas", "asdfgh", "qwerty", "zxcvb"), keyboard mashing, or strings that form no recognisable word in any language.
- Set is_gibberish=false for: real words (even misspelled), short queries ("food?", "hi", "ok"), numbers, punctuation-only, or anything that could be a genuine communication attempt.
- When is_gibberish=true, set domain="general", sub_intent="general_inquiry", message_kind="fresh_request", confidence=0.1, flow_type="concierge".

FLOW TYPE RULES (flow_type field):
- "factual": visitor wants an EXACT DATA answer — movie showtimes/listings, store hours, mall opening times, store/brand presence check ("is Nike here?"), facility location ("where is the prayer room?"), parking info, cross-mall availability.
  Key signals: "what movies are showing", "when does the mall open", "where is X", "do you have X", "is X here", "what time does"
- "concierge": visitor wants RECOMMENDATIONS, SUGGESTIONS, a PLAN, or experience curation — dining recommendations, shopping suggestions, activity planning, gift ideas, curated itineraries.
  Key signals: "suggest", "recommend", "what can we do", "plan for", "something for", "what should we", gift/occasion/companion context.
- Cross-mall queries (domain=cross_mall) → always "factual".
- Navigation domain (domain=navigation) → always "factual" unless message_kind=context_setting.
- Mall_info domain → usually "factual" (exact data about the mall).
- When in doubt, prefer "concierge".

SCENARIO VALUES (scenario field — extract the real-world context/occasion):
- "wedding_related" — bridesmaid, bride, groom, wedding, bridal, engagement, hen party
- "family_outing"   — with kids/child/son/daughter/family, family day/trip
- "date"            — date night, anniversary, with girlfriend/boyfriend/wife/husband, romantic
- "gift_shopping"   — gift for, present for, buying/shopping for someone
- "before_movie"    — before the movie, something quick before, movie starts in X
- "quick_visit"     — quick visit, in a hurry, short visit, not much time
- "birthday"        — birthday, celebrating
- "first_visit"     — first time here, never been, first visit
- "group_outing"    — with friends, group of friends, with colleagues
- "solo_visit"      — alone, by myself, solo, just me
- ""                — no clear real-world scenario

ROMANTIC OCCASION DETECTION (CRITICAL):
When the query contains romantic language ("romantic", "date", "date night", "for a couple",
"anniversary", "special evening", "for two", "intimate") — even WITHOUT explicit companion
context in scene memory — ALWAYS set scenario="date".
For dining queries with romantic language: set sub_intent="romantic_dining" and
response_mode="guided_recommendation" (NOT "best_effort_shortlist"). The romantic occasion
itself is specific context that warrants a curated shortlist.
Examples:
• "any romantic options here?"       → scenario="date", sub_intent="romantic_dining", response_mode="guided_recommendation"
• "any romantic restaurants?"        → scenario="date", sub_intent="romantic_dining", response_mode="guided_recommendation"
• "something romantic for dinner"    → scenario="date", sub_intent="romantic_dining", response_mode="guided_recommendation"
• "a nice romantic place to eat"     → scenario="date", sub_intent="romantic_dining", response_mode="guided_recommendation"
• "somewhere romantic"               → scenario="date", response_mode="guided_recommendation"

RESPONSE MODE RULES (response_mode field — how the bot should respond this turn):
- "direct_factual"          — user wants EXACT DATA: movie times, store hours, location, brand presence check, parking info. Only when flow_type=factual and intent is a precise data lookup.
- "guided_recommendation"   — user wants SUGGESTIONS or a SHORTLIST: recommend restaurants, suggest stores, curate options. The default for concierge flow with clear intent.
- "hybrid_plan"             — user requests a PLAN, ITINERARY, or SCHEDULE spanning MULTIPLE domains. Signals: explicit "AND" joining two domains ("food AND movies", "dinner AND a show"), "plan for", "itinerary", "full day", "what order should we", "can we fit", "X-hour schedule". CRITICAL: any message with TWO or more distinct activities (dining + entertainment, shopping + food, movie + meal) → ALWAYS "hybrid_plan". ALSO: any dining request that explicitly references a movie time or movie context ("something before the movie", "eat after the movie", "quick bite before the film", "dinner after the show") → ALWAYS "hybrid_plan" because the response must coordinate both the dining suggestion AND the movie timing.
- "best_effort_shortlist"   — user's intent is VAGUE or EXPLORATORY: "what can I do", "anything good", "surprise me", "i'm bored", first visit without clear intent. Present diverse safe options.
- "context_acknowledgement" — user is DECLARING CONTEXT (message_kind=context_setting) without a specific request: "I'm here with my family", "it's my girlfriend's birthday". Acknowledge and offer next-step options.
- "graceful_recovery"       — user's message is UNINTELLIGIBLE, completely off-topic, or intent cannot be determined (is_gibberish=true, domain=general with no intent).
- "clarification_request"   — user is requesting something the bot CANNOT DO: book taxis, order food delivery, make phone calls, place orders. Not for vague/unclear requests.
- ""                        — no strong signal; resolver will decide based on confidence

RESPONSE MODE DECISION GUIDE:
- flow_type=factual + specific data lookup → "direct_factual"
- flow_type=concierge + clear shopping/dining/activity intent → "guided_recommendation"
- message_kind=context_setting → "context_acknowledgement"
- message_kind IN (greeting, howru, thanks, farewell, identity, crisis, emotional) → "context_acknowledgement"
  (ALL smalltalk kinds are context acknowledgements — the bot acknowledges the visitor's state, not a request)
- two domains in one query (movies AND food, dinner AND entertainment, shopping AND dining) or explicit planning request → "hybrid_plan"
  Examples: "food and movies", "dinner and a show", "shopping and coffee", "catch a movie and eat", "movie then dinner", "food and maybe movies", "what should we eat before the movie" → "hybrid_plan"
  CRITICAL RULE: if the visitor explicitly wants BOTH food/dining AND entertainment/movies in the SAME turn → "hybrid_plan" (not guided_recommendation)
- vague/exploratory with no specific category → "best_effort_shortlist"
- is_gibberish=true OR completely unclear → "graceful_recovery"
- completely off-topic requests with NO mall relevance (jokes, riddles, trivia, non-mall questions) → "graceful_recovery"
  Examples: "tell me a joke", "say something funny", "tell me a riddle", "what's 2+2", "who invented the telephone" → "graceful_recovery"
  IMPORTANT: For off-topic/humor: set is_gibberish=false, domain=general, sub_intent=general_inquiry, message_kind=fresh_request, response_mode="graceful_recovery"
- taxi/order/delivery/booking requests → "clarification_request"
- pure context declarations with NO shopping/dining/activity request → "context_acknowledgement"
  Examples: "i am a bridesmaid", "it's my anniversary", "i'm here with my family", "it's his birthday" → context_setting + "context_acknowledgement"
  Key test: if the message ONLY declares who the visitor is or what the occasion is (no "show me", "recommend", "where is") → context_setting + context_acknowledgement
- ambiguous (modifier queries, short refinements, follow-ups) → ""

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
- add_dining_step     — user wants to add a dining activity to a multi-step plan
- add_coffee_step     — user wants to add a coffee/cafe stop to a multi-step plan

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

DO YOU HAVE X? / IS THERE X? / DO YOU CARRY X? — DOMAIN DISPATCH (CRITICAL):
The phrase "do you have X", "is there X", "do you carry X", "can I find X", "is X available",
"do you sell X" must be routed by what X IS — NOT by the phrase pattern alone.

• X is a BRAND or STORE NAME (H&M, Nike, Zara, Starbucks, McDonald's, Sephora,
  Danube, Muvi, VOX, Adidas, Herfy, Kudu, Cinnabon, any recognisable retail/F&B brand)
  → shopping/brand_availability (factual flow)

• X is a FOOD ITEM, DISH, INGREDIENT, or CUISINE TYPE (shrimp, sushi, pizza, shawarma,
  coffee, dessert, ice cream, burger, seafood, pasta, biryani, noodles, cake, chicken,
  kebab, salad, rice, sandwich, smoothie, juice, any food or drink item)
  → dining/general_dining (concierge flow)

• X is a FACILITY or SERVICE (parking, valet, prayer room, musallah, ATM, wheelchair,
  stroller, pram, baby room, nursing room, lost & found, information desk, wifi,
  customer service, toilets, restrooms, lockers, changing room)
  → services/service_info (factual) or services/parking_info specifically for parking/valet

• X is an ENTERTAINMENT or ACTIVITY (cinema, movies, bowling, trampoline, arcade,
  gaming, kids play area, yoga, gym, ice skating, go-kart, VR, escape room, any activity)
  → entertainment/general_entertainment (factual for presence check)

• X is a PRODUCT CATEGORY or TYPE that is NOT a brand name (perfume, jacket, shoes,
  toys, jewelry, handbag, sportswear, watches, sunglasses, books, electronics, clothes)
  → shopping/general_shopping (concierge flow)

NEVER route food items, facilities, activities, or generic product types to brand_availability.
brand_availability is ONLY for named brands and store chains.

DOMAINS AND SUB-INTENTS:
- cross_mall: cross_mall_search — visitor asks about brand/store/item availability beyond only the current mall.
  USE cross_mall when ANY of these apply:
  • Explicit multi-mall wording: "which of your malls", "which all malls", "both malls", "any Cenomi mall",
    "Mall of Arabia", "across malls", "at other malls", "where else", "all locations", "does [other mall] also have X".
  • "which malls does/do/have X" — ANY phrasing where a visitor asks which mall(s) carry/have/sell something.
    Examples: "which all malls does shrimp", "which malls do/have Starbucks", "which malls sell X".
  • Follow-up after a specific store/brand was discussed: "where else can I find it?", "is it at your other mall too?",
    "what about other Cenomi malls?" — use scene/last entity to infer the brand; still output cross_mall_search.
  NOTE: cross_mall applies even for food items, ingredients, or categories (e.g. "which malls have sushi", 
  "which all malls does shrimp", "where can I find seafood across your malls") — treat the food term as the search query.
  DO NOT use cross_mall for questions clearly about ONLY this mall with no multi-mall signal, e.g. "Do you have Nike?",
  "Is H&M here?", "Do you carry Zara?" (those → shopping/brand_availability).
- mall_info: overview ("tell me about the mall", "what is this place"), facilities_summary ("what facilities"), opening_hours ("mall opening hours"), family_friendliness ("is this mall family friendly", "can I come with kids"), what_is_available ("what shops are in the mall")
- exploration: open_exploration (vague "what can I do", "what's here"), activity_suggestion ("suggest something fun"), first_visit_guide ("first time here")
- dining: general_dining, romantic_dining, quick_bite, family_dining, cafe_recommendation, dessert_recommendation
- shopping: general_shopping, gift_recommendation, fashion_shopping, perfume_shopping, jewelry_shopping, accessories_shopping, offer_details, brand_availability ("do you have H&M?", "is Nike here?", "do you carry Zara?", "is there a Starbucks?") — brand_availability is ONLY for named brands/stores; food items, facilities, and activities use the domain-dispatch rules above.
- entertainment: general_entertainment, movie_showtime
- services: store_hours, parking_info, service_info, prayer_room, event_schedule, loyalty_info
- navigation: location_query
- general: general_inquiry (greetings, off-topic, unclear)

IMPORTANT — navigation/location_query vs services/service_info:
"Where is X?" should be classified as navigation/location_query when X is a RESTAURANT, STORE,
or known BRAND NAME — even if the name is misspelled.
services/service_info is ONLY for mall-provided services (information desk, customer service,
valet, prayer room, ATM, Wi-Fi, stroller rental, lost & found — NOT restaurants or shops).
Examples:
- "where is Herfy" → navigation/location_query (it's a restaurant)
- "where is herfa" → navigation/location_query (misspelling of "Herfy" — still a restaurant)
- "where is McDonald's" → navigation/location_query
- "where is the food court" → navigation/location_query
- "where is the information desk" → services/service_info
- "where is customer service" → services/service_info

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

- Multi-mall follow-ups: if the previous turn mentioned a specific store/brand and the user now asks "where else",
  "other malls", "all your malls", etc. → cross_mall/cross_mall_search (not brand_availability).
- Short follow-up queries (1-2 words) should be interpreted in the context of
  the previous conversation — but ONLY when they are within the same domain or naturally
  extend the current topic. If a short query clearly belongs to a DIFFERENT domain than
  the active_topic, it is a topic_switch, NOT a followup.
  (e.g. active_topic=shopping → "movies" → topic_switch; active_topic=dining → "perfume" → topic_switch)

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
  Examples:
  • Resignation: "nevermind", "never mind", "forget it", "fine", "doesn't matter",
    "don't bother", "nvm", "nevermind dude", "forget about it", "whatever forget it".
  • Stopping the flow: "stop", "okay stop", "ok stop", "just stop", "please stop",
    "stop recommending", "enough", "no more".
  • Complaints about the bot's performance: "chatbot is not working", "not working",
    "this isn't working", "nothing is working", "you're not helping", "not helpful",
    "this is useless", "you keep repeating", "same thing again", "still not useful",
    "this is terrible", "you're not understanding me".
  • Anger/frustration about a person (third-party or self): "[name] is pissed", "I'm pissed",
    "[name] is angry", "I'm furious", "I'm frustrated", "I'm done", "I give up", "forget it".
  IMPORTANT — ANGER → DISENGAGEMENT (not emotional): "[name] is pissed", "I'm pissed",
  "I'm angry", "I'm furious" → ALWAYS disengagement. emotional is for sadness/stress/boredom.
  Key signal: the message conveys resignation, frustration, anger, or dismissal — NOT a request for new content.
  Respond with a brief empathetic acknowledgement and an open-ended question — do NOT recycle prior recommendations.
  CRITICAL: NEVER classify as disengagement if the message contains "thanks", "thank you",
  or any positive word (great, amazing, perfect, awesome, brilliant, wonderful, fantastic).
  NEVER classify as disengagement if the message is a genuine topic request (food, movies, shopping, etc.).
  Disengagement is ONLY for messages that actively dismiss, resign, express anger, or express frustration.
  ABSOLUTE ACTION-REQUEST OVERRIDE: If the message contains an explicit desire, craving, or action
  for food/dining, shopping, or entertainment — "I wanna eat", "I want to buy", "show me", "I'm hungry",
  "give me food", "I need a jacket", "I want to watch" — it is ALWAYS dining/shopping/entertainment
  with message_kind="fresh_request" (or "refinement" if it refines an active topic).
  Even if the prior context involved frustration or disengagement, an explicit craving or mall-related
  action request is NEVER disengagement. The user has re-engaged.
  Examples of NEVER disengagement:
  • "I wanna eat junk food, a lot of unhealthy junk food" → dining/general_dining, fresh_request
  • "just give me food" (even after frustration) → dining/general_dining, fresh_request
  • "I want to buy a jacket" (even after complaint turns) → shopping/general_shopping, fresh_request
  • "something to eat" → dining/general_dining, fresh_request or refinement
- topic_switch: user changes topic ("instead", "forget that", "something else").
  IMPORTANT: Also use topic_switch when the user's message clearly refers to a DIFFERENT
  domain from the active topic — even without explicit transition words.
  Examples:
  • Active topic = dining, user says "men's wear" → shopping/fashion_shopping, topic_switch
  • Active topic = shopping, user says "what's showing at the cinema?" → entertainment, topic_switch
  • Active topic = entertainment, user says "food?" → dining, topic_switch
  • Active topic = dining, user says "I wanna buy a jacket" → shopping/general_shopping, topic_switch
  • Active topic = dining, user says "I also wanna buy a jacket" → shopping/general_shopping, topic_switch
  • Active topic = dining, user says "I want to watch horror movies" → entertainment/general_entertainment, topic_switch
  • Active topic = dining, user says "I want to watch horror movies?" → entertainment/general_entertainment, topic_switch
  Key test: if the user's message domain is unambiguously different from the active_topic
  field, classify as topic_switch, not followup or refinement.
  SHORT-QUERY RULE: This domain-switch test applies to ALL query lengths, including 1-2 words.
  Even single-word queries are topic_switch when they clearly refer to a different domain:
  • active_topic=shopping → "movies" or "cinema" → topic_switch to entertainment (NOT followup)
  • active_topic=dining → "shopping" or "clothes" or "perfume" → topic_switch (NOT followup)
  • active_topic=entertainment → "food" or "restaurant" → topic_switch to dining (NOT followup)
  NEVER classify a clear domain-crossing query as followup just because it is short.
  CATEGORY-WORD RULE: If the user sends a single word or short phrase that unambiguously
  belongs to a specific domain — a food item ("shrimp", "sushi", "burger"), a facility
  ("parking", "ATM", "prayer room"), or an activity ("cinema", "bowling", "trampoline") —
  AND the active_topic is a DIFFERENT domain, always classify as topic_switch to the
  correct domain using the DO YOU HAVE X? dispatch rules above.
  Examples:
  • active_topic=shopping, message="shrimp" → topic_switch, dining/general_dining
  • active_topic=dining, message="parking" → topic_switch, services/parking_info
  • active_topic=shopping, message="cinema" → topic_switch, entertainment/general_entertainment
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
  THANKS EXCEPTION (ABSOLUTE RULE): "thanks", "thank you", "thanks chatbot", "thank you so much",
  "thanks a lot", "cheers", "shukran" — ANY message that is primarily an expression of GRATITUDE
  must ALWAYS be classified as "thanks", NEVER as "followup" — even if the bot just asked a question
  or made an offer. Gratitude signals the end of the exchange, not confirmation of the offer.
  Examples:
  • Bot: "Want me to narrow this down?" → User: "Thanks" → thanks (NOT followup)
  • Bot: "Shall I suggest more?" → User: "Thanks chatbot" → thanks (NOT followup)
  • Bot: "Want me to map this out?" → User: "Thank you" → thanks (NOT followup)
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
  SUB-INTENT RULE: When message_kind="acknowledgement", ALWAYS set sub_intent="general_inquiry".
  NEVER carry the sub_intent from the previous bot turn (e.g. "ok" after a dessert suggestion
  must NOT become sub_intent="dessert_recommendation"). The user has not requested anything specific.
  Examples:
  • "ok" (after bot suggested desserts) → acknowledgement, sub_intent=general_inquiry
  • "oki" → acknowledgement, sub_intent=general_inquiry
  • "alright" → acknowledgement, sub_intent=general_inquiry
  CRITICAL DISTINCTION from disengagement: acknowledgement is for NEUTRAL vague fillers
  ("ok", "hmm", "alright") — messages with NO frustration or complaint.
  If the message contains ANY hint of complaint, frustration, or dismissal (e.g. "not working",
  "chatbot is not working", "useless", "pissed", "[X] is pissed"), classify as disengagement NOT acknowledgement.
  If the message mentions a person being angry or frustrated (e.g. "Binoo is pissed",
  "I'm pissed"), classify as disengagement, NOT acknowledgement or emotional.
- emotional: visitor expresses any AFFECTIVE or MOOD state — sad, stressed, bored, overwhelmed,
  OR happy, excited, content — that is NOT a specific mall request.
  The key test is whether the message is primarily about how the visitor FEELS rather than what
  they want to find or do.
  Negative-mood examples:
  • "I'm sad", "I feel down", "I'm bored", "I'm stressed", "I'm tired"
  • "I'm sad, give me a plan that will make me happy" — emotional, even though it asks for a plan
  • "give me something fun", "I need cheering up", "cheer me up", "make me happy"
  • "I'm overwhelmed", "I don't know what to do", "nothing sounds good"
  • "my wife seems down", "my friend is stressed", "they're having a rough day"
  Positive-mood examples (respond with warmth + open offer to help):
  • "i am happy" → emotional (respond warmly: "That's great to hear! What can I help you with?")
  • "I'm excited", "feeling great", "i'm in a good mood", "i am just happy about my life"
  • "this is great", "i'm really happy today"
  NOTE: Anger/frustration expressions ("[name] is pissed", "I'm pissed", "I'm angry",
  "I'm furious") → disengagement, NOT emotional. Emotional is for ANY mood expression that is
  NOT anger/frustration — it covers the full range from sadness to joy.
  CRITICAL MOOD OVERRIDE (absolute — overrides all other rules):
    • Any message starting with "I'm sad", "I feel down", "I'm stressed",
      "I'm tired", "I'm depressed", "I'm overwhelmed" → ALWAYS message_kind="emotional",
      even if the rest of the message contains "give me a plan", "suggest something", "what to do".
    • EXCEPTION — "I'm bored" in a MALL context: treat as an exploration discovery request
      (exploration/activity_suggestion, message_kind="fresh_request", response_mode="best_effort_shortlist").
      A visitor who says "I'm bored" at a mall wants activity suggestions, NOT emotional counselling.
      Do NOT classify "I'm bored" as emotional unless accompanied by clear distress ("I'm really bored
      and sad", "I'm bored and don't feel well"). Plain "I'm bored" or "bored" → exploration.
    • "cheer me up", "make me happy", "I need cheering up", "lift my spirits",
      "I'm having a bad day" → ALWAYS message_kind="emotional".
    • Pure mood declarations with no shopping/dining/entertainment request ("i am happy",
      "I'm excited") → ALWAYS message_kind="emotional", NOT fresh_request or acknowledgement.
  For all of the above, set domain=general, sub_intent=general_inquiry.
  "I'm sad, give me a plan that will make me happy" → emotional (NOT fresh_request or refinement).
  "cheer me up" → emotional (NOT fresh_request or exploration with fresh_request kind).
  "i am happy" → emotional (NOT acknowledgement or fresh_request).
  Do NOT inherit a shopping/dining/entertainment domain from active_topic for emotional turns.
  The bot responds with warmth + an open offer to help, not recycled recommendations.
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
- "I'm bored" → exploration/activity_suggestion, message_kind="fresh_request", response_mode="best_effort_shortlist" (mall discovery, NOT emotional)
- "First time at this mall" → exploration/first_visit_guide
SMALLTALK AND SAFETY MESSAGE KINDS (new — use these when the message is purely conversational or requires special handling):
- "greeting"  — pure greeting with no information request: "hi", "hello", "hey", "good morning", "marhaba", "ahlan"
- "howru"     — asking how the bot is: "how are you", "how's it going", "what's up"
- "thanks"    — expressing gratitude or satisfaction: "thanks", "thank you", "thx", "shukran", "cheers",
                "great thanks", "amazing thanks", "perfect thanks", "awesome thanks",
                "that's great thanks", "great! amazing thanks", "great! thanks",
                "thanks a lot", "thank you so much", "brilliant thanks",
                "lovely thanks", "wonderful thanks", "fantastic thanks",
                "nice one", "good one", "well done", "good job",
                "thanks chatbot", "thanks bot", "thank you chatbot",
                ANY message that combines a positive word (great, amazing, perfect, awesome,
                brilliant, wonderful, fantastic, excellent, lovely) with thanks/thank-you —
                even after a difficult exchange, classify as "thanks" not "disengagement".
                ABSOLUTE RULE: if the message's PRIMARY meaning is gratitude, classify as "thanks"
                regardless of prior conversation context, active topic, or whether the bot just
                asked a question. NEVER classify "thanks" / "thank you" as "followup" or
                "acknowledgement". Gratitude is ALWAYS "thanks".
- "farewell"  — saying goodbye: "bye", "goodbye", "see you", "take care", "ma'a salama"
- "identity"  — asking what/who the bot is: "who are you", "what are you", "are you a bot",
                "are you AI", "what is your name", "what can you do", "how do you work"
- "crisis"    — self-harm, suicidal, or extreme distress language:
                "shall I jump off the roof", "kill myself", "end my life", "want to die",
                "hurt myself", "life is not worth living", "I want to jump", "suicidal"
                IMPORTANT: classify as "crisis" even if framed as hypothetical. Safety first.
For all of the above, set domain="general" and sub_intent="general_inquiry".
These bypass the full pipeline and receive compassionate, context-appropriate responses.

- "Hi" / "Hello" → general/general_inquiry with message_kind="greeting", response_mode="context_acknowledgement"
- "How are you?" → general/general_inquiry with message_kind="howru", response_mode="context_acknowledgement"
- "Thanks" / "Thank you" → general/general_inquiry with message_kind="thanks", response_mode="context_acknowledgement"
- "Bye" → general/general_inquiry with message_kind="farewell", response_mode="context_acknowledgement"
- "Who are you?" → general/general_inquiry with message_kind="identity", response_mode="context_acknowledgement"
- "shall I jump off the roof" → general/general_inquiry with message_kind="crisis", response_mode="context_acknowledgement"
- "tell me a joke" → general/general_inquiry, message_kind="fresh_request", response_mode="graceful_recovery" (off-topic; mall cannot help)
- "say something funny" → general/general_inquiry, message_kind="fresh_request", response_mode="graceful_recovery"
- "tell me a riddle" → general/general_inquiry, message_kind="fresh_request", response_mode="graceful_recovery"
- "i am a bridesmaid" → general/general_inquiry, message_kind="context_setting", response_mode="context_acknowledgement", scenario="wedding_related"
- "i am a bridesmaid shopping for the wedding" → shopping/fashion_shopping, message_kind="context_setting", response_mode="guided_recommendation", scenario="wedding_related"
  RULE: when a context declaration is COMBINED with an explicit action intent ("shopping for", "looking for", "want to buy"),
  the domain is the ACTION's domain (shopping), not general. message_kind stays "context_setting".
  Compare: "i am a bridesmaid" (no action) → general/context_setting; "i am a bridesmaid shopping for the wedding" (has action) → shopping/context_setting.

WEDDING ROLE DECLARATIONS (context_setting turns that anchor domain to shopping):
- "i am the groom", "i'm the groom", "i am the best man", "i'm the best man",
  "i am a groomsman", "i'm a groomsman"
  → domain=shopping, sub_intent=general_shopping, message_kind=context_setting,
    response_mode=context_acknowledgement, scenario=wedding_related
- "i am the bride", "i'm the bride", "i am a bridesmaid" (without additional action request)
  → domain=shopping, sub_intent=fashion_shopping, message_kind=context_setting,
    response_mode=context_acknowledgement, scenario=wedding_related
These are context-setting turns that anchor the domain to shopping even without an explicit request.
Do NOT route wedding role declarations to domain=general — the occasion implies a shopping need.
The follow-up turns will inherit the shopping domain and wedding_related scenario correctly.
- "it's my anniversary" → general/general_inquiry, message_kind="context_setting", response_mode="context_acknowledgement", scenario="date"
- "we are in a hurry" → general/general_inquiry, message_kind="context_setting", scenario="quick_visit", response_mode="context_acknowledgement"
- "i'm in a hurry" → general/general_inquiry, message_kind="context_setting", scenario="quick_visit", response_mode="context_acknowledgement"
- "short on time" → general/general_inquiry, message_kind="context_setting", scenario="quick_visit", response_mode="context_acknowledgement"
- "we don't have much time" → general/general_inquiry, message_kind="context_setting", scenario="quick_visit", response_mode="context_acknowledgement"
- "food and movies" → entertainment/general_entertainment, message_kind="fresh_request", response_mode="hybrid_plan"
- "dinner and entertainment" → entertainment/general_entertainment, response_mode="hybrid_plan"
- "catch a movie and eat" → entertainment/general_entertainment, message_kind="fresh_request", response_mode="hybrid_plan"
- "movie then dinner" → entertainment/general_entertainment, message_kind="fresh_request", response_mode="hybrid_plan"
- "food and maybe movie also" → entertainment/general_entertainment, message_kind="fresh_request", response_mode="hybrid_plan"
- "we want to watch a movie and grab dinner" → entertainment/general_entertainment, message_kind="fresh_request", response_mode="hybrid_plan", secondary_intents=["add_dining_step"]
  RULE: for hybrid_plan queries combining movies + dining, always include "add_dining_step" in secondary_intents
  so the dining dimension is visible to downstream routing checks.
- "something quick before the movie" → dining/quick_bite, flow_type="concierge", response_mode="hybrid_plan" (before-movie dining = movie + food = hybrid)
- "something to eat before the film" → dining/quick_bite, flow_type="concierge", response_mode="hybrid_plan"
- "where can we eat after the movie?" → dining/general_dining, flow_type="concierge", response_mode="hybrid_plan" (post-movie dining = movie + food = hybrid)
- "dinner after the movie" → dining/general_dining, flow_type="concierge", response_mode="hybrid_plan"
- "show me movies" → entertainment/general_entertainment, flow_type="concierge", response_mode="guided_recommendation" (browse/discover — no schedule qualifier)
  CRITICAL DISTINCTION — movie intent routing:
  • flow_type="factual" + sub_intent="movie_showtime" ONLY when the query is an explicit SCHEDULE LOOKUP:
    "what movies are showing", "what's showing", "now showing", "what's playing",
    "what time does X start", "is X showing today", "what are the showtimes",
    "any kids movies today", "what movies are there?" — ALL of these want the CURRENT SCHEDULE → factual/movie_showtime
  • flow_type="concierge" + sub_intent="general_entertainment" when the query BROWSES or DISCOVERS films
    without asking for a schedule: "show me movies", "browse movies", "recommend a movie",
    "what good movies are on", "suggest a film" → concierge/guided_recommendation
  KEY TEST: if the user wants to SEE THE CURRENT LISTINGS (even phrased as "what movies are there?"),
  it is factual/movie_showtime — NOT concierge.  Only "show me movies" / "recommend a movie" without
  a schedule-lookup intent → concierge/general_entertainment.

MOVIE QUERIES — CRITICAL DISAMBIGUATION:
There are two distinct movie query types. Session context (family, companions, dining history) does NOT change this routing:

1. Schedule/showtimes lookup → entertainment/movie_showtime, flow_type="factual", response_mode="direct_factual"
   The user wants a LIST OF CURRENTLY PLAYING FILMS or SHOWTIME DATA.
   Examples: "what movies are showing?", "what's playing at the cinema?", "what films are on?",
   "now showing", "what's on?", "what movies are there?", "any kids movies today?",
   "what time does X play?", "what's showing?".
   Key test: the user is asking WHAT IS ON — current listings, schedule, or showtime data.

2. Browse/discover → entertainment/general_entertainment, flow_type="concierge", response_mode="guided_recommendation"
   The user wants to BROWSE or EXPLORE movie options — no schedule/listing intent.
   Examples: "show me movies", "browse movies", "suggest a movie", "recommend a film",
   "any good movies?", "what genre do you have?".
   Key test: the user wants SUGGESTIONS or to explore — NOT to look up what's currently screening.

ABSOLUTE RULE: "what movies are there?" → ALWAYS entertainment/movie_showtime, flow_type="factual",
response_mode="direct_factual" REGARDLESS of session context (family companions, prior dining queries,
or any other scene memory). Movie schedule queries are factual data lookups — session context cannot
convert a factual listing query into a guided_recommendation.

ABSOLUTE RULE: "show me movies" → ALWAYS entertainment/general_entertainment, flow_type="concierge",
response_mode="guided_recommendation" (browsing intent, not a schedule lookup).

IN-SESSION MOVIE DISAMBIGUATION (applies even when topic_lock=movie_lookup is active):
The lookup vs. browse distinction is based on query intent, NOT session state.
- "show me movies", "browse movies", "suggest a movie", "any good movies?" within an active movie session
  → STILL entertainment/general_entertainment, flow_type="concierge", response_mode="guided_recommendation"
  The session lock does NOT convert a browsing query into a factual schedule lookup.
- "what's showing?", "what movies are on?", "what's playing?" within an active movie session
  → entertainment/movie_showtime, flow_type="factual", response_mode="direct_factual" (schedule lookup intent)

"WITH KID" CONSTRAINT IN MOVIE SESSION:
When the active topic is movie_lookup (topic_lock=movie_lookup or active_topic=entertainment) and the user
adds "with kid", "for my kid", "kid-friendly movies", "for children" — this is a CONSTRAINT REFINEMENT
on the existing movie query, NOT a companion scene declaration:
- "with kid" (active_topic=entertainment/movie session) → entertainment/movie_showtime,
  message_kind="constraint_refinement", flow_type="factual", response_mode="direct_factual"
  The user wants to filter the movie list to kid-appropriate films.
- Do NOT classify "with kid" in a movie session as context_setting or guided_recommendation.
  The kid context is a filter on the existing factual query, not a new companion addition.

- "Where can I eat?" → dining/general_dining
- "Show me all the shopping offers" → shopping/offer_details
- "What offers are going on in Zara?" → shopping/offer_details
- "Any deals or discounts?" → shopping/offer_details
- "What perfume stores do you have?" → shopping/perfume_shopping
- "I wanna eat junk food, a lot of unhealthy junk food" → dining/general_dining, message_kind="fresh_request" (explicit craving — NEVER disengagement)
- "just give me food" → dining/general_dining, message_kind="fresh_request"
- "i am happy" → general/general_inquiry, message_kind="emotional" (positive mood — NOT acknowledgement)
- "i am just happy about my life" → general/general_inquiry, message_kind="emotional"
- "I want to watch horror movies?" → entertainment/general_entertainment, message_kind="topic_switch" (domain switch from dining)
- "i also wanna buy a jacket" → shopping/general_shopping, message_kind="topic_switch" (domain switch from dining)
- "something warmer" (after jacket suggestions) → shopping/general_shopping, message_kind="constraint_refinement"
- "ok" (after bot suggested desserts, no offer/question from bot) → general/general_inquiry, message_kind="acknowledgement", sub_intent="general_inquiry"
- "oki" → general/general_inquiry, message_kind="acknowledgement", sub_intent="general_inquiry"
- "What movie genre do I have?" → entertainment/movie_showtime, message_kind="clarification_request", response_mode="clarification_request" (bot cannot see the visitor's booking; should ask what genre they want)
- "what genre is my movie" → entertainment/movie_showtime, message_kind="clarification_request", response_mode="clarification_request"
- "where is herfy" → navigation/location_query, flow_type="factual" (restaurant location — NOT service_info)
- "where is herfa" → navigation/location_query, flow_type="factual" (misspelling of Herfy — still a restaurant)
- "where is McDonald's" → navigation/location_query, flow_type="factual"

CONFIDENCE CALIBRATION:
The "confidence" field reflects HOW CLEARLY YOU UNDERSTAND THE INTENT, not how specific the request is.
A vague but categorisable request is still high confidence when the domain and intent are unambiguous.
Use these guidelines:
- confidence=0.85–0.95 (high): Domain and intent are clear, even if the request is open-ended.
  Examples:
  • "i want to buy a gift"       → 0.85  (intent clear: gift shopping; openness ≠ ambiguity)
  • "what does this mall have?"  → 0.90  (intent clear: mall overview)
  • "with kid" (context add-on)  → 0.85  (context unambiguous given active conversation)
  • "where is Starbucks?"        → 0.95  (clear factual lookup)
  • "I want sushi for my wife's birthday" → 0.95  (clear intent + rich context)
- confidence=0.65–0.84 (medium): Intent is clear but genuinely incomplete — e.g. a fresh shopping
  request for a broad product category with no companions, no occasion, no target person stated.
  A single clarifying question would materially improve the result.
  Examples:
  • "I want to buy jackets" (no context at all) → 0.65
  • "show me shoes" (no context) → 0.65
  • "where can I eat?" (no constraint) → 0.70
  • "what can I do here?" (first turn, no context) → 0.65
- confidence<0.5 (low): Domain or primary intent cannot be reliably determined.
  Examples:
  • "anything good?"             → 0.40  (domain unknown → genuinely low)
  • "asdf"                       → 0.10  (gibberish → low)
CRITICAL RULE: Only score below 0.75 when you cannot reliably determine the domain or primary intent.
Openness of a request (broad category, no specific item) does NOT justify a low score when the
domain and intent are already clear.

RETRIEVAL DECISION RULES (retrieval_needed field):
Set retrieval_needed=true when the turn genuinely requires a live data lookup to answer well:
- flow_type="factual": ALWAYS set retrieval_needed=true (exact data lookups — showtimes, hours, locations, brand presence)
- flow_type="concierge" with a SPECIFIC constraint that requires live data (e.g. "restaurants near the cinema",
  "something open right now", "stores on level 2", "what's closest to entrance") → true
- flow_type="concierge" where the answer requires knowing CURRENT availability or SPECIFIC entity details
  (e.g. "do any restaurants here have outdoor seating?", "which shops have sale right now?") → true
- flow_type="concierge" AND secondary_intents include any audience/context filter
  (kid_friendly, family_filter, gift_for, budget_filter, romantic_filter, quick_filter,
  proximity_filter, group_filter) → true
  Rationale: when a filter is active the shortlist quality depends on live entity data.
  The initial response_mode hint may be upgraded downstream — do not gate retrieval on it.
  Examples: "any activities for the kids?" (kid_friendly) → true;
  "something nice for my son" (kid_friendly/gift_for) → true;
  "something affordable for dinner" (budget_filter) → true;
  "a romantic restaurant for tonight" (romantic_filter) → true;
  "something quick near the cinema" (quick_filter + proximity_filter) → true
- flow_type="concierge" with sub_intent in (gift_recommendation, romantic_dining, general_shopping)
  AND a specific target person, companion, or occasion is detectable (from current query OR scene context):
  → true. Rationale: personalised gift/romantic/shopping queries need live entity data to produce
  a confident guided_recommendation; pre-loaded context alone is insufficient.
  Examples:
  • "something for my son" (kid target) → true
  • "gift ideas for my girlfriend" (companion/target) → true
  • "any romantic options here?" (romantic occasion in query itself) → true
  • "romantic restaurants for tonight" → true
  • "I want to buy a gift for someone special" → true
Set retrieval_needed=false when general category suggestions from the mall's canonical context are sufficient:
- General recommendation requests with no specific constraint AND no audience filter: "suggest a restaurant",
  "what can I eat", "recommend some stores", "anything good to do", "where can we shop" → false
- Context-setting, companion declarations, acknowledgement, and off-topic turns → always false
- Vague exploratory requests with no audience filter ("something interesting", "I'm bored") → false
DEFAULT: false (most concierge recommendation turns do not need live retrieval)

COMPANION CONTEXT RULES (companion_context field):
Extract companions mentioned in THIS turn's message. Populate on ANY flow type (including factual).
Use these companion labels (matching scene memory vocabulary):
  child, kids, son, daughter, wife, husband, girlfriend, boyfriend, family, friends, solo
Examples:
  "with kid" → ["child"]
  "with my kids" → ["kids"]
  "for my 5 year old son" → ["child"]
  "with my wife and daughter" → ["wife", "daughter"]
  "with my girlfriend" → ["girlfriend"]
  "me and my husband" → ["husband"]
  "with friends" → ["friends"]
  "I'm alone" / "just me" / "solo" → ["solo"]
Leave companion_context=[] when no companion is mentioned in THIS specific turn (even if companions exist in scene memory).
Do NOT carry forward companions from scene memory — only extract what is explicitly stated THIS turn.

TOPIC LOCK RELEASE RULES (releases_topic_lock field):
The "Active topic lock" value (if provided in context) represents the domain the conversation has been locked to.
Set releases_topic_lock=true when the current message is clearly in a DIFFERENT domain from the active topic lock:
- topic_lock="movie_lookup" + user asks "something quick for lunch" → true (dining ≠ movies)
- topic_lock="movie_lookup" + user asks "any restaurants?" → true
- topic_lock="movie_lookup" + user asks "I want to buy a jacket" → true
- topic_lock="dining_recommendation" + user asks "show me movies" → true
- topic_lock="shopping_recommendation" + user asks "where can I eat?" → true
IMPORTANT: releases_topic_lock fires for ANY genuine domain shift on a fresh_request,
even without explicit transition words. Key test: if you are classifying this turn as
dining/shopping/entertainment AND the active_topic_lock is for a DIFFERENT domain,
set releases_topic_lock=true. Do not require "instead" or "forget that" phrasing.
Example: topic_lock=movie_lookup + user says "something quick for lunch" → releases_topic_lock=true
(dining intent ≠ entertainment domain — domain shift is enough, no keyword needed).

Set releases_topic_lock=false when the turn stays in or alongside the locked domain:
- Follow-up, refinement, or constraint on the same domain → false
- Context-setting that adds scene info without switching domain → false
- A companion declaration while in movie/factual flow → false (companion is additive, not a domain switch)
- No active topic lock → false (nothing to release)
DEFAULT: false
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
            max_tokens=450,
        )
    return _classifier_llm


@traced_node("interpret_turn")
async def interpret_turn(state: ConciergeState) -> dict:
    raw_msg = state.normalized_user_message
    msg = raw_msg  # No pre-normalization; LLM handles all phrasing variants

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
    if is_likely_unsupported(raw_msg):
        intent = InterpretedIntent(
            domain="general",
            sub_intent="general_inquiry",
            message_kind="fresh_request",
            confidence=0.1,
            raw_signals={"classifier_source": "unsupported_detector", "unsupported": True},
            primary_intent="unsupported",
            flow_type_candidate="concierge",
        )
        interp_contract = {
            "primary_intent": "unsupported",
            "scenario": "",
            "modifiers": [],
            "message_kind": "fresh_request",
            "flow_type": "concierge",
            "active_topic": "",
            "normalized_query": msg,
            "canonical_query_pattern": "",
            "topic_lock": "",
            "topic_lock_confidence": 0.0,
            "fact_scope": "",
        }
        return {
            "intent": intent,
            "dominant_context_type": "general",
            "debug_enrichment": DebugEnrichment(
                interpretation_contract=interp_contract,
                primary_intent=intent.primary_intent,
                normalized_query=msg,
                canonical_query_pattern="",
            ),
            "_trace_summary": "Intent[unsupported]: trivially empty/single-char input.",
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
            flow_type_candidate="concierge",
        )
        intent.raw_signals["classifier_source"] = "error_fallback"

    # ── LLM-driven gibberish detection ────────────────────────────────
    if intent.is_gibberish:
        intent.primary_intent = "unsupported"
        intent.confidence = 0.1
        intent.raw_signals["unsupported"] = True
        logger.info("LLM flagged input as gibberish: %r", raw_msg)

    # ── Flow type from LLM ────────────────────────────────────────────
    flow_candidate = intent.flow_type_candidate  # set by _llm_classify

    # Force concierge for context-setting/acknowledgement/companion-correction turns
    if intent.message_kind in ("context_setting", "companion_correction", "acknowledgement"):
        flow_candidate = "concierge"

    # ── Fact scope / entity type: deterministic lookup from sub_intent ──
    fact_scope = ""
    fact_entity_type = ""
    if flow_candidate == "factual" or intent.domain == "cross_mall":
        if intent.domain == "cross_mall" or intent.sub_intent == "cross_mall_search":
            fact_scope, fact_entity_type = "cross_mall_availability", "brand"
        else:
            fact_scope, fact_entity_type = _SUB_INTENT_TO_FACT_SCOPE.get(
                intent.sub_intent, ("", "")
            )

    # ── Scenario: from LLM output ─────────────────────────────────────
    scenario = intent.raw_signals.get("scenario", "")

    # ── Primary intent and dominant context type: deterministic maps ──
    key = f"{intent.domain}/{intent.sub_intent}"
    primary_intent = _PRIMARY_INTENT_MAP.get(key, "") or intent.domain or "general"
    dominant_ctx = _DOMAIN_CONTEXT_TYPE.get(intent.domain, "general")

    # LLM secondary_intents and modifiers are used directly
    secondary_intents: list[str] = list(intent.secondary_intents)
    modifiers: list[str] = list(intent.modifiers)

    # ── Topic lock confidence adjustment ─────────────────────────────
    topic_lock = state.scene.topic_lock
    topic_lock_confidence = state.scene.topic_lock_confidence
    if topic_lock:
        if intent.message_kind in ("followup", "refinement", "constraint_refinement"):
            topic_lock_confidence = min(1.0, topic_lock_confidence + 0.1)
        elif intent.message_kind in ("topic_switch", "fresh_request") and primary_intent:
            topic_lock_confidence = max(0.0, topic_lock_confidence - 0.3)

    # ── Set derived fields on intent ──────────────────────────────────
    intent.flow_type_candidate = flow_candidate
    intent.fact_scope_candidate = fact_scope
    intent.fact_entity_type_candidate = fact_entity_type
    intent.primary_intent = primary_intent
    intent.secondary_intents = secondary_intents
    intent.modifiers = modifiers

    interpretation_contract = {
        "primary_intent": primary_intent,
        "scenario": scenario,
        "modifiers": modifiers,
        "message_kind": intent.message_kind,
        "flow_type": flow_candidate or "tbd",
        "active_topic": scene_ctx.get("active_topic", ""),
        "normalized_query": msg,
        "canonical_query_pattern": "",
        "topic_lock": topic_lock,
        "topic_lock_confidence": round(topic_lock_confidence, 2),
        "fact_scope": fact_scope,
        "classifier_source": intent.raw_signals.get("classifier_source", "llm"),
    }
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
            canonical_query_pattern="",
            topic_lock=topic_lock,
            topic_lock_confidence=round(topic_lock_confidence, 2),
        ),
        "_trace_summary": (
            f"Intent[{classifier_source}]: "
            f"{intent.domain}/{intent.sub_intent} ({intent.message_kind}) "
            f"primary={primary_intent} secondary={secondary_intents} "
            f"flow={flow_candidate or 'tbd'} scenario={scenario or 'none'} "
            f"norm={msg!r}"
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

    # Active shopping task
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

    # Visit plan context
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

    # Recent conversation history
    recent_msgs = [m for m in state.messages if m.role in ("user", "assistant")][-6:]
    if recent_msgs:
        dialogue_lines = [
            f"  {m.role.capitalize()}: {m.content[:150]}"
            for m in recent_msgs
        ]
        context_parts.append("Recent conversation (most recent last):\n" + "\n".join(dialogue_lines))

    # Recent mood state
    if state.scene.recent_mood:
        context_parts.append(
            f"Visitor's recent emotional state: {state.scene.recent_mood}. "
            f"IMPORTANT: This is historical context only — do NOT use it to "
            f"re-classify genuine TOPIC requests or positive expressions. "
            f"If the current message is a genuine topic request "
            f"(e.g. 'food', 'movies', 'shopping'), a positive expression "
            f"(e.g. 'great', 'thanks', 'perfect'), or any real new topic, classify "
            f"it normally (fresh_request, thanks, followup, etc.). "
            f"EXCEPTION: Mood-driven requests that directly follow an emotional state "
            f"('cheer me up', 'make me happy', 'I need cheering up', 'lift my spirits', "
            f"'I need a pick-me-up') ARE still emotional — do NOT classify as fresh_request. "
            f"Only classify as 'disengagement' if the CURRENT message itself is "
            f"frustrated or resigned (e.g. 'nevermind', 'forget it', 'doesn't matter')."
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
    flow_type = parsed.get("flow_type", "concierge")
    scenario = str(parsed.get("scenario", "") or "")
    response_mode_hint = str(parsed.get("response_mode", "") or "")

    if domain not in VALID_DOMAINS:
        domain = "general"
    if sub_intent not in VALID_SUB_INTENTS:
        sub_intent = "general_inquiry"

    valid_kinds = {
        "fresh_request", "correction", "refinement", "constraint_refinement",
        "topic_switch", "followup", "context_setting",
        "disengagement", "category_negation", "emotional",
        "acknowledgement", "companion_correction",
        "greeting", "howru", "thanks", "farewell", "identity", "crisis",
    }
    if message_kind not in valid_kinds:
        message_kind = "fresh_request"

    if flow_type not in ("factual", "concierge"):
        flow_type = "concierge"

    # Guard: prevent positive-mood messages being misfired as disengagement.
    # Despite explicit prompt guidance, the LLM occasionally classifies "I'm just
    # happy about my life" or similar as disengagement when recent_mood is set.
    # Detect the misfire deterministically: if the LLM returned disengagement but
    # the message contains positive sentiment with no frustration signals, reclassify
    # to emotional so the response path handles it with warmth, not an apology.
    _POSITIVE_SIGNALS = frozenset({
        "happy", "great", "good", "excited", "glad", "love", "enjoy",
        "wonderful", "fantastic", "fine", "pleased", "content", "joy",
    })
    _FRUSTRATION_SIGNALS = frozenset({
        "pissed", "angry", "furious", "frustrated", "useless", "stop",
        "forget it", "nevermind", "not working", "doesn't matter",
        "give up", "done with", "i give up",
    })
    if message_kind == "disengagement":
        _msg_lower = msg.lower()
        _has_positive = any(w in _msg_lower for w in _POSITIVE_SIGNALS)
        _has_frustration = any(w in _msg_lower for w in _FRUSTRATION_SIGNALS)
        if _has_positive and not _has_frustration:
            message_kind = "emotional"
            domain = "general"
            sub_intent = "general_inquiry"

    # Guard: continuation kinds require an established topic.
    _CONTINUATION_KINDS = {"followup", "refinement", "constraint_refinement"}
    if (
        message_kind in _CONTINUATION_KINDS
        and not state.scene.active_topic
        and state.last_intent in ("", "general")
    ):
        message_kind = "fresh_request"

    # Parse scene_corrections
    raw_corrections = parsed.get("scene_corrections", [])
    scene_corrections: list[str] = (
        [str(c) for c in raw_corrections if isinstance(c, str)]
        if isinstance(raw_corrections, list)
        else []
    )

    # Parse secondary_intents and modifiers — LLM-reasoned from full context
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

    _VALID_RESPONSE_MODES = {
        "direct_factual", "guided_recommendation", "hybrid_plan",
        "best_effort_shortlist", "context_acknowledgement",
        "graceful_recovery", "clarification_request", "",
    }
    if response_mode_hint not in _VALID_RESPONSE_MODES:
        response_mode_hint = ""

    # LLM-extracted entity — strip whitespace and punctuation only
    entity_query = str(parsed.get("entity_query", "") or "").strip(" ?.,!")

    # New LLM-driven pipeline signals
    retrieval_needed = bool(parsed.get("retrieval_needed", False))

    raw_companion_context = parsed.get("companion_context", [])
    companion_context: list[str] = (
        [str(c) for c in raw_companion_context if isinstance(c, str)]
        if isinstance(raw_companion_context, list)
        else []
    )

    releases_topic_lock = bool(parsed.get("releases_topic_lock", False))

    intent = InterpretedIntent(
        domain=domain,
        sub_intent=sub_intent,
        message_kind=message_kind,
        confidence=confidence,
        scene_corrections=scene_corrections,
        secondary_intents=llm_secondary_intents,
        modifiers=llm_modifiers,
        entity_query=entity_query,
        flow_type_candidate=flow_type,
        response_mode_hint=response_mode_hint,
        is_gibberish=is_gibberish,
        retrieval_needed=retrieval_needed,
        companion_context=companion_context,
        releases_topic_lock=releases_topic_lock,
    )
    intent.raw_signals["scenario"] = scenario
    return intent
