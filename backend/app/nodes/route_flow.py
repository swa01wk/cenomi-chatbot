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
_FACTUAL_SUB_INTENTS: frozenset[str] = frozenset({
    "movie_showtime",
    "opening_hours",
    "store_hours",
    "location_query",
    "service_info",
    "prayer_room",
    "parking_info",
    "cross_mall_search",
    "offer_details",        # Offer/deal queries need exact retrieved data
    "brand_availability",   # "do you have X" / "is X here" needs exact presence check
})

# Domains where factual flow is almost always correct
_FACTUAL_DOMAINS: frozenset[str] = frozenset({
    "navigation",
    "cross_mall",
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
_FACTUAL_HARD_SIGNALS: tuple[str, ...] = (
    "what movies", "which movies", "movies do we have", "movies can i watch",
    "movies can i see", "now showing", "what's playing", "what is playing",
    "all movies", "movie list", "showtimes", "show times",
    "where is the atm", "where is atm", "atm location",
    "prayer room location", "where is the prayer",
    "opening hours", "closing hours", "what time do you close",
    "when do you open", "when do you close", "what are your hours",
    "what time does the mall",
    "do you have zara", "is zara here", "do you have nike",
    "is starbucks here", "do you have starbucks",
    "where is muvi", "where is vox", "where is the cinema",
    "do you have a cinema", "is there a cinema",
    "do you have prayer room", "do you have strollers",
    "where is h&m", "is h&m here",
    "does mall of arabia", "is it at mall of arabia", "which malls have",
    "any of your malls", "across malls",
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
    "tell me about the mall", "about the mall", "mall hours",
    "opening hours", "when does the mall open", "when do you open",
    "where is the", "how do i get to", "how to get to",
    "kids zone", "play area", "activity for kids",
    "what can we do", "what should we do",
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

    # ── 0. Domain lock: preserve factual primary intent across turns ──
    # When the user established a factual domain (e.g. movie_lookup) in a prior
    # turn, subsequent follow-ups MUST stay in that domain.
    # Companion/scene signals (e.g. "with kid") are treated as FILTERS, not
    # intent replacements.  Only an explicit topic switch releases the lock.
    prior_factual_intent = scene.active_primary_intent or ""
    if prior_factual_intent in _FACTUAL_PRIMARY_INTENTS:
        is_explicit_switch = (
            intent.message_kind in ("topic_switch", "context_setting")
            or _has_explicit_domain_switch(msg)
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
        # Factual sub-intent wins UNLESS the query is clearly about
        # planning *around* the movie (e.g. "something quick before the movie").
        # NOTE: "any movies with the kid?" is factual — kid is a FILTER, not override.
        has_concierge_hard = any(sig in msg for sig in _CONCIERGE_HARD_SIGNALS)
        is_planning_hybrid = (
            intent.sub_intent == "movie_showtime"
            and any(kw in msg for kw in ("before ", "after ", "plan ", "suggest"))
            and not _is_pure_lookup(msg)
        )
        if is_planning_hybrid or (has_concierge_hard and not _is_pure_lookup(msg)):
            flow_type = "concierge"
            routing_reason = (
                f"Hybrid query: sub_intent={intent.sub_intent} but planning context "
                f"('before/after/suggest') dominates — routing to concierge"
            )
            if not primary_intent:
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

    # ── 3. Navigation domain → always factual ────────────────────────
    elif not flow_type and intent.domain in _FACTUAL_DOMAINS:
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

    Note: child companion alone is NOT sufficient to trigger concierge when the
    current turn has a factual primary intent (e.g. movie lookup). The caller
    must separately check primary_intent against _FACTUAL_PRIMARY_INTENTS.

    Family visit IS a valid concierge trigger for non-factual queries (e.g.
    "where can we eat" when scene.visit_type == "family_visit").
    """
    has_companions = bool(
        scene.companions
        and any(c not in ("solo",) for c in scene.companions)
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
