"""
Global runtime registry — holds initialized singletons for the pipeline.

Initialized once at application startup (main.py lifespan).
Graph nodes and services import getters to access shared resources
without threading references through state.

Usage in any node / service:
    from app.runtime import get_mall_context, get_session_store
    ctx = get_mall_context("al_nakheel_plaza_28")
    store = get_session_store()

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.context.mall_context import MallContextLoader
    from app.services.feedback_normalizer import FeedbackNormalizer
    from app.services.feedback_service import FeedbackService
    from app.services.implicit_feedback_detector import ImplicitFeedbackDetector
    from app.services.session_store import AbstractSessionStore
    from app.services.session_tuning_engine import SessionTuningEngine

logger = logging.getLogger(__name__)

_mall_contexts: dict[str, MallContextLoader] = {}
_session_store: AbstractSessionStore | None = None
_checkpointer: Any | None = None
_feedback_service: FeedbackService | None = None
_implicit_detector: ImplicitFeedbackDetector | None = None
_feedback_normalizer: FeedbackNormalizer | None = None
_session_tuning_engine: SessionTuningEngine | None = None
_initialized: bool = False


async def initialize(mall_ids: list[str]) -> None:
    """Load mall intelligence for each mall ID and prepare shared services."""
    global _mall_contexts, _session_store, _checkpointer, _initialized
    global _feedback_service, _implicit_detector, _feedback_normalizer, _session_tuning_engine

    from app.config.settings import get_settings
    from app.context.mall_context import MallContextLoader
    from app.services.feedback_normalizer import FeedbackNormalizer
    from app.services.feedback_service import FeedbackService
    from app.services.implicit_feedback_detector import ImplicitFeedbackDetector
    from app.services.session_tuning_engine import SessionTuningEngine

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

    # ── Mall contexts ─────────────────────────────────────────────────────
    for mall_id in mall_ids:
        ctx = MallContextLoader(mall_id)
        await ctx.load()
        _mall_contexts[mall_id] = ctx
        logger.info("Runtime initialized for mall %s", mall_id)

    _feedback_service = FeedbackService()
    _implicit_detector = ImplicitFeedbackDetector()
    _feedback_normalizer = FeedbackNormalizer()
    _session_tuning_engine = SessionTuningEngine()

    _initialized = True
    logger.info("Runtime ready — loaded %d mall(s): %s", len(_mall_contexts), list(_mall_contexts.keys()))


def get_mall_context(mall_id: str) -> MallContextLoader:
    if not _mall_contexts:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    if mall_id not in _mall_contexts:
        raise RuntimeError(
            f"No context loaded for mall_id={mall_id!r}. "
            f"Available malls: {list(_mall_contexts.keys())}"
        )
    return _mall_contexts[mall_id]


def get_loaded_mall_ids() -> list[str]:
    """Return the list of mall IDs currently loaded in the runtime."""
    return list(_mall_contexts.keys())


def search_brand_across_malls(
    brand_name: str,
    home_mall_id: str | None = None,
) -> list[dict]:
    """
    Search all loaded mall contexts for a brand/store by display name.

    Results are sorted so the home mall always appears first, making it
    easy for the LLM to say "Yes, it's here at your mall AND at X".

    Each entry contains:
        mall_id, mall_name, entity_type, name, floor, category,
        is_home_mall, source
    """
    if not _mall_contexts:
        return []

    results = []
    query = brand_name.strip().lower()

    for mall_id, ctx in _mall_contexts.items():
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

    # Home mall results first, then alphabetically by mall name
    results.sort(key=lambda r: (0 if r["is_home_mall"] else 1, r["mall_name"]))
    return results


def get_all_mall_canonical_for_guard() -> dict:
    """
    Return a merged canonical dict covering all loaded malls, in the sectioned
    format expected by the hallucination guard's _MallFactIndex.
    Used for cross-mall responses so the guard does not strip valid entity names
    that belong to malls other than the home mall.
    """
    sections = ("stores", "dining", "cinemas", "movies", "events", "offers", "services")
    merged: dict = {s: [] for s in sections}
    for ctx in _mall_contexts.values():
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
    """Gracefully close any open connections (Redis, etc.)."""
    if _session_store is not None:
        close = getattr(_session_store, "close", None)
        if callable(close):
            await close()
            logger.info("Session store connection closed")


def is_initialized() -> bool:
    return _initialized
