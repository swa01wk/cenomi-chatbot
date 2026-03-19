"""
Route Flow node — determines the active flow type for this turn.

CONTRACT
────────
  Purpose:  Examine intent signals, scene context, and session history to
            make the authoritative routing decision: "concierge" or "factual".
            Sets flow_type, flow_routing_reason, retrieval_priority, and the
            hybrid intent bundle (primary_intent, secondary_intents, modifiers,
            dominant_context_type).
            Also resolves domain_locked, response_strategy, and filter_applied.
  Reads:    intent (including flow_type_candidate, primary_intent, secondary_intents,
            modifiers hints), scene, last_flow_type (from scene), raw_user_message
  Writes:   flow_type, flow_routing_reason, retrieval_priority, primary_intent,
            secondary_intents, modifiers, dominant_context_type,
            domain_locked, response_strategy, filter_applied
  Failure:  Falls back to concierge to preserve existing behavior
  Routing:  → graph builder uses flow_type to branch to concierge or factual path

Routing rules (in priority order):
  0. Domain lock: active factual primary intent → enforce factual, treat
     companion/scene signals as filters only (unless explicit topic switch)
  1. Cross-mall queries → factual
  2. Explicit factual sub-intents → factual (UNLESS planning modifiers override)
  3. Navigation domain → factual
  4. Hard factual keyword signals → factual
  5. Strong concierge sub-intents → concierge
  6. Scene context → concierge (only if NOT a factual primary intent)
  7. Hard concierge keyword signals → concierge
  8. Follow-up of a factual turn → factual
  9. Constraint refinement → inherit prior flow
  10. intent_candidate hint
  11. Default → concierge
"""

from __future__ import annotations

import logging

from app.models.state import ConciergeState
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# Sub-intents that require factual flow regardless of scene
# NOTE: "offer_details" intentionally excluded — offer/sale queries in a shopping
# context are recommendation-style ("any stores with sales?"), not raw data lookups.
_FACTUAL_SUB_INTENTS: frozenset[str] = frozenset({
    "movie_showtime",
    "opening_hours",
    "store_hours",
    "location_query",
    "service_info",
    "prayer_room",
    "parking_info",
    "cross_mall_search",
    "brand_availability",   # "do you have X" / "is X here" needs exact presence check
})

# Domains where factual flow is almost always correct
_FACTUAL_DOMAINS: frozenset[str] = frozenset({
    "navigation",
    "cross_mall",
    "mall_info",   # Mall overview, hours, family-friendliness → exact factual data
})

# Sub-intents that strongly prefer concierge flow
_CONCIERGE_SUB_INTENTS: frozenset[str] = frozenset({
    "open_exploration",
    "activity_suggestion",
    "first_visit_guide",
    "general_dining",
    "family_dining",
    "romantic_dining",
    "gift_recommendation",
    "perfume_shopping",
    "jewelry_shopping",
    "accessories_shopping",
    "fashion_shopping",
    "general_entertainment",
    "general_shopping",
    "cafe_recommendation",
    "dessert_recommendation",
})

# Keywords that anchor a query in factual retrieval
# NOTE: "show me movies" intentionally excluded — it reads as a recommendation
# request ("show me options"), not a raw listing query. "what movies do you have"
# and "now showing" remain factual.
_FACTUAL_HARD_SIGNALS: tuple[str, ...] = (
    "what movies", "which movies", "movies do we have", "movies can i watch",
    "movies can i see", "now showing", "what's playing", "what is playing",
    "all movies", "movie list", "showtimes", "show times",
    "movies are showing", "movies are there",
    "movies are on", "movies can i see",
    # Price queries → factual (data lookup)
    "whats the price", "what's the price", "what is the price",
    "where is the atm", "where is atm", "atm location",
    "prayer room location", "where is the prayer",
    "opening hours", "closing hours", "what time do you close",
    "when do you open", "when do you close", "what are your hours",
    "what time does the mall",
    "do you have zara", "is zara here", "do you have nike",
    "is nike here", "is nike at",
    "is starbucks here", "do you have starbucks",
    "is adidas here", "do you have adidas",
    "do they have nike", "do they have adidas",
    "do they have like nike", "do they have like adidas",
    "wants nike or adidas", "nike or adidas shoes",
    "where is muvi", "where is vox", "where is the cinema",
    "do you have a cinema", "is there a cinema",
    "do you have prayer room", "do you have strollers",
    "where is h&m", "is h&m here",
    "does mall of arabia", "is it at mall of arabia", "which malls have",
    "any of your malls", "across malls",
    # ── Facility / availability presence queries ─────────────────────
    "is there face painting",
    "is arabic coffee",
    "arabic coffee sold",
    "is there a gym",
    "is there a fitness",
    "does the mall have a gym",
    "does the mall pause",
    "do stores close during",
    "is there anywhere to charge",
    "is there a stationery",
    "do you have a stationery",
    "is there a prayer room",
    "where are the prayer rooms",
    "where is the changing room",
    "is there a baby changing",
    "is there a family toilet",
    "is there a supplement",
    "do you have supplement",
    "is there a salon",
    "where can i charge",
    "is there anywhere that does",
    # Cake / bakery availability and custom-order capability
    "can i get a birthday cake",
    "can i get a cake",
    "is there a bakery",
    "do you have a bakery",
    "where can i get a cake",
    "do any of them do custom",
    "do they do custom cakes",
    "custom cakes with",
    # Stroller/accessibility factual
    "stroller available",
    "borrow a stroller",
    "rent a stroller",
    "wheelchair available",
    "is the mall accessible",
    "is there a lift",
    "is there an elevator",
    # Charging / power
    "is there anywhere to charge",
    "phone charging",
    "charging station",
    # Supplement / nutrition stores
    "supplement or nutrition stores",
    "do you have any supplement",
    "supplement stores",
    "nutrition store",
    # Specific store-type availability queries
    "do you have any uniform", "do you have any supplement",
    "do you have any nutrition", "do you have a bakery",
    "do you have any bakery",
    # Pre-packed bundles / specific stock queries
    "pre-packed school supply",
    "do they do pre-packed",
    "school supply bundles",
    # Meeting / briefing spaces
    "meeting or briefing spaces",
    "briefing spaces",
    "meeting rooms in the mall",
    # Ticket pricing
    "how much is a ticket",
    "how much does a ticket",
    "how much for a ticket",
    "price of a ticket",
    "ticket price",
    "ticket prices",
    "ticket for 5",
    "tickets for",
)

# Primary intent labels that should always stay in factual flow even when
# companion/scene context is present (family acts as FILTER, not replacement)
_FACTUAL_PRIMARY_INTENTS: frozenset[str] = frozenset({
    "movie_lookup",
    "location_lookup",
    "mall_fact_lookup",
    "service_lookup",
    "cross_mall_lookup",
    "offer_lookup",
    "brand_availability",
    "movie_showtime",
    "store_hours",
    "opening_hours",
    "mall_overview",   # "tell me about the mall", "what does this mall have?" etc.
})

# ── Response strategy map ────────────────────────────────────────────────────
# Maps primary_intent → canonical response strategy name.
# Secondary filters can upgrade the strategy (e.g. movie_lookup + family_filter
# → filtered_factual_list instead of factual_list).
_PRIMARY_INTENT_RESPONSE_STRATEGY: dict[str, str] = {
    "movie_lookup":              "factual_list",
    "movie_showtime":            "factual_list",
    "brand_availability":        "factual_presence",
    "location_lookup":           "factual_presence",
    "mall_fact_lookup":          "structured_overview",
    "service_lookup":            "factual_list",
    "cross_mall_lookup":         "factual_presence",
    "offer_lookup":              "factual_list",
    "store_hours":               "factual_presence",
    "opening_hours":             "structured_overview",
    "shopping_recommendation":   "guided_plan",
    "dining_recommendation":     "guided_plan",
    "gift_recommendation":       "guided_plan",
    "concierge_recommendation":  "guided_plan",
    "exploration":               "guided_plan",
}

# Secondary filter combinations that upgrade the response strategy
_FILTER_STRATEGY_UPGRADES: dict[tuple[str, str], str] = {
    ("movie_lookup",   "family_filter"):   "filtered_factual_list",
    ("movie_lookup",   "kid_friendly"):    "filtered_factual_list",
    ("movie_lookup",   "budget_filter"):   "filtered_factual_list",
    ("service_lookup", "family_filter"):   "filtered_factual_list",
    ("movie_showtime", "family_filter"):   "filtered_factual_list",
    ("movie_showtime", "kid_friendly"):    "filtered_factual_list",
}

# Keywords that, when present in a message, indicate an EXPLICIT domain switch
# away from the currently locked factual domain. Conservative — should only
# trigger for unambiguous domain-change phrasing.
_EXPLICIT_DOMAIN_SWITCH_SIGNALS: tuple[str, ...] = (
    "show me restaurants", "find me a restaurant", "where to eat",
    "recommend a restaurant", "dining recommendation", "good place to eat",
    "suggest a restaurant",
    "shopping for", "looking to buy", "i want to buy", "what stores",
    "gift for", "present for", "buy something for",
    "mall hours",
    "opening hours", "when does the mall open", "when do you open",
    "where is the", "how do i get to", "how to get to",
    "kids zone", "play area", "activity for kids",
    "what can we do", "what should we do",
    # ── Movie recommendation queries (break movie domain lock) ────────
    "good movie for", "best movie for", "recommend a movie",
    "movie for couples", "good movies for couples",
    "what movie", "suggest a movie",
    # ── "Can we fit everything" → planning mode ───────────────────────
    "can we fit", "fit in movies", "fit in everything",
    # ── Salon / beauty recommendation (breaks service_lookup lock) ────
    "where can we all go", "where can we get a blow",
    "we all want to get", "blow-dry and makeup",
    # ── Ambiance / social queries that should break factual domain lock ──
    "somewhere we can sit", "somewhere not too loud", "somewhere not too crowded",
    "we want somewhere", "somewhere we all", "somewhere quiet",
    # ── Priority / planning queries break factual lock ────────────────
    "what's the priority order", "priority order for",
    "what if i can't find",
)

# Keywords that anchor a query in concierge planning
_CONCIERGE_HARD_SIGNALS: tuple[str, ...] = (
    "something quick before the movie",
    "something to do before",
    "before the movie",
    "after the movie",
    "suggest something",
    "recommend something",
    "what can we do",
    "what should we do",
    "plan for",
    "date night",
    "date plan",
    "gift for my",
    "gift for her",
    "gift for him",
    "with my 5", "with my 4", "with my 6", "with my 7",
    "with my kid", "with my child", "with my daughter", "with my son",
    "with my girlfriend", "with my boyfriend", "with my wife", "with my husband",
    "something fun for",
    # ── Routing / optimisation / recommendation queries ──────────────
    "most efficient order",
    "most efficient route",
    "what's the best order",
    "best order to visit",
    "best value for money",
    "value for money",
    "minimize walking",
    "minimise walking",
    "good movie for",
    "best movie for",
    "recommend a movie",
    "good movies for couples",
    "movies for couples",
    "what should i prioritize",
    "what should we prioritize",
    "what to prioritize",
    # Proximity constraints in dining context ("near cinema" after "something quick")
    "near cinema",
    "near the cinema",
    "close to cinema",
    # Ambiance / atmosphere refinements → concierge recommendation, not factual lookup
    "not too loud", "somewhere not too loud", "not too crowded",
    "somewhere quiet", "quiet atmosphere", "quieter dining",
    "somewhere we can sit", "we can sit together",
    "sit as a group", "sit together as a group",
    # Competitive / social activity for groups → concierge
    "challenges or competitions", "competitions we can do",
    "any challenges", "competitive activities",
    "something competitive", "fun competition",
    # Priority / ordering recommendations
    "priority order", "what's the priority", "priority of",
    "what if i can't find everything",
    # Photo / experiential spots → subjective recommendation
    "cool backdrops", "any cool backdrops", "cool photo spots",
    "instagram-worthy spots", "instagrammable spots",
    "nice photo spots", "photo together",
    # Cost comparison / value opinion queries → concierge recommendation
    "cheapest of those", "cheapest one", "which is cheapest",
    "best value of those", "most affordable of those",
    "the cheapest option",
    # Cross-domain evening/night plans
    "dinner and then a movie",
    "dinner and a movie",
    "movie and dinner",
    "and then a movie",
    "as a full night",
    "full evening",
    "full night out",
    # Movie recommendation requests (not raw listings) → concierge
    # "show me movies" reads as "show me options" (recommendation), not a raw listing
    "show me movies",
    "show me films",
    # Time-pressure dining with movie timing constraint → concierge
    # "something quick, movie starts in 40 mins" = dining request with time constraint,
    # NOT a movie showtime lookup. Must stay in concierge / dining recommendation flow.
    "movie starts in",
    "film starts in",
    "starts in 30", "starts in 40", "starts in 45", "starts in an hour",
    "our movie starts",
    "the movie starts",
)


@traced_node("route_flow")
async def route_flow(state: ConciergeState) -> dict:
    intent = state.intent
    scene = state.scene
    msg = (state.normalized_user_message or state.raw_user_message).lower()

    flow_type = ""
    routing_reason = ""
    retrieval_priority = "medium"
    domain_locked = False

    # Promote hybrid bundle from intent into local vars for clarity
    primary_intent = intent.primary_intent or ""
    secondary_intents = list(intent.secondary_intents)
    modifiers = list(intent.modifiers)
    dominant_context_type = state.dominant_context_type or ""

    # Inherit modifiers from prior scene memory (for follow-up turns)
    for f in (scene.active_secondary_filters or []):
        if f not in secondary_intents:
            secondary_intents.append(f)
    for m in (scene.active_modifiers or []):
        if m not in modifiers:
            modifiers.append(m)

    # ── 0a. "Show me movies" → concierge (recommendation, not raw listing) ──
    # Must run before factual routing so we get guided_recommendation, not direct_factual.
    # Check both normalized and raw (normalization may vary)
    raw_msg = (state.raw_user_message or "").lower()
    if not flow_type and any(
        sig in msg or sig in raw_msg
        for sig in ("show me movies", "show me films")
    ):
        flow_type = "concierge"
        routing_reason = (
            "'Show me movies' reads as recommendation request — display films, "
            "offer to filter by genre/age; route to concierge"
        )
        if not primary_intent:
            primary_intent = "movie_recommendation"

    # ── 0b. "Near cinema" as proximity constraint in concierge session → stay concierge ──
    # "near cinema" after "something quick" is adding a location filter, not a new movie lookup
    if not flow_type and scene.last_flow_type == "concierge" and any(
        sig in msg or sig in raw_msg for sig in ("near cinema", "near the cinema")
    ):
        flow_type = "concierge"
        routing_reason = (
            "Proximity constraint ('near cinema') in active concierge session "
            "→ concierge (location filter, not standalone factual lookup)"
        )

    # ── 0c. Dining intent + cinema proximity → always concierge ─────
    # "i want to eat near the cinema" / "food near the cinema" etc.
    # The proximity phrase is a LOCATION MODIFIER on the dining intent, not a
    # standalone cinema lookup.  This must run before Rule 2 (factual sub-intents)
    # which would otherwise claim any location_query sub_intent for factual flow.
    _DINING_INTENT_SIGNALS: tuple[str, ...] = (
        "eat", "food", "restaurant", "dining", "hungry", "grab",
        "bite", "lunch", "dinner", "breakfast", "snack",
    )
    _CINEMA_PROXIMITY_SIGNALS: tuple[str, ...] = (
        "near cinema", "near the cinema", "near vox", "near muvi",
        "close to cinema", "close to the cinema", "by the cinema",
        "next to cinema", "next to the cinema",
    )
    if not flow_type and any(
        ds in msg for ds in _DINING_INTENT_SIGNALS
    ) and any(
        cp in msg for cp in _CINEMA_PROXIMITY_SIGNALS
    ):
        flow_type = "concierge"
        routing_reason = (
            "Dining intent with cinema proximity modifier → concierge "
            "('near cinema' is a location filter, not a cinema lookup)"
        )
        if not primary_intent:
            primary_intent = "dining_recommendation"

    # ── 0. Domain lock: preserve factual primary intent across turns ──
    # When the user established a factual domain (e.g. movie_lookup) in a prior
    # turn, subsequent follow-ups MUST stay in that domain.
    # Companion/scene signals (e.g. "with kid") are treated as FILTERS, not
    # intent replacements.  Only an explicit topic switch releases the lock.
    prior_factual_intent = scene.active_primary_intent or ""
    if prior_factual_intent in _FACTUAL_PRIMARY_INTENTS:
        # Sub-intents that signal a genuine cross-domain switch (dining/shopping).
        # These release an active factual lock (e.g. movie → dining request).
        # Exploration/activity sub-intents are intentionally excluded — they can
        # appear on companion follow-ups ("with kid") that should stay factual.
        _DOMAIN_SWITCH_SUB_INTENTS: frozenset[str] = frozenset({
            "general_dining", "romantic_dining", "quick_bite", "family_dining",
            "cafe_recommendation", "dessert_recommendation",
            "general_shopping", "gift_recommendation", "fashion_shopping",
            "perfume_shopping", "jewelry_shopping", "accessories_shopping",
            # Activity/exploration sub-intents break a factual lock in multi-step
            # planning sessions (e.g. after stroller/accessibility queries in S5)
            "activity_suggestion",
            "open_exploration",
            "general_entertainment",
            # Companion/family context revelation breaks a factual movie lock.
            # "oh wait im with my 7 year old" → should switch to guided recommendation
            # (filter movies for kid-appropriate), not stay as factual movie listing.
            "family_filter",
            "companion_context",
        })
        is_explicit_switch = (
            intent.message_kind == "topic_switch"
            or _has_explicit_domain_switch(msg)
            or intent.sub_intent in _DOMAIN_SWITCH_SUB_INTENTS
            # Any concierge planning/recommendation signal overrides factual domain lock
            or any(sig in msg for sig in _CONCIERGE_HARD_SIGNALS)
        )
        if not is_explicit_switch:
            flow_type = "factual"
            routing_reason = (
                f"Domain lock: prior factual intent '{prior_factual_intent}' preserved; "
                f"secondary signals {secondary_intents or []} applied as filters only"
            )
            retrieval_priority = "high"
            # Inherit primary intent from scene when the current turn hasn't set one
            if not primary_intent or primary_intent not in _FACTUAL_PRIMARY_INTENTS:
                primary_intent = prior_factual_intent
            domain_locked = True

    # ── 1. Always-factual: cross-mall ────────────────────────────────
    if not flow_type and (
        intent.domain == "cross_mall" or intent.sub_intent == "cross_mall_search"
    ):
        flow_type = "factual"
        routing_reason = "Cross-mall brand availability query requires exact retrieval-first handling"
        retrieval_priority = "high"
        if not primary_intent:
            primary_intent = "cross_mall_lookup"

    # ── 2. Hard factual sub-intents ──────────────────────────────────
    elif not flow_type and intent.sub_intent in _FACTUAL_SUB_INTENTS:
        # Special case: location_query as a constraint_refinement in a concierge
        # session should stay concierge. "near cinema" after "something quick"
        # (concierge) is a proximity filter, not a pure location lookup.
        if (
            intent.sub_intent == "location_query"
            and intent.message_kind == "constraint_refinement"
            and scene.last_flow_type == "concierge"
        ):
            flow_type = "concierge"
            routing_reason = (
                "Location proximity constraint_refinement in active concierge session "
                "→ concierge (proximity filter, not a standalone location lookup)"
            )

        else:
            # Factual sub-intent wins UNLESS the query is clearly about
            # planning *around* the movie (e.g. "something quick before the movie").
            # NOTE: "any movies with the kid?" is factual — kid is a FILTER, not override.
            has_concierge_hard = any(sig in msg for sig in _CONCIERGE_HARD_SIGNALS)
            # Cross-domain secondary intents (e.g. add_dining_step from "food and movies")
            # also signal a hybrid planning query that must go to concierge.
            _CROSS_DOMAIN_SECONDARY_SET: frozenset[str] = frozenset({
                "add_dining_step", "add_coffee_step",
                "before_movie_constraint", "after_movie_constraint",
            })
            has_cross_domain_secondary = bool(
                set(secondary_intents) & _CROSS_DOMAIN_SECONDARY_SET
            )
            is_planning_hybrid = (
                intent.sub_intent == "movie_showtime"
                and (
                    any(kw in msg for kw in ("before ", "after ", "plan ", "suggest"))
                    or has_cross_domain_secondary
                )
                and not _is_pure_lookup(msg)
            )
            # Explicit domain-switch signals should also override factual sub-intent routing.
            has_explicit_switch = _has_explicit_domain_switch(msg)
            if is_planning_hybrid or has_explicit_switch or (has_concierge_hard and not _is_pure_lookup(msg)):
                flow_type = "concierge"
                routing_reason = (
                    f"Hybrid query: sub_intent={intent.sub_intent} but planning/switch context "
                    f"('before/after/suggest/explicit-switch') dominates — routing to concierge"
                )
                # Always override primary_intent to a non-factual label so the next
                # turn doesn't inherit a factual domain lock (e.g. movie_lookup).
                primary_intent = "concierge_recommendation"
            else:
                flow_type = "factual"
                routing_reason = (
                    f"Factual sub-intent '{intent.sub_intent}' is primary; "
                    f"secondary_intents={secondary_intents} act as filters"
                )
                retrieval_priority = "high"
                if not primary_intent:
                    primary_intent = intent.sub_intent

    # ── 2b. Movie listing hard signals — force factual even with scene context ──
    # "what movies are showing", "now showing", etc. are factual lookups.
    # Exception: "what movies" in a concierge continuation (followup/constraint_refinement
    # after a concierge turn) stays concierge — user is asking for recommendations,
    # not a raw listing. E.g. "ok after, what movies" after a dining planning session.
    elif not flow_type and any(sig in msg for sig in _FACTUAL_HARD_SIGNALS) and any(
        kw in msg for kw in ("movie", "movies", "cinema", "film", "showing", "playing")
    ):
        has_concierge_hard = any(sig in msg for sig in _CONCIERGE_HARD_SIGNALS)
        # Check if this is a movie query in the context of an ongoing concierge session
        is_concierge_continuation = (
            scene.last_flow_type == "concierge"
            and intent.message_kind in ("followup", "constraint_refinement", "refinement")
        )
        if (has_concierge_hard and not _is_pure_lookup(msg)) or is_concierge_continuation:
            flow_type = "concierge"
            routing_reason = "Movie query in planning/concierge context — routing to concierge"
        else:
            flow_type = "factual"
            routing_reason = "Movie listing hard signal — factual flow enforced"
            retrieval_priority = "high"
            if not primary_intent:
                primary_intent = "movie_lookup"

    # ── 3. Navigation domain → always factual ────────────────────────
    # Exception: context_setting turns (e.g. "i am here with my family" classified
    # as mall_info/family_friendliness) must be routed via concierge so they receive
    # a context_acknowledgement response, not a raw factual lookup.
    elif not flow_type and intent.domain in _FACTUAL_DOMAINS and intent.message_kind != "context_setting":
        flow_type = "factual"
        routing_reason = f"Domain '{intent.domain}' routes to factual flow by design"
        retrieval_priority = "high"
        if not primary_intent:
            primary_intent = "location_lookup"

    # ── 4. Hard factual keyword signals ──────────────────────────────
    elif not flow_type and any(sig in msg for sig in _FACTUAL_HARD_SIGNALS):
        has_concierge_hard = any(sig in msg for sig in _CONCIERGE_HARD_SIGNALS)
        # Factual keyword wins even when family/companion context present,
        # because family context should be a FILTER not an intent replacement.
        if has_concierge_hard and not _is_pure_lookup(msg):
            flow_type = "concierge"
            routing_reason = "Factual keyword detected but concierge planning signals override"
        else:
            flow_type = "factual"
            routing_reason = (
                "Explicit factual lookup keyword signals detected in message; "
                f"secondary_intents={secondary_intents} will filter results"
            )
            retrieval_priority = "high"

    # ── 5. Strong concierge sub-intents ──────────────────────────────
    elif not flow_type and intent.sub_intent in _CONCIERGE_SUB_INTENTS:
        flow_type = "concierge"
        routing_reason = f"Concierge sub-intent '{intent.sub_intent}' triggers planning flow"

    # ── 6. Scene context → concierge — but ONLY if primary intent is not factual ──
    elif not flow_type and (
        _has_strong_scene_context(scene) and primary_intent not in _FACTUAL_PRIMARY_INTENTS
    ):
        flow_type = "concierge"
        routing_reason = (
            "Visitor scene context (companions/occasion/visit_type) triggers concierge; "
            f"primary_intent={primary_intent or 'unset'}"
        )

    # ── 7. Hard concierge keyword signals ─────────────────────────────
    # NOTE: companion signals ("with my kid") are NOT allowed to override an
    # active factual primary intent — domain lock (Rule 0) handles those cases.
    elif not flow_type and any(sig in msg for sig in _CONCIERGE_HARD_SIGNALS):
        # If there's an active factual intent in scene, these signals are filters
        if primary_intent in _FACTUAL_PRIMARY_INTENTS:
            flow_type = "factual"
            routing_reason = (
                f"Concierge signal present but primary_intent='{primary_intent}' is "
                "factual — companion/scene signal treated as secondary filter"
            )
            retrieval_priority = "high"
            domain_locked = True
        else:
            flow_type = "concierge"
            routing_reason = "Explicit planning/suggestion keyword signals detected"

    # ── 7b. Context-setting → concierge + acknowledge scene ─────────
    # "i am bridesmaid", "i am here with the kid", etc. are scene context
    # declarations.  They MUST go concierge for acknowledgement + options.
    # They must NEVER be routed to factual even if family/companion signals
    # are present.
    elif not flow_type and intent.message_kind == "context_setting":
        flow_type = "concierge"
        routing_reason = (
            "Context-setting message: user declared role/companions/occasion. "
            "Routing to concierge for scene acknowledgement and next-step options."
        )
        # Ensure the response plan acknowledges the scene
        if not primary_intent:
            primary_intent = "discovery"

    # ── 8. Follow-up of a factual turn → stay factual ─────────────
    elif not flow_type and (
        scene.last_flow_type == "factual"
        and intent.message_kind in ("followup", "refinement")
    ):
        if intent.domain in ("dining", "shopping") and _has_strong_scene_context(scene):
            flow_type = "concierge"
            routing_reason = "User pivoted from factual follow-up into planning/dining context"
        else:
            flow_type = "factual"
            routing_reason = "Follow-up of previous factual turn — maintaining factual flow"
            retrieval_priority = "medium"
            # Inherit primary intent from scene memory for follow-ups
            if not primary_intent and scene.active_primary_intent:
                primary_intent = scene.active_primary_intent

    # ── 9. Constraint refinement stays in prior flow ──────────────
    elif not flow_type and intent.message_kind == "constraint_refinement":
        prior_flow = scene.last_flow_type or "concierge"
        flow_type = prior_flow
        routing_reason = f"Constraint refinement continues prior {prior_flow} flow"
        if not primary_intent and scene.active_primary_intent:
            primary_intent = scene.active_primary_intent

    # ── 10. Use interpret_turn's candidate hint ────────────────────
    elif not flow_type and intent.flow_type_candidate:
        flow_type = intent.flow_type_candidate
        routing_reason = f"Flow type set from interpret_turn hint: {flow_type}"
        if flow_type == "factual":
            retrieval_priority = "high"

    # ── 11. Default: concierge (safe fallback) ─────────────────────
    if not flow_type:
        flow_type = "concierge"
        routing_reason = "Default concierge flow — no strong factual signals detected"

    # ── Resolve response strategy ─────────────────────────────────────
    # Determine the canonical response strategy from primary_intent +
    # secondary_filters. This controls which response composer is used.
    response_strategy = _resolve_response_strategy(
        primary_intent, secondary_intents, modifiers, flow_type,
    )

    # ── Determine filter_applied flag ─────────────────────────────────
    filter_applied = bool(secondary_intents or modifiers) and flow_type == "factual"

    # ── Derive topic_lock for this turn ──────────────────────────────
    # topic_lock is set/updated by update_memory; here we just read it for
    # observability and compute a stability score for this turn.
    current_topic_lock = scene.topic_lock
    current_lock_confidence = scene.topic_lock_confidence

    # If flow_type and primary_intent are consistent with the lock, reinforce it.
    # If they diverge significantly (topic_switch), lower the confidence.
    if current_topic_lock and intent.message_kind == "topic_switch":
        current_lock_confidence = max(0.0, current_lock_confidence - 0.4)
    elif current_topic_lock and intent.message_kind in ("followup", "constraint_refinement"):
        current_lock_confidence = min(1.0, current_lock_confidence + 0.05)

    logger.info(
        "route_flow: %s → %s (primary=%s secondary=%s modifiers=%s priority=%s "
        "locked=%s strategy=%s topic_lock=%s lock_conf=%.2f) | %s",
        f"{intent.domain}/{intent.sub_intent}",
        flow_type,
        primary_intent,
        secondary_intents,
        modifiers,
        retrieval_priority,
        domain_locked,
        response_strategy,
        current_topic_lock,
        current_lock_confidence,
        routing_reason,
    )

    return {
        "flow_type": flow_type,
        "flow_routing_reason": routing_reason,
        "retrieval_priority": retrieval_priority,
        "primary_intent": primary_intent,
        "secondary_intents": secondary_intents,
        "modifiers": modifiers,
        "dominant_context_type": dominant_context_type,
        "domain_locked": domain_locked,
        "response_strategy": response_strategy,
        "filter_applied": filter_applied,
        "debug_enrichment": {
            "flow_type": flow_type,
            "flow_routing_reason": routing_reason,
            "retrieval_priority": retrieval_priority,
            "domain_locked": domain_locked,
            "response_strategy_resolved": response_strategy,
            "filter_applied": filter_applied,
            "topic_lock": current_topic_lock,
            "topic_lock_confidence": round(current_lock_confidence, 2),
        },
        "_trace_summary": (
            f"Flow routed → {flow_type} primary={primary_intent} "
            f"secondary={secondary_intents} locked={domain_locked} "
            f"strategy={response_strategy} (priority={retrieval_priority}) "
            f"topic_lock={current_topic_lock or 'none'} | "
            f"{routing_reason[:80]}"
        ),
    }


def _has_strong_scene_context(scene) -> bool:
    """
    True when the scene has enough context to warrant concierge treatment.

    Child/kid companions alone are intentionally excluded from ``has_companions``
    because child context is a *bias* (filter on results), not a hard intent
    override.  "any movies with the kid?" must stay factual even though "child"
    is in companions.  The family_visit *visit_type* still counts — it is set
    only when the user explicitly declares a family outing, not just when a
    child companion is detected.
    """
    # Strong companions: explicit relationship companions that imply concierge
    # context.  "child", "kids", "son", "daughter" are EXCLUDED — they act as
    # audience filters, not as intent overrides.
    _CHILD_COMPANION_TYPES: frozenset[str] = frozenset({
        "child", "kids", "son", "daughter",
    })
    has_companions = bool(
        scene.companions
        and any(c not in {"solo"} | _CHILD_COMPANION_TYPES for c in scene.companions)
    )
    has_occasion = bool(scene.occasion and scene.occasion not in ("before_movie", "after_movie"))
    has_visit_type = bool(scene.visit_type)
    has_goal = bool(scene.goal or scene.implicit_goal)
    has_scenario = bool(scene.scenario)
    return has_companions or has_occasion or has_visit_type or has_goal or has_scenario


def _is_pure_lookup(msg: str) -> bool:
    """
    True when the message reads like a direct factual lookup
    with no planning/context layer.

    Note: companion signals like "with my kid" on a movie query are filters,
    not planning context — "any movies with my kid" is a filtered factual lookup.
    """
    pure_lookup_patterns = (
        "what movies", "which movies", "all movies", "movie list",
        "any movies", "movies with", "movies for",  # filtered movie lookups
        "where is the", "where's the", "location of",
        "what time does", "what time do you", "opening hours",
        "do you have ", "is there a ", "is there an ",
        "now showing", "what's playing", "what is playing",
    )
    return any(p in msg for p in pure_lookup_patterns)


def _has_explicit_domain_switch(msg: str) -> bool:
    """
    Returns True ONLY when the message clearly switches to a different domain.

    Conservative — ambiguous follow-ups ("any with kid?", "anything quick?",
    "not expensive") should NOT trigger a domain switch.  Only explicit
    re-orientation phrases qualify.
    """
    return any(sig in msg for sig in _EXPLICIT_DOMAIN_SWITCH_SIGNALS)


def _resolve_response_strategy(
    primary_intent: str,
    secondary_intents: list[str],
    modifiers: list[str],
    flow_type: str,
) -> str:
    """
    Map primary_intent + secondary filters to a canonical response strategy name.

    Rules:
    - secondary filters can UPGRADE the base strategy (e.g. factual_list
      → filtered_factual_list when family_filter is present)
    - modifiers can also trigger upgrades
    - concierge flow always maps to guided_plan for planning intents
    """
    base_strategy = _PRIMARY_INTENT_RESPONSE_STRATEGY.get(primary_intent, "")

    if not base_strategy:
        if flow_type == "factual":
            return "factual_list"
        return "guided_plan"

    # Check if any secondary filter upgrades the strategy
    for (intent_key, filter_key), upgraded in _FILTER_STRATEGY_UPGRADES.items():
        if primary_intent == intent_key and (
            filter_key in secondary_intents or filter_key in modifiers
        ):
            return upgraded

    return base_strategy


def is_factual_flow(state: ConciergeState) -> bool:
    """Helper used by graph builder and other nodes to check flow type."""
    return state.flow_type == "factual"
