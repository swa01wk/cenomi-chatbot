"""
Compose Fact Response Context node — assembles the minimal, exact context
needed to generate a factual answer.

CONTRACT
────────
  Purpose:  For factual-flow turns, take retrieval results and produce a
            compact fact-payload that the response generator can use to
            give direct, structured, truthful answers.
  Reads:    retrieval (results), fact_scope, fact_entity_type,
            fact_query_entity, fact_response_mode, response_plan
  Writes:   fact_context (dict), context (minimal), response_plan (updated)
  Failure:  Empty retrieval → empty fact_context (LLM will say "not found")
  Routing:  Always → generate_response (factual path)

This node is intentionally separate from compose_context (concierge).
It must NOT inject:
  - shopping shortlists
  - dining shortlists
  - generic semantic enrichments
  - broad playbook-driven recommendations

It ONLY injects:
  - exact entity metadata (name, floor, zone, directions)
  - mall profile facts (hours, loyalty, parking)
  - cinema / movie payloads
  - service / facility location data
  - cross-mall result summaries
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.models.state import ConciergeState, ContextComposition, ResponsePlan
from app.nodes._tracing import traced_node
from app.runtime import search_brand_across_configured_malls
from app.services.cross_mall_brand import resolve_cross_mall_brand_query
from app.services.response_mode_resolver import resolve_response_mode

logger = logging.getLogger(__name__)

# Fact-scope → response strategy mapping
_SCOPE_STRATEGY: dict[str, str] = {
    "movie_schedule": "structured_fact_list",
    "mall_fact": "quick_answer",
    "store_lookup": "direct_lookup",
    "service_lookup": "route_hint",
    "cinema_lookup": "route_hint",
    "brand_availability": "direct_lookup",
    "cross_mall_availability": "cross_mall_availability",
    "route_hint": "route_hint",
}

# Entity caps for factual response — keep it tight
_FACTUAL_ENTITY_CAPS: dict[str, int] = {
    "movie_schedule": 20,
    "structured_fact_list": 15,
    "quick_answer": 1,
    "direct_lookup": 3,
    "route_hint": 2,
    "cross_mall_availability": 10,
    "schedule_answer": 20,
}


@traced_node("compose_fact_response_context")
async def compose_fact_response_context(state: ConciergeState) -> dict:
    scope = state.fact_scope or "store_lookup"
    response_mode = state.fact_response_mode or _SCOPE_STRATEGY.get(scope, "direct_lookup")
    query_entity = state.fact_query_entity or ""

    if scope == "cross_mall_availability" or state.intent.domain == "cross_mall":
        return await _compose_cross_mall_fact_context(state)

    retrieval_results = state.retrieval.retrieval_results
    fact_payload: dict[str, Any] = {
        "scope": scope,
        "entity_type": state.fact_entity_type,
        "query_entity": query_entity,
        "response_mode": response_mode,
        "results": [],
        "summary_notes": [],
    }

    extracted_entities: list[dict[str, Any]] = []
    result_count = 0

    for result in retrieval_results:
        if not result.get("data"):
            continue
        data = result["data"]
        result_count += 1

        data_type = data.get("type", "")

        if data_type == "movie_schedule_all":
            fact_payload["movies"] = data.get("movies", [])
            fact_payload["summary_notes"].append(
                f"Found {len(data.get('movies', []))} movies in schedule"
            )
            # Surface as entities for context
            for movie in data.get("movies", []):
                extracted_entities.append({
                    "entity_type": "movie",
                    "name": movie.get("title", ""),
                    "genre": movie.get("genre", ""),
                    "showtimes": movie.get("showtimes", []),
                    "duration_minutes": movie.get("duration_minutes"),
                    "source": "factual/movie_schedule",
                })

        elif data_type == "movie_schedule":
            fact_payload["movie"] = data
            fact_payload["summary_notes"].append(
                f"Specific movie found: {data.get('title', '')}"
            )
            extracted_entities.append({
                "entity_type": "movie",
                "name": data.get("title", ""),
                "genre": data.get("genre", ""),
                "showtimes": data.get("showtimes", []),
                "formats_available": data.get("formats_available", []),
                "duration_minutes": data.get("duration_minutes"),
                "rating": data.get("rating", ""),
                "source": "factual/movie_schedule",
            })

        elif data_type == "entity_location":
            fact_payload["location"] = data
            entity_name = data.get("name", query_entity or "entity")
            fact_payload["summary_notes"].append(
                f"Location found for {entity_name}: floor={data.get('floor')}"
            )
            extracted_entities.append({
                "entity_type": state.fact_entity_type or "store",
                "entity_id": data.get("entity_id", ""),
                "name": entity_name,
                "floor": data.get("floor", ""),
                "zone": data.get("zone", ""),
                "nearby_landmarks": data.get("nearby_landmarks", []),
                "directions_hint": data.get("directions_hint", ""),
                "source": "factual/location",
            })

        elif data_type == "facility_location":
            fact_payload["facility"] = data
            fac_name = data.get("name", "facility")
            fact_payload["summary_notes"].append(
                f"Facility found: {fac_name} on floor {data.get('floor', '?')}"
            )
            extracted_entities.append({
                "entity_type": "facility",
                "name": fac_name,
                "facility_type": data.get("facility_type", ""),
                "floor": data.get("floor", ""),
                "zone": data.get("zone", ""),
                "directions_hint": data.get("directions_hint", ""),
                "operating_hours": data.get("operating_hours", {}),
                "source": "factual/facility",
            })

        elif data_type == "service_details":
            fact_payload["service"] = data
            svc_name = data.get("name", "service")
            fact_payload["summary_notes"].append(
                f"Service found: {svc_name} ({data.get('service_category', '')})"
            )
            extracted_entities.append({
                "entity_type": "service",
                "entity_id": data.get("entity_id", ""),
                "name": svc_name,
                "service_category": data.get("service_category", ""),
                "description": data.get("description", ""),
                "floor": data.get("location", {}).get("floor", ""),
                "zone": data.get("location", {}).get("zone", ""),
                "is_free": data.get("is_free"),
                "pricing_notes": data.get("pricing_notes", ""),
                "source": "factual/service",
            })

        elif data_type == "service_list":
            svcs = data.get("services", [])
            fact_payload["services"] = svcs
            fact_payload["summary_notes"].append(
                f"Service list retrieved: {len(svcs)} services"
            )
            for svc in svcs:
                extracted_entities.append({
                    "entity_type": "service",
                    "entity_id": svc.get("entity_id", ""),
                    "name": svc.get("name", ""),
                    "service_category": svc.get("service_category", ""),
                    "description": svc.get("description", ""),
                    "floor": svc.get("location", {}).get("floor", ""),
                    "zone": svc.get("location", {}).get("zone", ""),
                    "is_free": svc.get("is_free"),
                    "pricing_notes": svc.get("pricing_notes", ""),
                    "source": "factual/service",
                })

        elif data_type == "entity_hours":
            if data.get("type") == "mall_hours":
                fact_payload["mall_hours"] = data.get("operating_hours", {})
                fact_payload["summary_notes"].append("Mall hours retrieved")
            else:
                fact_payload["entity_hours"] = data
                entity_name = data.get("name", "")
                fact_payload["summary_notes"].append(
                    f"Hours for {entity_name} retrieved"
                )

        elif data_type == "parking_details":
            fact_payload["parking"] = data
            fact_payload["summary_notes"].append("Parking details retrieved")

        elif data_type == "loyalty_program":
            fact_payload["loyalty"] = data
            fact_payload["summary_notes"].append("Loyalty program details retrieved")

        elif data_type == "brand_absent":
            queried_brand = data.get("queried_brand", query_entity or "the brand")
            mall_stores = data.get("mall_stores", [])
            fact_payload["absent_brand"] = queried_brand
            fact_payload["alternative_stores"] = mall_stores
            fact_payload["summary_notes"].append(
                f"Brand '{queried_brand}' is not in this mall — "
                f"{len(mall_stores)} stores available as alternatives"
            )
            # Mark retrieval_succeeded=True: we got a definitive confirmed-absent answer.
            # This allows the response LLM to suggest alternatives rather than saying
            # data is unavailable.
            retrieval_succeeded = True

        elif data_type == "active_offers":
            fact_payload["offers"] = data.get("offers", [])
            fact_payload["summary_notes"].append(
                f"{len(data.get('offers', []))} active offers found"
            )

        elif data_type == "events":
            fact_payload["events"] = data.get("events", [])
            fact_payload["summary_notes"].append(
                f"{len(data.get('events', []))} events found"
            )

    # ── Determine if retrieval succeeded ─────────────────────────────
    retrieval_succeeded = result_count > 0
    fact_payload["retrieval_succeeded"] = retrieval_succeeded
    if not retrieval_succeeded:
        fact_payload["summary_notes"].append(
            "No exact data retrieved — LLM should say data is unavailable"
        )

    # ── Hybrid filter application ──────────────────────────────────────
    # Apply secondary_intents as filters on retrieved entities.
    # e.g. family_filter → prefer kid_friendly movies; budget_filter → remove premium
    secondary_intents = state.secondary_intents or []
    modifiers = state.modifiers or []

    if "family_filter" in secondary_intents or "kid_friendly" in modifiers:
        # Flag movies/entities as family-filtered so the LLM can prioritise them
        fact_payload["family_filter_active"] = True
        fact_payload["summary_notes"].append(
            "Family/child filter active — prefer family-friendly and kid-friendly options"
        )
    if "budget_filter" in secondary_intents or "budget_sensitive" in modifiers:
        fact_payload["budget_filter_active"] = True
        fact_payload["summary_notes"].append(
            "Budget filter active — prefer affordable options"
        )
    if "before_movie_constraint" in secondary_intents or "near_cinema" in modifiers:
        fact_payload["proximity_filter_active"] = True
        fact_payload["summary_notes"].append(
            "Proximity/timing filter active — prefer options near cinema or time-efficient"
        )
    if "romantic_filter" in secondary_intents or "romantic" in modifiers:
        fact_payload["romantic_filter_active"] = True

    # Inject secondary/modifier context into the fact payload for generator use
    if secondary_intents:
        fact_payload["secondary_intents"] = secondary_intents
    if modifiers:
        fact_payload["modifiers"] = modifiers

    # ── Update context with factual entities only ─────────────────────
    entity_cap = _FACTUAL_ENTITY_CAPS.get(response_mode, 10)
    # Build semantic signals from modifiers so ranking is aware
    modifier_signals = [m for m in modifiers if m]
    minimal_context = ContextComposition(
        selected_topic_blocks=[scope],
        selected_entities=extracted_entities[:entity_cap],
        selected_semantic_signals=modifier_signals,
        ranking_notes=[
            f"factual_flow:{scope}",
            f"secondary_intents={secondary_intents}",
            f"modifiers={modifiers}",
        ],
        candidate_count_before_dedupe=len(extracted_entities),
    )

    # ── Update response plan for factual flow ─────────────────────────
    strategy = _SCOPE_STRATEGY.get(scope, "direct_lookup")
    updated_plan = state.response_plan.model_copy(deep=True)
    updated_plan.chosen_strategy = strategy
    updated_plan.response_shape_hint = _strategy_shape(strategy)
    updated_plan.answer_mode = "direct_answer"
    updated_plan.tone_mode = "structured"
    updated_plan.entity_cap = entity_cap
    updated_plan.must_acknowledge_scene = bool(secondary_intents or modifiers)
    # Hybrid response plan fields
    updated_plan.primary_goal = state.primary_intent or scope
    updated_plan.secondary_filters = secondary_intents
    updated_plan.dominant_context_type = state.dominant_context_type or scope
    updated_plan.fact_first = True
    # Allow a brief concierge tail only for hybrid queries with companion context
    updated_plan.concierge_tail_allowed = bool(
        "family_filter" in secondary_intents
        or "romantic_filter" in secondary_intents
    )

    # ── Response Mode Resolver (factual path) ─────────────────────────
    # The factual path skips choose_strategy, so resolve_response_mode must
    # be called here to ensure debug_enrichment and response_plan carry the
    # correct response_mode and confidence_level labels.
    # IMPORTANT: planning / hybrid checks inside resolve_response_mode run
    # BEFORE the factual-flow guard, so planning queries (e.g. "can we fit in
    # movies, food, and one activity in 3 hours?") correctly return hybrid_plan
    # even when the factual path was taken.
    rm, confidence_level, rm_reason, rm_fallback = resolve_response_mode(state)

    # Propagate into response_plan so generate_response and emit_debug_payload
    # both see the correct values.
    # NOTE: We do NOT update debug_enrichment here because generate_response
    # also returns debug_enrichment, and returning it from two sequential nodes
    # triggers INVALID_CONCURRENT_GRAPH_UPDATE in LangGraph 1.x.
    # emit_debug_payload already falls back to state.response_plan.response_mode
    # when debug_enrichment.response_mode is empty.
    updated_plan.response_mode = rm
    updated_plan.confidence_level = confidence_level

    logger.info(
        "compose_fact_response_context: scope=%s strategy=%s entities=%d "
        "succeeded=%s response_mode=%s confidence=%s",
        scope, strategy, len(extracted_entities), retrieval_succeeded, rm, confidence_level,
    )

    return {
        "fact_context": fact_payload,
        "context": minimal_context,
        "response_plan": updated_plan,
        "_trace_summary": (
            f"FactContext: scope={scope} | strategy={strategy} | "
            f"entities={len(extracted_entities)} | "
            f"retrieval={'ok' if retrieval_succeeded else 'empty'} | "
            f"mode={rm} [{confidence_level}]"
        ),
    }


async def _compose_cross_mall_fact_context(state: ConciergeState) -> dict:
    """Factual-flow cross-mall: resolve Redis/disk-backed mall contexts and search brand."""
    scope = "cross_mall_availability"
    response_mode = "cross_mall_availability"
    home_mid = state.mall_id or ""
    brand = resolve_cross_mall_brand_query(state)
    extracted_entities: list[dict[str, Any]] = []
    fact_payload: dict[str, Any] = {
        "scope": scope,
        "entity_type": state.fact_entity_type or "brand",
        "query_entity": brand,
        "response_mode": response_mode,
        "results": [],
        "summary_notes": [],
        "cross_mall_brand_query": brand,
    }

    if not brand:
        fact_payload["summary_notes"].append(
            "Cross-mall: no brand resolved from message or scene — ask which store/brand"
        )
        fact_payload["retrieval_succeeded"] = False
        cross_results: list[dict[str, Any]] = []
    else:
        cross_results = await search_brand_across_configured_malls(brand, home_mall_id=home_mid)
        at_home = [r for r in cross_results if r.get("is_home_mall")]
        other = [r for r in cross_results if not r.get("is_home_mall")]
        fact_payload["cross_mall_at_home"] = at_home
        fact_payload["cross_mall_other"] = other
        fact_payload["summary_notes"].append(
            f"cross-mall search for {brand!r}: {len(at_home)} at active mall, "
            f"{len(other)} at other mall(s)"
        )
        extracted_entities = list(cross_results)
        fact_payload["retrieval_succeeded"] = bool(cross_results)

    secondary_intents = state.secondary_intents or []
    modifiers = state.modifiers or []
    entity_cap = _FACTUAL_ENTITY_CAPS.get(response_mode, 10)
    modifier_signals = [m for m in modifiers if m]
    minimal_context = ContextComposition(
        selected_topic_blocks=[scope],
        selected_entities=extracted_entities[:entity_cap],
        selected_semantic_signals=modifier_signals,
        ranking_notes=[
            f"factual_flow:{scope}",
            f"brand={brand!r}",
            f"hits={len(extracted_entities)}",
        ],
        candidate_count_before_dedupe=len(extracted_entities),
    )
    strategy = _SCOPE_STRATEGY.get(scope, "direct_lookup")
    updated_plan = state.response_plan.model_copy(deep=True)
    updated_plan.chosen_strategy = strategy
    updated_plan.response_shape_hint = _strategy_shape(strategy)
    updated_plan.answer_mode = "direct_answer"
    updated_plan.tone_mode = "structured"
    updated_plan.entity_cap = entity_cap
    updated_plan.must_acknowledge_scene = bool(secondary_intents or modifiers)
    updated_plan.primary_goal = state.primary_intent or scope
    updated_plan.secondary_filters = secondary_intents
    updated_plan.dominant_context_type = state.dominant_context_type or scope
    updated_plan.fact_first = True
    updated_plan.concierge_tail_allowed = bool(
        "family_filter" in secondary_intents
        or "romantic_filter" in secondary_intents
    )
    rm, confidence_level, rm_reason, rm_fallback = resolve_response_mode(state)
    updated_plan.response_mode = rm
    updated_plan.confidence_level = confidence_level

    logger.info(
        "compose_fact_response_context (cross-mall): brand=%r hits=%d succeeded=%s",
        brand,
        len(extracted_entities),
        fact_payload.get("retrieval_succeeded"),
    )

    return {
        "fact_context": fact_payload,
        "context": minimal_context,
        "response_plan": updated_plan,
        "_trace_summary": (
            f"FactContext: cross_mall | brand={brand!r} | hits={len(extracted_entities)} | "
            f"mode={rm} [{confidence_level}]"
        ),
    }


def _strategy_shape(strategy: str) -> str:
    shapes = {
        "structured_fact_list": "fact_list",
        "quick_answer": "one_liner",
        "direct_lookup": "yes_no_plus_location",
        "route_hint": "directions",
        "cross_mall_availability": "cross_mall_table",
        "schedule_answer": "schedule_list",
    }
    return shapes.get(strategy, "brief_answer")
