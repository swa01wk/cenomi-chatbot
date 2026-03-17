"""
Semantic mall intelligence layer.

Converts raw mall JSON (canonical tenant data) into structured entities
enriched with semantic tags that enable concierge-level reasoning about
visitor intent — gift shopping, family outings, quick lunches, romantic
evenings, kids entertainment, dessert-after-movie, and more.

Usage:
    from app.context.semantic_mall_model import build_semantic_mall_context

    with open("data/canonical/al_nakheel_plaza_28.json") as f:
        raw = json.load(f)

    ctx = build_semantic_mall_context(raw)
    # ctx["tenants"]              — list of semantically-tagged Tenant dicts
    # ctx["categories"]           — tenants grouped by category
    # ctx["experience_clusters"]  — tenants grouped by experience type
    # ctx["mall_summary"]         — human-readable mall overview
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════
# Canonical Tenant schema
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Tenant:
    """
    A unified, semantically-enriched representation of any mall entity
    (store, restaurant, cinema, service, etc.).

    The tag fields power concierge reasoning beyond simple name matching.
    """

    id: str
    name: str
    category: str
    subcategories: list[str] = field(default_factory=list)

    experience_tags: list[str] = field(default_factory=list)
    audience_tags: list[str] = field(default_factory=list)
    price_tags: list[str] = field(default_factory=list)
    intent_tags: list[str] = field(default_factory=list)

    location: dict[str, Any] = field(default_factory=dict)
    floor: str = ""

    # Preserved raw metadata for downstream consumers
    entity_type: str = ""
    description: str = ""
    raw_tags: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "subcategories": self.subcategories,
            "experience_tags": self.experience_tags,
            "audience_tags": self.audience_tags,
            "price_tags": self.price_tags,
            "intent_tags": self.intent_tags,
            "location": self.location,
            "floor": self.floor,
            "entity_type": self.entity_type,
            "description": self.description,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Tag inference engine
# ═══════════════════════════════════════════════════════════════════════════


def _infer_experience_tags(entity: dict[str, Any]) -> list[str]:
    """Derive experience tags from entity attributes."""
    tags: list[str] = []
    etype = entity.get("entity_type", "")
    raw_tags = {t.lower() for t in entity.get("tags", [])}
    features = {f.lower() for f in entity.get("features", [])}
    dining_style = entity.get("dining_style", "")
    avg_meal = entity.get("average_meal_time_minutes", 0)
    price = entity.get("price_range", "")
    has_private = entity.get("has_private_dining", False)
    cuisine = entity.get("cuisine_type", "").lower()

    # Quick bite — fast dining options
    if dining_style in ("fast_casual", "quick_service", "food_court_counter"):
        tags.append("quick_bite")
    elif etype == "dining" and avg_meal and avg_meal <= 20:
        tags.append("quick_bite")

    # Romantic — premium dining with private/intimate setting
    if has_private and price in ("premium", "luxury"):
        tags.append("romantic")
    elif {"romantic", "date_night"} & raw_tags:
        tags.append("romantic")

    # Family dining
    if entity.get("has_kids_menu") or "family" in raw_tags:
        if etype == "dining":
            tags.append("family_dining")

    # Dessert
    if dining_style in ("dessert", "dessert_cafe") or "dessert" in raw_tags or "ice_cream" in raw_tags or "sweet_treat" in raw_tags:
        tags.append("dessert")

    # Cafe / coffee spot
    if dining_style in ("cafe", "dessert_cafe") or "coffee" in raw_tags:
        tags.append("cafe_hangout")

    # Luxury shopping
    if price in ("premium", "luxury") and etype == "store":
        tags.append("luxury_shopping")

    # Budget shopping
    if price == "budget" and etype == "store":
        tags.append("budget_shopping")

    # Movie / cinema combo
    if etype in ("cinema", "movie"):
        tags.append("movie_combo")
    elif "near_cinema" in raw_tags or "after_movie" in raw_tags:
        tags.append("movie_combo")

    # Kids entertainment
    if etype in ("cinema", "store") and (
        "kids" in raw_tags or "children" in raw_tags or "playful" in raw_tags
    ):
        tags.append("kids_entertainment")
    if entity.get("entity_type") == "movie":
        audience_fit = entity.get("audience_fit", [])
        if any(a in audience_fit for a in ("kids", "families", "young_children")):
            tags.append("kids_entertainment")

    # Sit-down dining
    if dining_style in ("casual_dining", "fine_dining") and avg_meal and avg_meal >= 40:
        tags.append("sit_down_meal")

    # Special occasion
    if "special_occasion" in raw_tags or has_private:
        tags.append("special_occasion")

    # Traditional / cultural experience
    if any(k in cuisine for k in ("saudi", "arabian", "arabic")):
        tags.append("cultural_dining")

    # Gift-ready experience (stores with gift wrapping, personalization)
    if {"gift_wrapping", "engraving", "custom_designs", "charm_customization"} & features:
        tags.append("gift_experience")

    # Beauty / self-care
    if "beauty" in raw_tags or "cosmetics" in raw_tags:
        tags.append("self_care")

    return _dedupe(tags)


def _infer_audience_tags(entity: dict[str, Any]) -> list[str]:
    """Derive audience tags from target_audience, features, and entity signals."""
    tags: list[str] = []
    target = [t.lower() for t in entity.get("target_audience", [])]
    raw_tags = {t.lower() for t in entity.get("tags", [])}
    audience_fit = [a.lower() for a in entity.get("audience_fit", [])]

    family_signals = {"families", "family", "parents", "new_parents"}
    kid_signals = {"kids", "children", "young_children", "teens"}
    couple_signals = {"couples", "romance_fans"}
    solo_signals = {"solo_visitors", "professionals", "students"}
    friend_signals = {"groups", "friends", "group_friendly", "casual_meetups"}

    combined = set(target) | raw_tags | set(audience_fit)

    if combined & family_signals:
        tags.append("family")
    if combined & kid_signals:
        tags.append("kids")
    if combined & couple_signals:
        tags.append("couple")
    if combined & solo_signals:
        tags.append("solo")
    if combined & friend_signals:
        tags.append("friends")

    if entity.get("has_kids_menu"):
        if "family" not in tags:
            tags.append("family")
        if "kids" not in tags:
            tags.append("kids")

    # Teens
    if {"teens", "young_adults", "teen_friendly"} & combined:
        tags.append("teens")

    return _dedupe(tags)


def _infer_price_tags(entity: dict[str, Any]) -> list[str]:
    """Map price_range to normalized price tags."""
    price = entity.get("price_range", "")
    mapping: dict[str, list[str]] = {
        "budget": ["budget"],
        "mid_range": ["mid_range"],
        "premium": ["premium"],
        "luxury": ["luxury", "premium"],
    }
    return mapping.get(price, ["mid_range"])


def _infer_intent_tags(entity: dict[str, Any]) -> list[str]:
    """Derive visitor intent tags — what brings someone to this tenant."""
    tags: list[str] = []
    etype = entity.get("entity_type", "")
    category = entity.get("category", "").lower()
    raw_tags = {t.lower() for t in entity.get("tags", [])}
    features = {f.lower() for f in entity.get("features", [])}

    # Dining intent
    if etype == "dining":
        tags.append("dining")

    # Gift intent
    if "gift" in raw_tags or any("gift" in f for f in features):
        tags.append("gift")

    # Fashion intent
    if category in ("fashion", "sportswear") or "fashion" in raw_tags:
        tags.append("fashion")

    # Electronics intent
    if category == "electronics" or "electronics" in raw_tags or "technology" in raw_tags:
        tags.append("electronics")

    # Entertainment intent
    if etype in ("cinema", "movie") or "entertainment" in raw_tags:
        tags.append("entertainment")

    # Beauty / grooming intent
    if category == "beauty" or "beauty" in raw_tags or "cosmetics" in raw_tags:
        tags.append("beauty")

    # Perfume / fragrance intent
    if category == "perfume" or "perfume" in raw_tags or "oud" in raw_tags or "fragrance" in raw_tags:
        tags.append("perfume")

    # Home / decor intent
    if category == "home" or "home" in raw_tags or "furniture" in raw_tags:
        tags.append("home_decor")

    # Kids / baby shopping intent
    if category == "kids" or "baby" in raw_tags or "children" in raw_tags:
        tags.append("kids_shopping")

    # Jewelry intent
    if category == "jewelry" or "jewelry" in raw_tags:
        tags.append("jewelry")

    # Accessories intent
    if category == "accessories" or "accessories" in raw_tags or "handbags" in raw_tags:
        tags.append("accessories")

    # Service intent
    if etype == "service":
        tags.append("services")

    # Event intent
    if etype == "event":
        tags.append("event")

    # Offer / deal intent
    if etype == "offer":
        tags.append("deals")

    return _dedupe(tags)


def _extract_subcategories(entity: dict[str, Any]) -> list[str]:
    """Build a subcategory list from available entity fields."""
    subs: list[str] = []
    sub = entity.get("subcategory", "")
    if sub:
        subs.append(sub)

    cuisine = entity.get("cuisine_type", "")
    if cuisine:
        subs.append(cuisine)

    genre = entity.get("genre", [])
    if genre:
        subs.extend(genre)

    return _dedupe(subs)


def _extract_location(entity: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Extract normalized location dict and floor string."""
    loc = entity.get("location", {})
    if isinstance(loc, dict):
        return loc, loc.get("floor", "")
    return {}, ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Entity → Tenant conversion
# ═══════════════════════════════════════════════════════════════════════════


_ENTITY_COLLECTIONS = [
    "stores",
    "dining",
    "cinemas",
    "movies",
    "services",
    "events",
    "offers",
]


def _entity_to_tenant(entity: dict[str, Any]) -> Tenant:
    """Convert a single raw entity dict into a semantically-tagged Tenant."""
    location, floor = _extract_location(entity)
    name = entity.get("name") or entity.get("title", "")

    return Tenant(
        id=entity.get("entity_id", ""),
        name=name,
        category=entity.get("category", entity.get("entity_type", "")),
        subcategories=_extract_subcategories(entity),
        experience_tags=_infer_experience_tags(entity),
        audience_tags=_infer_audience_tags(entity),
        price_tags=_infer_price_tags(entity),
        intent_tags=_infer_intent_tags(entity),
        location=location,
        floor=floor,
        entity_type=entity.get("entity_type", ""),
        description=entity.get("description", entity.get("synopsis", "")),
        raw_tags=entity.get("tags", []),
        features=entity.get("features", []),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Mall profile & overview builders (trusted data only)
# ═══════════════════════════════════════════════════════════════════════════

_FAMILY_KEYWORDS = frozenset({
    "stroller", "family", "baby", "kids", "children", "wheelchair",
    "nursing", "child", "playful",
})


def build_mall_profile(raw_mall_data: dict[str, Any]) -> dict[str, Any]:
    """
    Build a structured mall profile from **trusted canonical data only**.

    Returns a dict matching the MallProfileBlock schema.  Every field is
    derived from the raw mall JSON; nothing is inferred by the LLM.
    """
    profile = raw_mall_data.get("mall_profile", {})
    if not profile:
        return {}

    mall_id: str = profile.get("mall_id", "")
    name: str = profile.get("name", "")
    city: str = profile.get("city", "")
    address: str = profile.get("address", "")
    total_stores: int = profile.get("total_stores", 0)
    floors: list[str] = profile.get("floors", [])
    year_opened = profile.get("year_opened")
    zones: list[dict[str, Any]] = profile.get("zones", [])
    amenities: list[str] = profile.get("amenities", [])
    facilities: list[dict[str, Any]] = profile.get("facilities", [])
    parking: dict[str, Any] = profile.get("parking", {})

    # ── positioning (factual one-liner) ──────────────────────────────
    pos_parts: list[str] = []
    if total_stores:
        pos_parts.append(f"{total_stores}-store shopping destination")
    if city:
        pos_parts.append(f"in {city}")
    if floors:
        pos_parts.append(f"spanning {len(floors)} floors")
    if year_opened:
        pos_parts.append(f"(opened {year_opened})")
    positioning = " ".join(pos_parts)

    # ── opening_hours — straight from source ─────────────────────────
    hours_raw = profile.get("operating_hours", {})
    opening_hours: dict[str, str] = {}
    if isinstance(hours_raw, dict):
        for k, v in hours_raw.items():
            if v:
                opening_hours[k] = str(v) if not isinstance(v, str) else v

    # ── highlights (derived from zones + anchors) ────────────────────
    highlights: list[str] = []
    for zone in zones:
        zone_name = zone.get("name", "")
        anchors = zone.get("anchor_tenants", [])
        focus = zone.get("category_focus", [])
        if not zone_name:
            continue
        parts: list[str] = [zone_name]
        if focus:
            parts.append(f"({', '.join(focus[:3])})")
        if anchors:
            parts.append(f"— {', '.join(anchors[:3])}")
        highlights.append(" ".join(parts))

    # ── services_and_facilities ──────────────────────────────────────
    svc_fac: list[str] = list(amenities)
    for fac in facilities:
        fac_name = fac.get("name", "")
        fac_desc = fac.get("description", "")
        loc = fac.get("location", {})
        floor_str = loc.get("floor", "") if isinstance(loc, dict) else ""
        entry = fac_name
        if floor_str:
            entry += f" ({floor_str})"
        if fac_desc:
            entry += f" — {fac_desc}"
        if entry:
            svc_fac.append(entry)

    if parking:
        parking_bits: list[str] = []
        if parking.get("total_capacity"):
            parking_bits.append(f"{parking['total_capacity']} spaces")
        if parking.get("valet_available"):
            parking_bits.append("valet available")
        if parking.get("ev_charging"):
            parking_bits.append("EV charging")
        if parking.get("hourly_rate"):
            parking_bits.append(parking["hourly_rate"])
        if parking_bits:
            svc_fac.append(f"Parking: {', '.join(parking_bits)}")

    # ── family_friendly_notes (only from structured data) ────────────
    family_notes: list[str] = []
    for fac in facilities:
        fac_type = fac.get("facility_type", "")
        if fac_type in ("family_room", "prayer_room", "accessibility"):
            loc = fac.get("location", {})
            floor_str = loc.get("floor", "") if isinstance(loc, dict) else ""
            note = fac.get("name", "")
            if floor_str:
                note += f" ({floor_str})"
            fac_desc = fac.get("description", "")
            if fac_desc:
                note += f" — {fac_desc}"
            family_notes.append(note)

    for amenity in amenities:
        if any(kw in amenity.lower() for kw in _FAMILY_KEYWORDS):
            if amenity not in family_notes:
                family_notes.append(amenity)

    for zone in zones:
        focus = [f.lower() for f in zone.get("category_focus", [])]
        if any(f in ("kids", "family", "kids entertainment") for f in focus):
            zone_name = zone.get("name", "")
            desc = zone.get("description", "")
            if desc:
                family_notes.append(f"{zone_name}: {desc}")

    # ── summary ──────────────────────────────────────────────────────
    summary_sentences: list[str] = []
    if positioning:
        summary_sentences.append(f"{name} is a {positioning}.")
    zone_names = [z.get("name", "") for z in zones if z.get("name")]
    if zone_names:
        summary_sentences.append(
            f"Key areas include {', '.join(zone_names)}."
        )
    if amenities:
        summary_sentences.append(
            f"Visitor amenities: {', '.join(amenities[:5])}."
        )
    summary = " ".join(summary_sentences)

    # ── concierge_overview_lines ─────────────────────────────────────
    overview_lines: list[str] = []
    if summary_sentences:
        overview_lines.append(summary_sentences[0])
    if opening_hours.get("weekday"):
        overview_lines.append(
            f"Open weekdays {opening_hours['weekday']}."
        )
    if opening_hours.get("friday"):
        overview_lines.append(f"Friday hours: {opening_hours['friday']}.")
    if opening_hours.get("ramadan"):
        overview_lines.append(
            f"Ramadan hours: {opening_hours['ramadan']}."
        )
    if zone_names:
        overview_lines.append(
            f"The mall has {len(zone_names)} zones: {', '.join(zone_names)}."
        )
    if family_notes:
        overview_lines.append(
            f"Family-friendly facilities: {', '.join(family_notes[:3])}."
        )
    if highlights:
        overview_lines.append(f"Shopping highlight: {highlights[0]}.")

    return {
        "mall_id": mall_id,
        "name": name,
        "city": city,
        "positioning": positioning,
        "summary": summary,
        "address": address,
        "opening_hours": opening_hours,
        "highlights": highlights,
        "services_and_facilities": svc_fac,
        "family_friendly_notes": family_notes,
        "concierge_overview_lines": overview_lines,
    }


def build_mall_overview_block(mall_profile: dict[str, Any]) -> dict[str, Any]:
    """
    Build a ``mall_overview`` topic block from a structured mall profile.

    This block is injected at runtime when the user asks high-level
    questions such as "tell me about the mall" or "is this place
    family friendly?".
    """
    return {
        "topic_block_id": "mall_overview",
        "domain": "mall_info",
        "sub_intent": "overview",
        "summary_lines": mall_profile.get("concierge_overview_lines", []),
        "opening_hours": mall_profile.get("opening_hours", {}),
        "highlights": mall_profile.get("highlights", []),
        "facilities": mall_profile.get("services_and_facilities", []),
        "family_notes": mall_profile.get("family_friendly_notes", []),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Mall summary builder (legacy — kept for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════


def _build_mall_summary(raw: dict[str, Any], tenants: list[Tenant]) -> str:
    """Produce a concise human-readable mall summary for LLM context."""
    profile = raw.get("mall_profile", {})
    name = profile.get("name", "Mall")
    city = profile.get("city", "")
    floors = profile.get("floors", [])
    total = profile.get("total_stores", len(tenants))
    zones = profile.get("zones", [])
    zone_names = [z.get("name", "") for z in zones if z.get("name")]

    dining_count = sum(1 for t in tenants if t.entity_type == "dining")
    store_count = sum(1 for t in tenants if t.entity_type == "store")
    cinema_count = sum(1 for t in tenants if t.entity_type == "cinema")

    parts = [
        f"{name} is a {total}-store shopping destination in {city}",
        f"spanning {len(floors)} floors ({', '.join(floors)}).",
    ]
    if zone_names:
        parts.append(f"Key zones: {', '.join(zone_names)}.")
    parts.append(
        f"The mall currently features {store_count} retail stores, "
        f"{dining_count} dining outlets, and {cinema_count} cinema venue(s)."
    )

    hours = profile.get("operating_hours", {})
    if hours:
        weekday = hours.get("weekday", "")
        friday = hours.get("friday", "")
        if weekday:
            parts.append(f"Weekday hours: {weekday}.")
        if friday:
            parts.append(f"Friday hours: {friday}.")

    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Category and experience cluster builders
# ═══════════════════════════════════════════════════════════════════════════


def _build_categories(tenants: list[Tenant]) -> dict[str, list[dict[str, Any]]]:
    """Group tenants by their primary category."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tenants:
        groups[t.category].append({
            "id": t.id,
            "name": t.name,
            "floor": t.floor,
            "price_tags": t.price_tags,
            "intent_tags": t.intent_tags,
        })
    return dict(groups)


_EXPERIENCE_CLUSTER_DEFS: dict[str, dict[str, Any]] = {
    "gift_shopping": {
        "label": "Gift Shopping",
        "description": "Stores and outlets ideal for finding gifts",
        "match_experience": ["gift_experience", "luxury_shopping"],
        "match_intent": ["gift", "jewelry", "perfume", "accessories"],
    },
    "family_outing": {
        "label": "Family Outing",
        "description": "Family and kid-friendly options across the mall",
        "match_experience": ["family_dining", "kids_entertainment"],
        "match_audience": ["family"],
    },
    "quick_lunch": {
        "label": "Quick Lunch",
        "description": "Fast dining for visitors with limited time",
        "match_experience": ["quick_bite", "cafe_hangout"],
    },
    "romantic_evening": {
        "label": "Romantic Evening",
        "description": "Premium dining and experiences for couples",
        "match_experience": ["romantic", "special_occasion"],
        "match_audience": ["couple"],
    },
    "kids_entertainment": {
        "label": "Kids Entertainment",
        "description": "Activities, movies, and outlets for children",
        "match_experience": ["kids_entertainment"],
    },
    "dessert_after_movie": {
        "label": "Dessert After Movie",
        "description": "Sweet treats and dessert spots near the cinema",
        "match_experience": ["dessert", "movie_combo"],
    },
    "self_care_beauty": {
        "label": "Self-Care & Beauty",
        "description": "Beauty, cosmetics, and personal care destinations",
        "match_experience": ["self_care"],
        "match_intent": ["beauty"],
    },
    "budget_friendly": {
        "label": "Budget-Friendly",
        "description": "Affordable options across shopping and dining",
        "match_experience": ["budget_shopping", "quick_bite"],
        "match_price": ["budget"],
    },
    "cultural_experience": {
        "label": "Cultural Dining",
        "description": "Traditional Saudi and Arabian dining experiences",
        "match_experience": ["cultural_dining"],
    },
}


def _build_experience_clusters(
    tenants: list[Tenant],
) -> dict[str, dict[str, Any]]:
    """Group tenants into experience-based clusters for intent reasoning."""
    clusters: dict[str, dict[str, Any]] = {}

    for cluster_id, definition in _EXPERIENCE_CLUSTER_DEFS.items():
        matched: list[dict[str, Any]] = []
        for t in tenants:
            exp_set = set(t.experience_tags)
            aud_set = set(t.audience_tags)
            int_set = set(t.intent_tags)
            price_set = set(t.price_tags)

            hit = False
            if set(definition.get("match_experience", [])) & exp_set:
                hit = True
            if set(definition.get("match_audience", [])) & aud_set:
                hit = True
            if set(definition.get("match_intent", [])) & int_set:
                hit = True
            if set(definition.get("match_price", [])) & price_set:
                hit = True

            if hit:
                matched.append({
                    "id": t.id,
                    "name": t.name,
                    "category": t.category,
                    "floor": t.floor,
                    "experience_tags": t.experience_tags,
                    "audience_tags": t.audience_tags,
                })

        clusters[cluster_id] = {
            "label": definition["label"],
            "description": definition["description"],
            "tenants": matched,
            "count": len(matched),
        }

    return clusters


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def build_semantic_mall_context(
    raw_mall_json: dict[str, Any],
) -> dict[str, Any]:
    """
    Transform raw mall JSON into a semantic intelligence structure.

    Parameters
    ----------
    raw_mall_json : dict
        The full canonical mall JSON (e.g., al_nakheel_plaza_28.json) containing
        ``mall_profile``, ``stores``, ``dining``, ``cinemas``, ``movies``,
        ``services``, ``events``, and ``offers``.

    Returns
    -------
    dict with keys:
        ``mall_profile``          — structured profile (MallProfileBlock-compatible)
        ``mall_overview_block``   — topic block for runtime overview injection
        ``mall_summary``          — human-readable overview string
        ``tenants``               — list of Tenant dicts with semantic tags
        ``categories``            — tenants grouped by primary category
        ``experience_clusters``   — tenants grouped by experience scenarios

    The experience_clusters allow the chatbot to reason about composite
    visitor intents such as "gift shopping", "family outing",
    "quick lunch", "romantic evening", "kids entertainment",
    and "dessert after movie".
    """
    tenants: list[Tenant] = []

    for collection_key in _ENTITY_COLLECTIONS:
        for entity in raw_mall_json.get(collection_key, []):
            tenants.append(_entity_to_tenant(entity))

    profile = build_mall_profile(raw_mall_json)
    overview_block = build_mall_overview_block(profile) if profile else {}

    return {
        "mall_profile": profile,
        "mall_overview_block": overview_block,
        "mall_summary": _build_mall_summary(raw_mall_json, tenants),
        "tenants": [t.to_dict() for t in tenants],
        "categories": _build_categories(tenants),
        "experience_clusters": _build_experience_clusters(tenants),
    }
