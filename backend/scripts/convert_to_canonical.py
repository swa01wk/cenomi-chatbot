#!/usr/bin/env python3
"""
convert_to_canonical.py
=======================
Deterministic converter: output_mall_28.json → canonical/<mall_id>.json

Reads the structured mall output file produced by transform_mall_data.py and
converts it into the canonical schema that the Cenomi Concierge system expects.

Usage:
  python scripts/convert_to_canonical.py ../../output_mall_28.json
  python scripts/convert_to_canonical.py ../../output_mall_28.json --out-dir data/canonical
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# ──────────────────────────────────────────────────────────────────
# Category → entity mapping tables
# ──────────────────────────────────────────────────────────────────

DINING_CATEGORY_MAP: dict[str, tuple[str, str, str]] = {
    # raw_category: (canonical_category, subcategory, dining_style)
    "Chicken Cuisine":                                   ("Dining",   "Fast Food",      "fast_food"),
    "Fast food excluding Food Court":                    ("Dining",   "Fast Food",      "fast_food"),
    "Food items/specialities":                           ("Dining",   "Dessert",        "dessert"),
    "Coffee & Light Dining":                             ("Dining",   "Cafe",           "cafe"),
}

CINEMA_BRAND_NAMES = {"muvi cinema", "vox cinema", "vox cinemas", "cinemaxx"}

STORE_CATEGORY_MAP: dict[str, tuple[str, str, str, str]] = {
    # raw_category: (entity_type, canonical_category, subcategory, price_range)
    "Cosmetics":                                          ("store", "Beauty",      "Cosmetics & Skincare",   "mid_range"),
    "Jewellery":                                          ("store", "Jewelry",     "Fine Jewelry",           "premium"),
    "Watches":                                            ("store", "Accessories", "Watches & Timepieces",   "premium"),
    "Fashion accessories":                                ("store", "Fashion",     "Accessories & Bags",     "mid_range"),
    "Handbags-female wallets":                            ("store", "Fashion",     "Bags & Accessories",     "mid_range"),
    "Women's shoes":                                      ("store", "Fashion",     "Footwear",               "mid_range"),
    "Men's Arabic - Thobes":                              ("store", "Fashion",     "Traditional Wear",       "mid_range"),
    "Lingerie - In home lounge wear, swimwear, hoisery":  ("store", "Fashion",     "Lingerie & Intimate",    "mid_range"),
    "Optical":                                            ("store", "Health",      "Optical",                "mid_range"),
    "Home improvements":                                  ("store", "Home",        "Home Decor & Furniture", "mid_range"),
    "Media Sales-Media Sales":                            ("store", "Electronics", "Digital Media",          "mid_range"),
    "Pharmacy":                                           ("store", "Health",      "Pharmacy",               "mid_range"),
    "Value / Discount Department Stores":                 ("store", "Fashion",     "Value Fashion",          "budget"),
    "Hypermarket":                                        ("store", "Grocery",     "Hypermarket",            "budget"),
    "Full range sports wear and equipment":               ("store", "Sportswear",  "Athletic Apparel & Footwear", "mid_range"),
    "Other Misc Retail Stores":                           ("store", "Lifestyle",   "Miscellaneous",          "mid_range"),
    "Toddlers specialised area":                          ("store", "Kids",        "Kids Entertainment",     "mid_range"),
    "":                                                   ("store", "Retail",      "General",                "mid_range"),
}

# Override price_range for known premium brands
PREMIUM_BRANDS = {
    "l'occitane", "swarovski", "tous", "cole haan", "charles & keith",
    "coach", "nine west",
}
BUDGET_BRANDS = {
    "red tag", "brands for less", "miniso",
}

# ──────────────────────────────────────────────────────────────────
# Zone / floor inference from PMS unit codes
# ──────────────────────────────────────────────────────────────────

def _infer_location(pms_codes: list[str], tags: list[str]) -> dict:
    """Infer floor/zone from PMS unit codes and tags_en location hints."""
    code = (pms_codes[0].upper() if pms_codes else "")

    # Floor + zone from code prefix
    if code.startswith("CNL"):
        floor, zone = "Cinema Level", "Cinema Zone"
    elif code.startswith("GFHYPM"):
        floor, zone = "Ground", "Hypermarket Area"
    elif code.startswith("GFENT"):
        floor, zone = "Ground", "Entertainment Wing"
    elif code.startswith("GFE"):
        floor, zone = "Ground", "Entertainment Wing"
    elif code.startswith("FC"):
        floor, zone = "Ground", "Food Court"
    elif code.startswith("GFK"):
        floor, zone = "Ground", "Kiosk Row"
    elif code.startswith("MEZ"):
        floor, zone = "Mezzanine", "Main Gallery"
    elif code.startswith("GFA"):
        floor, zone = "Ground", "Main Gallery"
    elif code.startswith("GF"):
        floor, zone = "Ground", "Main Gallery"
    elif code.startswith("DM"):
        floor, zone = "Ground", "Digital Media Area"
    elif code.startswith("PRTB"):
        floor, zone = "Ground", "Service Area"
    else:
        floor, zone = "Ground", "Main Gallery"

    # Extract gate/location hint from tags if available
    directions_hint = ""
    for tag in tags:
        tl = tag.lower()
        if "gate" in tl or "floor" in tl or "near" in tl or "next to" in tl or "beside" in tl:
            directions_hint = tag
            break

    unit_number = pms_codes[0] if pms_codes else ""

    result: dict = {"floor": floor, "zone": zone, "unit_number": unit_number}
    if directions_hint:
        result["directions_hint"] = directions_hint
    if len(pms_codes) > 1:
        result["additional_units"] = pms_codes[1:]
    return result


# ──────────────────────────────────────────────────────────────────
# HTML stripping
# ──────────────────────────────────────────────────────────────────

def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"&nbsp;", " ", clean)
    clean = re.sub(r"&amp;", "&", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# ──────────────────────────────────────────────────────────────────
# Operating hours builders
# ──────────────────────────────────────────────────────────────────

def _build_operating_hours(timing_list: list[dict]) -> dict:
    """Convert MallTiming list to canonical operating_hours dict."""
    hours: dict = {}
    day_map = {
        "sunday": "sunday", "monday": "weekday", "tuesday": "weekday",
        "wednesday": "weekday", "thursday": "weekday",
        "friday": "friday", "saturday": "saturday",
    }
    for entry in timing_list:
        day = entry.get("dayen", "").lower()
        start = entry.get("start_time", "")
        end = entry.get("end_time", "")
        if start and end:
            time_str = f"{start}–{end}"
            key = day_map.get(day, day)
            if key == "weekday" and "weekday" not in hours:
                hours["weekday"] = time_str
            elif key not in hours:
                hours[key] = time_str
    if not hours:
        hours = {"weekday": "9:00 AM–11:00 PM", "friday": "1:00 PM–11:00 PM"}
    return hours


# ──────────────────────────────────────────────────────────────────
# Service extraction
# ──────────────────────────────────────────────────────────────────

def _service_title(service: dict) -> str:
    for item in service.get("service_details", {}).get("content", []):
        if item.get("type") == "title":
            return item.get("content", "")
    return f"Service {service.get('id', '?')}"


def _service_desc(service: dict) -> str:
    parts = []
    for item in service.get("details", []):
        t = item.get("type", "")
        c = item.get("content", "")
        if isinstance(c, list):
            parts.extend(c)
        elif c and t in ("paragraph", "heading", "bullet"):
            parts.append(c)
    return " ".join(parts)


def _build_canonical_services(raw_services: list[dict]) -> list[dict]:
    out = []
    for i, svc in enumerate(raw_services, 1):
        sd = svc.get("service_details", {})
        title = _service_title(svc)
        floor = sd.get("floor", "groundFloor").replace("groundFloor", "Ground").replace("firstFloor", "First")

        # Infer service_category from title
        tl = title.lower()
        if "atm" in tl:
            cat = "atm"
        elif "wheelchair" in tl:
            cat = "accessibility"
        elif "lost" in tl:
            cat = "lost_found"
        elif "information" in tl or "help" in tl or "desk" in tl:
            cat = "information"
        elif "stroller" in tl or "baby" in tl:
            cat = "family"
        elif "parking" in tl:
            cat = "parking"
        elif "prayer" in tl:
            cat = "prayer_room"
        elif "washroom" in tl or "restroom" in tl or "toilet" in tl:
            cat = "restroom"
        else:
            cat = "general"

        # Get phone from service_details.content
        phone = ""
        for item in sd.get("content", []):
            if item.get("type") == "phone":
                phone = item.get("content", "")
                break

        tags = ["essential"]
        if cat == "prayer_room":
            tags.append("prayer")
        if cat == "family":
            tags.append("family")
        if cat == "accessibility":
            tags.append("accessibility")

        out.append({
            "entity_id": f"t-svc-{i:03d}",
            "entity_type": "service",
            "name": title,
            "service_category": cat,
            "description": _service_desc(svc) or f"{title} available at {floor} Floor.",
            "location": {
                "floor": floor,
                "zone": "Main Gallery",
                "mappedin_location": sd.get("mappedin_location", ""),
            },
            "operating_hours": {"daily": "9:00 AM–11:00 PM"},
            "contact": {"phone": phone} if phone else {},
            "is_free": cat not in ("valet", "parking"),
            "icon": sd.get("icon", ""),
            "image": sd.get("image", ""),
            "tags": tags,
        })
    return out


# ──────────────────────────────────────────────────────────────────
# Movie extraction
# ──────────────────────────────────────────────────────────────────

def _build_canonical_movies(muvilist: dict, cinema_entity_id: str) -> list[dict]:
    movies = muvilist.get("en") or []
    out = []
    for i, m in enumerate(movies, 1):
        genre_names = [g.get("name", "") for g in (m.get("genres") or []) if g.get("name")]
        runtime = m.get("runtime") or 0
        release = (m.get("releaseDate") or "")[:10]
        movie_type = m.get("type", "")
        is_new = movie_type == "SHOWING_NOW"
        is_coming = movie_type == "COMING_SOON"

        out.append({
            "entity_id": f"t-movie-{i:03d}",
            "entity_type": "movie",
            "title": m.get("title", ""),
            "genre": genre_names,
            "duration_minutes": runtime,
            "language": m.get("movieLang", ""),
            "synopsis": m.get("synopsis", ""),
            "cinema_entity_id": cinema_entity_id,
            "status": movie_type,
            "is_new_release": is_new,
            "is_coming_soon": is_coming,
            "release_date": release,
            "booking_url": m.get("bookingUrl", ""),
            "trailer_url": m.get("trailerUrl", ""),
            "cover_url": m.get("coverUrl", ""),
            "image": m.get("image", ""),
            "tags": genre_names[:3] + (["new_release"] if is_new else []) + (["coming_soon"] if is_coming else []),
        })
    return out


# ──────────────────────────────────────────────────────────────────
# Engagement / offer extraction
# ──────────────────────────────────────────────────────────────────

def _build_canonical_offers(brands: list[dict]) -> list[dict]:
    out = []
    seen = set()
    i = 0
    for brand in brands:
        bid = brand.get("brand_id")
        bname = brand.get("brand_name_en", "")
        for eng in brand.get("engagements") or []:
            eid = eng.get("engagement_id")
            if eid in seen:
                continue
            seen.add(eid)
            i += 1
            out.append({
                "entity_id": f"t-offer-{i:03d}",
                "entity_type": "offer",
                "title": eng.get("title_en", ""),
                "offer_type": eng.get("type", "promotion"),
                "description": _strip_html(eng.get("description_en", "")),
                "terms_conditions": _strip_html(eng.get("terms_conditions_en", "")),
                "tenant_entity_ids": [f"t-store-brand-{bid}"],
                "brand_name": bname,
                "valid_from": (eng.get("start_date") or "")[:10],
                "valid_until": (eng.get("end_date") or "")[:10],
                "is_exclusive": bool(eng.get("is_exclusive")),
                "loyalty_exclusive": False,
                "tags": [t.get("tag_text", "") for t in eng.get("tags_en", []) if t.get("tag_text")],
            })
    return out


# ──────────────────────────────────────────────────────────────────
# Brand → store/dining/cinema
# ──────────────────────────────────────────────────────────────────

def _contact_from_brand(brand: dict) -> dict:
    contact: dict = {}
    code = brand.get("store_phone_code", "").strip()
    number = brand.get("store_phone_number", "").strip()
    if number:
        contact["phone"] = f"{code}{number}" if code else number
    email = brand.get("store_email", "").strip()
    if email:
        contact["email"] = email
    website = brand.get("store_website", "").strip()
    if website:
        contact["website"] = website
    return contact


def _social_from_brand(brand: dict) -> dict:
    social: dict = {}
    for key in ("instagram", "tiktok", "facebook", "twitter", "snapchat", "youtube"):
        val = brand.get(f"social_{key}", "").strip()
        if val:
            social[key] = val
    return social


def _price_range(brand: dict, default: str) -> str:
    name_lower = brand.get("brand_name_en", "").lower()
    if name_lower in PREMIUM_BRANDS:
        return "premium"
    if name_lower in BUDGET_BRANDS:
        return "budget"
    return default


def _tags_from_brand(brand: dict, cat: str, subcat: str) -> list[str]:
    base = [cat.lower().replace(" & ", "_").replace(" ", "_"),
            subcat.lower().replace(" & ", "_").replace(" ", "_")]
    if brand.get("anchor_brand"):
        base.append("anchor")
    if brand.get("google_rating"):
        base.append("highly_rated")
    return list(dict.fromkeys(base))  # dedupe preserving order


def convert_brands(brands_raw: list[dict], mall_timing: list[dict]) -> tuple[list, list, list | None]:
    """
    Returns (stores, dining, cinema_entity_or_None).
    Cinema is returned as the first dict if found.
    """
    stores: list[dict] = []
    dining: list[dict] = []
    cinema: dict | None = None

    store_idx = 0
    dining_idx = 0

    for brand in brands_raw:
        if not brand.get("is_published", True):
            continue

        bname = brand.get("brand_name_en", "").strip()
        if not bname:
            continue

        raw_cat = brand.get("category_name", "")
        pms_codes = brand.get("pms_unit_codes") or []
        tags_raw = [t.get("tag_text", "") for t in (brand.get("tags_en") or []) if t.get("tag_text")]
        location = _infer_location(pms_codes, tags_raw)
        contact = _contact_from_brand(brand)
        description = _strip_html(brand.get("description_en", ""))
        logo = brand.get("brand_logo", "")
        brand_id = brand.get("brand_id")

        # Build operating_hours from mall timing (use mall hours as default)
        op_hours = _build_operating_hours(mall_timing)

        # ── Cinema ──────────────────────────────────────────────
        if bname.lower() in CINEMA_BRAND_NAMES:
            cinema = {
                "entity_id": "t-cinema-001",
                "entity_type": "cinema",
                "name": bname,
                "brand": brand.get("group_name", bname),
                "description": description or f"{bname} — cinema at Al Nakheel Plaza.",
                "location": location,
                "operating_hours": op_hours,
                "contact": contact,
                "brand_logo": logo,
                "booking_url": brand.get("store_website", ""),
                "formats_available": ["Standard"],
                "price_range": "mid_range",
                "accepts_loyalty": False,
                "tags": ["cinema", "movies", "entertainment"],
            }
            continue

        # ── Dining ──────────────────────────────────────────────
        if raw_cat in DINING_CATEGORY_MAP:
            dining_idx += 1
            _, subcat, dining_style = DINING_CATEGORY_MAP[raw_cat]
            is_fast = dining_style in ("fast_food",)
            price = "budget" if is_fast else "mid_range"

            dining.append({
                "entity_id": f"t-dining-{dining_idx:03d}",
                "entity_type": "dining",
                "name": bname,
                "brand": brand.get("company_name_en", bname),
                "brand_id": brand_id,
                "cuisine_type": raw_cat,
                "dining_style": dining_style,
                "description": description or f"{bname} — {subcat.lower()} at Al Nakheel Plaza.",
                "location": location,
                "operating_hours": op_hours,
                "contact": contact,
                "brand_logo": logo,
                "price_range": price,
                "halal_certified": True,
                "has_kids_menu": dining_style in ("fast_food", "fast_casual"),
                "average_meal_time_minutes": 10 if dining_style == "dessert" else 20 if is_fast else 30,
                "accepts_loyalty": False,
                "group_name": brand.get("group_name", ""),
                "tags": [dining_style, "halal", raw_cat.lower().replace(" ", "_")][:5],
            })
            continue

        # ── Store (everything else) ──────────────────────────────
        store_idx += 1
        etype, cat, subcat, default_price = STORE_CATEGORY_MAP.get(
            raw_cat, ("store", "Retail", "General", "mid_range")
        )
        price = _price_range(brand, default_price)

        features: list[str] = []
        if brand.get("anchor_brand"):
            features.append("anchor_tenant")
        social = _social_from_brand(brand)

        store_obj: dict = {
            "entity_id": f"t-store-{store_idx:03d}",
            "entity_type": etype,
            "name": bname,
            "brand": brand.get("company_name_en", bname),
            "brand_id": brand_id,
            "category": cat,
            "subcategory": subcat,
            "description": description or f"{bname} — {subcat.lower()} store at Al Nakheel Plaza.",
            "location": location,
            "operating_hours": op_hours,
            "contact": contact,
            "price_range": price,
            "brand_logo": logo,
            "banner": brand.get("banner_en", ""),
            "anchor_brand": bool(brand.get("anchor_brand")),
            "accepts_loyalty": False,
            "group_name": brand.get("group_name", ""),
            "tags": _tags_from_brand(brand, cat, subcat),
        }
        if features:
            store_obj["features"] = features
        if social:
            store_obj["social"] = social
        stores.append(store_obj)

    return stores, dining, cinema


# ──────────────────────────────────────────────────────────────────
# Mall profile builder
# ──────────────────────────────────────────────────────────────────

def build_mall_profile(data: dict) -> dict:
    info = data.get("mall_information", {})
    timing = info.get("MallTiming") or []
    contact_raw = info.get("MallContact") or {}
    social_raw = info.get("Social") or {}

    gps_raw = data.get("gps_coordinates", "")
    coords: dict = {}
    if gps_raw and "," in gps_raw:
        try:
            lat_s, lng_s = gps_raw.split(",", 1)
            coords = {"lat": float(lat_s.strip()), "lng": float(lng_s.strip())}
        except ValueError:
            pass

    mall_name = info.get("MallNameEn") or data.get("marketing_name") or "Al Nakheel Plaza"
    city = data.get("city", "Buraidah")
    country = data.get("country", "Saudi Arabia")
    mall_id = f"al_nakheel_plaza_{data.get('property_group_id', 28)}"

    zones = [
        {
            "zone_id": "z-main",
            "name": "Main Gallery",
            "floor": "Ground",
            "description": "Primary shopping corridor with fashion, cosmetics, jewelry, accessories, and watches.",
            "category_focus": ["Fashion", "Cosmetics", "Jewelry", "Accessories", "Watches"],
        },
        {
            "zone_id": "z-kiosk",
            "name": "Kiosk Row",
            "floor": "Ground",
            "description": "Inline kiosks and small-format stores — perfumes, cafes, accessories.",
            "category_focus": ["Perfumes", "Cafe", "Accessories"],
        },
        {
            "zone_id": "z-foodcourt",
            "name": "Food Court",
            "floor": "Ground",
            "description": "Quick-service and fast-casual dining — McDonald's, Herfy, Kudu, Popeyes, Cinnabon.",
            "category_focus": ["Fast Food", "Cafe", "Dessert"],
        },
        {
            "zone_id": "z-entertainment",
            "name": "Entertainment Wing",
            "floor": "Ground",
            "description": "Large-format retail and entertainment — Adidas, Brands For Less, Home Box, Centrepoint, Fun Time.",
            "category_focus": ["Sportswear", "Value Fashion", "Home", "Kids Entertainment"],
        },
        {
            "zone_id": "z-hypermarket",
            "name": "Hypermarket Area",
            "floor": "Ground",
            "description": "Danube HyperMarket — groceries, fresh produce, and household essentials.",
            "category_focus": ["Grocery", "Hypermarket"],
        },
        {
            "zone_id": "z-cinema",
            "name": "Cinema Zone",
            "floor": "Cinema Level",
            "description": "Muvi Cinemas — the entertainment anchor with multiple screens.",
            "category_focus": ["Cinema", "Entertainment"],
        },
    ]

    landmarks = [
        {
            "landmark_id": "lm-gate1",
            "name": "Main Entrance (Gate 1)",
            "landmark_type": "entrance",
            "location": {"floor": "Ground", "zone": "Main Gallery"},
            "description": "Primary mall entrance.",
        },
        {
            "landmark_id": "lm-gate2",
            "name": "Gate 2",
            "landmark_type": "entrance",
            "location": {"floor": "Ground", "zone": "Main Gallery", "directions_hint": "Near Information Desk"},
            "description": "Secondary entrance — Information Desk and Customer Service nearby.",
        },
        {
            "landmark_id": "lm-infodesks",
            "name": "Information Desk",
            "landmark_type": "information_desk",
            "location": {"floor": "Ground", "zone": "Main Gallery", "directions_hint": "Gate 2, Customer Service area"},
            "description": "Guest services, lost & found, wheelchair and stroller loans.",
        },
    ]

    facilities = [
        {
            "facility_id": "fac-prayer-men",
            "name": "Men's Prayer Room",
            "facility_type": "prayer_room",
            "location": {"floor": "Ground", "zone": "Main Gallery", "directions_hint": "Near Gate 1, Lavalle Store"},
            "description": "Men's prayer room with ablution facilities.",
            "tags": ["prayer", "essential"],
        },
        {
            "facility_id": "fac-prayer-women",
            "name": "Women's Prayer Room",
            "facility_type": "prayer_room",
            "location": {"floor": "Ground", "zone": "Main Gallery"},
            "description": "Women's prayer room with ablution facilities.",
            "tags": ["prayer", "essential"],
        },
        {
            "facility_id": "fac-wheelchair",
            "name": "Wheelchair Service",
            "facility_type": "accessibility",
            "location": {"floor": "Ground", "zone": "Main Gallery"},
            "description": "Complimentary wheelchair loans available.",
            "tags": ["accessibility", "wheelchair"],
        },
        {
            "facility_id": "fac-stroller",
            "name": "Baby Stroller Service",
            "facility_type": "family",
            "location": {"floor": "Ground", "zone": "Main Gallery", "directions_hint": "Gate 2, near Doknah Kiosk"},
            "description": "Baby stroller loans available 4:00 PM – 11:00 PM.",
            "tags": ["family", "baby"],
        },
    ]

    return {
        "mall_id": mall_id,
        "name": mall_name,
        "marketing_name": data.get("marketing_name", mall_name),
        "city": city,
        "country": country,
        "address": f"{contact_raw.get('Address1En', '')} {contact_raw.get('Address2En', '')}".strip(),
        "coordinates": coords,
        "floors": ["Ground", "Cinema Level"],
        "zones": zones,
        "landmarks": landmarks,
        "facilities": facilities,
        "parking": {
            "notes": "Parking available at multiple gates — in front of each gate.",
            "details": "Ground floor parking. Located in front of each gate.",
        },
        "operating_hours": _build_operating_hours(timing),
        "contact": {
            "phone": contact_raw.get("Phone", ""),
            "email": contact_raw.get("Email", ""),
            "address": f"{contact_raw.get('Address1En', '')} {contact_raw.get('Address2En', '')}".strip(),
            "google_map": info.get("GoogleMapURL", ""),
            "social_media": {
                k: v.strip()
                for k, v in {
                    "instagram": social_raw.get("Instagram", ""),
                    "x": social_raw.get("X", ""),
                    "youtube": social_raw.get("Youtube", ""),
                    "linkedin": social_raw.get("LinkedIn", ""),
                }.items()
                if v.strip()
            },
        },
        "amenities": [
            "Free Parking",
            "Prayer Rooms (Ground Floor)",
            "Baby Stroller Loans",
            "Wheelchair Loans",
            "ATM Machines",
            "Information Desk",
            "Lost & Found",
            "WiFi (Mobily)",
            "Washrooms",
        ],
        "accessibility_features": [
            "Wheelchair ramps at entrances",
            "Accessible washrooms",
            "Complimentary wheelchair loans",
        ],
        "loyalty": {
            "program_name": "Cenomi Rewards",
            "description": "Earn points on purchases at participating stores.",
            "app_available": True,
            "signup_locations": ["Information Desk", "Cenomi Rewards app"],
        },
        "map_url": info.get("MallMapEn", ""),
        "logo": info.get("MallLogoEn", ""),
        "images": [
            v for v in info.get("MallImagesEn", {}).values() if v
        ],
        "property_group_id": data.get("property_group_id"),
        "description": info.get("MallDescriptionEn", ""),
        "sr_mallname": info.get("sr_mallname", ""),
    }


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def convert(source_path: Path) -> dict:
    print(f"📂 Reading: {source_path}")
    with source_path.open(encoding="utf-8") as f:
        data = json.load(f)

    info = data.get("mall_information", {})
    timing = info.get("MallTiming") or []

    # Build sections
    mall_profile = build_mall_profile(data)
    mall_id = mall_profile["mall_id"]

    brands_raw = data.get("brands") or []
    stores, dining, cinema = convert_brands(brands_raw, timing)

    # Cinema entity
    cinemas: list[dict] = []
    cinema_entity_id = "t-cinema-001"
    if cinema:
        cinemas = [cinema]
    else:
        # No cinema brand found — create a stub from muvi list presence
        muvilist = data.get("muvilist") or {}
        if muvilist.get("en") or muvilist.get("ar"):
            cinemas = [{
                "entity_id": cinema_entity_id,
                "entity_type": "cinema",
                "name": "Muvi Cinemas",
                "brand": "Muvi Cinemas",
                "description": "Muvi Cinemas at Al Nakheel Plaza — multiple screens with current releases.",
                "location": {"floor": "Cinema Level", "zone": "Cinema Zone"},
                "operating_hours": _build_operating_hours(timing),
                "formats_available": ["Standard"],
                "price_range": "mid_range",
                "accepts_loyalty": False,
                "tags": ["cinema", "movies", "entertainment"],
            }]

    # Movies
    muvilist = data.get("muvilist") or {}
    movies = _build_canonical_movies(muvilist, cinema_entity_id)

    # Services
    services = _build_canonical_services(data.get("services") or [])

    # Offers (from brand engagements)
    offers = _build_canonical_offers(brands_raw)

    canonical = {
        "mall_profile": mall_profile,
        "stores": stores,
        "dining": dining,
        "cinemas": cinemas,
        "movies": movies,
        "services": services,
        "events": [],    # None in source data
        "offers": offers,
    }

    # Summary
    print(f"  Mall:     {mall_profile['name']} ({mall_id})")
    print(f"  City:     {mall_profile['city']}, {mall_profile['country']}")
    print(f"  Stores:   {len(stores)}")
    print(f"  Dining:   {len(dining)}")
    print(f"  Cinemas:  {len(cinemas)}")
    print(f"  Movies:   {len(movies)}")
    print(f"  Services: {len(services)}")
    print(f"  Offers:   {len(offers)}")

    return canonical, mall_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert output_mall_XX.json → backend/data/canonical/<mall_id>.json",
    )
    parser.add_argument("input", type=Path, help="Path to output_mall_XX.json")
    parser.add_argument(
        "--out-dir", type=Path,
        default=BACKEND_DIR / "data" / "canonical",
        help="Directory to write canonical JSON (default: backend/data/canonical/)",
    )
    args = parser.parse_args()

    source = args.input
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        print(f"Error: {source} not found", file=sys.stderr)
        sys.exit(1)

    canonical, mall_id = convert(source)

    out_path = args.out_dir / f"{mall_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(canonical, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\n✅ Canonical written → {out_path}")
    print(f"\nNext step: run the synthesizer on this file:")
    print(f"  python scripts/synthesize_mall_data.py data/canonical/{mall_id}.json")


if __name__ == "__main__":
    main()
