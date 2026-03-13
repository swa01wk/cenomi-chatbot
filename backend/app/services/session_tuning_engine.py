"""
Session tuning engine — adapts pipeline behavior within a single conversation.

When the user gives negative feedback or the implicit detector fires,
this engine computes parameter adjustments that apply for the remainder
of the session.  These adjustments are session-scoped overrides on
top of the tenant config.

Design constraints:
  - Only SESSION-mutability parameters can be changed
  - Adjustments are additive — each feedback event nudges values
  - Safe boundaries are enforced (never exceed Pydantic field limits)
  - Adjustments are logged for observability

Tuning rules (signal → parameter adjustments):

  specificity_low     →  +entity_specificity_bias, +exact_name_usage_bias
  verbosity_high      →  response_length = "brief", −recommendation_density
  incorrect_scene     →  −assumption_aggressiveness, +clarification sensitivity
  incorrect_scope     →  −assumption_aggressiveness
  strategy_mismatch   →  +answer_first_bias, −max_followups
  tone_problem        →  +warmth, −formality
  poor_recommendation →  +favor_diversity_vs_confidence
  relevance_low       →  +prioritize_current_topic
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.models.feedback import (
    NormalizedFeedbackSignal,
    NormalizedSignalType,
    SessionTuningAdjustment,
    SessionTuningSnapshot,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Tuning rules — maps signal types to parameter nudges
# ═══════════════════════════════════════════════════════════════════════════

_NUDGE = 0.10  # default increment for float params

_TUNING_RULES: dict[NormalizedSignalType, list[dict[str, Any]]] = {
    NormalizedSignalType.SPECIFICITY_LOW: [
        {"group": "response_shape", "param": "default_shortlist_size", "action": "set", "value": 3},
        {"group": "response_shape", "param": "paragraph_vs_bullets", "action": "set", "value": "bullets"},
        {"group": "response_shape", "param": "recommendation_density", "action": "increase", "delta": _NUDGE},
    ],
    NormalizedSignalType.VERBOSITY_HIGH: [
        {"group": "response_shape", "param": "default_response_length", "action": "set", "value": "brief"},
        {"group": "response_shape", "param": "suggest_next_step", "action": "set", "value": False},
    ],
    NormalizedSignalType.INCORRECT_SCENE: [
        {"group": "clarification", "param": "refinement_bias", "action": "increase", "delta": _NUDGE},
    ],
    NormalizedSignalType.INCORRECT_SCOPE: [
        {"group": "session_adaptation", "param": "preserve_current_area_strongly", "action": "increase", "delta": _NUDGE},
        {"group": "session_adaptation", "param": "preserve_active_topic_strongly", "action": "increase", "delta": _NUDGE},
    ],
    NormalizedSignalType.STRATEGY_MISMATCH: [
        {"group": "response_shape", "param": "suggest_next_step", "action": "set", "value": True},
    ],
    NormalizedSignalType.TONE_PROBLEM: [
        {"group": "tone", "param": "practicalness_vs_exploratory", "action": "decrease", "delta": _NUDGE},
    ],
    NormalizedSignalType.POOR_RECOMMENDATION_QUALITY: [
        {"group": "ranking", "param": "favor_diversity_vs_confidence", "action": "increase", "delta": 0.15},
    ],
    NormalizedSignalType.RELEVANCE_LOW: [
        {"group": "response_shape", "param": "default_shortlist_size", "action": "increase_int", "delta": 1},
    ],
    NormalizedSignalType.INCORRECT_ENTITY_TYPE: [
        {"group": "ranking", "param": "favor_diversity_vs_confidence", "action": "increase", "delta": _NUDGE},
    ],
    NormalizedSignalType.MISSING_PLAYBOOK: [],
}


class SessionTuningEngine:
    """
    Maintains per-session parameter overrides driven by feedback signals.

    The engine keeps a SessionTuningSnapshot per session_id. Each new
    signal nudges the overrides.  The pipeline reads `.get_overrides()`
    to merge into the active tenant config.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, SessionTuningSnapshot] = {}

    def apply_signals(
        self,
        session_id: str,
        mall_id: str,
        signals: list[NormalizedFeedbackSignal],
    ) -> SessionTuningSnapshot:
        """Apply normalized signals to produce or update session overrides."""
        snapshot = self._snapshots.get(session_id)
        if snapshot is None:
            snapshot = SessionTuningSnapshot(session_id=session_id, mall_id=mall_id)
            self._snapshots[session_id] = snapshot

        for signal in signals:
            rules = _TUNING_RULES.get(signal.signal_type, [])
            for rule in rules:
                adj = self._apply_rule(snapshot, signal, rule)
                if adj:
                    snapshot.adjustment_log.append(adj)

        snapshot.updated_at = datetime.utcnow()
        return snapshot

    def get_overrides(self, session_id: str) -> dict[str, dict[str, Any]]:
        """Return the current session overrides dict (ready for apply_session_overrides)."""
        snapshot = self._snapshots.get(session_id)
        if snapshot is None:
            return {}
        return deepcopy(snapshot.active_overrides)

    def get_snapshot(self, session_id: str) -> SessionTuningSnapshot | None:
        return self._snapshots.get(session_id)

    def reset(self, session_id: str) -> None:
        self._snapshots.pop(session_id, None)

    # ── internal ─────────────────────────────────────────────────────

    def _apply_rule(
        self,
        snapshot: SessionTuningSnapshot,
        signal: NormalizedFeedbackSignal,
        rule: dict[str, Any],
    ) -> SessionTuningAdjustment | None:
        group = rule["group"]
        param = rule["param"]
        action = rule["action"]

        overrides = snapshot.active_overrides
        if group not in overrides:
            overrides[group] = {}

        old_value = overrides[group].get(param)

        if action == "set":
            new_value = rule["value"]
        elif action == "increase":
            base = old_value if isinstance(old_value, (int, float)) else 0.5
            new_value = min(1.0, base + rule["delta"] * signal.severity)
        elif action == "decrease":
            base = old_value if isinstance(old_value, (int, float)) else 0.5
            new_value = max(0.0, base - rule["delta"] * signal.severity)
        elif action == "increase_int":
            base = old_value if isinstance(old_value, int) else 3
            new_value = min(10, base + rule["delta"])
        else:
            return None

        if old_value == new_value:
            return None

        overrides[group][param] = new_value

        logger.debug(
            "Session tuning: %s.%s %s → %s (signal=%s, severity=%.2f)",
            group, param, old_value, new_value,
            signal.signal_type.value, signal.severity,
        )

        return SessionTuningAdjustment(
            trigger_signal=signal.signal_type,
            trigger_reason=f"{signal.source_type}:{signal.source_event_id}",
            group=group,
            param=param,
            old_value=old_value,
            new_value=new_value,
        )
