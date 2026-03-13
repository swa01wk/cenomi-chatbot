"""
Semantic enricher — applies enrichment rules to canonical entities.

Takes canonical entities and produces SemanticProfile objects by
matching entity attributes against enrichment rules and applying
tags, audience fit, vibe descriptors, and priority scores.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.models.semantic import EnrichmentRule, SemanticProfile

logger = logging.getLogger(__name__)


def _get_entity_attrs(entity: BaseModel) -> dict[str, Any]:
    """Extract matchable attributes from any canonical entity."""
    data = entity.model_dump()
    attrs: dict[str, Any] = {}

    for key in ("category", "subcategory", "price_range", "dining_style", "entity_type"):
        if key in data:
            attrs[key] = data[key]

    location = data.get("location")
    if isinstance(location, dict):
        attrs["zone"] = location.get("zone", "")
        attrs["floor"] = location.get("floor", "")

    for key in ("has_kids_menu", "has_private_dining", "has_outdoor_seating"):
        if key in data:
            attrs[key] = data[key]

    attrs["tags"] = data.get("tags", [])
    return attrs


def _matches(attrs: dict[str, Any], conditions: dict[str, list[str]]) -> bool:
    """Check if entity attributes satisfy all match conditions."""
    for field, required_values in conditions.items():
        entity_val = attrs.get(field)
        if entity_val is None:
            return False
        if isinstance(entity_val, list):
            if not any(v in entity_val for v in required_values):
                return False
        elif isinstance(entity_val, str):
            if entity_val not in required_values:
                return False
        elif isinstance(entity_val, bool):
            if str(entity_val).lower() not in [v.lower() for v in required_values]:
                return False
    return True


class SemanticEnricher:
    """Enriches canonical entities with semantic intelligence."""

    def __init__(self, rules: list[EnrichmentRule] | None = None):
        self._rules = rules or []

    def load_rules(self, rules_data: list[dict[str, Any]]) -> None:
        self._rules = [EnrichmentRule(**r) for r in rules_data]
        logger.info("Loaded %d enrichment rules", len(self._rules))

    def enrich_entity(
        self,
        entity: BaseModel,
        existing_profile: SemanticProfile | None = None,
    ) -> SemanticProfile:
        """
        Apply enrichment rules to a single canonical entity.

        If an existing profile is provided, it is used as the base
        and rule-derived tags are merged in.
        """
        data = entity.model_dump()
        entity_id = data.get("entity_id", "")
        entity_type = data.get("entity_type", "")
        attrs = _get_entity_attrs(entity)

        if existing_profile:
            profile = existing_profile.model_copy()
        else:
            profile = SemanticProfile(
                entity_id=entity_id,
                entity_type=entity_type,
                price_band=attrs.get("price_range", "mid_range"),
            )

        for rule in self._rules:
            if not _matches(attrs, rule.match_conditions):
                continue

            profile.semantic_tags = _merge_unique(profile.semantic_tags, rule.apply_tags)
            profile.audience_fit = _merge_unique(profile.audience_fit, rule.apply_audience)
            profile.vibe = _merge_unique(profile.vibe, rule.apply_vibe)
            profile.outing_fit = _merge_unique(profile.outing_fit, rule.apply_outing_fit)
            profile.gift_fit = _merge_unique(profile.gift_fit, rule.apply_gift_fit)
            profile.meal_fit = _merge_unique(profile.meal_fit, rule.apply_meal_fit)

            for scenario, boost in rule.priority_boost.items():
                current = profile.priority_scores.get(scenario, 0.0)
                profile.priority_scores[scenario] = min(1.0, max(current, boost))

        return profile

    def enrich_all(
        self,
        entities: dict[str, list[BaseModel]],
        existing_profiles: dict[str, SemanticProfile] | None = None,
    ) -> list[SemanticProfile]:
        """
        Enrich all canonical entities across all entity types.

        Returns a flat list of SemanticProfile objects.
        """
        existing = existing_profiles or {}
        profiles: list[SemanticProfile] = []

        for entity_type, entity_list in entities.items():
            if entity_type == "mall_profile":
                continue
            for entity in entity_list:
                entity_id = getattr(entity, "entity_id", None)
                if not entity_id:
                    continue
                existing_prof = existing.get(entity_id)
                profile = self.enrich_entity(entity, existing_prof)
                profiles.append(profile)

        logger.info("Enriched %d entities with semantic profiles", len(profiles))
        return profiles


def _merge_unique(base: list[str], additions: list[str]) -> list[str]:
    """Merge two lists preserving order and uniqueness."""
    seen = set(base)
    result = list(base)
    for item in additions:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
