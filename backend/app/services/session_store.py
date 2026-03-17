"""
In-memory session store — persists structured scene memory across turns.

Tracks only lightweight session metadata (last_intent, conversation_mode)
and structured scene memory. Raw LLM outputs are NEVER stored.

Single-process, single-mall session storage for the current phase.
Replace with Redis or database-backed store for production scaling.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.models.state import SceneMemory

logger = logging.getLogger(__name__)


class SessionData:
    """
    Mutable session record held in memory.

    Stores only structured state — never raw LLM outputs.
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
        }


class SessionStore:
    """Thread-safe in-memory session store."""

    def __init__(self, max_sessions: int = 1000):
        self._sessions: dict[str, SessionData] = {}
        self._max_sessions = max_sessions

    def get(self, session_id: str) -> SessionData | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, mall_id: str = "al_nakheel_plaza_28") -> SessionData:
        if session_id in self._sessions:
            return self._sessions[session_id]

        if len(self._sessions) >= self._max_sessions:
            self._evict_oldest()

        session = SessionData(session_id=session_id, mall_id=mall_id)
        self._sessions[session_id] = session
        logger.info("Created session %s for mall %s", session_id, mall_id)
        return session

    def save_turn(
        self,
        session_id: str,
        scene: SceneMemory,
        last_intent: str,
        conversation_mode: str,
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

    def reset(self, session_id: str) -> bool:
        if session_id in self._sessions:
            mall_id = self._sessions[session_id].mall_id
            self._sessions[session_id] = SessionData(
                session_id=session_id, mall_id=mall_id
            )
            logger.info("Reset session %s", session_id)
            return True
        return False

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def _evict_oldest(self) -> None:
        if not self._sessions:
            return
        oldest_id = min(self._sessions, key=lambda k: self._sessions[k].updated_at)
        del self._sessions[oldest_id]
        logger.info("Evicted oldest session %s", oldest_id)
