"""
Vector store service — semantic retrieval backed by Chroma (local) or Pinecone/Qdrant (hosted).

Architecture:
  Three-tier retrieval to avoid redundant embedding API calls:
    1. Redis vector cache  (cenomi:vcache:{mall_id}:{query_hash}) — ~1ms
    2. Chroma / Pinecone   (per-mall collection: cenomi_mall_{mall_id}) — ~100-200ms
    3. OpenAI embedding    (text-embedding-3-small) — only on full cache miss

Each mall gets its own named Chroma collection so searches are automatically
scoped to the requesting mall — no cross-contamination.

Usage:
    vs = get_vector_store()
    results = await vs.search("something trendy for a date night", mall_id="al_nakheel_plaza_28")
    # Returns list[RetrievalResult] with entity_id, score, entity_type metadata
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config.settings import Settings

logger = logging.getLogger(__name__)

try:
    import chromadb
    _CHROMA_AVAILABLE = True
except ImportError:
    chromadb = None  # type: ignore[assignment]
    _CHROMA_AVAILABLE = False


class VectorStoreService:
    """
    Async vector search with a Redis result-cache layer.

    Call connect() once after construction (at runtime.initialize time).
    Call search() from retriever nodes — safe to call concurrently.
    Call upsert() from the offline ingest_vectors.py script.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._chroma_client: Any | None = None
        self._redis: Any | None = None
        self._openai: Any | None = None
        self._available = False

    async def connect(self) -> None:
        """Open the Chroma client and optionally connect a Redis client."""
        store_type = self._settings.vector_store_type

        if store_type == "chroma":
            if not _CHROMA_AVAILABLE:
                logger.warning(
                    "chromadb not installed — vector search disabled. "
                    "Install with: pip install 'chromadb>=0.5'"
                )
                return
            try:
                persist_dir = self._settings.chroma_persist_dir
                self._chroma_client = await asyncio.to_thread(
                    chromadb.PersistentClient, path=persist_dir
                )
                logger.info("Chroma PersistentClient opened at %s", persist_dir)
            except Exception:
                logger.exception("Failed to open Chroma client — vector search disabled")
                return
        else:
            logger.warning("vector_store_type=%r is not yet implemented", store_type)
            return

        # Optional Redis result cache — only wired if redis_url is configured
        if self._settings.redis_url:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    self._settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
                logger.info("Vector store Redis cache connected")
            except Exception:
                logger.warning("Could not connect Redis cache for vector store", exc_info=True)

        # OpenAI async client for embeddings
        try:
            from openai import AsyncOpenAI
            self._openai = AsyncOpenAI(api_key=self._settings.openai_api_key)
        except Exception:
            logger.exception("Failed to create OpenAI client — embedding unavailable")
            return

        self._available = True
        logger.info(
            "VectorStoreService ready (store=%s, redis_cache=%s)",
            store_type,
            "enabled" if self._redis else "disabled",
        )

    @property
    def is_available(self) -> bool:
        return self._available

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        mall_id: str,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        """
        Semantic search scoped to a single mall.

        Returns a list of dicts with keys: entity_id, entity_type, score, mall_id.
        Scores are cosine distances converted to [0, 1] similarity (higher = better).
        """
        if not self._available:
            return []

        normalized = query.strip().lower()
        cache_key = self._cache_key(mall_id, normalized)

        # Tier 1: Redis cache hit
        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    logger.debug("Vector cache HIT for mall=%s query=%r", mall_id, normalized[:40])
                    return json.loads(cached)
            except Exception:
                logger.debug("Redis cache read failed", exc_info=True)

        # Tier 2: embed + Chroma query
        try:
            embedding = await self._embed(normalized)
            collection_name = self._collection_name(mall_id)

            results = await asyncio.to_thread(
                self._chroma_query,
                collection_name,
                embedding,
                top_k,
            )

            if results is None:
                return []

            output = self._format_results(results, mall_id)

            # Write to Redis cache
            if self._redis and output:
                try:
                    await self._redis.set(
                        cache_key,
                        json.dumps(output),
                        ex=self._settings.vector_cache_ttl,
                    )
                except Exception:
                    logger.debug("Redis cache write failed", exc_info=True)

            logger.info(
                "Vector search mall=%s query=%r → %d results",
                mall_id, normalized[:40], len(output),
            )
            return output

        except Exception:
            logger.warning("Vector search failed for mall=%s", mall_id, exc_info=True)
            return []

    async def upsert(self, entities: list[dict[str, Any]], mall_id: str) -> int:
        """
        Embed and upsert entity records into the mall's Chroma collection.

        Each entity dict must have: entity_id, entity_type, text (the text blob to embed).
        Returns the number of vectors upserted.
        """
        if not self._available:
            raise RuntimeError("VectorStoreService not available — check connect()")

        if not entities:
            return 0

        collection_name = self._collection_name(mall_id)

        # Batch embed all texts
        texts = [e["text"] for e in entities]
        embeddings = await self._embed_batch(texts)

        ids = [e["entity_id"] for e in entities]
        metadatas = [
            {
                "entity_id": e["entity_id"],
                "entity_type": e.get("entity_type", ""),
                "mall_id": mall_id,
            }
            for e in entities
        ]
        documents = texts

        await asyncio.to_thread(
            self._chroma_upsert,
            collection_name,
            ids,
            embeddings,
            metadatas,
            documents,
        )

        logger.info("Upserted %d vectors into collection %s", len(ids), collection_name)
        return len(ids)

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _collection_name(self, mall_id: str) -> str:
        return f"cenomi_mall_{mall_id}"

    def _cache_key(self, mall_id: str, query: str) -> str:
        h = hashlib.sha256(query.encode()).hexdigest()[:16]
        return f"cenomi:vcache:{mall_id}:{h}"

    async def _embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        response = await self._openai.embeddings.create(
            model=self._settings.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single API call (up to 2048 items)."""
        if not texts:
            return []
        # Chroma max batch is 5461; OpenAI max is 2048 — chunk if needed
        if len(texts) <= 2048:
            response = await self._openai.embeddings.create(
                model=self._settings.embedding_model,
                input=texts,
            )
            return [item.embedding for item in response.data]

        # Chunk for large batches
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), 2048):
            chunk = texts[i : i + 2048]
            response = await self._openai.embeddings.create(
                model=self._settings.embedding_model,
                input=chunk,
            )
            all_embeddings.extend(item.embedding for item in response.data)
        return all_embeddings

    def _chroma_query(
        self,
        collection_name: str,
        embedding: list[float],
        top_k: int,
    ) -> dict | None:
        """Synchronous Chroma query — runs in a thread pool."""
        try:
            collection = self._chroma_client.get_collection(name=collection_name)
        except Exception:
            logger.warning(
                "Chroma collection %r not found — run ingest_vectors.py first",
                collection_name,
            )
            return None

        return collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, collection.count()),
            include=["metadatas", "distances"],
        )

    def _chroma_upsert(
        self,
        collection_name: str,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
        documents: list[str],
    ) -> None:
        """Synchronous Chroma upsert — runs in a thread pool."""
        collection = self._chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )

    @staticmethod
    def _format_results(
        chroma_result: dict,
        mall_id: str,
    ) -> list[dict[str, Any]]:
        """Convert raw Chroma query output to a clean list of result dicts."""
        output: list[dict[str, Any]] = []
        ids_list = chroma_result.get("ids", [[]])[0]
        metas_list = chroma_result.get("metadatas", [[]])[0]
        dists_list = chroma_result.get("distances", [[]])[0]

        for entity_id, meta, dist in zip(ids_list, metas_list, dists_list):
            # Chroma cosine distance: 0 = identical, 2 = opposite
            # Convert to similarity score in [0, 1]
            score = max(0.0, 1.0 - dist / 2.0)
            output.append({
                "entity_id": entity_id,
                "entity_type": meta.get("entity_type", ""),
                "mall_id": mall_id,
                "score": round(score, 4),
                "source": "vector",
            })
        return output

    async def close(self) -> None:
        """Close Redis connection if open."""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
