"""
Context builder — assembles the final LLM-ready global context pack.

Orchestrates the full pipeline:
  raw data → canonical normalization → semantic enrichment → playbook assembly → context pack

Also provides runtime topic-block selection for efficient prompt injection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.context.semantic_mall_model import (
    build_mall_overview_block,
    build_mall_profile,
)
from app.models.context_pack import (
    ConciergeGuidelines,
    GlobalContextPack,
    MallProfileBlock,
    TopicBlock,
)
from app.models.playbook import ScenarioPlaybook
from app.models.semantic import SemanticProfile
from app.services.enricher import SemanticEnricher
from app.services.normalizer import MallNormalizer

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class ContextBuilder:
    """Builds and manages global context packs for mall intelligence."""

    def __init__(self, mall_id: str):
        self.mall_id = mall_id
        self._normalizer = MallNormalizer()
        self._enricher = SemanticEnricher()
        self._raw_json: dict[str, Any] = {}
        self._canonical: dict[str, Any] = {}
        self._profiles: list[SemanticProfile] = []
        self._playbooks: list[ScenarioPlaybook] = []
        self._context_pack: GlobalContextPack | None = None
        self._mall_profile_block: MallProfileBlock | None = None

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_canonical(self, path: Path | None = None) -> dict[str, Any]:
        """Load and normalize canonical data from JSON."""
        file_path = path or (DATA_DIR / "canonical" / f"{self.mall_id}.json")
        raw = _load_json(file_path)
        self._raw_json = raw
        self._canonical = self._normalizer.normalize_all(raw)
        self._canonical["mall_profile"] = self._normalizer.normalize_mall_profile(
            raw.get("mall_profile", {})
        )
        logger.info("Loaded canonical data from %s", file_path)
        return self._canonical

    def load_semantic(self, path: Path | None = None) -> list[SemanticProfile]:
        """Load semantic intelligence (rules + hand-curated profiles)."""
        file_path = path or (DATA_DIR / "semantic" / f"{self.mall_id}.json")
        raw = _load_json(file_path)

        self._enricher.load_rules(raw.get("enrichment_rules", []))

        existing_profiles: dict[str, SemanticProfile] = {}
        for p in raw.get("profiles", []):
            profile = SemanticProfile(**p)
            existing_profiles[profile.entity_id] = profile

        self._profiles = self._enricher.enrich_all(self._canonical, existing_profiles)
        logger.info("Loaded %d semantic profiles", len(self._profiles))
        return self._profiles

    def load_playbooks(self, path: Path | None = None) -> list[ScenarioPlaybook]:
        """Load scenario playbooks from JSON."""
        file_path = path or (DATA_DIR / "playbooks" / f"{self.mall_id}.json")
        raw = _load_json(file_path)
        self._playbooks = [ScenarioPlaybook(**p) for p in raw]
        logger.info("Loaded %d playbooks", len(self._playbooks))
        return self._playbooks

    # ------------------------------------------------------------------
    # Context pack assembly
    # ------------------------------------------------------------------

    def build_context_pack(self) -> GlobalContextPack:
        """Assemble the complete global context pack from all layers."""
        mall_profile = self._canonical.get("mall_profile")
        if not mall_profile:
            raise ValueError("Cannot build context pack without mall profile")

        profile_data = mall_profile.model_dump()

        # Build the structured mall profile block from raw trusted data
        raw_profile_dict = build_mall_profile(self._raw_json)
        self._mall_profile_block = (
            MallProfileBlock(**raw_profile_dict) if raw_profile_dict else None
        )

        topic_blocks = self._build_topic_blocks()

        # Inject mall_overview topic block built from the profile
        if self._mall_profile_block:
            overview_data = build_mall_overview_block(raw_profile_dict)
            topic_blocks["mall_overview"] = TopicBlock(
                topic="mall_overview",
                summary="High-level mall overview, hours, highlights, and facilities",
                entities=[],
                semantic_highlights=overview_data.get("highlights", []),
                concierge_tips=overview_data.get("summary_lines", []),
            )

        pack = GlobalContextPack(
            mall_id=self.mall_id,
            mall_profile=self._mall_profile_block,
            mall_profile_summary=self._build_profile_summary(profile_data),
            operational_context=self._build_operational_context(profile_data),
            topic_blocks=topic_blocks,
            events_and_offers=self._build_events_offers(),
            playbooks=[self._playbook_summary(pb) for pb in self._playbooks],
            contextual_reasoning_hints=self._build_reasoning_hints(),
            concierge_guidelines=self._build_guidelines(),
        )

        self._context_pack = pack
        logger.info("Built global context pack for %s", self.mall_id)
        return pack

    def build_full_pipeline(self) -> GlobalContextPack:
        """Execute the full pipeline: load → normalize → enrich → build."""
        self.load_canonical()
        self.load_semantic()
        self.load_playbooks()
        return self.build_context_pack()

    def save_context_pack(self, path: Path | None = None) -> Path:
        """Serialize the context pack to JSON."""
        if not self._context_pack:
            raise ValueError("No context pack built yet — call build_context_pack first")

        file_path = path or (DATA_DIR / "context_packs" / f"{self.mall_id}_context.json")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            self._context_pack.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("Saved context pack to %s", file_path)
        return file_path

    # ------------------------------------------------------------------
    # Runtime topic block selection
    # ------------------------------------------------------------------

    @property
    def mall_profile_block(self) -> MallProfileBlock | None:
        """Return the structured mall profile block, if built."""
        return self._mall_profile_block

    def get_topic_block(self, topic: str) -> TopicBlock | None:
        """Retrieve a single topic block by key for runtime injection."""
        if not self._context_pack:
            return None
        return self._context_pack.topic_blocks.get(topic)

    def get_blocks_for_intent(self, intent: str) -> list[TopicBlock]:
        """Select topic blocks relevant to a detected intent."""
        if not self._context_pack:
            return []

        intent_topic_map: dict[str, list[str]] = {
            "dining": ["dining"],
            "food": ["dining"],
            "restaurant": ["dining"],
            "eat": ["dining"],
            "gift": ["gift"],
            "present": ["gift"],
            "shopping": ["gift"],
            "perfume": ["gift"],
            "fragrance": ["gift"],
            "oud": ["gift"],
            "jewelry": ["gift"],
            "jewellery": ["gift"],
            "accessories": ["gift"],
            "family": ["family", "dining", "mall_overview"],
            "kids": ["family"],
            "children": ["family"],
            "movie": ["movie", "dining"],
            "cinema": ["movie"],
            "film": ["movie"],
            "quick": ["quick_visit", "dining"],
            "hurry": ["quick_visit"],
            "service": ["services"],
            "help": ["services"],
            "prayer": ["services"],
            "parking": ["services"],
            "date": ["dining", "movie", "gift"],
            "romantic": ["dining", "gift"],
            "anniversary": ["dining", "gift", "movie"],
            "mall": ["mall_overview", "services"],
            "about": ["mall_overview"],
            "overview": ["mall_overview"],
            "what": ["mall_overview"],
            "tell": ["mall_overview"],
            "hours": ["mall_overview", "services"],
            "facilities": ["mall_overview", "services"],
            "amenities": ["mall_overview", "services"],
        }

        selected_topics: set[str] = set()
        intent_lower = intent.lower()
        for keyword, topics in intent_topic_map.items():
            if keyword in intent_lower:
                selected_topics.update(topics)

        if "exploration" in intent_lower or "open_exploration" in intent_lower:
            selected_topics = {"mall_overview", "dining", "gift", "movie", "family", "services"}

        if not selected_topics:
            selected_topics = {"dining", "gift", "services"}

        blocks = []
        for topic in selected_topics:
            block = self._context_pack.topic_blocks.get(topic)
            if block:
                blocks.append(block)
        return blocks

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_profile_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        zones_summary = {}
        for z in profile.get("zones", []):
            label = f"{z['name']} ({z.get('floor', '')})"
            zones_summary[label] = ", ".join(z.get("anchor_tenants", []))

        return {
            "name": profile.get("name", ""),
            "city": f"{profile.get('city', '')}, {profile.get('country', '')}",
            "address": profile.get("address", ""),
            "floors": profile.get("floors", []),
            "key_zones": zones_summary,
            "total_stores": profile.get("total_stores", 0),
        }

    def _build_operational_context(self, profile: dict[str, Any]) -> dict[str, Any]:
        hours = profile.get("operating_hours", {})
        parking = profile.get("parking", {})
        return {
            "hours": hours,
            "parking": {
                "capacity": parking.get("total_capacity", 0),
                "rate": parking.get("hourly_rate", ""),
                "valet": parking.get("valet_available", False),
                "ev_charging": parking.get("ev_charging", False),
            },
            "accessibility": profile.get("accessibility_features", []),
            "amenities": profile.get("amenities", []),
        }

    def _build_topic_blocks(self) -> dict[str, TopicBlock]:
        profiles_by_id = {p.entity_id: p for p in self._profiles}
        blocks: dict[str, TopicBlock] = {}

        dining_entities = self._canonical.get("dining", [])
        if dining_entities:
            blocks["dining"] = self._build_entity_block(
                "dining",
                "Dining options at the mall",
                dining_entities,
                profiles_by_id,
            )

        stores = self._canonical.get("stores", [])
        gift_stores = [
            s for s in stores
            if profiles_by_id.get(s.entity_id)
            and any(t in profiles_by_id[s.entity_id].gift_fit for t in profiles_by_id[s.entity_id].gift_fit)
        ]
        if gift_stores:
            blocks["gift"] = self._build_entity_block(
                "gift_shopping",
                "Gift-friendly stores",
                gift_stores,
                profiles_by_id,
            )

        family_entities = [
            e for entities in [stores, dining_entities]
            for e in entities
            if profiles_by_id.get(getattr(e, "entity_id", ""))
            and "family_friendly" in profiles_by_id[getattr(e, "entity_id", "")].audience_fit
        ]
        if family_entities:
            blocks["family"] = self._build_entity_block(
                "family_and_kids",
                "Family and kid-friendly options",
                family_entities,
                profiles_by_id,
            )

        cinemas = self._canonical.get("cinemas", [])
        movies = self._canonical.get("movies", [])
        movie_entities = cinemas + movies
        if movie_entities:
            blocks["movie"] = self._build_entity_block(
                "cinema_and_movies",
                "Cinema and currently showing movies",
                movie_entities,
                profiles_by_id,
            )

        blocks["quick_visit"] = TopicBlock(
            topic="quick_visit",
            summary="Fast options for visitors with limited time",
            concierge_tips=[
                "Direct quick visitors to Ground floor or Second floor Food Court",
                "Mention exact floor and zone for fast navigation",
            ],
        )

        services = self._canonical.get("services", [])
        if services:
            blocks["services"] = self._build_entity_block(
                "services_and_facilities",
                "Mall services and visitor facilities",
                services,
                profiles_by_id,
            )

        return blocks

    def _build_entity_block(
        self,
        topic: str,
        summary: str,
        entities: list[Any],
        profiles_by_id: dict[str, SemanticProfile],
    ) -> TopicBlock:
        entity_dicts = []
        semantic_highlights = []
        tips = []

        for entity in entities:
            entity_data = entity.model_dump()
            entity_id = entity_data.get("entity_id", "")
            name = entity_data.get("name") or entity_data.get("title", "")
            entity_dicts.append({
                "entity_id": entity_id,
                "name": name,
                "type": entity_data.get("entity_type", ""),
            })

            profile = profiles_by_id.get(entity_id)
            if profile and profile.concierge_notes:
                tips.append(f"{name}: {profile.concierge_notes[:120]}")
            if profile and profile.when_to_recommend:
                semantic_highlights.append(
                    f"{name} — best for: {', '.join(profile.when_to_recommend[:2])}"
                )

        return TopicBlock(
            topic=topic,
            summary=summary,
            entities=entity_dicts,
            semantic_highlights=semantic_highlights[:8],
            concierge_tips=tips[:6],
        )

    def _build_events_offers(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        entity_id_to_name: dict[str, str] = {}
        for section in ("stores", "dining", "cinemas", "services"):
            for entity in self._canonical.get(section, []):
                eid = getattr(entity, "entity_id", "")
                name = getattr(entity, "name", "")
                if eid and name:
                    entity_id_to_name[eid] = name

        for event in self._canonical.get("events", []):
            items.append({
                "type": "event",
                "title": event.title,
                "dates": f"{event.start_date} to {event.end_date}",
                "description": event.description,
            })
        for offer in self._canonical.get("offers", []):
            tenant_names = [
                entity_id_to_name[tid]
                for tid in getattr(offer, "tenant_entity_ids", [])
                if tid in entity_id_to_name
            ]
            items.append({
                "type": "offer",
                "title": offer.title,
                "valid": f"{offer.valid_from} to {offer.valid_until}",
                "discount": offer.discount_value,
                "description": offer.description,
                "stores": tenant_names,
                "terms": getattr(offer, "terms", ""),
            })
        return items

    @staticmethod
    def _playbook_summary(pb: ScenarioPlaybook) -> dict[str, Any]:
        return {
            "id": pb.playbook_id,
            "scenario": pb.scenario,
            "trigger": ", ".join(pb.trigger_conditions[:3]),
            "preferred_tags": pb.preferred_semantic_tags[:5],
            "shortlist_size": pb.shortlist_size_hint,
        }

    @staticmethod
    def _build_reasoning_hints() -> list[str]:
        return [
            "When a user mentions a specific floor, prioritize entities on that floor",
            "Proactively mention prayer rooms during prayer times",
            "Consider meal time context when recommending dining",
            "For gift recommendations, ask about the recipient if not specified",
            "Mention current promotions and offers when relevant",
            "During Ramadan, clarify adjusted operating hours proactively",
            "If unsure about something, say so and suggest the Information Desk",
        ]

    def _build_guidelines(self) -> ConciergeGuidelines:
        mall_profile = self._canonical.get("mall_profile")
        mall_name = getattr(mall_profile, "name", None) or self.mall_id.replace("_", " ").title()
        return ConciergeGuidelines(
            persona_name=f"{mall_name} Concierge",
            tone="friendly, knowledgeable, helpful, professional",
            language="English (with Arabic cultural awareness)",
            response_style="Answer first, then elaborate. Be concise but thorough.",
            grounding_rules=[
                "Always base answers on verified mall information",
                "Never invent store names, locations, or hours",
                "If unsure, say so and suggest the Information Desk",
                "Respect cultural context and local customs",
            ],
            cultural_notes=[
                "Saudi Arabia observes prayer times — be sensitive about timing",
                "All dining is halal",
                "Friday opening times are later due to Friday prayer",
                "Ramadan hours are significantly different",
            ],
            do_not=[
                "Do not make assumptions about visitor gender or family structure",
                "Do not share personal opinions about brands",
                "Do not provide outdated prices or hours",
            ],
        )


def _load_json(path: Path) -> Any:
    if not path.exists():
        logger.warning("Data file not found: %s", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
