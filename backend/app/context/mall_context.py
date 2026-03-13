"""
Mall context loader — builds the runtime context injected into every turn.

Responsible for:
- Loading the global context pack (canonical + semantic + playbooks)
- Providing topic-block selection for efficient prompt injection
- Exposing entity lookups for the concierge pipeline

This is the bridge between static data and the live concierge state.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.context_pack import GlobalContextPack, TopicBlock
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

    @property
    def is_loaded(self) -> bool:
        return self._loaded
