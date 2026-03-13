"""
Retrieval service — structured search across mall canonical data.

Supports:
- Canonical entity lookup (name match, category match)
- Semantic profile search (tag-based filtering)
- Playbook matching (scenario-based)

The retriever is called by compose_context and fetch_exact_facts
nodes in the graph pipeline.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.runtime import get_mall_context


class RetrievalResult(BaseModel):
    """A single retrieval result from any search layer."""

    content: str
    source: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MallRetriever:
    """Structured retrieval over mall intelligence."""

    def __init__(self, mall_id: str):
        self.mall_id = mall_id

    async def retrieve(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        """Run multi-layer retrieval and return ranked results."""
        results: list[RetrievalResult] = []
        results.extend(await self.search_canonical(query, top_k))
        return sorted(results, key=lambda r: r.score, reverse=True)[:top_k]

    async def search_canonical(
        self, query: str, top_k: int = 5
    ) -> list[RetrievalResult]:
        """Name-based search over canonical tenant entities."""
        try:
            mall_ctx = get_mall_context()
        except RuntimeError:
            return []

        query_lower = query.lower()
        results: list[RetrievalResult] = []

        for entity_type in ("stores", "dining", "cinemas", "services"):
            canonical = mall_ctx._builder._canonical.get(entity_type, [])
            for entity in canonical:
                name = getattr(entity, "name", "").lower()
                if not name:
                    continue

                score = 0.0
                if name in query_lower:
                    score = 1.0
                elif any(w in query_lower for w in name.split() if len(w) > 2):
                    score = 0.6

                if score > 0:
                    results.append(
                        RetrievalResult(
                            content=f"{entity.name} ({entity_type})",
                            source=f"canonical/{entity_type}",
                            score=score,
                            metadata={"entity_id": entity.entity_id},
                        )
                    )

        return sorted(results, key=lambda r: r.score, reverse=True)[:top_k]

    async def search_semantic(
        self, query: str, tags: list[str] | None = None, top_k: int = 5
    ) -> list[RetrievalResult]:
        """Tag-based search over semantic profiles."""
        try:
            mall_ctx = get_mall_context()
        except RuntimeError:
            return []

        search_tags = set(tags or [])
        query_words = set(query.lower().split())
        results: list[RetrievalResult] = []

        for profile in mall_ctx._builder._profiles:
            profile_tags = set(profile.semantic_tags)
            tag_overlap = profile_tags & (search_tags | query_words)
            if not tag_overlap:
                continue

            score = len(tag_overlap) * 0.2
            results.append(
                RetrievalResult(
                    content=f"{profile.entity_id} ({profile.entity_type})",
                    source="semantic",
                    score=min(score, 1.0),
                    metadata={
                        "entity_id": profile.entity_id,
                        "matched_tags": list(tag_overlap),
                    },
                )
            )

        return sorted(results, key=lambda r: r.score, reverse=True)[:top_k]

    async def match_playbooks(
        self, intent: str, signals: list[str] | None = None
    ) -> list[RetrievalResult]:
        """Find matching scenario playbooks for the given intent."""
        try:
            mall_ctx = get_mall_context()
        except RuntimeError:
            return []

        pb = mall_ctx.match_playbook(intent=intent, context_signals=signals)
        if pb:
            return [
                RetrievalResult(
                    content=f"Playbook: {pb.scenario}",
                    source="playbook",
                    score=0.8,
                    metadata={"playbook_id": pb.playbook_id},
                )
            ]
        return []
