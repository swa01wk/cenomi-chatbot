"""
Fetch Exact Facts node — retrieves precise data for factual queries.

CONTRACT
────────
  Purpose:  Execute targeted retrieval against canonical data for queries
            that need exact answers (store hours, showtimes, offers, etc.).
  Reads:    retrieval (targets), active_mall_id, normalized_user_message
  Writes:   retrieval (updated with results), context (enriched with facts)
  Failure:  Retrieval error → warning, proceed with empty results
  Routing:  Always → generate_response
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

    mall_ctx = get_mall_context()
    msg = state.normalized_user_message.lower()

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

    retrieval.retrieval_results = results

    for r in results:
        if r.get("data"):
            context.selected_entities.append(r["data"])

    found_count = sum(1 for r in results if r.get("data"))
    return {
        "retrieval": retrieval,
        "context": context,
        "_trace_summary": (
            f"Fetched {len(results)} targets "
            f"({found_count} with data)"
        ),
        "_trace_warnings": trace_warnings,
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
    canonical = mall_ctx._builder._canonical
    services = canonical.get("services", [])
    for svc in services:
        name = svc.name.lower()
        cat = svc.service_category.lower()
        if name in msg or cat in msg:
            return {
                "type": "service_details",
                "entity_id": svc.entity_id,
                "name": svc.name,
                "service_category": svc.service_category,
                "description": svc.description,
                "location": svc.location.model_dump(),
                "operating_hours": svc.operating_hours.model_dump(),
                "is_free": svc.is_free,
                "pricing_notes": svc.pricing_notes,
            }
    return None
