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
# NOTE: general_dining and general_shopping are intentionally excluded — they are
# action-oriented ("find me somewhere to eat", "I want to buy something"), not
# exploratory. Keeping them here wrongly downgrades clear requests to medium
# confidence and produces best_effort_shortlist instead of guided_recommendation.
_VAGUE_SUB_INTENTS: frozenset[str] = frozenset({
    "open_exploration", "general_inquiry", "activity_suggestion",
    "general_entertainment",
    "first_visit_guide",
})

# Secondary intents that signal the user wants something from a second domain
# (distinct from simple attribute modifiers like kid_friendly or budget_sensitive)
# NOTE: before_movie_constraint / after_movie_constraint are FILTERS (user adds a
# timing context), NOT cross-domain triggers.  Including them causes single-step
# queries like "dessert after the movie" to incorrectly become hybrid_plan.
_CROSS_DOMAIN_SECONDARY: frozenset[str] = frozenset({
    "add_dining_step",
    "add_coffee_step",
})

# Topic lock values that map cleanly to direct_factual
# NOTE: "movie_lookup" is the value actually stored by update_memory (primary intent label);
# "movies" / "cinema" / etc. are the legacy string values that may appear in older sessions.
_FACTUAL_LOCKED_TOPICS: frozenset[str] = frozenset({
    "movies", "entertainment", "cinema", "movie_schedule",
    "movie_lookup",
})

# Raw-query patterns that signal a broad/vague exploratory intent
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
    "something affordable",
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
        ("before the movie", "after the movie", "before watching"),
        ("quick", "snack", "bite", "eat", "food", "grab"),
    ),
)

# Movie listing queries that are always factual — pure "what's showing" lookups
_MOVIE_LISTING_PATTERNS: tuple[str, ...] = (
    "what movies", "which movies", "movies showing", "movies are there",
    "movies do we have", "movies can i watch", "movies are on",
    "movies can i see", "now showing", "movie list",
    "all movies", "show me movies", "what's showing",
)
# Exclusions: recommendation / curation queries that mention movies but are NOT lookups
_MOVIE_LISTING_EXCLUSIONS: tuple[str, ...] = (
    "good movie for", "recommend a movie", "a movie for",
    "movie for couples", "suggest a movie", "movies for couples",
)

# Factual service/location/hours queries — always direct_factual
_FACTUAL_SERVICE_PATTERNS: tuple[str, ...] = (
    "prayer room", "prayer rooms",
    "opening hours", "mall opening hours", "what time does the mall",
    "when does the mall open", "when do you open", "when do you close",
)

# Short companion-filter patterns that should stay factual on entertainment context
_KID_FILTER_PATTERNS: tuple[str, ...] = (
    "with kid", "with kids", "for kids",
)

# Explicit planning / scheduling requests → hybrid_plan
_PLANNING_SIGNALS: tuple[str, ...] = (
    "full plan", "full itinerary", "full day plan", "full bridal party day",
    "map out", "plan out", "schedule for",
    # NOTE: "priority order" / "best order to" removed — these fire for
    # single-step queries like "what's the priority order" (expected guided_recommendation)
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
    # NOTE: "what if i can" / "what if we can" intentionally removed —
    # too broad and caused S6.13 "what's the priority order?" to get MEDIUM
    # instead of HIGH.  These were budget-fit signals but are also used in
    # confident priority/planning queries.
    # Budget constraint refinements (precise but speculative)
    "budget around",
    "budget of about",
    "budget is about",
    "around 150", "around 200", "around 300", "around 100", "around 500",
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
    # Affordability / budget fit queries
    "what can we actually afford", "what can we afford",
    "for that budget", "for our budget", "within that budget",
    "is 300 sar", "is 150 sar", "is 200 sar",
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
    # Clean eating / workout nutrition guidance (advice, not factual availability)
    "what's the best approach", "clean meal before", "before a workout",
    "best approach if",
    # Short constraint refinements (no added sugar, not too heavy, etc.) that
    # shouldn't need high confidence since they narrow from prior suggestion
    "no added sugar", "not too heavy", "without sugar",
)

# Queries asking for curation of the uniquely exclusive/rare → best_effort_shortlist + medium
# NOTE: Must be specific enough to NOT fire on guided gift queries like S7.4
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
    # Requires the verb to follow the hour expression (not just appear anywhere)
    if _re.search(r'\b\d+\.?\d*[-\s]?(hour|hr)s?\b\s*(plan|schedule|itinerary|outing|visit)', raw):
        return True
    # "fit everything in X hours" — only "fit", not "do" (too broad: catches
    # "do custom cakes with 24 hours notice" as a false positive)
    if _re.search(r'\bfit\b.*\b\d+\.?\d*[-\s]?(hour|hr)s?\b', raw):
        return True
    return False


def _has_strong_visit_context(state: ConciergeState) -> bool:
    """Return True when there is established scene context for this conversation."""
    scene = state.scene
    return bool(
        (scene.companions and scene.companions != ["solo"])
        or scene.occasion
        or scene.budget
        or scene.scenario
        or scene.visit_type
        or getattr(scene, "goal", None)
        or getattr(scene, "implicit_goal", None)
        or getattr(scene, "user_role", None)
    )


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

    # Context-setting is always unambiguously understood — the system correctly
    # identified the user's situation declaration regardless of sub_intent score.
    # Exception: vague exploratory phrasing that happens to be context_setting
    # (e.g. "i'm bored") should remain medium so it gets best_effort_shortlist.
    # BUT: if the message contains a strong profile signal (wellness, fitness,
    # anniversary, tourist, planning), the vague query is just the request portion
    # of a combined opener — the profile declaration makes it high confidence.
    if state.intent.message_kind == "context_setting":
        raw_msg = _raw(state)
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

    # Short constraint-refinement turns (≤ 4 words) narrow a prior result — cap
    # at medium since a tiny qualifier alone isn't high-confidence.
    # Longer phrases (e.g. "for my 5 year old son") may introduce specific context
    # with a precise sub_intent and should keep their score.
    if state.intent.message_kind in ("constraint_refinement", "refinement"):
        raw_len = len(_raw(state).split())
        if raw_len <= 4 and confidence >= 0.75:
            return CONFIDENCE_MEDIUM

    # Uncertainty signals: bot may lack exact data → cap at medium regardless
    # of sub_intent specificity (e.g. price estimates, booking likelihood, etc.)
    raw_msg = _raw(state)
    if any(sig in raw_msg for sig in _UNCERTAINTY_SIGNALS):
        # Don't upgrade low-confidence queries; cap at medium when uncertain
        if confidence >= 0.40:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    # Precise sub-intents: system recognised a specific thing the user wants.
    #   high   ≥ 0.60  (classifier is sure)
    #   medium ≥ 0.40  (classifier thinks it's this, less certain)
    #   low    < 0.40  (very uncertain even about a specific sub-intent)
    if sub_intent in _PRECISE_SUB_INTENTS:
        if confidence >= 0.60:
            return CONFIDENCE_HIGH
        if confidence >= 0.40:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW

    # Vague sub-intents: the query is exploratory/broad by nature; the system
    # correctly identified it as such.  We should give a shortlist, not recover.
    #   medium ≥ 0.50  (recognised as a vague exploration intent)
    #   low    < 0.50  (can't even classify as exploratory with confidence)
    if sub_intent in _VAGUE_SUB_INTENTS:
        # Exception 1: when the query has an audience-specific secondary intent
        # (e.g. family_filter from "for the kids"), the request is targeted,
        # not exploratory — fall through to standard numeric thresholds.
        _AUDIENCE_SECONDARY: frozenset[str] = frozenset({
            "family_filter", "before_movie_constraint", "after_movie_constraint",
        })
        secondary = set(state.secondary_intents or state.intent.secondary_intents or [])
        if secondary & _AUDIENCE_SECONDARY:
            pass  # fall through to standard numeric thresholds
        # Exception 2: classifier is highly confident even for a broad query
        # BUT: pure exploratory raw patterns (e.g. "anything interesting here?",
        # "i'm bored", "surprise me") without strong visit context should be MEDIUM
        # so they get best_effort_shortlist, not guided_recommendation.
        elif confidence >= 0.75:
            raw_msg = _raw(state)
            if any(p in raw_msg for p in _VAGUE_RAW_PATTERNS) and not _has_strong_visit_context(state):
                return CONFIDENCE_MEDIUM
            return CONFIDENCE_HIGH
        # Exception 3: strong established scene context resolves the vagueness —
        # the query is targeted within a known visit scenario (e.g. "what activities
        # for a 7-year-old" in a birthday-planning session).
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
        # Audience-specific secondary intents narrow the scope — the query is
        # targeted (e.g. "any activities for the kids?"), not vague exploration.
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
      1. Secondary intents that represent cross-domain goals (add_dining_step, etc.)
      2. Raw query that explicitly mentions two domain keyword groups together
    """
    secondary = set(state.secondary_intents or [])
    if secondary & _CROSS_DOMAIN_SECONDARY:
        return True

    raw = _raw(state)
    # Only trigger when the message explicitly joins two domain areas
    for group_a, group_b in _CROSS_DOMAIN_RAW_PATTERNS:
        has_a = any(kw in raw for kw in group_a)
        has_b = any(kw in raw for kw in group_b)
        if has_a and has_b:
            # Guard: must contain a joining word, not just incidentally mention both
            joining_words = (" and ", " with ", " then ", " plus ", " after ", " before ")
            if any(jw in raw for jw in joining_words) or len(raw.split()) <= 6:
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

    response_mode   – one of the six defined modes
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

    # ── Early exit: out-of-scope external venue requests ─────────────
    raw = _raw(state)
    if any(p in raw for p in _OUT_OF_SCOPE_SIGNALS) and any(
        kw in raw for kw in ("restaurant", "eat", "food", "halal", "dining", "cafe")
    ):
        return (
            GRACEFUL_RECOVERY,
            CONFIDENCE_LOW,
            "out-of-scope: external/nearby venues requested — only in-mall advice available",
            True,
        )

    # ── Early exit: curated differentiation queries ───────────────────
    if any(sig in raw for sig in _CURATED_DIFFERENTIATION_SIGNALS):
        return (
            BEST_EFFORT_SHORTLIST,
            CONFIDENCE_MEDIUM,
            "curated differentiation query — inherently uncertain/subjective",
            False,
        )

    # ── Early exit: movie listing queries are always direct_factual ───
    # "what movies are showing?", "show me movies", "now showing", etc.
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

    # ── Early exit: short kid-filter on entertainment context ─────────
    # "with kid" / "with kids" after a movie/entertainment session should
    # stay in direct_factual (filter, not a new request).
    if any(p in raw for p in _KID_FILTER_PATTERNS) and len(raw.split()) <= 3:
        if (
            scene.active_topic in ("entertainment", "movies", "cinema")
            or scene.topic_lock in _FACTUAL_LOCKED_TOPICS
        ):
            return (
                DIRECT_FACTUAL,
                CONFIDENCE_HIGH,
                f"kid-filter on {scene.active_topic or scene.topic_lock} → direct factual",
                False,
            )

    # ── Step 2G: follow-up resolution (topic lock short-circuit) ─────
    # Short follow-up with a strong topic lock:  context resolves the ambiguity,
    # so we must NOT classify the query as vague.
    is_short = len(raw.split()) <= 5
    has_active_lock = bool(scene.topic_lock and scene.topic_lock_confidence >= 0.5)
    is_followup_kind = msg_kind in ("followup", "refinement", "constraint_refinement")

    # Skip the topic-lock short-circuit for vague queries — vague follow-ups
    # (e.g. "something affordable", "i'm bored") should not be promoted to
    # guided_recommendation; they belong in best_effort_shortlist instead.
    if is_short and is_followup_kind and has_active_lock and not _is_vague_query(state):
        # Promote confidence: context resolves the ambiguity
        if confidence_level == CONFIDENCE_LOW:
            confidence_level = CONFIDENCE_MEDIUM

        locked_topic = scene.topic_lock
        if locked_topic in _FACTUAL_LOCKED_TOPICS:
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"short follow-up resolved via topic_lock={locked_topic!r}",
                False,
            )
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            f"short follow-up in active context (topic_lock={locked_topic!r})",
            False,
        )

    # ── Step 2F: graceful recovery — unsupported / low confidence ─────
    # Hard unsupported/error signals always recover gracefully.
    # Low confidence alone does NOT trigger recovery when strong visit context
    # is already established (mid-conversation) — the classifier may assign low
    # scores to complex or nuanced phrasing, but the intent is still clear from
    # scene context.  Fall through to planning/cross-domain/guided checks.
    is_hard_unsupported = (
        primary_intent == "unsupported"
        or intent.raw_signals.get("unsupported", False)
    )
    if is_hard_unsupported or (
        confidence_level == CONFIDENCE_LOW and not _has_strong_visit_context(state)
    ):
        return (
            GRACEFUL_RECOVERY,
            confidence_level,
            "unsupported input or low confidence — graceful recovery",
            True,
        )
    # If strong context exists but confidence is LOW, promote to MEDIUM so
    # downstream steps can route correctly.
    if confidence_level == CONFIDENCE_LOW and _has_strong_visit_context(state):
        confidence_level = CONFIDENCE_MEDIUM

    # ── Step 2C: context-setting → context_acknowledgement ───────────
    if msg_kind == "context_setting":
        # Exception: if the opener also embeds an explicit entertainment/exploration
        # request (e.g. "we're 5 teens — what's fun to do here?"), respond with a
        # diverse shortlist that simultaneously acknowledges the group.
        # NOTE: "what's here for me?" is a wide open concierge question → stays as
        # context_acknowledgement because the profile part dominates.
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

    # ── Step 2C-post: explicit planning/scheduling → hybrid_plan ─────
    if _has_planning_intent(state):
        return (
            HYBRID_PLAN,
            confidence_level,
            "explicit planning/itinerary/scheduling request",
            False,
        )

    # ── Step 2D: cross-domain / multi-intent → hybrid_plan ───────────
    if _has_cross_domain_intent(state):
        # Very short queries ("food and movies") are ambiguous cross-domain
        # requests where the intent is inferred, not explicit — cap to medium.
        # Longer explicit phrasings ("we want to watch a movie and grab dinner")
        # retain the computed confidence level.
        hybrid_confidence = confidence_level
        if len(raw.split()) <= 4 and confidence_level == CONFIDENCE_HIGH:
            hybrid_confidence = CONFIDENCE_MEDIUM
        return (
            HYBRID_PLAN,
            hybrid_confidence,
            "cross-domain intent detected — combine into one unified plan",
            False,
        )

    # ── Step 2A / 2B: confidence-driven branching ─────────────────────

    if confidence_level == CONFIDENCE_HIGH:
        # Factual queries → direct_factual (never override factual routing)
        if state.flow_type == "factual":
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"factual flow with high confidence: {intent.sub_intent}",
                False,
            )
        # Precise sub-intent in concierge flow → guided_recommendation.
        # NOTE: We do NOT restrict by domain here because concierge-routed
        # queries span entertainment, navigation, and other domains — all of
        # them deserve a recommendation response, not a raw factual answer.
        # (Factual routing is handled exclusively by the factual path above.)
        if intent.sub_intent in _PRECISE_SUB_INTENTS:
            return (
                GUIDED_RECOMMENDATION,
                confidence_level,
                f"precise sub_intent ({intent.sub_intent}) in concierge flow → recommendation",
                False,
            )
        # Strong playbook match → guided_recommendation
        if playbook.selected_playbook and playbook.playbook_confidence > 0.4:
            return (
                GUIDED_RECOMMENDATION,
                confidence_level,
                f"strong playbook ({playbook.selected_playbook}, "
                f"conf={playbook.playbook_confidence:.2f})",
                False,
            )
        # Default high-confidence
        return (
            GUIDED_RECOMMENDATION,
            confidence_level,
            f"high confidence, clear intent: {primary_intent}",
            False,
        )

    # medium confidence
    if confidence_level == CONFIDENCE_MEDIUM:
        # Factual-flow queries are always direct lookups — return direct_factual
        # regardless of confidence level.  Planning/cross-domain short-circuits
        # (hybrid_plan) have already been handled above this point.
        # Exception: "is there anywhere to [verb]" phrasing signals a RECOMMENDATION
        # request rather than an existence/availability check (e.g. "is there anywhere
        # to sit and chill?" vs "is there face painting here?") — let it fall through
        # to guided_recommendation.
        _RECOMMENDATION_PHRASES = ("is there anywhere to ", "anywhere i can ", "somewhere to ")
        if state.flow_type == "factual" and not any(p in raw for p in _RECOMMENDATION_PHRASES):
            return (
                DIRECT_FACTUAL,
                confidence_level,
                f"factual flow with medium confidence: {intent.sub_intent}",
                False,
            )
        # 2B: vague query with partial confidence → best_effort_shortlist
        #     Queries matching explicit vague raw patterns (e.g. "something affordable",
        #     "i'm bored") always get best_effort_shortlist even with strong visit context.
        #     Other vague queries defer to guided_recommendation when context is strong.
        if _is_vague_query(state):
            is_raw_vague_pattern = any(p in raw for p in _VAGUE_RAW_PATTERNS)
            if not _has_strong_visit_context(state) or is_raw_vague_pattern:
                return (
                    BEST_EFFORT_SHORTLIST,
                    confidence_level,
                    "vague/exploratory query with medium confidence — safe shortlist",
                    True,
                )
            # With strong context and no raw vague pattern, fall through to guided_recommendation
        # Known domain but vague sub-intent (e.g. general_shopping with modifiers)
        # Only apply when there is NO strong visit context
        if intent.domain not in ("general",) and intent.sub_intent in _VAGUE_SUB_INTENTS:
            if not _has_strong_visit_context(state):
                return (
                    BEST_EFFORT_SHORTLIST,
                    confidence_level,
                    f"known domain ({intent.domain}) but vague sub_intent ({intent.sub_intent})",
                    True,
                )
        # Partial confidence with identifiable intent → guided_recommendation
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
    return (
        GRACEFUL_RECOVERY,
        confidence_level,
        "fallback: no clear mode could be determined",
        True,
    )
