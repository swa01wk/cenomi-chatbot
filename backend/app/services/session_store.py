"""
Session store — persists structured scene memory across turns.

Tracks only lightweight session metadata (last_intent, conversation_mode)
and structured scene memory. Raw LLM outputs are NEVER stored.

Two implementations are provided:
  - SessionStore      — in-memory LRU dict (development / single-process)
  - RedisSessionStore — Redis-backed (production; requires redis[asyncio])

Both implement AbstractSessionStore and are interchangeable. Runtime selects
the appropriate class based on BACKEND_REDIS_URL in settings.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from app.models.state import SceneMemory

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared data container
# ─────────────────────────────────────────────────────────────────────────────


class SessionData:
    """
    Mutable session record.

    Stores structured state and the full raw conversation log so the LLM
    can see every prior exchange on each new turn.
    """

    __slots__ = (
        "session_id",
        "mall_id",
        "scene",
        "last_intent",
        "conversation_mode",
        "turn_count",
        "created_at",
        "updated_at",
        "conversation_history",
    )

    def __init__(self, session_id: str, mall_id: str = "al_nakheel_plaza_28"):
        self.session_id = session_id
        self.mall_id = mall_id
        self.scene = SceneMemory()
        self.last_intent: str = ""
        self.conversation_mode: str = ""
        self.turn_count: int = 0
        self.created_at: float = time.time()
        self.updated_at: float = self.created_at
        # Raw dialogue log: [{"role": "user"|"assistant", "content": str}, ...]
        # Accumulated across turns; passed to the LLM on every new turn so it
        # sees the full conversation, not just structured SceneMemory signals.
        self.conversation_history: list[dict] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mall_id": self.mall_id,
            "scene": self.scene.model_dump(),
            "last_intent": self.last_intent,
            "conversation_mode": self.conversation_mode,
            "turn_count": self.turn_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "conversation_history": self.conversation_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionData":
        obj = cls.__new__(cls)
        obj.session_id = data["session_id"]
        obj.mall_id = data.get("mall_id", "al_nakheel_plaza_28")
        obj.scene = SceneMemory(**data.get("scene", {}))
        obj.last_intent = data.get("last_intent", "")
        obj.conversation_mode = data.get("conversation_mode", "")
        obj.turn_count = data.get("turn_count", 0)
        obj.created_at = data.get("created_at", time.time())
        obj.updated_at = data.get("updated_at", time.time())
        obj.conversation_history = data.get("conversation_history", [])
        return obj


# ─────────────────────────────────────────────────────────────────────────────
# Abstract interface
# ─────────────────────────────────────────────────────────────────────────────


class AbstractSessionStore(ABC):
    """Common async interface for all session store implementations."""

    @abstractmethod
    async def get(self, session_id: str) -> SessionData | None: ...

    @abstractmethod
    async def get_or_create(self, session_id: str, mall_id: str = "al_nakheel_plaza_28") -> SessionData: ...

    @abstractmethod
    async def save_turn(
        self,
        session_id: str,
        scene: SceneMemory,
        last_intent: str,
        conversation_mode: str,
        user_message: str = "",
        assistant_message: str = "",
    ) -> None: ...

    @abstractmethod
    async def reset(self, session_id: str) -> bool: ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────
# In-memory implementation (default / development)
# ─────────────────────────────────────────────────────────────────────────────


class SessionStore(AbstractSessionStore):
    """Async-compatible in-memory session store with LRU eviction."""

    def __init__(self, max_sessions: int = 1000):
        self._sessions: dict[str, SessionData] = {}
        self._max_sessions = max_sessions

    async def get(self, session_id: str) -> SessionData | None:
        return self._sessions.get(session_id)

    async def get_or_create(self, session_id: str, mall_id: str = "al_nakheel_plaza_28") -> SessionData:
        if session_id in self._sessions:
            return self._sessions[session_id]

        if len(self._sessions) >= self._max_sessions:
            self._evict_oldest()

        session = SessionData(session_id=session_id, mall_id=mall_id)
        self._sessions[session_id] = session
        logger.info("Created session %s for mall %s", session_id, mall_id)
        return session

    async def save_turn(
        self,
        session_id: str,
        scene: SceneMemory,
        last_intent: str,
        conversation_mode: str,
        user_message: str = "",
        assistant_message: str = "",
    ) -> None:
        session = self._sessions.get(session_id)
        if not session:
            logger.warning("save_turn called for unknown session %s", session_id)
            return

        session.scene = scene
        session.last_intent = last_intent
        session.conversation_mode = conversation_mode
        session.turn_count += 1
        session.updated_at = time.time()
        if user_message and assistant_message:
            session.conversation_history.append({"role": "user", "content": user_message})
            session.conversation_history.append({"role": "assistant", "content": assistant_message})

    async def reset(self, session_id: str) -> bool:
        if session_id in self._sessions:
            mall_id = self._sessions[session_id].mall_id
            self._sessions[session_id] = SessionData(
                session_id=session_id, mall_id=mall_id
            )
            logger.info("Reset session %s", session_id)
            return True
        return False

    async def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def _evict_oldest(self) -> None:
        if not self._sessions:
            return
        oldest_id = min(self._sessions, key=lambda k: self._sessions[k].updated_at)
        del self._sessions[oldest_id]
        logger.info("Evicted oldest session %s", oldest_id)


# ─────────────────────────────────────────────────────────────────────────────
# Redis implementation (production)
# ─────────────────────────────────────────────────────────────────────────────

_REDIS_KEY_PREFIX = "cenomi:session:"


class RedisSessionStore(AbstractSessionStore):
    """
    Redis-backed async session store.

    Keys:   cenomi:session:{session_id}  → JSON-serialised SessionData
    TTL:    Configurable (default 1800 s = 30 minutes), refreshed on every save_turn.

    Requires:  redis[asyncio]>=5.0  (pip install 'redis[asyncio]')

    All public methods are async — call them with `await` from async contexts.
    This is safe inside FastAPI/uvicorn's event loop.
    """

    def __init__(self, redis_url: str, ttl: int = 1800):
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise ImportError(
                "redis[asyncio] is required for RedisSessionStore. "
                "Install it with: pip install 'redis[asyncio]'"
            ) from exc

        self._client = aioredis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl

    def _key(self, session_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{session_id}"

    async def get(self, session_id: str) -> SessionData | None:
        raw = await self._client.get(self._key(session_id))
        if raw is None:
            return None
        try:
            return SessionData.from_dict(json.loads(raw))
        except Exception:
            logger.warning("Failed to deserialise session %s from Redis", session_id, exc_info=True)
            return None

    async def get_or_create(
        self, session_id: str, mall_id: str = "al_nakheel_plaza_28"
    ) -> SessionData:
        existing = await self.get(session_id)
        if existing is not None:
            return existing

        session = SessionData(session_id=session_id, mall_id=mall_id)
        await self._client.set(
            self._key(session_id),
            json.dumps(session.to_dict()),
            ex=self._ttl,
        )
        logger.info("Created Redis session %s for mall %s", session_id, mall_id)
        return session

    async def save_turn(
        self,
        session_id: str,
        scene: SceneMemory,
        last_intent: str,
        conversation_mode: str,
        user_message: str = "",
        assistant_message: str = "",
    ) -> None:
        session = await self.get(session_id)
        if session is None:
            logger.warning("save_turn: Redis session %s not found; skipping", session_id)
            return

        session.scene = scene
        session.last_intent = last_intent
        session.conversation_mode = conversation_mode
        session.turn_count += 1
        session.updated_at = time.time()
        if user_message and assistant_message:
            session.conversation_history.append({"role": "user", "content": user_message})
            session.conversation_history.append({"role": "assistant", "content": assistant_message})

        await self._client.set(
            self._key(session_id),
            json.dumps(session.to_dict()),
            ex=self._ttl,
        )

    async def reset(self, session_id: str) -> bool:
        existing = await self.get(session_id)
        if existing is None:
            return False
        fresh = SessionData(session_id=session_id, mall_id=existing.mall_id)
        await self._client.set(
            self._key(session_id),
            json.dumps(fresh.to_dict()),
            ex=self._ttl,
        )
        logger.info("Reset Redis session %s", session_id)
        return True

    async def delete(self, session_id: str) -> bool:
        deleted = await self._client.delete(self._key(session_id))
        return deleted > 0

    async def close(self) -> None:
        """Close the Redis connection pool. Call during app shutdown."""
        await self._client.aclose()
