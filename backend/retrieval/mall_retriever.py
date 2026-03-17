"""
Structured mall retrieval layer.

Provides deterministic, LLM-safe access to mall tenant data.  Every function
returns only names and metadata that exist in the canonical JSON — the LLM
must never invent tenant names.

Usage:
    retriever = MallRetriever.from_default()
    result = retriever.get_tenants_by_category("jewelry")
    # {"category": "jewelry", "tenants": ["L'azurde", "Pandora"]}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "canonical"
_DEFAULT_MALL_ID = "al_nakheel_plaza_28"

_DINING_STYLES_CAFE = frozenset({"cafe"})
_DINING_STYLES_DESSERT = frozenset({"dessert"})

_LUXURY_PRICE_TIERS = frozenset({"premium", "luxury"})

_FAMILY_ENTERTAINMENT_TAGS = frozenset({
    "kids", "children", "family", "kids_entertainment",
    "playful", "baby",
})


class MallRetriever:
    """
    Structured retrieval over a single mall's canonical data.

    All lookups are performed against the pre-loaded JSON; results are
    guaranteed to contain only real tenant/entity names.
    """

    def __init__(self, raw_data: dict[str, Any]) -> None:
        self._stores: list[dict[str, Any]] = raw_data.get("stores", [])
        self._dining: list[dict[str, Any]] = raw_data.get("dining", [])
        self._cinemas: list[dict[str, Any]] = raw_data.get("cinemas", [])
        self._services: list[dict[str, Any]] = raw_data.get("services", [])
        self._events: list[dict[str, Any]] = raw_data.get("events", [])
        self._offers: list[dict[str, Any]] = raw_data.get("offers", [])
        self._movies: list[dict[str, Any]] = raw_data.get("movies", [])

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path) -> MallRetriever:
        """Load from an explicit JSON file path."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(raw)

    @classmethod
    def from_default(cls, mall_id: str = _DEFAULT_MALL_ID) -> MallRetriever:
        """Load the default canonical JSON for *mall_id*."""
        path = _DATA_DIR / f"{mall_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Canonical data not found: {path}")
        return cls.from_file(path)

    # ------------------------------------------------------------------
    # Core retrieval API
    # ------------------------------------------------------------------

    def get_tenants_by_category(self, category: str) -> dict[str, Any]:
        """
        Return all tenants whose ``category``, ``subcategory``, ``cuisine_type``,
        ``dining_style``, or ``tags`` match *category* (case-insensitive).

        Returns a structured dict — never free-text.
        """
        cat_lower = category.strip().lower()
        matched: list[dict[str, Any]] = []

        for entity in self._all_entities():
            if self._matches_category(entity, cat_lower):
                matched.append(self._tenant_summary(entity))

        return {
            "category": category,
            "tenants": [m["name"] for m in matched],
            "details": matched,
        }

    def get_dining_options(self) -> dict[str, Any]:
        """Return all dining outlets grouped by dining style."""
        by_style: dict[str, list[str]] = {}
        details: list[dict[str, Any]] = []

        for d in self._dining:
            style = d.get("dining_style", "other")
            name = d.get("name", "")
            by_style.setdefault(style, []).append(name)
            details.append(self._tenant_summary(d))

        return {
            "category": "dining",
            "tenants": [d.get("name", "") for d in self._dining],
            "by_style": by_style,
            "details": details,
        }

    def get_family_entertainment(self) -> dict[str, Any]:
        """
        Return entertainment and activity options suitable for families/kids.

        Scans cinemas, movies, dining with kids menus, stores with
        kid-focused tags, and entertainment-zone entities.
        """
        matched: list[dict[str, Any]] = []

        for cinema in self._cinemas:
            matched.append(self._tenant_summary(cinema))

        for movie in self._movies:
            audience = {a.lower() for a in movie.get("audience_fit", [])}
            if audience & {"kids", "families", "young_children"}:
                matched.append({
                    "name": movie.get("title", ""),
                    "entity_id": movie.get("entity_id", ""),
                    "entity_type": "movie",
                    "floor": "",
                    "zone": "",
                })

        for d in self._dining:
            if d.get("has_kids_menu"):
                matched.append(self._tenant_summary(d))

        for s in self._stores:
            tags = {t.lower() for t in s.get("tags", [])}
            cat = s.get("category", "").lower()
            if tags & _FAMILY_ENTERTAINMENT_TAGS or cat == "kids":
                matched.append(self._tenant_summary(s))

        return {
            "category": "family_entertainment",
            "tenants": _unique_names(matched),
            "details": matched,
        }

    def get_cafes(self) -> dict[str, Any]:
        """Return dining outlets whose ``dining_style`` is ``cafe``."""
        matched: list[dict[str, Any]] = []

        for d in self._dining:
            style = d.get("dining_style", "")
            tags = {t.lower() for t in d.get("tags", [])}
            if style in _DINING_STYLES_CAFE or "coffee" in tags or "cafe" in tags:
                matched.append(self._tenant_summary(d))

        return {
            "category": "cafe",
            "tenants": [m["name"] for m in matched],
            "details": matched,
        }

    def get_luxury_shops(self) -> dict[str, Any]:
        """Return stores with ``price_range`` in {premium, luxury}."""
        matched: list[dict[str, Any]] = []

        for s in self._stores:
            if s.get("price_range", "") in _LUXURY_PRICE_TIERS:
                matched.append(self._tenant_summary(s))

        return {
            "category": "luxury",
            "tenants": [m["name"] for m in matched],
            "details": matched,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _all_entities(self) -> list[dict[str, Any]]:
        return (
            self._stores
            + self._dining
            + self._cinemas
            + self._services
            + self._events
            + self._offers
            + self._movies
        )

    @staticmethod
    def _matches_category(entity: dict[str, Any], cat_lower: str) -> bool:
        """Check whether *entity* belongs to *cat_lower* by any field."""
        fields_to_check = [
            entity.get("category", ""),
            entity.get("subcategory", ""),
            entity.get("cuisine_type", ""),
            entity.get("dining_style", ""),
            entity.get("entity_type", ""),
        ]
        for field_val in fields_to_check:
            if cat_lower in field_val.lower():
                return True

        tags = entity.get("tags", [])
        for tag in tags:
            if cat_lower in tag.lower():
                return True

        return False

    @staticmethod
    def _tenant_summary(entity: dict[str, Any]) -> dict[str, Any]:
        """Extract a minimal structured summary from a raw entity dict."""
        name = entity.get("name") or entity.get("title", "")
        loc = entity.get("location", {})
        return {
            "name": name,
            "entity_id": entity.get("entity_id", ""),
            "entity_type": entity.get("entity_type", ""),
            "floor": loc.get("floor", "") if isinstance(loc, dict) else "",
            "zone": loc.get("zone", "") if isinstance(loc, dict) else "",
        }


def _unique_names(items: list[dict[str, Any]]) -> list[str]:
    """Deduplicate names while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        name = item.get("name", "")
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result
