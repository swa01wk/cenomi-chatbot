"""
transform_mall_data.py

Reads 5 JSON source files from ./data and produces a single structured output
JSON for mall property_group_id = 28, shaped to match the sample.json contract.

Source files:
  - data/sample.json        → output schema contract
  - data/mall_and_movie.json → mall metadata + muvilist
  - data/services.json       → mall services
  - data/engagements.json    → promotions/offers (reverse-mapped by brand_id)
  - data/brands.json         → brand/tenant records (reverse-mapped by available_in_malls)

Output:
  - output_mall_28.json
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

TARGET_PROPERTY_GROUP_ID = 28
DATA_DIR = "./data"
OUTPUT_FILE = "output_mall_28.json"


# ─────────────────────────────────────────────
# Helper: I/O
# ─────────────────────────────────────────────

def load_json(filepath: str) -> Any:
    """Load and parse a JSON file safely. Returns None on any error."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"[WARN] File not found: {filepath}")
        return None
    except json.JSONDecodeError as exc:
        print(f"[WARN] JSON decode error in {filepath}: {exc}")
        return None


# ─────────────────────────────────────────────
# Helper: ID normalisation
# ─────────────────────────────────────────────

def normalize_id(value: Any) -> str:
    """
    Coerce any ID value (int, str, float) to a stripped string so that
    comparisons like 28 == "28" are always handled correctly.
    """
    if value is None:
        return ""
    return str(value).strip()


# ─────────────────────────────────────────────
# Helper: brands index
# ─────────────────────────────────────────────

def index_brands_by_id(brands_list: List[Dict]) -> Dict[str, Dict]:
    """
    Build a lookup dict keyed by normalised brand_id for O(1) access
    when merging engagements into brands.
    """
    index: Dict[str, Dict] = {}
    for brand in brands_list:
        bid = normalize_id(brand.get("brand_id"))
        if bid:
            index[bid] = brand
    return index


# ─────────────────────────────────────────────
# Helper: filtering
# ─────────────────────────────────────────────

def filter_engagements_for_mall(
    engagements_list: List[Dict],
    property_group_id: int,
) -> List[Dict]:
    """
    Return only engagements whose property_group_ids list contains the
    target property_group_id (reverse-mapped; safe across int/str types).
    """
    target = normalize_id(property_group_id)
    matched: List[Dict] = []
    for eng in engagements_list:
        if not isinstance(eng, dict):
            continue
        pg_ids = eng.get("property_group_ids") or []
        if any(normalize_id(pgid) == target for pgid in pg_ids):
            matched.append(eng)
    return matched


def filter_brands_for_mall(
    brands_list: List[Dict],
    property_group_id: int,
) -> List[Dict]:
    """
    Return only brands whose available_in_malls list contains an entry
    with the target property_group_id (reverse-mapped; safe across int/str).
    """
    target = normalize_id(property_group_id)
    matched: List[Dict] = []
    for brand in brands_list:
        if not isinstance(brand, dict):
            continue
        available = brand.get("available_in_malls") or []
        if any(normalize_id(m.get("property_group_id")) == target for m in available if isinstance(m, dict)):
            matched.append(brand)
    return matched


# ─────────────────────────────────────────────
# Helper: source record extractors
# ─────────────────────────────────────────────

def get_mall_record(mall_list: List[Dict], property_group_id: int) -> Optional[Dict]:
    """
    Find and return the first mall record whose property_group_id matches.
    Returns None when the mall is absent.
    """
    target = normalize_id(property_group_id)
    for mall in mall_list:
        if not isinstance(mall, dict):
            continue
        if normalize_id(mall.get("property_group_id")) == target:
            return mall
    return None


def get_services_for_mall(services_data: Dict) -> List[Dict]:
    """
    Extract the services list from the already-filtered services.json payload.
    The file is pre-filtered for property_group_id = 28, so all entries are used.
    """
    if not isinstance(services_data, dict):
        return []
    inner = services_data.get("data") or {}
    return inner.get("services") or []


# ─────────────────────────────────────────────
# Transformation: muvilist
# ─────────────────────────────────────────────

def transform_movie(movie: Dict) -> Dict:
    """
    Map a raw movie record to the sample.json muvilist movie schema.
    Strips genre display colours (fontColor / backgroundColor) not present
    in sample.json. Preserves all other expected movie-level fields.
    """
    raw_genres = movie.get("genres") or []
    genres = [
        {"id": g.get("id"), "name": g.get("name")}
        for g in raw_genres
        if isinstance(g, dict)
    ]

    return {
        "id": movie.get("id"),
        "type": movie.get("type"),
        "image": movie.get("image", ""),
        "title": movie.get("title", ""),
        "genres": genres,
        "runtime": movie.get("runtime"),
        "coverUrl": movie.get("coverUrl", ""),
        "subtitle": movie.get("subtitle", ""),
        "synopsis": movie.get("synopsis", ""),
        "titleTag": movie.get("titleTag", ""),
        "movieLang": movie.get("movieLang", ""),
        "shareLink": movie.get("shareLink", ""),
        "bookingUrl": movie.get("bookingUrl", ""),
        "trailerUrl": movie.get("trailerUrl", ""),
        "releaseDate": movie.get("releaseDate"),
        "descriptionTag": movie.get("descriptionTag", ""),
        "isAdvancedBooking": movie.get("isAdvancedBooking", False),
    }


def transform_muvilist(raw_muvilist: Any) -> Dict:
    """
    Transform the raw muvilist dict (with 'ar' and 'en' lists) into the
    sample.json-compatible shape, applying per-movie transformation.
    """
    if not isinstance(raw_muvilist, dict):
        return {"ar": [], "en": []}

    return {
        "ar": [transform_movie(m) for m in (raw_muvilist.get("ar") or []) if isinstance(m, dict)],
        "en": [transform_movie(m) for m in (raw_muvilist.get("en") or []) if isinstance(m, dict)],
    }


# ─────────────────────────────────────────────
# Transformation: mall_information
# ─────────────────────────────────────────────

def transform_mall_timing(timing_list: Any) -> List[Dict]:
    """
    Keep only the English day name plus start/end times, matching
    the sample.json MallTiming item shape (dayen, end_time, start_time).
    """
    if not isinstance(timing_list, list):
        return []
    result = []
    for entry in timing_list:
        if not isinstance(entry, dict):
            continue
        result.append({
            "dayen": entry.get("dayen", ""),
            "end_time": entry.get("end_time", ""),
            "start_time": entry.get("start_time", ""),
        })
    return result


def transform_mall_contact(raw_contact: Any) -> Dict:
    """
    Keep only the English-language contact keys that appear in sample.json.
    (Email, Phone, Address1En, Address2En)
    """
    if not isinstance(raw_contact, dict):
        return {}
    return {
        "Email": raw_contact.get("Email", ""),
        "Phone": raw_contact.get("Phone", ""),
        "Address1En": raw_contact.get("Address1En", ""),
        "Address2En": raw_contact.get("Address2En", ""),
    }


def transform_mall_information(raw_info: Any) -> Dict:
    """
    Map source mall_information to the sample.json mall_information schema.
    Only English-variant keys and schema-defined keys are retained.
    """
    if not isinstance(raw_info, dict):
        return {}

    raw_social = raw_info.get("Social") or {}
    social = {
        "X": raw_social.get("X", ""),
        "Threads": raw_social.get("Threads", ""),
        "Youtube": raw_social.get("Youtube", ""),
        "Facebook": raw_social.get("Facebook", ""),
        "LinkedIn": raw_social.get("LinkedIn", ""),
        "Instagram": raw_social.get("Instagram", ""),
    }

    raw_images = raw_info.get("MallImagesEn") or {}
    mall_images_en = {
        "Image1": raw_images.get("Image1", ""),
        "Image2": raw_images.get("Image2", ""),
        "Image3": raw_images.get("Image3", ""),
    }

    return {
        "Social": social,
        "region": raw_info.get("region", ""),
        "MallMapEn": raw_info.get("MallMapEn", ""),
        "mallmapid": raw_info.get("mallmapid", ""),
        "MallLogoEn": raw_info.get("MallLogoEn", ""),
        "MallNameEn": raw_info.get("MallNameEn", ""),
        "MallTiming": transform_mall_timing(raw_info.get("MallTiming")),
        "MallContact": transform_mall_contact(raw_info.get("MallContact")),
        "TimingMsgEn": raw_info.get("TimingMsgEn", ""),
        "sr_mallname": raw_info.get("sr_mallname", ""),
        "GoogleMapURL": raw_info.get("GoogleMapURL", ""),
        "MallImagesEn": mall_images_en,
        "MallDescriptionEn": raw_info.get("MallDescriptionEn", ""),
        "GravtyMappingAttribute": raw_info.get("GravtyMappingAttribute", ""),
    }


# ─────────────────────────────────────────────
# Transformation: services
# ─────────────────────────────────────────────

def transform_service_detail_item(item: Dict) -> Dict:
    """
    Transform one item inside a service's 'details' list.
    Keeps: content, type (always if present), button_icon_type (button items only).
    Strips: content_ar and any other Arabic-only keys absent from sample.json.
    """
    if not isinstance(item, dict):
        return {}
    out: Dict = {}
    # button_icon_type is a functional attribute, keep it when present
    if "button_icon_type" in item:
        out["button_icon_type"] = item["button_icon_type"]
    if "content" in item:
        out["content"] = item["content"]
    if "type" in item:
        out["type"] = item["type"]
    return out


def transform_service_details_content_item(item: Dict) -> Dict:
    """
    Transform one item inside service_details.content list.
    Follows sample.json pattern: all types carry content+type;
    the 'time' type additionally carries content_ar (the only case
    where content_ar appears in the sample.json schema).
    """
    if not isinstance(item, dict):
        return {}
    out: Dict = {
        "content": item.get("content", ""),
        "type": item.get("type", ""),
    }
    if item.get("type") == "time" and "content_ar" in item:
        out["content_ar"] = item["content_ar"]
    return out


def transform_service(service: Dict) -> Dict:
    """
    Transform a raw service record to the sample.json service schema.
    Maps: details, id, service_details (content, floor, icon, image,
    mappedin_location, navigation_enable). Strips Arabic-only keys.
    """
    if not isinstance(service, dict):
        return {}

    details = [
        transform_service_detail_item(d)
        for d in (service.get("details") or [])
        if isinstance(d, dict)
    ]

    raw_sd = service.get("service_details") or {}
    sd_content = [
        transform_service_details_content_item(c)
        for c in (raw_sd.get("content") or [])
        if isinstance(c, dict)
    ]

    service_details = {
        "content": sd_content,
        "floor": raw_sd.get("floor", ""),
        "icon": raw_sd.get("icon", ""),
        "image": raw_sd.get("image", ""),
        "mappedin_location": raw_sd.get("mappedin_location", ""),
        "navigation_enable": raw_sd.get("navigation_enable", False),
    }

    return {
        "details": details,
        "id": service.get("id"),
        "service_details": service_details,
    }


# ─────────────────────────────────────────────
# Transformation: engagements
# ─────────────────────────────────────────────

def transform_engagement(eng: Dict) -> Dict:
    """
    Transform a raw engagement record to the sample.json engagement schema.
    Maps only the English-facing fields and structural metadata. Keeps
    images_en as-is (url + is_primary) since that mirrors sample.json images_en.
    """
    if not isinstance(eng, dict):
        return {}

    images_en = [
        {"url": img.get("url", ""), "is_primary": img.get("is_primary", False)}
        for img in (eng.get("images_en") or [])
        if isinstance(img, dict)
    ]

    tags_en = [
        {"tag_id": t.get("tag_id", ""), "tag_text": t.get("tag_text", "")}
        for t in (eng.get("tags_en") or [])
        if isinstance(t, dict)
    ]

    return {
        "engagement_id": eng.get("engagement_id"),
        "brand_id": eng.get("brand_id"),
        "tenant_profile_id": eng.get("tenant_profile_id"),
        "title_en": eng.get("title_en", ""),
        "type": eng.get("type", ""),
        "description_en": eng.get("description_en", ""),
        "terms_conditions_en": eng.get("terms_conditions_en", ""),
        "start_date": eng.get("start_date"),
        "end_date": eng.get("end_date"),
        "publish_date": eng.get("publish_date"),
        "is_exclusive": eng.get("is_exclusive", 0),
        "images_en": images_en,
        "tags_en": tags_en,
        "ext_url": eng.get("ext_url", ""),
        "brand_logo": eng.get("brand_logo", ""),
        "brand_name": eng.get("brand_name", ""),
        "group_name": eng.get("group_name", ""),
    }


# ─────────────────────────────────────────────
# Transformation: brands
# ─────────────────────────────────────────────

def transform_brand(brand: Dict, engagements: List[Dict]) -> Dict:
    """
    Transform a raw brand record to the sample.json brand schema.
    Keeps only English-variant keys and schema-defined metadata.
    Filters available_in_malls to only the entry for property_group_id = 28.
    Attaches the brand's pre-filtered engagements list.
    """
    if not isinstance(brand, dict):
        return {}

    target = normalize_id(TARGET_PROPERTY_GROUP_ID)

    # Filter available_in_malls to only the current mall's entry
    raw_available = brand.get("available_in_malls") or []
    available_in_malls = [
        {
            "mall_id": m.get("mall_id"),
            "property_group_id": m.get("property_group_id"),
            "brand_id": m.get("brand_id"),
            "pms_unit_code": m.get("pms_unit_code") or [],
        }
        for m in raw_available
        if isinstance(m, dict) and normalize_id(m.get("property_group_id")) == target
    ]

    images_en = [
        {"url": img.get("url", ""), "is_primary": img.get("is_primary", False)}
        for img in (brand.get("images_en") or [])
        if isinstance(img, dict)
    ]

    tags_en = [
        {"tag_id": t.get("tag_id", ""), "tag_text": t.get("tag_text", "")}
        for t in (brand.get("tags_en") or [])
        if isinstance(t, dict)
    ]

    return {
        "brand_id": brand.get("brand_id"),
        "anchor_brand": brand.get("anchor_brand", 0),
        "tenant_profile_id": brand.get("tenant_profile_id"),
        "brand_name_en": brand.get("brand_name_en", ""),
        "brand_logo": brand.get("brand_logo", ""),
        "company_name_en": brand.get("company_name_en", ""),
        "category_name": brand.get("category_name", ""),
        "group_name": brand.get("group_name", ""),
        "brand_profile_id": brand.get("brand_profile_id"),
        "store_phone_code": brand.get("store_phone_code", ""),
        "store_phone_number": brand.get("store_phone_number", ""),
        "store_email": brand.get("store_email", ""),
        "store_website": brand.get("store_website", ""),
        "publish_date": brand.get("publish_date"),
        "is_published": brand.get("is_published", False),
        "social_tiktok": brand.get("social_tiktok", ""),
        "social_instagram": brand.get("social_instagram", ""),
        "social_facebook": brand.get("social_facebook", ""),
        "social_threads": brand.get("social_threads", ""),
        "social_twitter": brand.get("social_twitter", ""),
        "social_snapchat": brand.get("social_snapchat", ""),
        "social_youtube": brand.get("social_youtube", ""),
        "description_en": brand.get("description_en", ""),
        "banner_en": brand.get("banner_en", ""),
        "images_en": images_en,
        "tags_en": tags_en,
        "google_rating": brand.get("google_rating"),
        "reviews": brand.get("reviews"),
        "available_in_malls": available_in_malls,
        "pms_unit_codes": brand.get("pms_unit_codes") or [],
        # Engagements are attached here because they are linked via brand_id.
        # sample.json does not show this key, but engagements are brand-scoped
        # data that must be embedded somewhere in the output — brand level is
        # the logical home given the reverse-mapping relationship.
        "engagements": [transform_engagement(e) for e in engagements],
    }


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────

def validate_output(output: Dict, sample: Dict) -> None:
    """
    Check that top-level keys in the output match those in sample.json
    (ignoring the extra 'engagements' key added inside brand objects).
    Prints warnings for any missing or unexpected top-level keys.
    """
    sample_keys = set(sample.keys())
    output_keys = set(output.keys())

    missing = sample_keys - output_keys
    extra = output_keys - sample_keys

    if missing:
        print(f"[VALIDATION] Missing top-level keys vs sample.json: {missing}")
    if extra:
        print(f"[VALIDATION] Extra top-level keys not in sample.json: {extra}")
    if not missing and not extra:
        print("[VALIDATION] Top-level keys match sample.json exactly.")

    # Spot-check nested sections
    if "services" in output and not isinstance(output["services"], list):
        print("[VALIDATION] 'services' is not a list.")
    if "brands" in output and not isinstance(output["brands"], list):
        print("[VALIDATION] 'brands' is not a list.")
    if "muvilist" in output:
        ml = output["muvilist"]
        if not isinstance(ml, dict) or "ar" not in ml or "en" not in ml:
            print("[VALIDATION] 'muvilist' does not have expected 'ar'/'en' structure.")
    if "mall_information" in output and not isinstance(output["mall_information"], dict):
        print("[VALIDATION] 'mall_information' is not a dict.")


# ─────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────

def build_output_mall_28() -> Dict:
    """
    Main orchestration function.
    1. Load all 5 source files.
    2. Extract and filter relevant records for property_group_id = 28.
    3. Transform each section into the sample.json-compatible shape.
    4. Assemble the final output object.
    5. Validate and print a transformation summary.
    Returns the completed output dict.
    """

    # ── Load source files ────────────────────────────────────────────────────

    sample_raw       = load_json(os.path.join(DATA_DIR, "sample.json"))
    mall_movie_raw   = load_json(os.path.join(DATA_DIR, "mall_and_movie.json"))
    services_raw     = load_json(os.path.join(DATA_DIR, "services.json"))
    engagements_raw  = load_json(os.path.join(DATA_DIR, "engagements.json"))
    brands_raw       = load_json(os.path.join(DATA_DIR, "brands.json"))

    sample: Dict = sample_raw if isinstance(sample_raw, dict) else {}

    # ── Extract list payloads from API-envelope wrappers ─────────────────────

    mall_list: List[Dict] = (
        (mall_movie_raw or {}).get("data", {}).get("list") or []
    )
    engagement_list: List[Dict] = (
        (engagements_raw or {}).get("data", {}).get("list") or []
    )
    brand_list: List[Dict] = (
        (brands_raw or {}).get("data", {}).get("list") or []
    )

    # ── Mall data mapping ─────────────────────────────────────────────────────
    # Locate the single mall record for property_group_id = 28 from
    # mall_and_movie.json, which is used for top-level fields and muvilist.

    mall_record = get_mall_record(mall_list, TARGET_PROPERTY_GROUP_ID)
    mall_found = mall_record is not None

    if not mall_found:
        print(f"[WARN] No mall found for property_group_id = {TARGET_PROPERTY_GROUP_ID}.")
        mall_record = {}

    # ── Services mapping ──────────────────────────────────────────────────────
    # services.json is already scoped to property_group_id = 28; extract list.

    raw_services = get_services_for_mall(services_raw or {})
    services = [transform_service(s) for s in raw_services if isinstance(s, dict)]

    # ── Engagements mapping ───────────────────────────────────────────────────
    # Filter engagements whose property_group_ids list includes 28.
    # Then group them by brand_id for O(1) brand-level merging below.

    engagements_for_mall = filter_engagements_for_mall(engagement_list, TARGET_PROPERTY_GROUP_ID)

    engagements_by_brand: Dict[str, List[Dict]] = {}
    for eng in engagements_for_mall:
        bid = normalize_id(eng.get("brand_id"))
        if bid:
            engagements_by_brand.setdefault(bid, []).append(eng)

    # ── Brands mapping ────────────────────────────────────────────────────────
    # Filter brands whose available_in_malls contains property_group_id = 28.
    # Merge each brand's engagements before transforming.

    filtered_brands = filter_brands_for_mall(brand_list, TARGET_PROPERTY_GROUP_ID)
    brands = [
        transform_brand(
            brand=b,
            engagements=engagements_by_brand.get(normalize_id(b.get("brand_id")), []),
        )
        for b in filtered_brands
        if isinstance(b, dict)
    ]

    # ── Final sample.json-shaped output assembly ──────────────────────────────
    # Top-level keys mirror sample.json exactly. mall_information and muvilist
    # are sourced from mall_and_movie.json; services from services.json;
    # brands (with embedded engagements) from brands.json + engagements.json.

    output: Dict = {
        "property_group_id": mall_record.get("property_group_id", TARGET_PROPERTY_GROUP_ID),
        "marketing_name": mall_record.get("marketing_name", ""),
        "city": mall_record.get("city", ""),
        "country": mall_record.get("country", ""),
        "mall_information": transform_mall_information(mall_record.get("mall_information")),
        "image": mall_record.get("image", ""),
        "gps_coordinates": mall_record.get("gps_coordinates", ""),
        "muvilist": transform_muvilist(mall_record.get("muvilist")),
        "services": services,
        "brands": brands,
    }

    # ── Validation ────────────────────────────────────────────────────────────

    validate_output(output, sample)

    # Count total engagements included across all brands
    total_engagements = sum(len(b.get("engagements", [])) for b in brands)

    # ── Transformation summary ────────────────────────────────────────────────

    print("\n─── Transformation Summary ───────────────────────────────")
    print(f"  Mall found          : {'Yes — ' + str(mall_record.get('marketing_name', '')) if mall_found else 'No'}")
    print(f"  Services included   : {len(services)}")
    print(f"  Brands included     : {len(brands)}")
    print(f"  Engagements included: {total_engagements}")
    print(f"  Output path         : {os.path.abspath(OUTPUT_FILE)}")
    print("──────────────────────────────────────────────────────────\n")

    return output


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main() -> None:
    output = build_output_mall_28()

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=4)
        print(f"[OK] Output written → {OUTPUT_FILE}")
    except OSError as exc:
        print(f"[ERROR] Could not write output file: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
