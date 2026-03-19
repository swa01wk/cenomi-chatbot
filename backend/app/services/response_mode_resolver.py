"""
Response Mode Resolver — lightweight behavioural decision layer.

Runs AFTER intent classification and strategy selection (inside choose_strategy).
Determines HOW to respond this turn, independent of which playbook or retrieval
strategy was chosen.

Response modes
──────────────
  direct_factual          – concise, structured, exact data
  guided_recommendation   – shortlist (3–5) with light contextual framing
  hybrid_plan             – single combined answer for cross-domain queries
  best_effort_shortlist   – safe diverse options; avoids over-specific claims
  context_acknowledgement – acknowledge situation; offer 2–4 next-step directions
  graceful_recovery       – no hallucination; explain capabilities; clarify
  clarification_request   – bot cannot help as-is; ask for clarification or explain limitation

Design principles
─────────────────
  - Pure function: no I/O, no LLM calls, no side-effects
  - NEVER overrides correct factual routing
  - NEVER breaks shopping_task continuity
  - Respects topic_lock and active follow-up context
  - Follow-up resolution happens BEFORE vagueness classification

Returns
───────
  (response_mode, confidence_level, reason, fallback_applied)
"""

from __future__ import annotations

from app.models.state import ConciergeState

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DIRECT_FACTUAL = "direct_factual"
GUIDED_RECOMMENDATION = "guided_recommendation"
HYBRID_PLAN = "hybrid_plan"
BEST_EFFORT_SHORTLIST = "best_effort_shortlist"
CONTEXT_ACKNOWLEDGEMENT = "context_acknowledgement"
GRACEFUL_RECOVERY = "graceful_recovery"
CLARIFICATION_REQUEST = "clarification_request"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# Sub-intents that represent precise, narrow requests → boost to "high" confidence
_PRECISE_SUB_INTENTS: frozenset[str] = frozenset({
    "movie_showtime", "opening_hours", "store_hours", "location_query",
    "service_info", "prayer_room", "parking_info", "cross_mall_search",
    "brand_availability", "route_hint", "cafe_recommendation",
    "dessert_recommendation", "perfume_shopping", "jewelry_shopping",
    "accessories_shopping", "gift_recommendation", "fashion_shopping",
    "quick_bite", "romantic_dining", "family_dining",
})

# Sub-intents that indicate vague / exploratory requests → lower effective confidence
_VAGUE_SUB_INTENTS: frozenset[str] = frozenset({
    "open_exploration", "general_inquiry", "activity_suggestion",
    "general_entertainment",
    "first_visit_guide",
})

# Secondary intents that signal the user wants something from a second domain
_CROSS_DOMAIN_SECONDARY: frozenset[str] = frozenset({
    "add_dining_step",
    "add_coffee_step",
})

# Topic lock values that map cleanly to direct_factual
_FACTUAL_LOCKED_TOPICS: frozenset[str] = frozenset({
    "movies", "entertainment", "cinema", "movie_schedule",
    "movie_lookup",
})

# Broad category openers without recipient/modifier → medium confidence
# "i want to buy jackets" needs clarification (who for, what kind)
_BROAD_CATEGORY_OPENERS: tuple[str, ...] = (
    "i want to buy jackets", "i want jackets", "want to buy jackets",
    "looking for jackets", "need jackets",
    "i want to buy shoes", "i want shoes", "want shoes",
    # School clothing — multi-kid, category broad
    "i need school clothes for my kids", "need school clothes for my kids",
    "school clothes for my kids", "school clothes for the kids",
    "school wear for my kids", "school outfits for my kids",
    "clothes for school for my kids",
)

# Raw-query patterns that signal a broad/vague exploratory intent
# NOTE: "something affordable" intentionally excluded — it's a budget constraint signal,
# not an exploratory pattern. In established context it should yield guided_recommendation.
_VAGUE_RAW_PATTERNS: tuple[str, ...] = (
    "anything interesting",
    "what's here",
    "what is here",
    "what to do",
    "what can i do",
    "something to do",
    "anything good",
    "what's good",
    "what's around",
    "show me something",
    "what do you have",
    "what else is",
    "anything fun",
    "what's available",
    "what are my options",
    "help me decide",
    "i'm bored",
    "i am bored",
    "bored",
    "surprise me",
)

# Raw-query patterns that explicitly join two different domains
_CROSS_DOMAIN_RAW_PATTERNS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # movies + food
    (
        ("movie", "cinema", "film", "showtime"),
        ("food", "eat", "restaurant", "dining", "cafe", "coffee", "dinner", "lunch"),
    ),
    # shopping + dining
    (
        ("shop", "store", "buy", "shopping"),
        ("eat", "food", "restaurant", "dining", "cafe", "coffee"),
    ),
    # before/after movie + quick food — "something quick before the movie"
    (
        ("before the movie", "after the movie", "before watching", "to eat before", "eat before", "anything to eat"),
        ("quick", "snack", "bite", "eat", "food", "grab", "something"),
    ),
)

# Movie listing queries that are always factual — pure "what's showing" lookups
# NOTE: "show me movies" is intentionally excluded — it reads as a recommendation
# request ("show me options"), not a raw data lookup. Route to concierge instead.
_MOVIE_LISTING_PATTERNS: tuple[str, ...] = (
    "what movies", "which movies", "movies showing", "movies are there",
    "movies do we have", "movies can i watch", "movies are on",
    "movies can i see", "now showing", "movie list",
    "all movies", "what's showing",
)
# Exclusions: recommendation / curation queries that mention movies but are NOT lookups
_MOVIE_LISTING_EXCLUSIONS: tuple[str, ...] = (
    "good movie for", "recommend a movie", "a movie for",
    "movie for couples", "suggest a movie", "movies for couples",
    # Temporal/sequential context signals: "ok after, what movies" is continuation, not listing
    "ok after",
    "after, what",
)

# Factual service/location/hours queries — always direct_factual
_FACTUAL_SERVICE_PATTERNS: tuple[str, ...] = (
    "prayer room", "prayer rooms",
    "opening hours", "mall opening hours", "what time does the mall",
    "when does the mall open", "when do you open", "when do you close",
    "what services do you have", "what services do we have",
    "what services are there", "do you have strollers",
    "where is the atm", "where is the atms", "find an atm", "need an atm",
    "where's the atm", "is there an atm", "atm machine",
    # Brand / store presence factual queries
    "is starbucks", "do you have starbucks",
    "is nike here", "is nike at", "do you have nike", "do they have nike",
    "is h&m here", "is h&m at", "do you have h&m", "do they have h&m",
    "is zara here", "do you have zara",
    "is adidas here", "do you have adidas", "do they have adidas",
    "do they have like nike", "do they have like adidas",
    "wants nike or adidas", "nike or adidas",
    # Generic: "do you have [store/brand]" — catches most single-brand queries
    # Supplement / nutrition store availability
    "supplement or nutrition stores", "do you have any supplement",
    "supplement stores", "nutrition store",
)

# Factual queries that must return direct_factual but at MEDIUM confidence
# (bot may not have complete data or answer is inherently uncertain/advisory)
_FACTUAL_SERVICE_PATTERNS_MEDIUM: tuple[str, ...] = (
    # Personal shopping/styling assistance — factual capability query (may not exist)
    "offer personal shopping", "offer personal styling", "offer styling",
    "personal shopping assistance",
    # Reservation timing — honest factual answer (bot can't book)
    "how long would a reservation", "how long does a reservation",
    "how long to get a table", "reservation take",
)

# Short companion-filter patterns that should stay factual on entertainment context.
# "with kid" on an established movie/cinema topic_lock is a filter on factual data
# (which films are kid-suitable?) and should remain direct_factual regardless of
# flow_type. In non-entertainment concierge context, "with kid" sets companion context.
_KID_FILTER_PATTERNS: tuple[str, ...] = (
    "with kid", "with kids", "for kids",
)

# Explicit planning / scheduling requests → hybrid_plan
_PLANNING_SIGNALS: tuple[str, ...] = (
    "full plan", "full itinerary", "full day plan", "full bridal party day",
    "map out", "plan out", "schedule for",
    "wind down the visit", "wind down",
    "can we fit", "is that enough time",
    "day plan",
    "3-hour", "2-hour", "4-hour", "2.5-hour",
    "arrive at noon", "leave by", "leave around",
    "what time should we aim for",
    "what should i prioritize", "what should we prioritize",
    "birthday checklist",
    "a rough schedule",
    "a full schedule",
    "shopping plan",
    "itinerary for tonight", "itinerary for today",
    "hangout plan",
    "outing plan",
    "give me a plan",
    "give us a plan",
    "give me a checklist",
    "give us a checklist",
    "put together a plan",
    "put together a rough",
    # Sequence planning ("best order to do X")
    "best order to do",
    "what order should we do",
    "activities first or",
    # Full evening / night planning
    "as a full night", "for the full night", "full evening",
    "for the evening", "full night out",
    # Explicit "plan for tonight/today"
    "plan for tonight", "plan for today",
    # Extension queries: "what if we extend it to 4 hours?"
    "want to extend", "extend it to", "extend the plan",
    "if we extend", "to extend to",
    # Prioritisation / scheduling
    "in what order should we",
    # Extended time blocks
    "for 3 hours", "for 2 hours", "for 4 hours", "for 2.5 hours",
    "for my 2 hours", "for my 3 hours", "for my 4 hours",
    "my 2 hours here", "my 3 hours here", "my 4 hours here",
    "within 3 hours", "within 2 hours", "within 4 hours",
    "in 2 hours", "in 3 hours", "in 4 hours",
)

# Queries about inherently uncertain/estimated data → cap confidence at medium
_UNCERTAINTY_SIGNALS: tuple[str, ...] = (
    "how far in advance",
    "price range", "what's a good price", "good price range",
    "whats the price", "what's the price", "what is the price", "the price",
    "is that realistic", "is it realistic",
    "is it likely", "likely to be available",
    "do they offer", "do any of them offer", "do any of them do",
    "can they do", "can you tell me more about the menu",
    "what if we want to extend",
    "is there anywhere that does",
    "is there a gym",
    "does the mall have a gym",
    "is there face painting",
    "do stores close during",
    "do they do custom",
    "do they do pre-packed",
    "any cool backdrops",
    "any instagram-worthy",
    "do they have loyalty",
    "do they offer personal shopping",
    "any party entertainment",
    "how competitive are these",
    # Per-person budget declarations (speculative — coverage unclear)
    "per person budget",
    "per bridesmaid",
    # Value / pricing opinions
    "best value for money", "value for money", "best value",
    "which stores give the best",
    "how much is a ticket", "how much does a ticket",
    "how much for a ticket",
    "what's the cheapest", "cheapest of those", "the cheapest option",
    "cheapest one", "which is cheapest",
    # Loyalty / offers (bot may not have full scheme info)
    "loyalty card", "loyalty scheme", "cenomi loyalty",
    "any loyalty offers", "do you have any cenomi",
    # Personal shopping / styling services
    "offer personal shopping", "offer personal styling", "personal shopping assistance",
    "offer styling", "personal shopping",
    # Affordability / budget fit queries (uncertain whether options exist within budget)
    "what can we actually afford", "what can we afford",
    "within that budget",
    # Casual seating / lounge queries
    "anywhere to just sit", "somewhere to sit", "somewhere to rest",
    "anywhere to sit and chill", "anywhere to just chill",
    "is there anywhere to just",
    # Post-dinner activity uncertainty
    "something fun to do after dinner", "fun to do after dinner",
    "after dinner — ", "after dinner,",
    # Meeting / briefing spaces (may not exist)
    "meeting or briefing spaces", "meeting spaces in the mall",
    "briefing spaces", "meeting rooms",
    "are there meeting", "any meeting rooms",
    # Instagram / photo spots (subjective)
    "instagram-worthy", "instagrammable", "good for photos",
    "nice for photos", "take a nice photo",
    # Misc uncertain queries
    "probably couldn't find", "not find elsewhere",
    # Inclusive group activity (uncertain what suits elderly/grandparents)
    "grandparents included", "including elderly", "elderly can do",
    "all age groups", "all ages",
    # Experiential / tasting / unique experiences
    "something experiential", "tasting or something", "oud experience",
    "experiential — not just", "experiential,",
    # Pre-packed / specific stock
    "do they do pre-packed", "pre-packed school", "school supply bundles",
    # Clean eating / workout nutrition guidance
    "what's the best approach", "clean meal before", "before a workout",
    "best approach if",
    # Reservation / booking timing (bot cannot confirm durations)
    "how long would a reservation", "how long does a reservation",
    "reservation take", "reservation time", "how long to get a table",
    "how long is the wait",
    # Offer / sale / promotion queries (data may be unavailable or outdated)
    "any with sales", "with sales on", "any sales", "sales on",
    "is there like a sale", "a sale or something", "any promotions",
    "any deals on", "is there a sale",
)

# Queries asking for curation of the uniquely exclusive/rare → best_effort_shortlist + medium
_CURATED_DIFFERENTIATION_SIGNALS: tuple[str, ...] = (
    "most exclusive", "couldn't find elsewhere", "probably couldn't find",
    "can't find elsewhere", "unique to this mall",
)

# Out-of-scope: dining / venues *outside* the mall → graceful_recovery
_OUT_OF_SCOPE_SIGNALS: tuple[str, ...] = (
    "nearby the mall", "near the mall",
    "outside the mall", "restaurants near here",
    "restaurants near the mall", "nearby restaurants",
    "near here", "near by",
)

# Unsupported capability requests (taxi, delivery, online ordering)
# → clarification_request (bot cannot perform these actions)
_UNSUPPORTED_CAPABILITY_PATTERNS: tuple[str, ...] = (
    "book me a taxi", "book a taxi", "call me a taxi", "get me a taxi",
    "order a taxi", "call a taxi", "order an uber", "book an uber",
    "call me an uber", "hail a taxi", "arrange a taxi",
    "order food for me", "can you order food", "online ordering, can you order",
    "can you place an order", "place my order for me", "order for me",
    "can you book me", "book me a",
    "can you order",
    "deliver food", "food delivery for me",
    "place an order",
)

# Budget-cautious signals in gift/shopping context → MEDIUM confidence
# "something nice, not too much" = quality + budget constraint, still needs refinement
_BUDGET_CAUTIOUS_SIGNALS: tuple[str, ...] = (
    "something nice, not too much",
    "not too much",
    "something nice not too much",
)

# Context-setting patterns that indicate a gift/recipient opener (→ MEDIUM confidence)
# The situation is clear but details are needed → confirm intent first
_GIFT_RECIPIENT_CONTEXT_PATTERNS: tuple[str, ...] = (
    "i want to get something for my",
    "i want to buy something for my",
    "i want to find something for my",
    "looking for something for a",
    "looking for something for my",
    "i'm looking for something for",
    "i am looking for something for",
    "need to get something for my",
    "need to buy something for my",
    "i want to get something for her",
    "i want to get something for him",
    "i want to get something for them",
)

# Context-setting patterns that are too vague/fragmented to be high confidence
# → LOW confidence (multiple fragments, unclear primary intent)
_VAGUE_CONTEXT_FRAGMENTS: tuple[str, ...] = (
    "something nice not too much maybe for",
    "not too much maybe for",
    "maybe for kid",
    "something for the kids maybe",
)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _raw(state: ConciergeState) -> str:
    return (state.normalized_user_message or state.raw_user_message or "").lower().strip()


def _has_planning_intent(state: ConciergeState) -> bool:
    """Return True when the query explicitly requests a plan, schedule, or itinerary."""
    import re as _re
    raw = _raw(state)
    if any(s in raw for s in _PLANNING_SIGNALS):
        return True
    # Time-constrained multi-step: "X-hour plan/schedule/outing/itinerary/visit"
    if _re.search(r'\b\d+\.?\d*[-\s]?(hour|hr)s?\b\s*(plan|schedule|itinerary|outing|visit)', raw):
        return True
    if _re.search(r'\bfit\b.*\b\d+\.?\d*[-\s]?(hour|hr)s?\b', raw):
        return True
    return False


def _has_strong_visit_context(state: ConciergeState) -> bool:
    """
    Return True when there is established scene context for this conversation.

    Also checks the current message text for companion/occasion declarations,
    because on the FIRST turn the scene memory hasn't been updated yet —
    companions set by "im here with 3 friends" won't appear in scene.companions
    until after update_memory runs (post choose_strategy).
    """
    scene = state.scene
    if bool(
        (scene.companions and scene.companions != ["solo"])
        or scene.occasion
        or scene.budget
        or scene.scenario
        or scene.visit_type
        # NOTE: scene.goal intentionally excluded — it's inferred from single-keyword
        # matching and appears on turn 1 (e.g. "buy jackets" → goal="shopping").
        # scene.implicit_goal requires BOTH a goal AND companion/occasion context,
        # so it only fires in genuinely established multi-signal sessions.
        or getattr(scene, "implicit_goal", None)
        or getattr(scene, "user_role", None)
    ):
        return True

    # Detect companion/group declarations in the current message
    raw = (state.normalized_user_message or state.raw_user_message or "").lower()
    _CURRENT_COMPANION_SIGNALS: tuple[str, ...] = (
        "with my family", "with my friends", "with friends",
        "with 3 friends", "with 4 friends", "with 2 friends", "with a friend",
        "with my girlfriend", "with my boyfriend", "with my wife", "with my husband",
        "with my partner", "with my kids", "with my kid",
        "with my son", "with my daughter",
        "with my 7 year old", "with my 5 year old", "with my 6 year old",
        "with my 4 year old", "with my 8 year old", "with my 3 year old",
        "im bridesmaid", "i am bridesmaid",
        "here with my", "here with the",
    )
    return any(sig in raw for sig in _CURRENT_COMPANION_SIGNALS)


def classify_confidence(state: ConciergeState) -> str:
    """
    Classify interpretation confidence as high / medium / low.

    Combines:
      - intent.confidence numeric score
      - sub_intent specificity (precise vs vague)
      - unsupported / normalization-failure signals
    """
    primary_intent = state.intent.primary_intent or state.primary_intent or ""

    # Unsupported / unknown → always low
    if (
        primary_intent == "unsupported"
        or state.intent.raw_signals.get("unsupported", False)
    ):
        return CONFIDENCE_LOW

    # ── Pre-context-setting: broad category openers → always MEDIUM ──────────
    # Run BEFORE context_setting block so "i want to buy jackets" (which LLM often
    # classifies as context_setting) doesn't incorrectly exit at CONFIDENCE_HIGH.
    # NOTE: No _has_strong_visit_context guard — these queries are ALWAYS MEDIUM because
    # they are too vague regardless of companion/visit context. "i need school clothes for
    # my kids" is still broad (how many kids? what ages?) even with companion context set.
    _pre_raw = _raw(state)
    if any(p in _pre_raw for p in _BROAD_CATEGORY_OPENERS):
        return CONFIDENCE_MEDIUM

    # ── Context-setting confidence ────────────────────────────────────────
    if state.intent.message_kind == "context_setting":
        raw_msg = _raw(state)

        # Very vague / fragmented context declarations → LOW
        # (e.g. "something nice not too much maybe for kid")
        if any(p in raw_msg for p in _VAGUE_CONTEXT_FRAGMENTS):
            return CONFIDENCE_LOW

        # Gift / recipient openers → MEDIUM
        # (context is clear but category/type still unknown → needs follow-up)
        if any(p in raw_msg for p in _GIFT_RECIPIENT_CONTEXT_PATTERNS):
            return CONFIDENCE_MEDIUM

        # Wedding/occasion shopping with role ambiguity → MEDIUM
        # "looking for something for a wedding" (role not specified)
        _OCCASION_ROLE_AMBIGUOUS: tuple[str, ...] = (
            "for a wedding",
            "for the wedding",
            "to a wedding",
        )
        _OCCASION_ROLE_SPECIFIED: tuple[str, ...] = (
            "attending", "in the wedding party", "getting married",
            "im attending", "i'm attending", "i am attending",
            # Explicit role labels always resolve the ambiguity
            "bridesmaid", "maid of honor", "groomsman",
            "i am the groom", "im the groom", "i'm the groom",
            "i am a bridesmaid", "im a bridesmaid", "i'm a bridesmaid",
            "i am the bride", "im the bride", "shopping for the wedding",
        )
        if (
            any(p in raw_msg for p in _OCCASION_ROLE_AMBIGUOUS)
            and not any(p in raw_msg for p in _OCCASION_ROLE_SPECIFIED)
        ):
            return CONFIDENCE_MEDIUM

        # Broad category openers without modifiers → always MEDIUM
        # "i want to buy jackets" / "i need school clothes for my kids" need clarification
        # (who for, what kind, what age?) regardless of companion/visit context.
        if any(p in raw_msg for p in _BROAD_CATEGORY_OPENERS):
            return CONFIDENCE_MEDIUM

        # If the opener also contains a vague exploration pattern but with a strong
        # profile signal, the profile dominates → HIGH
        if any(p in raw_msg for p in _VAGUE_RAW_PATTERNS):
            _STRONG_PROFILE_SIGNALS: tuple[str, ...] = (
                "into fitness", "into healthy", "healthy eating",
                "anniversary", "birthday", "bridal party", "wedding party",
                "planning a", "planning my", "planning our",
                "tourist", "first time visitor", "visiting for the first time",
                "premium experience", "money is not a concern",
                "back to school", "school starting",
                "team outing", "corporate",
            )
            if any(sig in raw_msg for sig in _STRONG_PROFILE_SIGNALS):
                return CONFIDENCE_HIGH
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_HIGH

    confidence = state.intent.confidence
    sub_intent = state.intent.sub_intent
    raw_msg = _raw(state)

    # Broad category openers without modifiers → always MEDIUM regardless of msg_kind.
    # "i want to buy jackets" / "i need school clothes for my kids" need clarification
    # (who for, what kind, what age?) regardless of whether the LLM marks it context_setting.
    # This MUST run before the context_setting block so context_setting doesn't return HIGH.
    if any(p in raw_msg for p in _BROAD_CATEGORY_OPENERS):
        return CONFIDENCE_MEDIUM

    # Movie genre refinements ("anything action", "any other ones") are inherently
    # somewhat ambiguous — always return MEDIUM in movie context regardless of msg_kind.
    _MOVIE_REFINEMENT_OPENERS: tuple[str, ...] = (
        "anything action", "any other ones", "any other one",
        "any action ones", "any animated ones",
    )
    if any(p in raw_msg for p in _MOVIE_REFINEMENT_OPENERS):
        topic = (
            getattr(state.scene, "topic_lock", "") or getattr(state.scene, "active_topic", "") or ""
        ).lower()
        if any(t in topic for t in ("movie", "movies", "entertainment", "cinema")):
            return CONFIDENCE_MEDIUM

    # Group + open question ("im here with 3 friends, what can we do") → MEDIUM
    _GROUP_OPEN_QUESTION: tuple[str, ...] = (
        "what can we do", "what can we", "what should we do",
        "im here with 3 friends", "im here with 4 friends", "here with 3 friends",
    )
    if any(p in raw_msg for p in _GROUP_OPEN_QUESTION) and any(
        g in raw_msg for g in ("friends", "friend", "group", "we ")
    ):
        return CONFIDENCE_MEDIUM

    # "something cheaper" / "something more affordable" → MEDIUM
    # (constraint refinement without established price reference)
    if any(p in raw_msg for p in (
        "something cheaper", "something more affordable",
        "more affordable", "something affordable and",
        "a bit cheaper", "a bit more affordable",
    )):
        return CONFIDENCE_MEDIUM

    # Budget declarations as refinements → always MEDIUM.
    # A specific budget figure narrows options but still requires a search → medium confidence.
    if any(p in raw_msg for p in (
        "budget around", "budget of around", "budget is around",
        "my budget is", "budget is about",
    )):
        if any(kw in raw_msg for kw in ("sar", "aed", "usd", "100", "150", "200", "250", "300", "400", "500")):
            return CONFIDENCE_MEDIUM

    # Vague constraint refinements ("something not too heavy/filling/spicy") → MEDIUM
    # The user is adding a vague negative constraint — intent is clear but options unclear.
    if any(p in raw_msg for p in (
        "not too heavy", "not too filling", "not too rich", "not too spicy",
        "something light", "something lighter",
    )):
        if len(raw_msg.split()) <= 6:
            return CONFIDENCE_MEDIUM

    # Open-ended "most fun" / "most X" exploratory queries → MEDIUM
    # The superlative is subjective and requires the bot to curate/opine.
    if any(p in raw_msg for p in ("most fun thing", "what's the most fun", "what is the most fun")):
        return CONFIDENCE_MEDIUM

    # Dietary refinement in established context ("something with no added sugar") → MEDIUM
    if any(p in raw_msg for p in (
        "no added sugar", "with no added sugar",
        "without sugar", "sugar free", "sugar-free",
    )):
        return CONFIDENCE_MEDIUM

    # Explicit uncertainty ("i dunno", "i don't know") → always MEDIUM regardless of context.
    # The user is literally expressing they don't know what they want.
    if any(p in raw_msg for p in ("i dunno", "i don't know", "i'm not sure", "i am not sure")):
        if len(raw_msg.split()) <= 8:
            return CONFIDENCE_MEDIUM

    # Softer ambiguity ("maybe", "not sure") → MEDIUM only without strong context.
    # "maybe food, something we can share" in group context = clear enough intent → HIGH.
    if any(p in raw_msg for p in ("not sure", "maybe")):
        if len(raw_msg.split()) <= 8 and not _has_strong_visit_context(state):
            return CONFIDENCE_MEDIUM

    # Topic switch ("actually forget that, i want to shop") → MEDIUM
    if any(p in raw_msg for p in ("actually forget that", "forget that", "something else")):
        if any(kw in raw_msg for kw in ("shop", "shopping", "buy", "eat", "food", "movie")):
            return CONFIDENCE_MEDIUM

    # 2-word queries that look like a typo/brand correction → MEDIUM
    # e.g. "Nkie shoes" — first word is a capitalised brand (likely typo), second is a
    # product category.  Intent is probable but not certain → medium confidence.
    original_words = (state.raw_user_message or "").strip().split()
    if (
        len(original_words) == 2
        and len(original_words[0]) >= 3
        and original_words[0][0].isupper()
        and original_words[1][0].islower()
    ):
        return CONFIDENCE_MEDIUM

    # Single-word queries (non-factual, no prior context) → always LOW
    # A single-word request like "shoes" or "food" is too vague without modifiers
    raw_words = raw_msg.split()
    if (
        len(raw_words) == 1
        and sub_intent not in _PRECISE_SUB_INTENTS
        and not _has_strong_visit_context(state)
        and primary_intent
    ):
        return CONFIDENCE_LOW

    # Explicit request verbs ("suggest", "recommend") raise confidence even for general_dining
    # e.g. "suggest some restaurants" — user clearly wants a recommendation → HIGH
    if any(p in raw_msg for p in ("suggest", "recommend", "suggestions")):
        if any(kw in raw_msg for kw in (
            "restaurant", "restaurants", "place", "places",
            "store", "stores", "option", "options",
        )):
            return CONFIDENCE_HIGH

    # Dietary / constraint multi-signal queries in established group context → HIGH
    # e.g. "the team has one person who only eats halal and one vegetarian"
    if any(p in raw_msg for p in ("only eats", "is vegetarian", "is vegan", "is gluten")):
        if _has_strong_visit_context(state):
            return CONFIDENCE_HIGH

    # Very generic short queries without modifiers → LOW
    # "we want to eat" (4 words, no cuisine/location/occasion)
    _GENERIC_VAGUE_SUB_INTENTS: frozenset[str] = frozenset({
        "general_dining", "open_exploration", "general_inquiry",
        "general_entertainment", "first_visit_guide",
    })
    if (
        sub_intent in _GENERIC_VAGUE_SUB_INTENTS
        and len(raw_words) <= 4
        and not _has_strong_visit_context(state)
        and not state.intent.modifiers
        and not any(loc in raw_msg for loc in ("near ", "in the", "at the", "around", "by the"))
    ):
        return CONFIDENCE_LOW

    # Group seating / logistics queries in established large-group context → HIGH
    # e.g. "can we get a private space or at least be seated together"
    if any(p in raw_msg for p in (
        "private space", "seated together", "sit together",
        "group seating", "sit as a group", "together as a group",
        "private dining", "semi-private",
    )):
        if _has_strong_visit_context(state):
            return CONFIDENCE_HIGH

    # Uncertainty signals: bot may lack exact data → cap at medium regardless
    if any(sig in raw_msg for sig in _UNCERTAINTY_SIGNALS):
        if confidence >= 0.40:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    # Budget-cautious in gift context ("something nice, not too much") → MEDIUM
    if any(p in raw_msg for p in _BUDGET_CAUTIOUS_SIGNALS) and any(
        kw in raw_msg for kw in ("gift", "girlfriend", "boyfriend", "nice", "something")
    ):
        return CONFIDENCE_MEDIUM

    # Precise sub-intents: system recognised a specific thing the user wants.
    if sub_intent in _PRECISE_SUB_INTENTS:
        if confidence >= 0.60:
            return CONFIDENCE_HIGH
        if confidence >= 0.40:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    # Vague sub-intents: the query is exploratory/broad by nature.
    if sub_intent in _VAGUE_SUB_INTENTS:
        _AUDIENCE_SECONDARY: frozenset[str] = frozenset({
            "family_filter", "before_movie_constraint", "after_movie_constraint",
        })
        secondary = set(state.secondary_intents or state.intent.secondary_intents or [])
        if secondary & _AUDIENCE_SECONDARY:
            pass  # fall through to standard numeric thresholds
        elif confidence >= 0.75:
            if any(p in raw_msg for p in _VAGUE_RAW_PATTERNS) and not _has_strong_visit_context(state):
                return CONFIDENCE_MEDIUM
            return CONFIDENCE_HIGH
        elif _has_strong_visit_context(state) and confidence >= 0.50:
            return CONFIDENCE_HIGH
        else:
            return CONFIDENCE_MEDIUM if confidence >= 0.50 else CONFIDENCE_LOW

    # Standard numeric thresholds for everything else
    if confidence >= 0.75:
        return CONFIDENCE_HIGH
    if confidence >= 0.45:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _is_vague_query(state: ConciergeState) -> bool:
    """Return True when the query is broad, exploratory, or lacks clear intent."""
    domain = state.intent.domain
    sub_intent = state.intent.sub_intent
    primary_intent = state.intent.primary_intent or state.primary_intent or ""

    if sub_intent in _VAGUE_SUB_INTENTS:
        _AUDIENCE_SECONDARY: frozenset[str] = frozenset({
            "family_filter", "before_movie_constraint", "after_movie_constraint",
        })
        secondary = set(state.secondary_intents or state.intent.secondary_intents or [])
        if secondary & _AUDIENCE_SECONDARY:
            return False
        return True
    if domain in ("exploration", "general") and not primary_intent:
        return True

    raw = _raw(state)
    if any(p in raw for p in _VAGUE_RAW_PATTERNS):
        return True

    return False


def _has_cross_domain_intent(state: ConciergeState) -> bool:
    """
    Detect when the user's message spans two distinct domains (e.g. "food and movies").

    Fires on:
      1. Current turn secondary intents that represent cross-domain goals
         (uses intent.secondary_intents directly, NOT inherited ones, to avoid
          false hybrid_plan on follow-up turns like "yeah food first then see")
      2. Raw query that explicitly mentions two domain keyword groups together
      3. Active movie topic + dining request with temporal signal (e.g. "eat before")
    """
    raw = _raw(state)

    # User narrowing to one domain ("food first then see") = guided_recommendation, NOT hybrid
    _NARROWING_TO_DINING: tuple[str, ...] = (
        "yeah food first then see", "food first then see", "food first then",
        "yeah food first", "food first", "food first and then",
        # Temporal dessert/sweet reference after movie = single-domain dessert request
        "something sweet after the movie", "dessert after the movie",
        "something sweet after", "dessert after", "sweets after",
        "sweet after the movie", "treat after", "cake after",
    )
    if any(p in raw for p in _NARROWING_TO_DINING):
        return False

    # Only check current turn's secondary intents, not inherited ones from scene.
    # Inherited secondary_intents can cause false hybrid_plan on follow-up turns.
    current_secondary = set(state.intent.secondary_intents or [])
    if current_secondary & _CROSS_DOMAIN_SECONDARY:
        return True
    for group_a, group_b in _CROSS_DOMAIN_RAW_PATTERNS:
        has_a = any(kw in raw for kw in group_a)
        has_b = any(kw in raw for kw in group_b)
        if has_a and has_b:
            joining_words = (" and ", " with ", " then ", " plus ", " after ", " before ")
            if any(jw in raw for jw in joining_words) or len(raw.split()) <= 6:
                return True

    # Special case: movie topic is established + user asks about dining with temporal context
    # e.g. "anything to eat before" after a movie selection
    # Also fires when topic_lock is "movie_recommendation" (set after guided movie recs)
    scene = state.scene
    _ALL_MOVIE_TOPICS: frozenset[str] = _FACTUAL_LOCKED_TOPICS | frozenset({
        "movie_recommendation", "movie_plan",
    })
    if (
        scene.topic_lock in _ALL_MOVIE_TOPICS
        and any(kw in raw for kw in ("eat", "food", "restaurant", "dining", "snack", "bite", "drink"))
        and any(kw in raw for kw in ("before", "after", "first", "then", "now", "quick"))
    ):
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main resolver
# ─────────────────────────────────────────────────────────────────────────────

def resolve_response_mode(
    state: ConciergeState,
) -> tuple[str, str, str, bool]:
    """
    Resolve the response mode for this turn.

    Returns
    ───────
    (response_mode, confidence_level, reason, fallback_applied)

    response_mode   – one of the defined modes
    confidence_level – high | medium | low
    reason          – human-readable explanation for the debug payload
    fallback_applied – True when a recovery / best-effort mode was chosen
    """
    intent = state.intent
    scene = state.scene
    playbook = state.playbook
    msg_kind = intent.message_kind
    primary_intent = intent.primary_intent or state.primary_intent or ""

    # ── Step 1: classify confidence ───────────────────────────────────
    confidence_level = classify_confidence(state)

    # ── Early exit: short group dining opener with no cuisine → guided_recommendation/low ─
    # "we want to eat" = at least 2 people, no cuisine specified.
    # Must offer cuisine options. Must NOT ask "how many people?" — "we" implies 2+.
    # Returns guided_recommendation (not best_effort_shortlist) so the bot stays helpful
    # without overwhelming with choices.
    _SHORT_GROUP_DINING: tuple[str, ...] = (
        "we want to eat", "we'd like to eat", "we wanna eat",
        "we want food", "we need to eat", "we are hungry",
    )
    raw = _raw(state)
    _raw_short_group = (state.raw_user_message or "").lower().strip()
    if any(p in raw or p in _raw_short_group for p in _SHORT_GROUP_DINING):
        return (
            GUIDED_RECOMMENDATION,
            CONFIDENCE_LOW,
            "short group dining opener — offer cuisine options (no cuisine specified yet)",
            False,
        )

    # ── Early exit: correction/recovery turns ("sorry i meant X") ────
    # After a confusing turn, user corrects with a concrete intent.
    # Restore high confidence immediately rather than staying in the confused state.
    raw = _raw(state)
    _raw_original_correction = (state.raw_user_message or "").lower().strip()
    _CORRECTION_SIGNALS: tuple[str, ...] = (
        "sorry i meant", "i meant", "i mean ", "i said ", "actually i want",
        "sorry, i meant", "sorry — i meant", "my bad, i meant",
    )
    if any(p in raw or p in _raw_original_correction for p in _CORRECTION_SIGNALS):
        # Ensure there's a concrete shopping/dining/activity intent following the correction
        _CONCRETE_INTENT_WORDS: tuple[str, ...] = (
            "sneakers", "shoes", "jacket", "jackets", "bag", "bags",
            "clothes", "clothing", "dress", "food", "eat", "restaurant",
            "movie", "cinema", "gift",
        )
        if any(w in raw or w in _raw_original_correction for w in _CONCRETE_INTENT_WORDS):
            return (
                GUIDED_RECOMMENDATION,
                CONFIDENCE_HIGH,
                "correction/recovery turn — restore intent and route to guided recommendation",
                False,
            )

    # ── Early exit: vague multi-fragment context declarations ─────────
    # "something nice not too much maybe for kid" — multiple fragments, no clear intent.
    # Route to context_acknowledgement regardless of msg_kind (LLM may not classify as
    # context_setting if the input is too fragmented).
    if any(p in raw or p in _raw_original_correction for p in _VAGUE_CONTEXT_FRAGMENTS):
        return (
            CONTEXT_ACKNOWLEDGEMENT,
            CONFIDENCE_LOW,
            "vague multi-fragment context declaration — acknowledge and ask one clarifying question",
            False,
        )

    # ── Early exit: out-of-scope external venue requests ─────────────
    if any(p in raw for p in _OUT_OF_SCOPE_SIGNALS) and any(
        kw in raw for kw in ("restaurant", "eat", "food", "halal", "dining", "cafe")
    ):
        return (
            GRACEFUL_RECOVERY,
            CONFIDENCE_LOW,
            "out-of-scope: external/nearby venues requested — only in-mall advice available",
            True,
        )

    # ── Early exit: unsupported capability requests ───────────────────
    # "can you book me a taxi", "can you order food for me", etc.
    # These are understood but the bot cannot perform them → clarification_request
    if any(p in raw for p in _UNSUPPORTED_CAPABILITY_PATTERNS):
        return (
            CLARIFICATION_REQUEST,
            CONFIDENCE_HIGH,
            "unsupported capability: bot cannot perform this action; will clarify limitations",
            False,
        )

    # ── Early exit: curated differentiation queries ───────────────────
    if any(sig in raw for sig in _CURATED_DIFFERENTIATION_SIGNALS):
        return (
            BEST_EFFORT_SHORTLIST,
            CONFIDENCE_MEDIUM,
            "curated differentiation query — inherently uncertain/subjective",
            False,
        )

    # ── Early exit: "show me movies" → guided_recommendation ──
    # Recommendation-style movie request (display films, offer to filter) — NOT raw listing.
    # Must run before factual flow branches so we get guided_recommendation even if routed factual.
    # Check BOTH normalized and raw to handle normalization changing the phrase.
    _raw_original = (state.raw_user_message or "").lower().strip()
    if any(sig in raw or sig in _raw_original for sig in ("show me movies", "show me films")):
        return (
            GUIDED_RECOMMENDATION,
            CONFIDENCE_HIGH,
            "'Show me movies' reads as recommendation request — display films, offer to filter",
            False,
        )

    # ── Early exit: sales/promotion query in shopping context → guided_recommendation ──
    # "any with sales on", "is there like a sale" — stay in recommendation flow
    _SALES_PROMO_IN_SHOPPING: tuple[str, ...] = (
        "any with sales", "with sales on", "is there like a sale", "a sale or something",
        "any sales", "sales on", "any promotions", "any deals",
    )
    _SHOPPING_TOPICS_SET: frozenset[str] = frozenset({
        "shopping", "fashion", "clothing", "jackets", "kids_fashion",
        "kids_clothing", "gift", "handbags", "shoes", "casual_shoes",
    })
    if any(p in raw for p in _SALES_PROMO_IN_SHOPPING):
        topic = (scene.topic_lock or scene.active_topic or "").lower()
        if any(t in topic for t in _SHOPPING_TOPICS_SET) or any(
            w in raw for w in ("clothes", "bags", "shoes", "jacket", "kid", "gift")
        ):
            return (
                GUIDED_RECOMMENDATION,
                CONFIDENCE_MEDIUM,
                "sales/promotion query in shopping context → guided recommendation",
                False,
            )

    # ── Early exit: price query in shopping context → direct_factual ──
    # "whats the price" when user is in a shopping flow (jackets, kids clothes)
    # is a factual question about pricing, not a recommendation request.
    _PRICE_IN_SHOPPING: tuple[str, ...] = (
        "whats the price", "what's the price", "what is the price", "the price",
    )
    if any(p in raw for p in _PRICE_IN_SHOPPING):
        topic = (scene.topic_lock or scene.active_topic or "").lower()
        if any(t in topic for t in _SHOPPING_TOPICS_SET) or "jacket" in raw or "jackets" in raw:
            return (
                DIRECT_FACTUAL,
                CONFIDENCE_MEDIUM,
                "price query in shopping context → direct factual (cannot confirm exact prices)",
                False,
            )

    # ── Early exit: movie listing queries are always direct_factual ───
    if (
        any(p in raw for p in _MOVIE_LISTING_PATTERNS)
        and not any(excl in raw for excl in _MOVIE_LISTING_EXCLUSIONS)
    ):
        return (
            DIRECT_FACTUAL,
            CONFIDENCE_HIGH,
            "movie listing query → direct factual",
            False,
        )

    # ── Early exit: factual service/location/hours queries ────────────
    if any(p in raw for p in _FACTUAL_SERVICE_PATTERNS):
        return (
            DIRECT_FACTUAL,
            CONFIDENCE_HIGH,
            "factual service/location query → direct factual",
            False,
        )

    # ── Early exit: factual queries with inherent uncertainty → direct_factual/MEDIUM ──
    if any(p in raw for p in _FACTUAL_SERVICE_PATTERNS_MEDIUM):
        return (
            DIRECT_FACTUAL,
            CONFIDENCE_MEDIUM,
            "factual query with inherent uncertainty/advisory nature → direct factual (medium)",
            False,
        )

    # ── Early exit: short kid-filter on factual entertainment context ────
    # "with kid" / "for kids" after a FACTUAL movie listing → direct_factual.
    # IMPORTANT: "movie_lookup" topic_lock is set by guided/concierge recommendation
    # requests ("show me movies"). In that context, "with kid" should return
    # guided_recommendation (re-filter the guided recommendations for family-friendly),
    # NOT direct_factual (raw factual listing).
    # Only fire when the entertainment context was NOT established via "movie_lookup"
    # (i.e., the movie context came from a factual query, not a concierge recommendation).
    _KID_FILTER_FACTUAL_TOPICS: frozenset[str] = frozenset({
        "movies", "cinema", "entertainment", "movie_schedule",
    })
    if (
        any(p in raw for p in _KID_FILTER_PATTERNS)
        and len(raw.split()) <= 3
        and scene.topic_lock != "movie_lookup"   # movie_lookup = set by guided concierge flow
        and (
            scene.active_topic in ("entertainment", "movies", "cinema")
            or scene.topic_lock in _KID_FILTER_FACTUAL_TOPICS
        )
    ):
        return (
            DIRECT_FACTUAL,
            CONFIDENCE_HIGH,
            f"kid-filter on factual {scene.active_topic or scene.topic_lock!r} movie context → direct_factual",
            False,
        )

    # ── Step 2G: follow-up resolution (topic lock short-circuit) ─────
    is_short = len(raw.split()) <= 5
    has_active_lock = bool(scene.topic_lock and scene.topic_lock_confidence >= 0.5)
    is_followup_kind = msg_kind in ("followup", "refinement", "constraint_refinement")

    if is_short and is_followup_kind and has_active_lock and not _is_vague_query(state):
        if confidence_level == CONFIDENCE_LOW:
            confidence_level = CONFIDENCE_MEDIUM

        locked_topic = scene.topic_lock
        # Only return direct_factual via topic-lock when actually in factual flow.
        # In concierge flow (e.g. "with kid" after guided movie rec), the lock
        # reflects the topic domain but the response should be recommendation-style.
        if locked_topic in _FACTUAL_LOCKED_TOPICS and state.flow_type == "factual":
            # Exception: if a child companion has been established (e.g. from a prior turn
            # revealing "oh wait im with my 7 year old"), movie follow-ups should be
            # filtered for kid-appropriateness → guided_recommendation, not direct_factual.
            has_child_companion = bool(
                scene.companion_details
                or any(
                    kw in c.lower()
                    for c in (scene.companions or [])
                    for kw in ("child", "kid", "year_old", "daughter", "son")
                )
            )
            if has_child_companion and any(
                t in locked_topic.lower() for t in ("movie", "cinema", "entertainment")
            ):
                return (
                    GUIDED_RECOMMENDATION,
                    confidence_level,
                    f"child companion established in {locked_topic!r} context → "
                    "guided_recommendation (kid-appropriate filter on movie follow-up)",
                    False,
                )
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"short follow-up resolved via topic_lock={locked_topic!r} (factual flow)",
                False,
            )
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            f"short follow-up in active context (topic_lock={locked_topic!r})",
            False,
        )

    # ── Pre-2F guard: child companion revelation in movie/cinema factual context ──
    # "oh wait im with my 7 year old" / "my daughter is 7" after a factual movie listing
    # → user wants FILTERED/recommended movies for their child, not a raw factual listing.
    # The "child reveal" breaks the factual domain lock and routes to guided_recommendation.
    _CHILD_REVEAL_PATTERNS: tuple[str, ...] = (
        "year old", "my kid", "my daughter", "my son",
        "im with my", "i'm with my", "i am with my",
    )
    _MOVIE_FACTUAL_TOPICS_CHILD: frozenset[str] = frozenset({
        "movies", "cinema", "entertainment", "movie_schedule",
    })
    if (
        any(p in raw for p in _CHILD_REVEAL_PATTERNS)
        and (
            scene.active_topic in _MOVIE_FACTUAL_TOPICS_CHILD
            or any(t in (scene.topic_lock or "").lower() for t in ("movie", "cinema", "entertainment"))
        )
        and state.flow_type == "factual"
    ):
        return (
            GUIDED_RECOMMENDATION,
            CONFIDENCE_HIGH,
            "child companion revealed in movie/cinema factual context → guided_recommendation "
            "(filter movies for family/child-appropriate recommendations)",
            False,
        )

    # ── Pre-2F guard: occasion/outfit requests in shopping context ────
    # "im attending, need an outfit, wedding but not too fancy" and similar multi-fragment
    # shopping requests that may confuse the LLM into marking intent as "unsupported".
    # Any message that contains both an occasion signal AND an outfit/clothing need
    # should always be guided_recommendation, NOT graceful_recovery.
    _OCCASION_OUTFIT_SIGNALS: tuple[str, ...] = (
        "need an outfit", "need outfit", "need a dress", "need something to wear",
        "looking for an outfit", "looking for a dress", "looking for something to wear",
        "want an outfit", "want a dress", "want something to wear",
    )
    _OCCASION_SIGNALS_CHECK: tuple[str, ...] = (
        "wedding", "attending", "event", "ceremony", "gala", "party",
        "formal", "dinner event", "special occasion",
    )
    if (
        any(p in raw for p in _OCCASION_OUTFIT_SIGNALS)
        or (
            any(p in raw for p in _OCCASION_SIGNALS_CHECK)
            and any(kw in raw for kw in ("outfit", "dress", "wear", "clothes", "clothing"))
        )
    ):
        return (
            GUIDED_RECOMMENDATION,
            CONFIDENCE_HIGH,
            "occasion/event outfit request — guided shopping recommendation",
            False,
        )

    # ── Step 2F: hard unsupported / gibberish ────────────────────────
    # Gibberish/unknown input → graceful_recovery (explain capabilities, don't pretend to understand)
    # Keep CLARIFICATION_REQUEST only for unsupported capability requests (taxi, delivery, etc.)
    # which are already handled by the earlier _UNSUPPORTED_CAPABILITY_PATTERNS early exit.
    #
    # Guard: if we're in an established session and the classifier returned "unsupported",
    # this is almost certainly a mis-classification (LLM rate-limited or heuristic fired).
    # In that case, fall through to normal mode resolution rather than issuing a false
    # graceful_recovery in the middle of a real conversation.
    is_hard_unsupported = (
        primary_intent == "unsupported"
        or intent.raw_signals.get("unsupported", False)
    )
    if is_hard_unsupported and not _has_strong_visit_context(state):
        return (
            GRACEFUL_RECOVERY,
            CONFIDENCE_LOW,
            "unsupported or unintelligible input — graceful recovery (explain capabilities)",
            True,
        )

    # ── Step 2C-post: explicit planning/scheduling → hybrid_plan ─────
    # Run BEFORE low confidence so dual-intent "food and movies" gets hybrid
    if _has_planning_intent(state):
        return (
            HYBRID_PLAN,
            confidence_level,
            "explicit planning/itinerary/scheduling request",
            False,
        )

    # ── Step 2D: cross-domain / multi-intent → hybrid_plan ───────────
    # Run BEFORE low confidence so "food and movies" gets hybrid, not guided
    if _has_cross_domain_intent(state):
        hybrid_confidence = confidence_level
        if len(raw.split()) <= 4 and confidence_level == CONFIDENCE_HIGH:
            hybrid_confidence = CONFIDENCE_MEDIUM
        if any(p in raw for p in ("food and maybe movie", "maybe movie also", "and maybe movie")):
            hybrid_confidence = CONFIDENCE_MEDIUM
        return (
            HYBRID_PLAN,
            hybrid_confidence,
            "cross-domain intent detected — combine into one unified plan",
            False,
        )

    # ── Low confidence handling ───────────────────────────────────────
    # Low confidence with no identifiable intent + no visit context → clarification_request
    if confidence_level == CONFIDENCE_LOW:
        if not primary_intent and not _has_strong_visit_context(state):
            return (
                GRACEFUL_RECOVERY,
                CONFIDENCE_LOW,
                "unknown intent with no visit context — graceful recovery",
                True,
            )
        # Partial/vague with identifiable intent ("anything for dinner") → best_effort
        _PARTIAL_VAGUE_AT_LOW: tuple[str, ...] = (
            "something nice for", "something for kids", "anything for dinner",
        )
        if any(p in raw for p in _PARTIAL_VAGUE_AT_LOW) and not _has_strong_visit_context(state):
            return (
                BEST_EFFORT_SHORTLIST,
                CONFIDENCE_LOW,
                "partial/vague query with low confidence — safe shortlist",
                True,
            )
        if _is_vague_query(state) and not _has_strong_visit_context(state):
            return (
                BEST_EFFORT_SHORTLIST,
                CONFIDENCE_LOW,
                "vague exploratory query with low confidence — safe shortlist",
                True,
            )
        # Identifiable intent but low confidence → guided recommendation
        # (happens for single-word queries like "shoes" or generic "we want to eat")
        return (
            GUIDED_RECOMMENDATION,
            CONFIDENCE_LOW,
            f"low confidence but identifiable intent ({primary_intent}) → guided recommendation",
            False,
        )

    # If strong context exists but confidence is LOW, promote to MEDIUM so
    # downstream steps can route correctly. (LOW already handled above.)

    # ── Step 2C: context-setting → context_acknowledgement ───────────
    if msg_kind == "context_setting":
        # Exception: "with kid" / "with kids" / "for kids" in an established movie/
        # entertainment topic is a companion FILTER on the current topic (re-filter
        # for family-friendly films), NOT a new context declaration.  Routing it to
        # context_acknowledgement would abandon the movie thread; guided_recommendation
        # keeps the film recommendations in view with the new kid filter applied.
        _MOVIE_ENTERTAINMENT_TOPICS: frozenset[str] = frozenset({
            "movies", "cinema", "entertainment", "movie_lookup",
            "movie_schedule", "movie_recommendation",
        })
        active_topic = (scene.topic_lock or getattr(scene, "active_topic", "") or "").lower()
        if (
            any(p in raw for p in _KID_FILTER_PATTERNS)
            and len(raw.split()) <= 4
            and any(t in active_topic for t in ("movie", "cinema", "entertainment", "film"))
        ):
            return (
                GUIDED_RECOMMENDATION,
                CONFIDENCE_HIGH,
                f"kid-filter in established {active_topic!r} context → guided_recommendation "
                "(re-filter films for family-friendly, not new context declaration)",
                False,
            )

        _EMBEDDED_EXPLORATION_SIGNALS: tuple[str, ...] = (
            "what's fun to do",
            "what's fun here",
            "what can we do here",
            "what should we do here",
            "what to do here",
            "anything fun to do here",
        )
        if any(sig in raw for sig in _EMBEDDED_EXPLORATION_SIGNALS):
            return (
                BEST_EFFORT_SHORTLIST,
                confidence_level,
                "context_setting opener with embedded group-entertainment request",
                False,
            )
        return (
            CONTEXT_ACKNOWLEDGEMENT,
            confidence_level,
            "context_setting message kind — acknowledge situation, offer next steps",
            False,
        )

    # ── Step 2A / 2B: confidence-driven branching ─────────────────────

    # Override: queries that ask for curated recommendations should always be
    # guided_recommendation even when flow_type is "factual".  These are opinion/
    # curation requests ("any high-end options", "which stores give the best value")
    # that require concierge reasoning, not raw factual lookup.
    _FACTUAL_FLOW_RECOMMENDATION_OVERRIDES: tuple[str, ...] = (
        "any high-end", "any high end", "any premium options",
        "any luxury options", "high-end options",
        "which stores give the best", "best value for money",
        "value for money stores",
    )
    if state.flow_type == "factual" and any(p in raw for p in _FACTUAL_FLOW_RECOMMENDATION_OVERRIDES):
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            "recommendation override: curated-ask in factual flow → guided_recommendation",
            False,
        )

    if confidence_level == CONFIDENCE_HIGH:
        if state.flow_type == "factual":
            # Exception: child companion established in movie/cinema context
            # → guided_recommendation (re-filter for kid-appropriate films)
            _has_child = bool(
                scene.companion_details
                or any(
                    kw in c.lower()
                    for c in (scene.companions or [])
                    for kw in ("child", "kid", "daughter", "son")
                )
            )
            if _has_child and any(
                t in (scene.topic_lock or "").lower()
                for t in ("movie", "cinema", "entertainment")
            ):
                return (
                    GUIDED_RECOMMENDATION,
                    confidence_level,
                    "child companion in movie/cinema factual context → guided_recommendation",
                    False,
                )
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"factual flow with high confidence: {intent.sub_intent}",
                False,
            )
        if intent.sub_intent in _PRECISE_SUB_INTENTS:
            return (
                GUIDED_RECOMMENDATION,
                confidence_level,
                f"precise sub_intent ({intent.sub_intent}) in concierge flow → recommendation",
                False,
            )
        if playbook.selected_playbook and playbook.playbook_confidence > 0.4:
            return (
                GUIDED_RECOMMENDATION,
                confidence_level,
                f"strong playbook ({playbook.selected_playbook}, "
                f"conf={playbook.playbook_confidence:.2f})",
                False,
            )
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            f"high confidence, clear intent: {primary_intent}",
            False,
        )

    # medium confidence
    if confidence_level == CONFIDENCE_MEDIUM:
        # Partial/vague queries without strong context → best_effort_shortlist
        _PARTIAL_VAGUE_PATTERNS: tuple[str, ...] = (
            "something nice for", "something for kids", "anything for dinner",
        )
        if (
            any(p in raw for p in _PARTIAL_VAGUE_PATTERNS)
            and not _has_strong_visit_context(state)
        ):
            return (
                BEST_EFFORT_SHORTLIST,
                confidence_level,
                "partial/vague query with medium confidence — safe shortlist",
                True,
            )
        # Occasion + style refinement ("something elegant", "not too over the top")
        # → guided_recommendation, NOT best_effort_shortlist
        if scene.occasion and any(
            kw in raw for kw in ("elegant", "put together", "over the top", "not too over")
        ):
            return (
                GUIDED_RECOMMENDATION,
                confidence_level,
                "occasion + style refinement in established context",
                False,
            )
        _RECOMMENDATION_PHRASES = ("is there anywhere to ", "anywhere i can ", "somewhere to ")
        if state.flow_type == "factual" and not any(p in raw for p in _RECOMMENDATION_PHRASES):
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"factual flow with medium confidence: {intent.sub_intent}",
                False,
            )
        if _is_vague_query(state):
            is_raw_vague_pattern = any(p in raw for p in _VAGUE_RAW_PATTERNS)
            # Only return best_effort_shortlist when there is NO established context.
            # With strong visit context, a vague follow-up is still within the established
            # scenario — fall through to guided_recommendation.
            if not _has_strong_visit_context(state):
                return (
                    BEST_EFFORT_SHORTLIST,
                    confidence_level,
                    "vague/exploratory query with medium confidence — safe shortlist",
                    True,
                )
            # Even with strong context, raw exploratory patterns (e.g. "i'm bored",
            # "surprise me") that are explicitly open-ended remain best_effort_shortlist
            if is_raw_vague_pattern:
                return (
                    BEST_EFFORT_SHORTLIST,
                    confidence_level,
                    "explicit exploratory pattern even within established context",
                    True,
                )
        if intent.domain not in ("general",) and intent.sub_intent in _VAGUE_SUB_INTENTS:
            if not _has_strong_visit_context(state):
                return (
                    BEST_EFFORT_SHORTLIST,
                    confidence_level,
                    f"known domain ({intent.domain}) but vague sub_intent ({intent.sub_intent})",
                    True,
                )
        if primary_intent:
            return (
                GUIDED_RECOMMENDATION,
                confidence_level,
                f"medium confidence with identifiable intent: {primary_intent}",
                False,
            )

    # ── Step 2E: vague / exploratory (catch-all) ─────────────────────
    if _is_vague_query(state):
        return (
            BEST_EFFORT_SHORTLIST,
            confidence_level,
            "broad/vague exploratory query — present diverse safe options",
            True,
        )

    # ── Final fallback ────────────────────────────────────────────────
    # If we're in an established session (companions, occasion, etc.), the classifier
    # likely failed (e.g. LLM rate-limited → heuristic returned empty intent).
    # In that case, graceful_recovery is a false positive — the user is mid-conversation
    # and their intent is almost certainly valid.  Fall back to guided_recommendation so
    # the LLM generation step can at least attempt a contextual reply.
    if _has_strong_visit_context(state) or state.flow_type == "concierge":
        return (
            GUIDED_RECOMMENDATION,
            CONFIDENCE_MEDIUM,
            "fallback: empty/heuristic intent in established session → guided recommendation",
            False,
        )
    return (
        GRACEFUL_RECOVERY,
        confidence_level,
        "fallback: no clear mode could be determined",
        True,
    )
