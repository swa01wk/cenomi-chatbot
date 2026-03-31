"""
Resolve Fact Scope node — refines the exact fact-lookup parameters for factual flow.

CONTRACT
────────
  Purpose:  Given a factual-flow turn, determine precisely WHAT is being
            asked, the entity type, the named entity if any, the scope,
            and the appropriate response mode.
  Reads:    intent, raw_user_message/normalized_user_message, scene,
            flow_type (must be "factual"), intent.fact_scope_candidate
  Writes:   fact_scope, fact_entity_type, fact_query_entity,
            fact_response_mode, retrieval (targets for fetch_exact_facts)
  Failure:  Falls back to broad fact scope with all-entity search
  Routing:  Always → fetch_exact_facts (factual path)

Fact scopes:
  mall_fact          → mall hours, facilities overview, mall profile
  store_lookup       → specific store/brand lookup
  service_lookup     → ATM, prayer room, stroller, info desk, services
  cinema_lookup      → cinema existence/location
  movie_schedule     → showtimes, movie listings, now-showing
  brand_availability → "do you have X?" — yes/no + location
  cross_mall_availability → cross-mall brand check
  route_hint         → "where is X?" — floor + zone + directions

Response modes:
  quick_answer       → mall hours, one-line facts
  direct_lookup      → brand availability, store presence
  route_hint         → directions/location
  structured_fact_list → movie list, service list
  schedule_answer    → showtimes
  cross_mall_availability → cross-mall results
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.models.state import ConciergeState, RetrievalDecision
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# Default response modes for each scope — used when the LLM candidate is trusted
# directly without running the keyword rules (which also set response_mode).
_SCOPE_DEFAULT_RESPONSE_MODE: dict[str, str] = {
    "movie_schedule":          "structured_fact_list",
    "mall_fact":               "quick_answer",
    "service_lookup":          "route_hint",
    "cinema_lookup":           "route_hint",
    "brand_availability":      "direct_lookup",
    "cross_mall_availability": "cross_mall_availability",
    "route_hint":              "route_hint",
    "store_lookup":            "direct_lookup",
}

# ── Keyword → fact_scope mapping (fallback only) ───────────────────────────
# Only consulted when the LLM classifier left fact_scope_candidate empty.
_SCOPE_RULES: list[tuple[tuple[str, ...], str, str, str]] = [
    # (keywords, fact_scope, fact_entity_type, response_mode)
    (
        ("movie", "movies", "film", "films", "showtime", "showtimes",
         "now showing", "what's playing", "playing", "cinema schedule",
         "cinema programme", "what can i watch", "what movies"),
        "movie_schedule", "movie", "structured_fact_list",
    ),
    (
        ("opening hours", "closing hours", "what time do you close",
         "what time do you open", "when do you open", "when do you close",
         "mall hours", "mall timing", "what time does", "what are your hours"),
        "mall_fact", "mall", "quick_answer",
    ),
    (
        ("atm", "cash machine", "cash point"),
        "service_lookup", "facility", "route_hint",
    ),
    (
        ("prayer room", "prayer rooms", "praying room", "mosque",
         "musallah", "namaz room", "where to pray"),
        "service_lookup", "facility", "route_hint",
    ),
    (
        ("stroller", "pram", "wheelchair", "baby chair",
         "baby trolley", "baby cart"),
        "service_lookup", "facility", "direct_lookup",
    ),
    (
        ("parking", "car park", "valet"),
        "service_lookup", "parking", "quick_answer",
    ),
    (
        ("information desk", "customer service", "help desk",
         "concierge desk"),
        "service_lookup", "facility", "route_hint",
    ),
    (
        ("where is muvi", "where is vox", "where is the cinema",
         "cinema location", "cinema floor", "how to get to the cinema"),
        "cinema_lookup", "cinema", "route_hint",
    ),
    (
        ("where is",),
        "route_hint", "entity", "route_hint",
    ),
    (
        ("does mall of arabia", "is it at mall", "which malls have",
         "any of your malls", "across malls", "other mall", "other malls",
         "also have", "mall of arabia", "cenomi malls"),
        "cross_mall_availability", "brand", "cross_mall_availability",
    ),
    (
        ("do you have", "is there a", "is there an", "do you carry",
         "can i find", "available here", "is it here",
         "is starbucks", "is zara", "is nike", "is h&m", "is mango",
         "is there starbucks", "is there zara",
         # Generic "is X here / available" pattern handled via rule below
         ),
        "brand_availability", "store", "direct_lookup",
    ),
    (
        ("loyalty", "rewards program", "cenomi rewards", "points program"),
        "mall_fact", "loyalty", "quick_answer",
    ),
    (
        ("facilities", "services list", "what services", "what facilities",
         "amenities"),
        "service_lookup", "facility", "structured_fact_list",
    ),
    (
        ("events", "what events", "upcoming events", "any events"),
        "mall_fact", "event", "structured_fact_list",
    ),
    (
        ("offers", "deals", "discounts", "promotions", "sales",
         "what offers"),
        "mall_fact", "offer", "structured_fact_list",
    ),
]

# Scope → retrieval targets mapping
_SCOPE_RETRIEVAL_TARGETS: dict[str, list[str]] = {
    "mall_fact": ["entity_hours", "loyalty_program"],
    "store_lookup": ["entity_hours", "entity_location"],
    "service_lookup": ["facility_location", "service_details"],
    "cinema_lookup": ["entity_location"],
    "movie_schedule": ["movie_schedule"],
    "brand_availability": ["entity_location"],
    "cross_mall_availability": [],  # handled by compose_fact_response_context cross-mall path
    "route_hint": ["entity_location", "facility_location"],
}

# Special sub-scope overrides per mall_fact sub-type
_MALL_FACT_RETRIEVAL: dict[str, list[str]] = {
    "mall": ["entity_hours"],
    "loyalty": ["loyalty_program"],
    "event": ["events"],
    "offer": ["active_offers"],
    "parking": ["parking_details"],
}


@traced_node("resolve_fact_scope")
async def resolve_fact_scope(state: ConciergeState) -> dict:
    msg = (state.normalized_user_message or state.raw_user_message).lower()
    intent = state.intent

    # ── Trust LLM classifier's fact_scope_candidate when set ─────────────────
    # The classifier already has full context to determine scope; only fall back
    # to the keyword rules when it left the field empty.
    scope = intent.fact_scope_candidate or ""
    entity_type = intent.fact_entity_type_candidate or ""
    response_mode = _SCOPE_DEFAULT_RESPONSE_MODE.get(scope, "") if scope else ""
    query_entity = ""

    # ── Keyword rules: fallback only when classifier left scope empty ─────────
    if not scope:
        for keywords, rule_scope, rule_entity_type, rule_mode in _SCOPE_RULES:
            if any(kw in msg for kw in keywords):
                scope = rule_scope
                entity_type = rule_entity_type
                response_mode = rule_mode
                break

        # Catch-all: "is X here / available" → brand_availability
        if not scope:
            _brand_here_pat = re.compile(
                r"\bis\s+\w[\w &'-]{1,20}\s+(?:here|available|in (?:this|the) mall)",
                re.I,
            )
            if _brand_here_pat.search(msg):
                scope = "brand_availability"
                entity_type = "store"
                response_mode = "direct_lookup"

    # ── Extract named entity ───────────────────────────────────────────
    # Prefer the LLM-extracted entity from interpret_turn (zero regex, more robust).
    # Fall back to the regex pattern matcher for scopes/turns where the LLM
    # left entity_query empty (e.g. concierge turns that hit factual keywords).
    llm_entity = (intent.entity_query or "").strip()
    query_entity = llm_entity or _extract_entity_name(msg, scope)

    # ── Determine retrieval targets ────────────────────────────────────
    retrieval_targets = _resolve_retrieval_targets(scope, entity_type, msg)

    # ── Set mall_fact sub-type for retrieval tuning ────────────────────
    if scope == "mall_fact" and entity_type in _MALL_FACT_RETRIEVAL:
        retrieval_targets = _MALL_FACT_RETRIEVAL[entity_type]

    # ── Fallback defaults ─────────────────────────────────────────────
    if not scope:
        scope = "store_lookup"
        entity_type = "entity"
        response_mode = "direct_lookup"
        retrieval_targets = ["entity_location", "entity_hours"]

    if not response_mode:
        response_mode = "direct_lookup"

    retrieval = RetrievalDecision(
        retrieval_needed=True,
        retrieval_reason=f"Factual flow: {scope} lookup requires exact data",
        retrieval_targets=retrieval_targets,
    )

    logger.info(
        "resolve_fact_scope: scope=%s entity_type=%s mode=%s entity=%r targets=%s",
        scope, entity_type, response_mode, query_entity, retrieval_targets,
    )

    return {
        "fact_scope": scope,
        "fact_entity_type": entity_type,
        "fact_query_entity": query_entity,
        "fact_response_mode": response_mode,
        "retrieval": retrieval,
        "_trace_summary": (
            f"FactScope: {scope} | entity_type={entity_type} | "
            f"mode={response_mode} | targets={retrieval_targets} | "
            f"entity={query_entity!r}"
        ),
    }


def _extract_entity_name(msg: str, scope: str) -> str:
    """
    Try to extract the named entity from the message (e.g. 'Starbucks',
    'Zara', 'Muvi Cinema').  Used for targeted retrieval lookups.
    """
    # "where is X" / "do you have X" / "is there a X"
    patterns = [
        r"where is (?:the |a |an )?([a-z0-9 &'-]{2,30})(?:\?|$|,|\s+on|\s+at|\s+in)",
        r"where'?s (?:the |a |an )?([a-z0-9 &'-]{2,30})(?:\?|$|,|\s+on|\s+at|\s+in)",
        r"do you have (?:a |an )?([a-z0-9 &'-]{2,30})(?:\?|$|,|\s+here|\s+at)",
        r"is (?:there |a |an )?([a-z0-9 &'-]{2,30}) here",
        r"is ([a-z0-9 &'-]{2,30}) (?:here|available|in this mall)",
        r"find (?:the |a |an )?([a-z0-9 &'-]{2,30})",
        r"looking for (?:the |a |an )?([a-z0-9 &'-]{2,30})",
    ]
    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            candidate = m.group(1).strip()
            # Filter out generic words
            _STOP_WORDS = {
                "mall", "store", "shop", "place", "restaurant", "cinema",
                "atm", "prayer", "parking", "stroller", "wheelchair",
                "movies", "film",
            }
            if candidate.lower() not in _STOP_WORDS and len(candidate) >= 2:
                return candidate.title()
    return ""


def _resolve_retrieval_targets(scope: str, entity_type: str, msg: str) -> list[str]:
    """Map fact scope to retrieval targets list."""
    base = _SCOPE_RETRIEVAL_TARGETS.get(scope, ["entity_location"])

    # Enrich targets for specific cases
    if scope == "movie_schedule":
        return ["movie_schedule"]

    if scope == "route_hint":
        if any(k in msg for k in ("cinema", "muvi", "vox", "reel")):
            return ["entity_location"]
        if any(k in msg for k in ("atm", "prayer", "restroom", "stroller")):
            return ["facility_location", "service_details"]
        return ["entity_location", "facility_location"]

    if scope == "brand_availability":
        return ["entity_location"]

    if scope == "service_lookup":
        return ["facility_location", "service_details"]

    if scope == "mall_fact":
        return ["entity_hours"]

    return base
