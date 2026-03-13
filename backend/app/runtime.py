"""
Global runtime registry — holds initialized singletons for the pipeline.

Initialized once at application startup (main.py lifespan).
Graph nodes and services import getters to access shared resources
without threading references through state.

Usage in any node / service:
    from app.runtime import get_mall_context, get_session_store
    ctx = get_mall_context()
    store = get_session_store()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.context.mall_context import MallContextLoader
    from app.services.feedback_normalizer import FeedbackNormalizer
    from app.services.feedback_service import FeedbackService
    from app.services.implicit_feedback_detector import ImplicitFeedbackDetector
    from app.services.session_store import SessionStore
    from app.services.session_tuning_engine import SessionTuningEngine

logger = logging.getLogger(__name__)

_mall_context: MallContextLoader | None = None
_session_store: SessionStore | None = None
_feedback_service: FeedbackService | None = None
_implicit_detector: ImplicitFeedbackDetector | None = None
_feedback_normalizer: FeedbackNormalizer | None = None
_session_tuning_engine: SessionTuningEngine | None = None
_initialized: bool = False


async def initialize(mall_id: str) -> None:
    """Load all mall intelligence and prepare the session store."""
    global _mall_context, _session_store, _initialized
    global _feedback_service, _implicit_detector, _feedback_normalizer, _session_tuning_engine

    from app.context.mall_context import MallContextLoader
    from app.services.feedback_normalizer import FeedbackNormalizer
    from app.services.feedback_service import FeedbackService
    from app.services.implicit_feedback_detector import ImplicitFeedbackDetector
    from app.services.session_store import SessionStore
    from app.services.session_tuning_engine import SessionTuningEngine

    _session_store = SessionStore()

    _mall_context = MallContextLoader(mall_id)
    await _mall_context.load()

    _feedback_service = FeedbackService()
    _implicit_detector = ImplicitFeedbackDetector()
    _feedback_normalizer = FeedbackNormalizer()
    _session_tuning_engine = SessionTuningEngine()

    _initialized = True
    logger.info("Runtime initialized for mall %s", mall_id)


def get_mall_context() -> MallContextLoader:
    if _mall_context is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _mall_context


def get_session_store() -> SessionStore:
    if _session_store is None:
        raise RuntimeError("Runtime not initialized — call runtime.initialize() first")
    return _session_store


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


def is_initialized() -> bool:
    return _initialized
