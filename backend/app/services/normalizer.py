"""
Canonical normalizer — transforms raw mall feeds into canonical entities.

Accepts heterogeneous upstream data (API responses, CSV exports, JSON dumps)
and produces validated, normalized Pydantic models ready for semantic enrichment.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.mall import (
    ContactInfo,
    FacilityInfo,
    LandmarkInfo,
    LocationRef,
    LoyaltyProgram,
    MallProfile,
    OperatingSchedule,
    ParkingInfo,
    ZoneInfo,
)
from app.models.tenant import (
    Cinema,
    DiningOutlet,
    Event,
    Movie,
    Offer,
    ScreenInfo,
    ServicePoint,
    Store,
)

logger = logging.getLogger(__name__)


def _schedule(raw: dict[str, Any]) -> OperatingSchedule:
    return OperatingSchedule(
        weekday=raw.get("weekday", ""),
        friday=raw.get("friday", ""),
        saturday=raw.get("saturday", ""),
        ramadan=raw.get("ramadan", ""),
        notes=raw.get("notes", ""),
    )


def _contact(raw: dict[str, Any]) -> ContactInfo:
    return ContactInfo(
        phone=raw.get("phone", ""),
        email=raw.get("email", ""),
        website=raw.get("website", ""),
        social_media=raw.get("social_media", {}),
    )


def _location(raw: dict[str, Any]) -> LocationRef:
    return LocationRef(
        floor=raw.get("floor", ""),
        zone=raw.get("zone", ""),
        unit_number=raw.get("unit_number", ""),
        nearby_landmarks=raw.get("nearby_landmarks", []),
        directions_hint=raw.get("directions_hint", ""),
    )


class MallNormalizer:
    """Normalizes raw mall data into canonical Pydantic models."""

    def normalize_mall_profile(self, raw: dict[str, Any]) -> MallProfile:
        zones = [
            ZoneInfo(**z) for z in raw.get("zones", [])
        ]
        landmarks = [
            LandmarkInfo(
                landmark_id=lm["landmark_id"],
                name=lm["name"],
                landmark_type=lm.get("landmark_type", "other"),
                location=_location(lm.get("location", {})),
                description=lm.get("description", ""),
            )
            for lm in raw.get("landmarks", [])
        ]
        facilities = [
            FacilityInfo(
                facility_id=f["facility_id"],
                name=f["name"],
                facility_type=f.get("facility_type", ""),
                location=_location(f.get("location", {})),
                operating_hours=_schedule(f.get("operating_hours", {})),
                description=f.get("description", ""),
                accessibility=f.get("accessibility", []),
                tags=f.get("tags", []),
            )
            for f in raw.get("facilities", [])
        ]
        parking = ParkingInfo(**raw.get("parking", {}))
        loyalty_raw = raw.get("loyalty")
        loyalty = LoyaltyProgram(**loyalty_raw) if loyalty_raw else None

        return MallProfile(
            mall_id=raw["mall_id"],
            name=raw["name"],
            name_ar=raw.get("name_ar", ""),
            city=raw.get("city", ""),
            country=raw.get("country", "Saudi Arabia"),
            address=raw.get("address", ""),
            coordinates=raw.get("coordinates", {}),
            floors=raw.get("floors", []),
            zones=zones,
            landmarks=landmarks,
            facilities=facilities,
            parking=parking,
            operating_hours=_schedule(raw.get("operating_hours", {})),
            contact=_contact(raw.get("contact", {})),
            amenities=raw.get("amenities", []),
            accessibility_features=raw.get("accessibility_features", []),
            loyalty=loyalty,
            year_opened=raw.get("year_opened"),
            total_stores=raw.get("total_stores", 0),
            metadata=raw.get("metadata", {}),
        )

    def normalize_store(self, raw: dict[str, Any]) -> Store:
        return Store(
            entity_id=raw["entity_id"],
            name=raw["name"],
            name_ar=raw.get("name_ar", ""),
            brand=raw.get("brand", ""),
            category=raw.get("category", ""),
            subcategory=raw.get("subcategory", ""),
            description=raw.get("description", ""),
            location=_location(raw.get("location", {})),
            operating_hours=_schedule(raw.get("operating_hours", {})),
            contact=_contact(raw.get("contact", {})),
            price_range=raw.get("price_range", "mid_range"),
            target_audience=raw.get("target_audience", []),
            features=raw.get("features", []),
            accepts_loyalty=raw.get("accepts_loyalty", False),
            tags=raw.get("tags", []),
        )

    def normalize_dining(self, raw: dict[str, Any]) -> DiningOutlet:
        return DiningOutlet(
            entity_id=raw["entity_id"],
            name=raw["name"],
            name_ar=raw.get("name_ar", ""),
            brand=raw.get("brand", ""),
            cuisine_type=raw.get("cuisine_type", ""),
            dining_style=raw.get("dining_style", "casual_dining"),
            description=raw.get("description", ""),
            location=_location(raw.get("location", {})),
            operating_hours=_schedule(raw.get("operating_hours", {})),
            contact=_contact(raw.get("contact", {})),
            price_range=raw.get("price_range", "mid_range"),
            has_kids_menu=raw.get("has_kids_menu", False),
            has_outdoor_seating=raw.get("has_outdoor_seating", False),
            has_private_dining=raw.get("has_private_dining", False),
            halal_certified=raw.get("halal_certified", True),
            average_meal_time_minutes=raw.get("average_meal_time_minutes", 30),
            reservations_accepted=raw.get("reservations_accepted", False),
            delivery_available=raw.get("delivery_available", False),
            target_audience=raw.get("target_audience", []),
            features=raw.get("features", []),
            accepts_loyalty=raw.get("accepts_loyalty", False),
            tags=raw.get("tags", []),
        )

    def normalize_cinema(self, raw: dict[str, Any]) -> Cinema:
        screens = [ScreenInfo(**s) for s in raw.get("screens", [])]
        return Cinema(
            entity_id=raw["entity_id"],
            name=raw["name"],
            name_ar=raw.get("name_ar", ""),
            brand=raw.get("brand", ""),
            description=raw.get("description", ""),
            location=_location(raw.get("location", {})),
            operating_hours=_schedule(raw.get("operating_hours", {})),
            contact=_contact(raw.get("contact", {})),
            screens=screens,
            formats_available=raw.get("formats_available", []),
            has_vip_lounge=raw.get("has_vip_lounge", False),
            snack_bar=raw.get("snack_bar", True),
            price_range=raw.get("price_range", "mid_range"),
            accepts_loyalty=raw.get("accepts_loyalty", False),
            tags=raw.get("tags", []),
        )

    def normalize_movie(self, raw: dict[str, Any]) -> Movie:
        return Movie(**raw)

    def normalize_service(self, raw: dict[str, Any]) -> ServicePoint:
        return ServicePoint(
            entity_id=raw["entity_id"],
            name=raw["name"],
            name_ar=raw.get("name_ar", ""),
            service_category=raw.get("service_category", ""),
            description=raw.get("description", ""),
            location=_location(raw.get("location", {})),
            operating_hours=_schedule(raw.get("operating_hours", {})),
            contact=_contact(raw.get("contact", {})),
            is_free=raw.get("is_free", True),
            pricing_notes=raw.get("pricing_notes", ""),
            tags=raw.get("tags", []),
        )

    def normalize_event(self, raw: dict[str, Any]) -> Event:
        loc_raw = raw.get("location")
        location = _location(loc_raw) if loc_raw else None
        return Event(
            entity_id=raw["entity_id"],
            title=raw["title"],
            title_ar=raw.get("title_ar", ""),
            event_type=raw.get("event_type", ""),
            description=raw.get("description", ""),
            location=location,
            start_date=raw.get("start_date", ""),
            end_date=raw.get("end_date", ""),
            recurring=raw.get("recurring", False),
            schedule_notes=raw.get("schedule_notes", ""),
            target_audience=raw.get("target_audience", []),
            is_free=raw.get("is_free", True),
            registration_required=raw.get("registration_required", False),
            tags=raw.get("tags", []),
        )

    def normalize_offer(self, raw: dict[str, Any]) -> Offer:
        return Offer(**raw)

    def normalize_all(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Normalize a complete raw mall data payload into canonical entities."""
        result: dict[str, Any] = {}

        raw_profile = raw_data.get("mall_profile")
        if raw_profile:
            result["mall_profile"] = self.normalize_mall_profile(raw_profile)

        result["stores"] = [
            self.normalize_store(s) for s in raw_data.get("stores", [])
        ]
        result["dining"] = [
            self.normalize_dining(d) for d in raw_data.get("dining", [])
        ]
        result["cinemas"] = [
            self.normalize_cinema(c) for c in raw_data.get("cinemas", [])
        ]
        result["movies"] = [
            self.normalize_movie(m) for m in raw_data.get("movies", [])
        ]
        result["services"] = [
            self.normalize_service(s) for s in raw_data.get("services", [])
        ]
        result["events"] = [
            self.normalize_event(e) for e in raw_data.get("events", [])
        ]
        result["offers"] = [
            self.normalize_offer(o) for o in raw_data.get("offers", [])
        ]

        total = sum(len(v) for v in result.values() if isinstance(v, list))
        logger.info("Normalized %d entities for mall %s", total, raw_data.get("mall_profile", {}).get("mall_id", "unknown"))
        return result
