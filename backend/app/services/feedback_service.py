"""
Feedback service — orchestrates the full feedback lifecycle.

Responsibilities:
  1. Record explicit feedback events
  2. Delegate implicit detection (on every turn)
  3. Normalize all signals
  4. Trigger session tuning
  5. Accumulate for tenant-level aggregation
  6. Persist everything (JSON-file storage for current phase)

This is the single entry-point that the API and pipeline call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.feedback import (
    FeedbackEvent,
    FeedbackType,
    FeedbackReasonTag,
    ImplicitFeedbackEvent,
    NormalizedFeedbackSignal,
)
from app.utils.ids import generate_feedback_id

logger = logging.getLogger(__name__)

_FEEDBACK_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "feedback"
_EVENTS_DIR = _FEEDBACK_DIR / "events"
_IMPLICIT_DIR = _FEEDBACK_DIR / "implicit"
_NORMALIZED_DIR = _FEEDBACK_DIR / "normalized"


class FeedbackService:
    """
    Central orchestrator for the feedback loop.

    Stateless — all state is in the per-session stores and on-disk JSON.
    """

    def __init__(self) -> None:
        self._events: list[FeedbackEvent] = []
        self._implicit_events: list[ImplicitFeedbackEvent] = []
        self._normalized: list[NormalizedFeedbackSignal] = []

    # ── explicit feedback ────────────────────────────────────────────

    async def record_explicit(
        self,
        *,
        session_id: str,
        tenant_id: str = "cenomi_mall_01",
        mall_id: str = "cenomi_mall_01",
        turn_id: str = "",
        user_message: str = "",
        assistant_response: str = "",
        feedback_type: FeedbackType,
        feedback_reasons: list[str] | None = None,
        feedback_text: str = "",
        strategy_used: str = "",
        playbook_used: str = "",
        selected_entities: list[dict[str, Any]] | None = None,
        selected_context_blocks: list[str] | None = None,
    ) -> FeedbackEvent:
        """Record an explicit feedback event from the UI."""
        reasons = _parse_reasons(feedback_reasons or [])

        event = FeedbackEvent(
            feedback_id=generate_feedback_id(),
            session_id=session_id,
            tenant_id=tenant_id,
            mall_id=mall_id,
            turn_id=turn_id,
            user_message=user_message,
            assistant_response=assistant_response,
            feedback_type=feedback_type,
            feedback_reasons=reasons,
            feedback_text=feedback_text,
            strategy_used=strategy_used,
            playbook_used=playbook_used,
            selected_entities=selected_entities or [],
            selected_context_blocks=selected_context_blocks or [],
            timestamp=datetime.utcnow(),
        )

        self._events.append(event)
        await self._persist(event, _EVENTS_DIR, event.feedback_id)
        logger.info(
            "Recorded explicit feedback %s type=%s session=%s",
            event.feedback_id,
            event.feedback_type.value,
            session_id,
        )
        return event

    # ── implicit feedback ────────────────────────────────────────────

    async def record_implicit(self, event: ImplicitFeedbackEvent) -> None:
        """Store an implicit feedback event (produced by the detector)."""
        self._implicit_events.append(event)
        await self._persist(event, _IMPLICIT_DIR, event.event_id)
        logger.info(
            "Recorded implicit feedback %s signals=%s session=%s",
            event.event_id,
            [s.value for s in event.detected_signals],
            event.session_id,
        )

    # ── normalized signals ───────────────────────────────────────────

    async def record_normalized(self, signals: list[NormalizedFeedbackSignal]) -> None:
        """Store normalized feedback signals."""
        for sig in signals:
            self._normalized.append(sig)
            await self._persist(sig, _NORMALIZED_DIR, sig.signal_id)
        logger.info("Recorded %d normalized signals", len(signals))

    # ── queries ──────────────────────────────────────────────────────

    def get_session_events(self, session_id: str) -> list[FeedbackEvent]:
        return [e for e in self._events if e.session_id == session_id]

    def get_session_implicit(self, session_id: str) -> list[ImplicitFeedbackEvent]:
        return [e for e in self._implicit_events if e.session_id == session_id]

    def get_session_signals(self, session_id: str) -> list[NormalizedFeedbackSignal]:
        return [s for s in self._normalized if s.session_id == session_id]

    def get_all_events(self) -> list[FeedbackEvent]:
        return list(self._events)

    def get_all_normalized(self) -> list[NormalizedFeedbackSignal]:
        return list(self._normalized)

    # ── persistence ──────────────────────────────────────────────────

    async def _persist(self, obj: Any, directory: Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(obj.model_dump(), default=str, indent=2))


def _parse_reasons(raw: list[str]) -> list[FeedbackReasonTag]:
    """Best-effort conversion of free-form tag strings to enum values."""
    result: list[FeedbackReasonTag] = []
    lookup = {tag.value: tag for tag in FeedbackReasonTag}
    for r in raw:
        normalized = r.lower().replace(" ", "_").replace("-", "_")
        if normalized in lookup:
            result.append(lookup[normalized])
        else:
            for tag in FeedbackReasonTag:
                if normalized in tag.value or tag.value in normalized:
                    result.append(tag)
                    break
    return result
