"""
Mall-level models and shared types.

Defines the canonical mall profile, structural entities (zones, landmarks,
facilities, parking, loyalty), and shared types used across all entity models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared types — used across mall.py and tenant.py
# ---------------------------------------------------------------------------


class OperatingSchedule(BaseModel):
    """Operating hours for any mall entity."""

    weekday: str = ""
    friday: str = ""
    saturday: str = ""
    ramadan: str = ""
    notes: str = ""


class ContactInfo(BaseModel):
    """Contact information for any entity."""

    phone: str = ""
    email: str = ""
    website: str = ""
    social_media: dict[str, str] = Field(default_factory=dict)


class LocationRef(BaseModel):
    """Physical location within the mall."""

    floor: str
    zone: str
    unit_number: str = ""
    nearby_landmarks: list[str] = Field(default_factory=list)
    directions_hint: str = ""


# ---------------------------------------------------------------------------
# Mall structural entities
# ---------------------------------------------------------------------------


class ZoneInfo(BaseModel):
    """A named zone or wing within the mall."""

    zone_id: str
    name: str
    name_ar: str = ""
    floor: str
    description: str = ""
    anchor_tenants: list[str] = Field(default_factory=list)
    category_focus: list[str] = Field(default_factory=list)


class LandmarkInfo(BaseModel):
    """A navigation anchor point within the mall."""

    landmark_id: str
    name: str
    landmark_type: Literal[
        "entrance",
        "elevator",
        "escalator",
        "fountain",
        "atrium",
        "information_desk",
        "parking_entrance",
        "prayer_room",
        "restroom",
        "atm",
        "other",
    ] = "other"
    location: LocationRef
    description: str = ""


class FacilityInfo(BaseModel):
    """A mall-provided facility or amenity."""

    facility_id: str
    name: str
    facility_type: str
    location: LocationRef
    operating_hours: OperatingSchedule = Field(default_factory=OperatingSchedule)
    description: str = ""
    accessibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ParkingInfo(BaseModel):
    """Parking details for the mall."""

    total_capacity: int = 0
    levels: list[str] = Field(default_factory=list)
    ev_charging: bool = False
    valet_available: bool = False
    hourly_rate: str = ""
    notes: str = ""


class LoyaltyProgram(BaseModel):
    """Mall loyalty program details."""

    program_name: str
    description: str = ""
    tiers: list[dict[str, str]] = Field(default_factory=list)
    earn_rules: str = ""
    redemption_options: list[str] = Field(default_factory=list)
    participating_stores_hint: str = ""
    signup_locations: list[str] = Field(default_factory=list)
    app_available: bool = False


# ---------------------------------------------------------------------------
# Mall profile (top-level identity)
# ---------------------------------------------------------------------------


class MallProfile(BaseModel):
    """Top-level canonical mall identity and configuration."""

    mall_id: str
    name: str
    name_ar: str = ""
    city: str = ""
    country: str = "Saudi Arabia"
    address: str = ""
    coordinates: dict[str, float] = Field(default_factory=dict)
    floors: list[str] = Field(default_factory=list)
    zones: list[ZoneInfo] = Field(default_factory=list)
    landmarks: list[LandmarkInfo] = Field(default_factory=list)
    facilities: list[FacilityInfo] = Field(default_factory=list)
    parking: ParkingInfo = Field(default_factory=ParkingInfo)
    operating_hours: OperatingSchedule = Field(default_factory=OperatingSchedule)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    amenities: list[str] = Field(default_factory=list)
    accessibility_features: list[str] = Field(default_factory=list)
    loyalty: LoyaltyProgram | None = None
    year_opened: int | None = None
    total_stores: int = 0
    metadata: dict = Field(default_factory=dict)
