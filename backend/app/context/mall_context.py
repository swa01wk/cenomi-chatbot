"""
Mall context loader — builds the runtime context injected into every turn.

Responsible for:
- Loading the global context pack (canonical + semantic + playbooks)
- Providing topic-block selection for efficient prompt injection
- Exposing entity lookups for the concierge pipeline

This is the bridge between static data and the live concierge state.

Redis Tier 2 caching:
  serialize()         — pack raw_json + enriched profiles + playbooks into a JSON string
  from_serialized()   — restore a fully-ready loader from that string without any disk I/O
  The round-trip skips canonical normalization and semantic enrichment (~50-100ms saved).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.models.context_pack import GlobalContextPack, MallProfileBlock, TopicBlock
from app.models.playbook import ScenarioPlaybook
from app.models.semantic import SemanticProfile
from app.services.context_builder import ContextBuilder
from app.services.playbook_engine import PlaybookEngine

logger = logging.getLogger(__name__)


class MallContextLoader:
    """Loads and assembles mall intelligence for the concierge pipeline."""

    def __init__(self, mall_id: str):
        self.mall_id = mall_id
        self._builder = ContextBuilder(mall_id)
        self._playbook_engine = PlaybookEngine()
        self._context_pack: GlobalContextPack | None = None
        self._profiles: list[SemanticProfile] = []
        self._loaded = False

    async def load(self) -> None:
        """Load all mall intelligence layers from data files."""
        if self._loaded:
            return

        self._builder.load_canonical()
        self._profiles = self._builder.load_semantic()
        playbooks = self._builder.load_playbooks()
        self._playbook_engine.load_playbooks(
            [pb.model_dump() for pb in playbooks]
        )
        self._context_pack = self._builder.build_context_pack()
        self._loaded = True
        logger.info("Mall context loaded for %s", self.mall_id)

    # ------------------------------------------------------------------
    # Redis Tier 2 serialization — fast round-trip without disk I/O
    # ------------------------------------------------------------------

    def serialize(self) -> str:
        """
        Serialize this loader to a JSON string for Redis Tier 2 storage.

        Captures: raw canonical JSON, enriched semantic profiles, playbooks.
        Restoring via from_serialized() skips file I/O and normalization.
        Only call after load() has completed.
        """
        if not self._loaded:
            raise RuntimeError(
                f"Cannot serialize unloaded MallContextLoader for {self.mall_id}"
            )
        payload = {
            "mall_id": self.mall_id,
            "raw_json": self._builder._raw_json,
            "profiles": [p.model_dump() for p in self._profiles],
            "playbooks": [pb.model_dump() for pb in self._builder._playbooks],
        }
        return json.dumps(payload, default=str)

    @classmethod
    def from_serialized(cls, data: str) -> MallContextLoader:
        """
        Restore a fully-ready MallContextLoader from a serialized JSON string.

        Reconstructs canonical models via normalize_all (pure Python, no disk I/O),
        then restores pre-enriched profiles and playbooks directly from dicts.
        The context pack is rebuilt in-process from the restored state.
        """
        payload = json.loads(data)
        mall_id: str = payload["mall_id"]
        raw_json: dict[str, Any] = payload["raw_json"]

        ctx = cls(mall_id)

        # Reconstruct canonical models from raw JSON (pure Python, no disk I/O)
        ctx._builder._raw_json = raw_json
        ctx._builder._canonical = ctx._builder._normalizer.normalize_all(raw_json)
        ctx._builder._canonical["mall_profile"] = (
            ctx._builder._normalizer.normalize_mall_profile(
                raw_json.get("mall_profile", {})
            )
        )

        # Restore pre-enriched semantic profiles (skip re-enrichment)
        ctx._profiles = [SemanticProfile(**p) for p in payload["profiles"]]
        ctx._builder._profiles = ctx._profiles

        # Restore playbooks and prime the engine
        playbooks = [ScenarioPlaybook(**pb) for pb in payload["playbooks"]]
        ctx._builder._playbooks = playbooks
        ctx._playbook_engine.load_playbooks([pb.model_dump() for pb in playbooks])

        # Rebuild the context pack in-process from the restored layers
        ctx._context_pack = ctx._builder.build_context_pack()
        ctx._loaded = True

        logger.info("Mall context restored from serialized cache: %s", mall_id)
        return ctx

    def get_context_pack(self) -> dict[str, Any]:
        """Return the full context pack as a dict for ConciergeState."""
        if not self._context_pack:
            return {}
        return self._context_pack.model_dump()

    def get_topic_blocks(self, intent: str) -> list[TopicBlock]:
        """Select topic blocks relevant to a detected user intent."""
        return self._builder.get_blocks_for_intent(intent)

    def get_topic_block(self, topic: str) -> TopicBlock | None:
        """Retrieve a single topic block by key."""
        return self._builder.get_topic_block(topic)

    def match_playbook(
        self,
        intent: str,
        context_signals: list[str] | None = None,
    ) -> ScenarioPlaybook | None:
        """Find the best-matching playbook for a given intent."""
        return self._playbook_engine.match_playbook(intent, context_signals)

    def rank_for_playbook(
        self,
        playbook: ScenarioPlaybook,
    ) -> list[dict[str, Any]]:
        """Rank entities according to a playbook's strategy."""
        return self._playbook_engine.rank_entities(playbook, self._profiles)

    def get_mall_profile(self) -> MallProfileBlock | None:
        """Return the structured mall profile block for overview questions."""
        if not self._context_pack:
            return None
        return self._context_pack.mall_profile

    def get_map_url(self) -> str:
        """Return the Mappedin interactive map URL for this mall, or empty string."""
        raw = self._builder._raw_json or {}
        return raw.get("mall_profile", {}).get("map_url", "")

    def get_entity_by_id(self, entity_id: str) -> dict | None:
        """Look up a single canonical entity across all types."""
        canonical = self._builder._canonical
        for entity_type in ("stores", "dining", "cinemas", "movies", "services", "events", "offers"):
            for entity in canonical.get(entity_type, []):
                if getattr(entity, "entity_id", None) == entity_id:
                    return entity.model_dump()
        return None

    def get_semantic_profile(self, entity_id: str) -> SemanticProfile | None:
        """Look up the semantic profile for a given entity."""
        for profile in self._profiles:
            if profile.entity_id == entity_id:
                return profile
        return None

    def get_entities_by_category(
        self,
        category_key: str,
    ) -> list[dict[str, Any]]:
        """
        Deterministic lookup: return all canonical entities matching
        a category rule key (e.g. "cafe", "perfume", "beauty").

        Delegates to ``MallRetriever.search_by_category`` synchronously
        by directly applying category rules against canonical data.
        Returns enriched entity dicts ready for prompt injection.
        """
        from app.retrieval.retriever import MallRetriever

        retriever = MallRetriever(self.mall_id)
        # We can't await here in sync code, so use the same matching logic directly
        from app.retrieval.retriever import _CATEGORY_RULES

        rule = _CATEGORY_RULES.get(category_key)
        if not rule:
            return []

        entity_types = rule["entity_types"]
        match_rules = rule["match"]
        exclude_category = {c.lower() for c in rule.get("exclude_category", set())}
        exclude_ds = {d.lower() for d in rule.get("exclude_dining_style", set())}
        results: list[dict[str, Any]] = []

        for etype in entity_types:
            canonical = self._builder._canonical.get(etype, [])
            for entity in canonical:
                if retriever._entity_matches_rule(
                    entity, match_rules, exclude_category, exclude_ds,
                ):
                    entity_data = entity.model_dump()
                    entity_id = entity.entity_id
                    profile = self.get_semantic_profile(entity_id)

                    enriched: dict[str, Any] = {
                        "entity_id": entity_id,
                        "name": entity_data.get("name") or entity_data.get("title", ""),
                        "entity_type": entity_data.get("entity_type", etype.rstrip("s")),
                        "category": entity_data.get("category", ""),
                        "subcategory": entity_data.get("subcategory", ""),
                        "description": entity_data.get("description", ""),
                        "score": 1.0,
                        "source": f"category/{category_key}",
                    }

                    loc = entity_data.get("location")
                    if isinstance(loc, dict):
                        enriched["floor"] = loc.get("floor", "")
                        enriched["zone"] = loc.get("zone", "")
                        enriched["directions_hint"] = loc.get("directions_hint", "")

                    if profile:
                        enriched["concierge_notes"] = profile.concierge_notes
                        enriched["semantic_tags"] = profile.semantic_tags[:8]
                        enriched["audience_fit"] = profile.audience_fit
                        enriched["vibe"] = profile.vibe

                    results.append(enriched)

        logger.info(
            "Category lookup '%s': %d entities", category_key, len(results),
        )
        return results

    def search_semantic_by_tags(
        self,
        tags: list[str],
        audience_tags: list[str] | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search semantic profiles by tag overlap, optionally filtered by
        audience tags.  Returns enriched entity dicts scored by relevance.
        """
        search_tags = set(tags)
        audience_set = set(audience_tags or [])
        scored: list[tuple[float, dict[str, Any]]] = []

        for profile in self._profiles:
            profile_tags = set(profile.semantic_tags)
            profile_audience = set(profile.audience_fit)

            tag_overlap = profile_tags & search_tags
            if not tag_overlap and not (audience_set and profile_audience & audience_set):
                continue

            score = len(tag_overlap) * 0.2
            if audience_set:
                audience_overlap = profile_audience & audience_set
                score += len(audience_overlap) * 0.3

            entity_data = self.get_entity_by_id(profile.entity_id)
            if not entity_data:
                continue

            enriched: dict[str, Any] = {
                "entity_id": profile.entity_id,
                "name": entity_data.get("name") or entity_data.get("title", ""),
                "entity_type": profile.entity_type,
                "score": round(min(score, 1.0), 2),
                "source": "semantic",
                "matched_tags": list(tag_overlap),
                "concierge_notes": profile.concierge_notes,
                "semantic_tags": profile.semantic_tags[:8],
                "audience_fit": profile.audience_fit,
                "vibe": profile.vibe,
            }

            loc = entity_data.get("location")
            if isinstance(loc, dict):
                enriched["floor"] = loc.get("floor", "")
                enriched["zone"] = loc.get("zone", "")

            scored.append((score, enriched))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def get_canonical_for_guard(self) -> dict[str, Any]:
        """
        Return canonical data as plain dicts in the sectioned format that
        the hallucination guard's _MallFactIndex expects:
        {stores, dining, cinemas, movies, events, offers, services}.
        """
        canonical = self._builder._canonical
        if not canonical:
            return {}
        return {
            section: [e.model_dump() for e in canonical.get(section, [])]
            for section in ("stores", "dining", "cinemas", "movies", "events", "offers", "services")
        }

    def get_canonical_for_prompt(self) -> dict[str, Any]:
        """
        Return canonical mall data in the flat structure that
        ``_format_mall_context`` expects: mall_profile (with zones,
        floors, facilities), a merged tenants list, operational context,
        and events/offers.
        """
        canonical = self._builder._canonical
        if not canonical:
            return {}

        mp = canonical.get("mall_profile")
        profile_dict = mp.model_dump() if mp else {}

        tenants: list[dict[str, Any]] = []
        for section in ("stores", "dining"):
            for entity in canonical.get(section, []):
                d = entity.model_dump()
                d.setdefault("entity_type", section.rstrip("s"))
                tenants.append(d)

        for entity in canonical.get("cinemas", []):
            d = entity.model_dump()
            d.setdefault("entity_type", "cinema")
            tenants.append(d)

        pack = self.get_context_pack()

        # Build a flat services list for the dedicated services prompt block.
        services: list[dict[str, Any]] = [
            entity.model_dump()
            for entity in canonical.get("services", [])
        ]

        return {
            "mall_profile": profile_dict,
            "tenants": tenants,
            "services": services,
            "operational_context": pack.get("operational_context", {}),
            "events_and_offers": pack.get("events_and_offers", []),
        }

    @property
    def is_loaded(self) -> bool:
        return self._loaded
