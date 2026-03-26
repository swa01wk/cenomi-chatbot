"""
Global runtime registry — holds initialized singletons for the pipeline.

Initialized once at application startup (main.py lifespan).
Graph nodes and services import getters to access shared resources
without threading references through state.

Usage in any node / service:
    from app.runtime import get_mall_context, get_session_store
    ctx = get_mall_context("al_nakheel_plaza_28")
    store = get_session_store()

Mall context loading — three-tier design:
    Tier 1 — Python RAM (LRU, N=mall_cache_size, instant 0ms access)
        MallContextLoader objects for the most-recently requested malls.
    Tier 2 — Redis  (all 20 malls, cenomi:ctx:{mall_id}, TTL=mall_ctx_redis_ttl)
        Serialized canonical+profiles+playbooks; restoration skips disk I/O
        and normalization (~50-100× faster than cold Tier 3 load).
    Tier 3 — Disk  (canonical/ semantic/ playbooks/ JSON files, ~50-200ms)
        Source of truth; only hit on the very first request for a mall.

    concierge.py calls `await ensure_mall_loaded(mall_id)` before each turn
    so that get_mall_context() is always a fast synchronous RAM lookup.

Session store selection (controlled by BACKEND_REDIS_URL):
    - Empty / unset  →  in-memory SessionStore (development default)
    - Redis URL set  →  RedisSessionStore with 30-min TTL

LangGraph checkpointer (controlled by BACKEND_ENABLE_CHECKPOINTER):
    - False (default)        →  no checkpointer; graph is stateless between turns
    - True + no Redis URL    →  MemorySaver (in-process snapshots for dev/replay)
    - True + Redis URL set   →  AsyncRedisSaver (durable cross-process snapshots)
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.context.mall_context import MallContextLoader
    from app.services.feedback_normalizer import FeedbackNormalizer
    from app.services.feedback_service import FeedbackService
    from app.services.implicit_feedback_detector import ImplicitFeedbackDetector
    from app.services.session_store import AbstractSessionStore
    from app.services.session_tuning_engine import SessionTuningEngine
    from app.services.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


class LRUMallContextRegistry:
    """
    Three-tier LRU cache of MallContextLoader objects.

    Tier 1 — Python RAM (this OrderedDict, capacity-bounded, instant 0ms)
    Tier 2 — Redis  (cenomi:ctx:{mall_id}, restored without disk I/O, ~1-5ms)
    Tier 3 — Disk   (canonical/ semantic/ playbooks/ JSON files, ~50-200ms)

    ensure_loaded() walks the tier chain and writes back to Redis on Tier 3
    hits so the next eviction/reload is served from Tier 2 instead of disk.

    All public API is split into:
      - async ensure_loaded(mall_id)  — call before the turn begins
      - sync  get(mall_id)            — use inside graph nodes
    """

    def __init__(
        self,
        capacity: int,
        known_mall_ids: list[str],
        redis_url: str = "",
        redis_ttl: int = 3600,
    ) -> None:
        self._capacity = max(1, capacity)
        self._known_mall_ids: list[str] = list(known_mall_ids)
        self._loaded: OrderedDict[str, MallContextLoader] = OrderedDict()
        self._redis_url = redis_url
        self._redis_ttl = redis_ttl
        self._redis: Any | None = None  # lazily created on first access

    async def _get_redis(self) -> Any | None:
        """Return (and lazily create) the Redis client, or None if not configured."""
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            logger.info("LRUMallContextRegistry: Redis Tier 2 connected")
        except Exception:
            logger.warning(
                "LRUMallContextRegistry: could not connect to Redis — "
                "mall context will always load from disk (Tier 3)",
                exc_info=True,
            )
        return self._redis

    async def ensure_loaded(self, mall_id: str) -> None:
        """
        Ensure mall is in RAM.  Tier resolution order:

          Tier 1 — RAM hit   → move-to-end, return instantly
          Tier 2 — Redis hit → from_serialized(), no disk I/O
          Tier 3 — disk load → MallContextLoader.load(), then write to Redis
        """
        if mall_id in self._loaded:
            self._loaded.move_to_end(mall_id)
            return

        from app.context.mall_context import MallContextLoader

        ctx: MallContextLoader | None = None
        redis = await self._get_redis()
        cache_key = f"cenomi:ctx:{mall_id}"

        # Tier 2: Redis cache
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    ctx = MallContextLoader.from_serialized(cached)
                    logger.info(
                        "Mall context restored from Redis (Tier 2): %s", mall_id
                    )
            except Exception:
                logger.warning(
                    "Redis Tier 2 read failed for mall=%s — falling back to disk",
                    mall_id,
                    exc_info=True,
                )
                ctx = None

        # Tier 3: disk load
        if ctx is None:
            ctx = MallContextLoader(mall_id)
            await ctx.load()
            logger.info("Mall context loaded from disk (Tier 3): %s", mall_id)

            # Write to Redis so future restores skip Tier 3
            if redis:
                try:
                    await redis.set(cache_key, ctx.serialize(), ex=self._redis_ttl)
                    logger.debug(
                        "Mall context written to Redis (Tier 2): %s (TTL=%ds)",
                        mall_id, self._redis_ttl,
                    )
                except Exception:
                    logger.warning(
                        "Redis Tier 2 write failed for mall=%s — context still in RAM",
                        mall_id,
                        exc_info=True,
                    )

        # Evict LRU entry if at capacity (evicted mall stays in Redis Tier 2)
        if len(self._loaded) >= self._capacity:
            evicted_id, _ = self._loaded.popitem(last=False)
            logger.info(
                "LRU eviction: mall=%s → evicted from RAM (still in Redis Tier 2)",
                evicted_id,
            )

        self._loaded[mall_id] = ctx
        logger.info(
            "Mall context in LRU RAM cache: %s (size=%d/%d)",
            mall_id, len(self._loaded), self._capacity,
        )

    def get(self, mall_id: str) -> MallContextLoader:
        """Synchronous get — mall must already be in RAM (call ensure_loaded first)."""
        if mall_id not in self._loaded:
            raise RuntimeError(
                f"Mall {mall_id!r} not in RAM cache. "
                "ensure_mall_loaded() must be awaited before graph invocation."
            )
        self._loaded.move_to_end(mall_id)
        return self._loaded[mall_id]

    def get_known_ids(self) -> list[str]:
        """Return all mall IDs known to this registry (from BACKEND_MALL_IDS)."""
        return list(self._known_mall_ids)

    def get_loaded_ids(self) -> list[str]:
        """Return mall IDs currently in the RAM LRU cache."""
        return list(self._loaded.keys())

    def items(self):  # noqa: ANN201
        return self._loaded.items()

    def values(self):  # noqa: ANN201
        return self._loaded.values()

    def __contains__(self, mall_id: str) -> bool:
        return mall_id in self._loaded

    def __len__(self) -> int:
        return len(self._loaded)


# ── Module-level singletons ───────────────────────────────────────────────────
_mall_registry: LRUMallContextRegistry | None = None
_vector_store: VectorStoreService | None = None
_session_store: AbstractSessionStore | None = None
_checkpointer: Any | None = None
_feedback_service: FeedbackService | None = None
_implicit_detector: ImplicitFeedbackDetector | None = None
_feedback_normalizer: FeedbackNormalizer | None = None
_session_tuning_engine: SessionTuningEngine | None = None
_initialized: bool = False


async def initialize(mall_ids: list[str]) -> None:
    """Load mall intelligence for each mall ID and prepare shared services."""
    global _mall_registry, _vector_store, _session_store, _checkpointer, _initialized
    global _feedback_service, _implicit_detector, _feedback_normalizer, _session_tuning_engine

    from app.config.settings import get_settings
    from app.services.feedback_normalizer import FeedbackNormalizer
    from app.services.feedback_service import FeedbackService
    from app.services.implicit_feedback_detector import ImplicitFeedbackDetector
    from app.services.session_tuning_engine import SessionTuningEngine
    from app.services.vector_store import VectorStoreService

    settings = get_settings()

    # ── Session store ─────────────────────────────────────────────────────
    if settings.redis_url:
        from app.services.session_store import RedisSessionStore
        _session_store = RedisSessionStore(
            redis_url=settings.redis_url,
            ttl=settings.redis_session_ttl,
        )
        logger.info("Session store: Redis (%s, TTL=%ds)", settings.redis_url, settings.redis_session_ttl)
    else:
        from app.services.session_store import SessionStore
        _session_store = SessionStore()
        logger.info("Session store: in-memory LRU")

    # ── LangGraph checkpointer ────────────────────────────────────────────
    if settings.enable_checkpointer:
        if settings.redis_url:
            try:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                _checkpointer = AsyncRedisSaver.from_conn_string(settings.redis_url)
                logger.info("Checkpointer: AsyncRedisSaver")
            except ImportError:
                logger.warning(
                    "langgraph-checkpoint-redis not installed; falling back to MemorySaver. "
                    "Install with: pip install langgraph-checkpoint-redis"
                )
                from langgraph.checkpoint.memory import MemorySaver
                _checkpointer = MemorySaver()
                logger.info("Checkpointer: MemorySaver (fallback)")
        else:
            from langgraph.checkpoint.memory import MemorySaver
            _checkpointer = MemorySaver()
            logger.info("Checkpointer: MemorySaver (dev)")
    else:
        _checkpointer = None
        logger.info("Checkpointer: disabled")

    # ── Mall context LRU registry ─────────────────────────────────────────
    # Create the registry with all known mall IDs but only eagerly load up
    # to mall_cache_size malls at startup. The rest are loaded lazily.
    # Redis Tier 2 is wired here — if redis_url is empty the registry
    # silently falls back to disk-only (Tier 3) loading.
    _mall_registry = LRUMallContextRegistry(
        capacity=settings.mall_cache_size,
        known_mall_ids=mall_ids,
        redis_url=settings.redis_url,
        redis_ttl=settings.mall_ctx_redis_ttl,
    )
    preload_ids = mall_ids[: settings.mall_cache_size]
    for mall_id in preload_ids:
        await _mall_registry.ensure_loaded(mall_id)
        logger.info("Runtime initialized for mall %s", mall_id)

    if len(mall_ids) > settings.mall_cache_size:
        deferred = mall_ids[settings.mall_cache_size :]
        logger.info(
            "Deferred %d mall(s) (beyond cache_size=%d): %s",
            len(deferred), settings.mall_cache_size, deferred,
        )

    # ── Vector store ──────────────────────────────────────────────────────
    _vector_store = VectorStoreService(settings)
    await _vector_store.connect()

    _feedback_service = FeedbackService()
    _implicit_detector = ImplicitFeedbackDetector()
    _feedback_normalizer = FeedbackNormalizer()
    _session_tuning_engine = SessionTuningEngine()

    _initialized = True
    logger.info(
        "Runtime ready — loaded %d/%d mall(s): %s",
        len(_mall_registry),
        len(mall_ids),
        _mall_registry.get_loaded_ids(),
    )


async def ensure_mall_loaded(mall_id: str) -> None:
    """
    Ensure the given mall is loaded into the LRU RAM cache.

    Call this at the start of every request handler before any synchronous
    get_mall_context() calls.  If the mall is already in RAM, this is a
    no-op (O(1) dict lookup).  If it is cold, disk I/O happens here — not
    inside graph nodes — so latency is predictable.
    """
    if _mall_registry is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    await _mall_registry.ensure_loaded(mall_id)


def get_mall_context(mall_id: str) -> MallContextLoader:
    if _mall_registry is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _mall_registry.get(mall_id)


def get_loaded_mall_ids() -> list[str]:
    """Return the list of mall IDs currently loaded in RAM."""
    if _mall_registry is None:
        return []
    return _mall_registry.get_loaded_ids()


def get_vector_store() -> VectorStoreService | None:
    """Return the VectorStoreService singleton, or None if not initialized."""
    return _vector_store


def search_brand_across_malls(
    brand_name: str,
    home_mall_id: str | None = None,
) -> list[dict]:
    """
    Search all loaded mall contexts for a brand/store by display name.

    Only malls currently in the LRU RAM cache are searched.  Malls that
    have not yet received any requests are not yet loaded and are skipped.

    Results are sorted so the home mall always appears first.

    Each entry contains:
        mall_id, mall_name, entity_type, name, floor, category,
        is_home_mall, source
    """
    if _mall_registry is None:
        return []

    results = []
    query = brand_name.strip().lower()

    for mall_id, ctx in _mall_registry.items():
        canonical = ctx._builder._canonical
        mall_profile = canonical.get("mall_profile")
        mall_name = getattr(mall_profile, "name", mall_id) if mall_profile else mall_id

        for etype in ("stores", "dining", "cinemas"):
            for entity in canonical.get(etype, []):
                entity_name = getattr(entity, "name", "") or ""
                if query in entity_name.lower():
                    results.append({
                        "mall_id": mall_id,
                        "mall_name": mall_name,
                        "entity_type": etype,
                        "name": entity_name,
                        "floor": getattr(entity, "floor", "") or "",
                        "category": getattr(entity, "category", "") or "",
                        "is_home_mall": mall_id == home_mall_id,
                        "source": "cross_mall_search",
                    })

    results.sort(key=lambda r: (0 if r["is_home_mall"] else 1, r["mall_name"]))
    return results


def get_all_mall_canonical_for_guard() -> dict:
    """
    Return a merged canonical dict covering all loaded malls, in the sectioned
    format expected by the hallucination guard's _MallFactIndex.
    """
    sections = ("stores", "dining", "cinemas", "movies", "events", "offers", "services")
    merged: dict = {s: [] for s in sections}
    if _mall_registry is None:
        return merged
    for ctx in _mall_registry.values():
        guard_data = ctx.get_canonical_for_guard()
        for section in sections:
            merged[section].extend(guard_data.get(section, []))
    return merged


def get_session_store() -> AbstractSessionStore:
    if _session_store is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _session_store


def get_checkpointer() -> Any | None:
    """
    Return the active LangGraph checkpointer, or None if disabled.

    Pass the result to build_concierge_graph(checkpointer=...).
    When not None, callers must also pass a thread_id in the invoke config:
        config={"configurable": {"thread_id": "<session_id>:<turn_id>"}}
    """
    return _checkpointer


def get_feedback_service() -> FeedbackService:
    if _feedback_service is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _feedback_service


def get_implicit_detector() -> ImplicitFeedbackDetector:
    if _implicit_detector is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _implicit_detector


def get_feedback_normalizer() -> FeedbackNormalizer:
    if _feedback_normalizer is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _feedback_normalizer


def get_session_tuning_engine() -> SessionTuningEngine:
    if _session_tuning_engine is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _session_tuning_engine


async def shutdown() -> None:
    """Gracefully close any open connections (Redis, vector store, etc.)."""
    if _session_store is not None:
        close = getattr(_session_store, "close", None)
        if callable(close):
            await close()
            logger.info("Session store connection closed")

    if _mall_registry is not None and _mall_registry._redis is not None:
        try:
            await _mall_registry._redis.aclose()
            logger.info("Mall registry Redis (Tier 2) connection closed")
        except Exception:
            logger.debug("Error closing mall registry Redis client", exc_info=True)

    if _vector_store is not None:
        await _vector_store.close()
        logger.info("Vector store connection closed")


def is_initialized() -> bool:
    return _initialized
