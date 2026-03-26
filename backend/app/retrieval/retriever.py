"""
Retrieval service — structured search across mall canonical data.

Supports:
- Canonical entity lookup (name match, category match)
- Category-based deterministic retrieval (structured tenant lookup)
- Semantic profile search (tag-based filtering)
- Playbook matching (scenario-based)

The retriever is called by compose_context and fetch_exact_facts
nodes in the graph pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.runtime import get_mall_context

logger = logging.getLogger(__name__)


class RetrievalResult(BaseModel):
    """A single retrieval result from any search layer."""

    content: str
    source: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# Maps user-facing category terms to canonical category/subcategory/tag/
# dining_style/cuisine_type values used in the mall JSON.  Each entry
# specifies which entity_types to scan and what field values to match.
_CATEGORY_RULES: dict[str, dict[str, Any]] = {
    "restaurant": {
        "entity_types": ["dining"],
        "match": {
            # fast_food is the actual style used in the data for restaurant-type
            # places (Kudu, McDonald's, Herfy, Popeyes, Corn World)
            "dining_style": {"casual_dining", "fine_dining", "fast_casual", "fast_food"},
        },
        "exclude_dining_style": {"cafe", "dessert"},
    },
    # all_dining matches every dining entity regardless of style — used for
    # broad queries like "dinner?", "something to eat", general_dining intent
    "all_dining": {
        "entity_types": ["dining"],
        "match": {
            "dining_style": {
                "casual_dining", "fine_dining", "fast_casual",
                "fast_food", "cafe", "dessert", "dessert_cafe", "quick_service",
            },
        },
    },
    "fast_food": {
        "entity_types": ["dining"],
        "match": {
            "dining_style": {"fast_casual", "quick_service", "fast_food"},
            "tags": {"burgers", "fast_casual", "quick", "fast_food"},
        },
        "exclude_dining_style": {"cafe", "dessert", "fine_dining"},
    },
    "cafe": {
        "entity_types": ["dining"],
        "match": {
            "dining_style": {"cafe"},
            "tags": {"cafe", "coffee"},
        },
    },
    "coffee": {
        "entity_types": ["dining"],
        "match": {
            "dining_style": {"cafe"},
            "tags": {"cafe", "coffee"},
        },
    },
    "dessert": {
        "entity_types": ["dining"],
        "match": {
            # dessert_cafe covers Cinnabon and MammaBunz (sweet/bun cafés)
            "dining_style": {"dessert", "dessert_cafe"},
            "tags": {"dessert", "sweet_treat", "ice_cream", "cheesecake"},
        },
    },
    "clothing": {
        "entity_types": ["stores"],
        "match": {
            "category": {"fashion"},
            "tags": {"clothing", "fashion"},
        },
        "exclude_category": {"sportswear"},
    },
    "fashion": {
        "entity_types": ["stores"],
        "match": {
            "category": {"fashion"},
            "tags": {"clothing", "fashion"},
        },
    },
    "perfume": {
        "entity_types": ["stores"],
        "match": {
            "category": {"perfume", "fragrance"},
            "subcategory_contains": {"perfume", "perfumery", "oud"},
            "tags": {"perfume", "oud", "bakhoor"},
        },
        "exclude_category": {"home", "beauty"},
    },
    "beauty": {
        "entity_types": ["stores"],
        "match": {
            "category": {"beauty"},
            "tags": {"beauty", "cosmetics", "skincare"},
        },
        "exclude_category": {"jewelry", "perfume"},
    },
    "jewelry": {
        "entity_types": ["stores"],
        "match": {
            "category": {"jewelry"},
            "tags": {"jewelry", "gold", "diamonds", "silver", "crystal"},
        },
        "exclude_category": {"accessories"},
    },
    "accessories": {
        "entity_types": ["stores"],
        "match": {
            "category": {"accessories"},
            "tags": {"accessories", "handbags"},
        },
        "exclude_category": {"jewelry", "beauty"},
    },
    "gift": {
        "entity_types": ["stores"],
        "match": {
            "category": {"jewelry", "perfume", "beauty", "accessories"},
            "tags": {"gift"},
        },
    },
    "electronics": {
        "entity_types": ["stores"],
        "match": {
            "category": {"electronics"},
            "tags": {"electronics", "technology"},
        },
    },
    "kids": {
        "entity_types": ["stores"],
        "match": {
            "category": {"kids"},
            "tags": {"kids", "baby", "children"},
        },
    },
    "sportswear": {
        "entity_types": ["stores"],
        "match": {
            "category": {"sportswear"},
            "tags": {"sportswear", "athletic", "fitness"},
        },
    },
    "home": {
        "entity_types": ["stores"],
        "match": {
            "category": {"home"},
            "tags": {"home", "furniture", "decor"},
        },
    },
    "all_stores": {
        "entity_types": ["stores"],
        "match": {
            "category": {
                "fashion", "jewelry", "electronics", "beauty",
                "kids", "sportswear", "home", "perfume", "accessories",
            },
        },
    },
}

# User query keywords that map to a category rule key
_QUERY_TO_CATEGORY: dict[str, str] = {
    "fast food": "fast_food",
    "fast-food": "fast_food",
    "burger": "fast_food",
    "burgers": "fast_food",
    "quick bite": "fast_food",
    "quick food": "fast_food",
    "restaurant": "restaurant",
    "restaurants": "restaurant",
    # Broad food queries → all_dining so cafes are also shown
    "dinner": "all_dining",
    "lunch": "all_dining",
    "meal": "all_dining",
    "meals": "all_dining",
    "hungry": "all_dining",
    "eating": "all_dining",
    "food": "all_dining",
    "eat": "all_dining",
    "dining": "all_dining",
    "cafe": "cafe",
    "cafes": "cafe",
    "coffee": "coffee",
    "coffee shop": "coffee",
    "dessert": "dessert",
    "desserts": "dessert",
    "ice cream": "dessert",
    "sweet": "dessert",
    "sweets": "dessert",
    "cake": "dessert",
    "pastry": "dessert",
    "cheesecake": "dessert",
    "shopping": "all_stores",
    "shop": "all_stores",
    "clothing": "clothing",
    "clothes": "clothing",
    "fashion": "fashion",
    "perfume": "perfume",
    "perfumes": "perfume",
    "fragrance": "perfume",
    "fragrances": "perfume",
    "oud": "perfume",
    "bakhoor": "perfume",
    "incense": "perfume",
    "beauty": "beauty",
    "cosmetics": "beauty",
    "skincare": "beauty",
    "makeup": "beauty",
    "jewelry": "jewelry",
    "jewellery": "jewelry",
    "gold": "jewelry",
    "silver jewelry": "jewelry",
    "accessories": "accessories",
    "handbags": "accessories",
    "bags": "accessories",
    "gift": "gift",
    "gifts": "gift",
    "gift store": "gift",
    "gift stores": "gift",
    "gift shop": "gift",
    "present": "gift",
    "electronics": "electronics",
    "tech": "electronics",
    "gadgets": "electronics",
    "kids": "kids",
    "baby": "kids",
    "children": "kids",
    "sportswear": "sportswear",
    "sports": "sportswear",
    "athletic": "sportswear",
    "home": "home",
    "furniture": "home",
    "decor": "home",
}


_RELATED_CATEGORIES: dict[str, list[str]] = {
    "coffee": ["dessert"],
    "cafe": ["dessert"],
    "dessert": ["coffee"],
    "fast_food": ["all_dining", "dessert"],
    "restaurant": ["all_dining", "cafe"],
    "all_dining": ["cafe", "dessert"],
    "perfume": ["beauty"],
    "beauty": ["perfume"],
    "jewelry": ["accessories"],
    "accessories": ["jewelry"],
    "gift": ["jewelry", "perfume", "beauty", "accessories"],
}


def get_related_categories(category_key: str) -> list[str]:
    """Return category keys that pair well with the given category."""
    return _RELATED_CATEGORIES.get(category_key, [])


def detect_category_from_query(query: str) -> str | None:
    """Detect the best matching category rule key from a user query."""
    lower = query.lower()
    # Try multi-word matches first (longest match wins)
    for phrase in sorted(_QUERY_TO_CATEGORY, key=len, reverse=True):
        if phrase in lower:
            return _QUERY_TO_CATEGORY[phrase]
    return None


def detect_category_from_intent(sub_intent: str) -> str | None:
    """Map a sub_intent to a category rule key."""
    mapping: dict[str, str] = {
        # all_dining returns every dining entity — best for broad "dinner?",
        # "something to eat?" style queries where the user hasn't asked for
        # a specific sub-category (restaurant vs. cafe)
        "general_dining": "all_dining",
        "romantic_dining": "all_dining",
        "family_dining": "all_dining",
        "quick_bite": "fast_food",
        "cafe_recommendation": "cafe",
        "dessert_recommendation": "dessert",
        "general_shopping": "all_stores",
        "fashion_shopping": "fashion",
        "gift_recommendation": "gift",
        "perfume_shopping": "perfume",
        "jewelry_shopping": "jewelry",
        "accessories_shopping": "accessories",
    }
    return mapping.get(sub_intent)


def build_scene_prefix(scene) -> str:
    """
    Build a short keyword prefix from SceneMemory signals for vector query augmentation.

    Prepended to the raw user query before embedding so the resulting vector
    reflects the visitor's actual context rather than the bare query text.
    Queries from different scenes therefore land in different neighbourhoods of
    the embedding space, returning more contextually relevant entities.

    Example:
        scene(occasion="anniversary", companions=["partner"], visit_type="date")
        → "anniversary partner date"

        scene(companions=["kids"], visit_type="family_outing", budget="budget")
        → "kids family outing budget"

    Only fields with non-empty values contribute terms.  Up to three companion
    entries are included to keep the prefix concise.  Underscore-separated
    values (e.g. ``family_outing``) are normalised to space-separated words.
    """
    parts: list[str] = []
    if scene.occasion:
        parts.append(scene.occasion.replace("_", " "))
    if scene.companions:
        parts.extend(c.replace("_", " ") for c in scene.companions[:3])
    if scene.visit_type:
        parts.append(scene.visit_type.replace("_", " "))
    if scene.budget:
        parts.append(scene.budget.replace("_", " "))
    # Deduplicate while preserving insertion order
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            unique.append(p)
    return " ".join(unique)


class MallRetriever:
    """Structured retrieval over mall intelligence."""

    def __init__(self, mall_id: str):
        self.mall_id = mall_id

    async def retrieve(self, query: str, top_k: int = 12) -> list[RetrievalResult]:
        """Run multi-layer retrieval and return ranked results."""
        results: list[RetrievalResult] = []
        results.extend(await self.search_canonical(query, top_k))
        return sorted(results, key=lambda r: r.score, reverse=True)[:top_k]

    async def search_by_category(
        self,
        category_key: str,
        max_results: int = 20,
    ) -> list[RetrievalResult]:
        """
        Deterministic category-based retrieval.

        Returns ALL entities matching the canonical category rules for
        ``category_key``.  This is the structured retrieval path that
        ensures complete, category-accurate results for tenant-lookup
        queries like "What cafes are available?" or "What beauty stores
        are in the mall?".
        """
        rule = _CATEGORY_RULES.get(category_key)
        if not rule:
            return []

        try:
            mall_ctx = get_mall_context(self.mall_id)
        except RuntimeError:
            return []

        entity_types = rule["entity_types"]
        match_rules = rule["match"]
        exclude_category = {c.lower() for c in rule.get("exclude_category", set())}
        exclude_ds = {d.lower() for d in rule.get("exclude_dining_style", set())}
        results: list[RetrievalResult] = []

        for etype in entity_types:
            canonical = mall_ctx._builder._canonical.get(etype, [])
            for entity in canonical:
                if self._entity_matches_rule(entity, match_rules, exclude_category, exclude_ds):
                    name = getattr(entity, "name", "") or getattr(entity, "title", "")
                    results.append(
                        RetrievalResult(
                            content=f"{name} ({etype})",
                            source=f"category/{category_key}",
                            score=1.0,
                            metadata={
                                "entity_id": entity.entity_id,
                                "category_key": category_key,
                            },
                        )
                    )

        logger.info(
            "Category search '%s': %d results", category_key, len(results),
        )
        return results[:max_results]

    @staticmethod
    def _entity_matches_rule(
        entity: Any,
        match_rules: dict[str, Any],
        exclude_category: set[str],
        exclude_dining_style: set[str],
    ) -> bool:
        """Check if entity matches any of the category match rules."""
        entity_category = getattr(entity, "category", "").lower()
        entity_tags = {t.lower() for t in getattr(entity, "tags", [])}
        entity_subcategory = getattr(entity, "subcategory", "").lower()
        entity_dining_style = getattr(entity, "dining_style", "").lower()

        if exclude_category and entity_category in exclude_category:
            return False
        if exclude_dining_style and entity_dining_style in exclude_dining_style:
            return False

        for field, values in match_rules.items():
            if field == "category" and entity_category in values:
                return True
            if field == "tags" and entity_tags & values:
                return True
            if field == "dining_style" and entity_dining_style in values:
                return True
            if field == "subcategory_contains":
                if any(term in entity_subcategory for term in values):
                    return True
        return False

    async def search_canonical(
        self, query: str, top_k: int = 12
    ) -> list[RetrievalResult]:
        """Name-based search over canonical tenant entities."""
        try:
            mall_ctx = get_mall_context(self.mall_id)
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
        self, query: str, tags: list[str] | None = None, top_k: int = 12, scene=None
    ) -> list[RetrievalResult]:
        """
        Vector similarity search over embedded entity descriptions.

        Uses VectorStoreService (Chroma + Redis cache) when available.
        Falls back to tag-word overlap when the vector store is not initialised
        (e.g. before ingest_vectors.py has been run, or chromadb not installed).

        ``scene`` — optional SceneMemory; when provided, scene signals
        (occasion, companions, visit_type, budget) are prepended to the query
        before embedding so results reflect the visitor's actual context.
        """
        if scene is not None:
            prefix = build_scene_prefix(scene)
            if prefix:
                query = f"{prefix} {query}".strip()
                logger.debug("search_semantic augmented query: %r", query[:80])

        from app.runtime import get_vector_store
        vs = get_vector_store()

        if vs is not None and vs.is_available:
            raw = await vs.search(query, mall_id=self.mall_id, top_k=top_k)
            return [
                RetrievalResult(
                    content=r["entity_id"],
                    source="vector",
                    score=r["score"],
                    metadata={
                        "entity_id": r["entity_id"],
                        "entity_type": r.get("entity_type", ""),
                    },
                )
                for r in raw
            ]

        # ── Fallback: tag-word overlap (pre-vector / dev mode) ────────────
        try:
            mall_ctx = get_mall_context(self.mall_id)
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
                    source="semantic_tags",
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
            mall_ctx = get_mall_context(self.mall_id)
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
