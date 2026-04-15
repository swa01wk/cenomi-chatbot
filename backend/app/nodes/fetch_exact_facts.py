"""
Fetch Exact Facts node — retrieves precise data for factual queries.

CONTRACT
────────
  Purpose:  Execute targeted retrieval against canonical data for queries
            that need exact answers (store hours, showtimes, offers, etc.).
  Reads:    retrieval (targets), active_mall_id, normalized_user_message,
            flow_type, fact_query_entity, fact_scope
  Writes:   retrieval (updated with results), context (enriched with facts)
  Failure:  Retrieval error → warning, proceed with empty results
  Routing:  Always → generate_response (concierge) or
                     compose_fact_response_context (factual)

Flow-aware behavior:
  - factual flow: retrieval is required and dominant; fact_query_entity is used
    for precise name-based lookup; partial retrieval is surfaced honestly.
  - concierge flow: retrieval is conditional and supportive (existing behavior).
"""

from __future__ import annotations

from typing import Any

from app.models.state import ConciergeState, RetrievalDecision
from app.nodes._tracing import traced_node
from app.runtime import get_mall_context


@traced_node("fetch_exact_facts")
async def fetch_exact_facts(state: ConciergeState) -> dict:
    retrieval = state.retrieval.model_copy(deep=True)
    context = state.context.model_copy(deep=True)
    trace_warnings: list[str] = []

    mall_ctx = get_mall_context(state.mall_id)
    msg = state.normalized_user_message.lower()

    # In factual flow, enrich the message with the resolved entity name
    # for more precise lookups (e.g. "starbucks" for "do you have starbucks?")
    fact_query_entity = getattr(state, "fact_query_entity", "") or ""
    if fact_query_entity:
        # Inject entity name into msg if not already present
        if fact_query_entity.lower() not in msg:
            msg = f"{msg} {fact_query_entity.lower()}"

    results: list[dict[str, Any]] = []
    for target in retrieval.retrieval_targets:
        try:
            data = _fetch_target(target, msg, mall_ctx)
            results.append({
                "target": target,
                "status": "found" if data else "not_found",
                "data": data,
            })
        except Exception as exc:
            results.append({"target": target, "status": "error", "data": None})
            trace_warnings.append(f"Retrieval for '{target}' failed: {exc}")

    # Brand-absence sentinel: when a brand_availability query returns no entity location,
    # inject a brand_absent result with the mall store catalog so the response LLM can
    # proactively suggest similar stores that ARE present in the mall.
    fact_scope = getattr(state, "fact_scope", "") or ""
    if fact_scope == "brand_availability" and fact_query_entity:
        for r in results:
            if r.get("target") == "entity_location" and r.get("status") == "not_found":
                mall_catalog = _lookup_mall_store_catalog(mall_ctx)
                r["data"] = {
                    "type": "brand_absent",
                    "queried_brand": fact_query_entity,
                    "confirmed_absent": True,
                    "mall_stores": mall_catalog,
                }
                r["status"] = "found"
                trace_warnings.append(
                    f"Brand '{fact_query_entity}' not found; injecting brand_absent sentinel "
                    f"with {len(mall_catalog)} alternative stores"
                )
                break

    retrieval.retrieval_results = results

    # In factual flow: only inject facts, NOT semantic entities, into context.
    # In concierge flow: append normally so downstream compose_context can use them.
    is_factual = getattr(state, "flow_type", "") == "factual"
    if not is_factual:
        for r in results:
            if r.get("data"):
                data = r["data"]
                # Enforce entity_cap from choose_strategy to avoid bloating LLM context.
                # The * 3 multiplier gives the response LLM headroom to select the most
                # relevant subset from a pre-filtered pool rather than the entire catalog.
                if "entities" in data:
                    cap = getattr(state.response_plan, "entity_cap", None)
                    if cap:
                        data = {**data, "entities": data["entities"][: cap * 3]}
                context.selected_entities.append(data)

    found_count = sum(1 for r in results if r.get("data"))
    flow_tag = "factual" if is_factual else "concierge"
    return {
        "retrieval": retrieval,
        "context": context,
        "_trace_summary": (
            f"Fetched[{flow_tag}] {len(results)} targets "
            f"({found_count} with data)"
        ),
        "_trace_warnings": trace_warnings if trace_warnings else [],
    }


def _fetch_target(target: str, msg: str, mall_ctx) -> dict[str, Any] | None:
    """Dispatch retrieval to the appropriate canonical lookup."""
    handlers: dict[str, Any] = {
        "entity_hours": _lookup_entity_hours,
        "movie_schedule": _lookup_movie_schedule,
        "active_offers": _lookup_active_offers,
        "events": _lookup_events,
        "loyalty_program": _lookup_loyalty,
        "entity_location": _lookup_entity_location,
        "facility_location": _lookup_facility_location,
        "parking_details": _lookup_parking,
        "service_details": _lookup_service_details,
        # Concierge recommendation targets — entity lists for downstream LLM composition
        "dining_list": _lookup_dining_list,
        "store_list": _lookup_store_list,
        "entertainment_list": _lookup_entertainment_list,
    }
    handler = handlers.get(target)
    if handler:
        return handler(msg, mall_ctx)
    return None


def _lookup_entity_hours(msg: str, mall_ctx) -> dict | None:
    """Find entity hours by name match in the query."""
    pack = mall_ctx.get_context_pack()
    op_ctx = pack.get("operational_context", {})

    for entity_type in ("stores", "dining", "cinemas"):
        canonical = mall_ctx._builder._canonical.get(entity_type, [])
        for entity in canonical:
            name = getattr(entity, "name", "").lower()
            if name and name in msg:
                hours = entity.operating_hours
                return {
                    "type": "entity_hours",
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "operating_hours": hours.model_dump() if hours else {},
                    "location": entity.location.model_dump() if entity.location else {},
                }

    if op_ctx.get("hours"):
        return {
            "type": "mall_hours",
            "operating_hours": op_ctx["hours"],
        }
    return None


def _lookup_movie_schedule(msg: str, mall_ctx) -> dict | None:
    canonical = mall_ctx._builder._canonical
    movies = canonical.get("movies", [])
    if not movies:
        return None

    for movie in movies:
        title = movie.title.lower()
        if title in msg or any(w in msg for w in title.split() if len(w) > 3):
            return {
                "type": "movie_schedule",
                "entity_id": movie.entity_id,
                "title": movie.title,
                "genre": movie.genre,
                "duration_minutes": movie.duration_minutes,
                "formats_available": movie.formats_available,
                "showtimes": movie.showtimes,
                "rating": movie.rating,
                "cinema_entity_id": movie.cinema_entity_id,
            }

    return {
        "type": "movie_schedule_all",
        "movies": [
            {
                "title": m.title,
                "genre": m.genre,
                "showtimes": m.showtimes,
                "duration_minutes": m.duration_minutes,
                "rating": getattr(m, "rating", None),
                "is_sports_broadcast": any(
                    g.lower() in ("sport", "sports", "live event")
                    for g in (
                        m.genre if isinstance(m.genre, list) else [m.genre or ""]
                    )
                ),
            }
            for m in movies
        ],
    }


def _lookup_active_offers(msg: str, mall_ctx) -> dict | None:
    canonical = mall_ctx._builder._canonical
    offers = canonical.get("offers", [])
    if not offers:
        return None

    return {
        "type": "active_offers",
        "offers": [
            {
                "entity_id": o.entity_id,
                "title": o.title,
                "description": o.description,
                "discount_value": o.discount_value,
                "valid_from": o.valid_from,
                "valid_until": o.valid_until,
                "terms": o.terms,
            }
            for o in offers
        ],
    }


def _lookup_events(msg: str, mall_ctx) -> dict | None:
    canonical = mall_ctx._builder._canonical
    events = canonical.get("events", [])
    if not events:
        return None

    return {
        "type": "events",
        "events": [
            {
                "entity_id": e.entity_id,
                "title": e.title,
                "description": e.description,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "target_audience": e.target_audience,
                "is_free": e.is_free,
            }
            for e in events
        ],
    }


def _lookup_loyalty(msg: str, mall_ctx) -> dict | None:
    canonical = mall_ctx._builder._canonical
    profile = canonical.get("mall_profile")
    if not profile or not profile.loyalty:
        return None

    loyalty = profile.loyalty
    return {
        "type": "loyalty_program",
        "program_name": loyalty.program_name,
        "description": loyalty.description,
        "tiers": loyalty.tiers,
        "earn_rules": loyalty.earn_rules,
        "redemption_options": loyalty.redemption_options,
        "signup_locations": loyalty.signup_locations,
    }


def _lookup_entity_location(msg: str, mall_ctx) -> dict | None:
    for entity_type in ("stores", "dining", "cinemas", "services"):
        canonical = mall_ctx._builder._canonical.get(entity_type, [])
        for entity in canonical:
            name = getattr(entity, "name", "").lower()
            if name and name in msg:
                loc = entity.location
                return {
                    "type": "entity_location",
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "floor": loc.floor,
                    "zone": loc.zone,
                    "unit_number": loc.unit_number,
                    "nearby_landmarks": loc.nearby_landmarks,
                    "directions_hint": loc.directions_hint,
                }
    return None


def _lookup_facility_location(msg: str, mall_ctx) -> dict | None:
    canonical = mall_ctx._builder._canonical
    profile = canonical.get("mall_profile")
    if not profile:
        return None

    for facility in profile.facilities:
        name = facility.name.lower()
        ftype = facility.facility_type.lower()
        if name in msg or ftype in msg:
            return {
                "type": "facility_location",
                "name": facility.name,
                "facility_type": facility.facility_type,
                "floor": facility.location.floor,
                "zone": facility.location.zone,
                "directions_hint": facility.location.directions_hint,
                "operating_hours": facility.operating_hours.model_dump(),
            }

    keywords = {
        "prayer": "prayer_room",
        "pray": "prayer_room",
        "restroom": "restroom",
        "bathroom": "restroom",
        "atm": "atm",
    }
    for kw, ftype in keywords.items():
        if kw in msg:
            for facility in profile.facilities:
                if ftype in facility.facility_type.lower() or ftype in facility.name.lower():
                    return {
                        "type": "facility_location",
                        "name": facility.name,
                        "facility_type": facility.facility_type,
                        "floor": facility.location.floor,
                        "zone": facility.location.zone,
                    }
    return None


def _lookup_parking(msg: str, mall_ctx) -> dict | None:
    pack = mall_ctx.get_context_pack()
    op_ctx = pack.get("operational_context", {})
    parking = op_ctx.get("parking")
    if parking:
        return {"type": "parking_details", **parking}
    return None


def _lookup_service_details(msg: str, mall_ctx) -> dict | None:
    """Return matching service(s) for a query.

    For general list/facilities queries returns ALL services so the LLM can
    present a complete picture.  For specific queries (e.g. "where is the ATM")
    returns the first matching service.
    """
    canonical = mall_ctx._builder._canonical
    services = canonical.get("services", [])

    def _to_dict(svc) -> dict:
        return {
            "type": "service_details",
            "entity_id": svc.entity_id,
            "name": svc.name,
            "service_category": getattr(svc, "service_category", ""),
            "description": getattr(svc, "description", ""),
            "location": svc.location.model_dump() if svc.location else {},
            "operating_hours": svc.operating_hours.model_dump() if svc.operating_hours else {},
            "is_free": getattr(svc, "is_free", None),
            "pricing_notes": getattr(svc, "pricing_notes", None),
        }

    # The LLM routes general facility/amenity queries with sub_intent=facilities_summary.
    # When the query has no specific entity name, return ALL services so the LLM
    # can generate a complete facilities list — no keyword matching needed here.
    if not msg.strip():
        all_svcs = [_to_dict(s) for s in services]
        return {"type": "service_list", "services": all_svcs} if all_svcs else None

    # Try to find a named match first
    matched: list[dict] = []
    for svc in services:
        name = svc.name.lower()
        cat = getattr(svc, "service_category", "").lower()
        if name in msg or cat in msg:
            matched.append(_to_dict(svc))

    # For multiple matches or when no name matched (general list intent),
    # return the full service catalogue so the LLM has complete context.
    if len(matched) > 1 or (not matched):
        all_svcs = [_to_dict(s) for s in services]
        if all_svcs:
            return {"type": "service_list", "services": all_svcs}
        return None

    return matched[0]


def _lookup_dining_list(msg: str, mall_ctx) -> dict | None:
    """Return the full canonical dining entity list for concierge recommendation turns."""
    canonical = mall_ctx._builder._canonical
    dining = canonical.get("dining", [])
    if not dining:
        return None
    return {
        "type": "dining_list",
        "entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "cuisine_type": getattr(e, "cuisine_type", None),
                "dining_type": getattr(e, "dining_type", None),
                "description": getattr(e, "description", None),
                "price_range": getattr(e, "price_range", None),
                "tags": getattr(e, "tags", []),
                "location": e.location.model_dump() if e.location else {},
                "operating_hours": e.operating_hours.model_dump() if e.operating_hours else {},
            }
            for e in dining
        ],
    }


def _lookup_store_list(msg: str, mall_ctx) -> dict | None:
    """Return the full canonical store entity list for concierge recommendation turns."""
    canonical = mall_ctx._builder._canonical
    stores = canonical.get("stores", [])
    if not stores:
        return None
    return {
        "type": "store_list",
        "entities": [
            {
                "entity_id": e.entity_id,
                "name": e.name,
                "store_category": getattr(e, "store_category", None),
                "brand": getattr(e, "brand", None),
                "description": getattr(e, "description", None),
                "price_range": getattr(e, "price_range", None),
                "tags": getattr(e, "tags", []),
                "location": e.location.model_dump() if e.location else {},
                "operating_hours": e.operating_hours.model_dump() if e.operating_hours else {},
            }
            for e in stores
        ],
    }


def _lookup_mall_store_catalog(mall_ctx, cap: int = 25) -> list[dict]:
    """
    Return a capped store catalog for brand-absence alternative suggestions.

    When a queried brand is absent from the mall, passing the full store list
    (stripped to name, category, price_range, floor) lets the response LLM use
    its own knowledge to suggest the most relevant alternatives — no separate
    categorisation call needed.
    """
    stores: list[dict] = []
    for etype in ("stores", "dining"):
        canonical = mall_ctx._builder._canonical.get(etype, [])
        for entity in canonical:
            stores.append({
                "name": entity.name,
                "category": (
                    getattr(entity, "category", "")
                    or getattr(entity, "cuisine_type", "")
                    or ""
                ),
                "price_range": getattr(entity, "price_range", "") or "",
                "floor": entity.location.floor if entity.location else "",
            })
            if len(stores) >= cap:
                break
        if len(stores) >= cap:
            break
    return stores[:cap]


def _lookup_entertainment_list(msg: str, mall_ctx) -> dict | None:
    """Return canonical entertainment/activity entities for concierge recommendation turns."""
    canonical = mall_ctx._builder._canonical
    cinemas = canonical.get("cinemas", [])
    services = canonical.get("services", [])
    _ENTERTAINMENT_CATEGORIES = {
        "cinema", "entertainment", "bowling", "arcade", "gaming",
        "activity", "kids_play", "trampoline", "ice_skating",
    }
    activity_services = [
        s for s in services
        if any(cat in getattr(s, "service_category", "").lower()
               for cat in _ENTERTAINMENT_CATEGORIES)
    ]
    entities = []
    for e in cinemas:
        entities.append({
            "entity_id": e.entity_id,
            "name": e.name,
            "entity_type": "cinema",
            "description": getattr(e, "description", None),
            "tags": getattr(e, "tags", []),
            "location": e.location.model_dump() if e.location else {},
            "operating_hours": e.operating_hours.model_dump() if e.operating_hours else {},
        })
    for e in activity_services:
        entities.append({
            "entity_id": e.entity_id,
            "name": e.name,
            "entity_type": "activity",
            "service_category": getattr(e, "service_category", None),
            "description": getattr(e, "description", None),
            "tags": getattr(e, "tags", []),
            "location": e.location.model_dump() if e.location else {},
            "operating_hours": e.operating_hours.model_dump() if e.operating_hours else {},
        })
    if not entities:
        return None
    return {"type": "entertainment_list", "entities": entities}
