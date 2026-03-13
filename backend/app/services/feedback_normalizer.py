"""
Feedback normalizer — converts explicit and implicit feedback into
canonical NormalizedFeedbackSignal records.

Signal classification rules:

  Explicit reason tag          →  Normalized signal(s)
  ─────────────────────────────────────────────────────
  not_relevant                 →  relevance_low
  too_generic                  →  specificity_low
  too_long                     →  verbosity_high
  wrong_assumption             →  incorrect_scene
  wanted_exact_details         →  specificity_low
  wrong_store_suggestion       →  poor_recommendation_quality, incorrect_entity_type
  wrong_dining_suggestion      →  poor_recommendation_quality, incorrect_entity_type
  tone_felt_robotic            →  tone_problem
  asked_too_many_questions     →  strategy_mismatch
  should_recommend_else        →  poor_recommendation_quality

  Implicit signal              →  Normalized signal(s)
  ─────────────────────────────────────────────────────
  incorrect_scene_inference    →  incorrect_scene
  wrong_context                →  incorrect_scope
  price_mismatch               →  poor_recommendation_quality
  audience_mismatch            →  incorrect_scene
  recommendation_scope_error   →  poor_recommendation_quality
  location_correction          →  incorrect_scope
  time_correction              →  incorrect_scope
  preference_override          →  strategy_mismatch
  comparison_request           →  specificity_low
  urgency_signal               →  strategy_mismatch
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.feedback import (
    FeedbackEvent,
    FeedbackReasonTag,
    FeedbackType,
    ImplicitFeedbackEvent,
    ImplicitSignalType,
    NormalizedFeedbackSignal,
    NormalizedSignalType,
)
from app.utils.ids import generate_feedback_id

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Mapping tables
# ═══════════════════════════════════════════════════════════════════════════


_EXPLICIT_REASON_MAP: dict[FeedbackReasonTag, list[tuple[NormalizedSignalType, float]]] = {
    FeedbackReasonTag.NOT_RELEVANT: [
        (NormalizedSignalType.RELEVANCE_LOW, 0.85),
    ],
    FeedbackReasonTag.TOO_GENERIC: [
        (NormalizedSignalType.SPECIFICITY_LOW, 0.80),
    ],
    FeedbackReasonTag.TOO_LONG: [
        (NormalizedSignalType.VERBOSITY_HIGH, 0.75),
    ],
    FeedbackReasonTag.WRONG_ASSUMPTION: [
        (NormalizedSignalType.INCORRECT_SCENE, 0.85),
    ],
    FeedbackReasonTag.WANTED_EXACT_DETAILS: [
        (NormalizedSignalType.SPECIFICITY_LOW, 0.70),
    ],
    FeedbackReasonTag.WRONG_STORE_SUGGESTION: [
        (NormalizedSignalType.POOR_RECOMMENDATION_QUALITY, 0.85),
        (NormalizedSignalType.INCORRECT_ENTITY_TYPE, 0.60),
    ],
    FeedbackReasonTag.WRONG_DINING_SUGGESTION: [
        (NormalizedSignalType.POOR_RECOMMENDATION_QUALITY, 0.85),
        (NormalizedSignalType.INCORRECT_ENTITY_TYPE, 0.60),
    ],
    FeedbackReasonTag.TONE_FELT_ROBOTIC: [
        (NormalizedSignalType.TONE_PROBLEM, 0.80),
    ],
    FeedbackReasonTag.ASKED_TOO_MANY_QUESTIONS: [
        (NormalizedSignalType.STRATEGY_MISMATCH, 0.80),
    ],
    FeedbackReasonTag.SHOULD_RECOMMEND_SOMETHING_ELSE: [
        (NormalizedSignalType.POOR_RECOMMENDATION_QUALITY, 0.75),
    ],
}


_IMPLICIT_SIGNAL_MAP: dict[ImplicitSignalType, list[tuple[NormalizedSignalType, float]]] = {
    ImplicitSignalType.INCORRECT_SCENE_INFERENCE: [
        (NormalizedSignalType.INCORRECT_SCENE, 0.80),
    ],
    ImplicitSignalType.WRONG_CONTEXT: [
        (NormalizedSignalType.INCORRECT_SCOPE, 0.75),
    ],
    ImplicitSignalType.PRICE_MISMATCH: [
        (NormalizedSignalType.POOR_RECOMMENDATION_QUALITY, 0.70),
    ],
    ImplicitSignalType.AUDIENCE_MISMATCH: [
        (NormalizedSignalType.INCORRECT_SCENE, 0.75),
    ],
    ImplicitSignalType.RECOMMENDATION_SCOPE_ERROR: [
        (NormalizedSignalType.POOR_RECOMMENDATION_QUALITY, 0.70),
    ],
    ImplicitSignalType.LOCATION_CORRECTION: [
        (NormalizedSignalType.INCORRECT_SCOPE, 0.70),
    ],
    ImplicitSignalType.TIME_CORRECTION: [
        (NormalizedSignalType.INCORRECT_SCOPE, 0.65),
    ],
    ImplicitSignalType.PREFERENCE_OVERRIDE: [
        (NormalizedSignalType.STRATEGY_MISMATCH, 0.55),
    ],
    ImplicitSignalType.COMPARISON_REQUEST: [
        (NormalizedSignalType.SPECIFICITY_LOW, 0.50),
    ],
    ImplicitSignalType.URGENCY_SIGNAL: [
        (NormalizedSignalType.STRATEGY_MISMATCH, 0.50),
    ],
}


class FeedbackNormalizer:
    """
    Converts explicit and implicit feedback into normalized signals.

    One input event may produce zero, one, or many normalized signals.
    """

    def normalize_explicit(self, event: FeedbackEvent) -> list[NormalizedFeedbackSignal]:
        """Produce normalized signals from an explicit feedback event."""
        if event.feedback_type == FeedbackType.THUMBS_UP:
            return []

        signals: list[NormalizedFeedbackSignal] = []
        seen: set[NormalizedSignalType] = set()

        for reason in event.feedback_reasons:
            mappings = _EXPLICIT_REASON_MAP.get(reason, [])
            for signal_type, severity in mappings:
                if signal_type in seen:
                    continue
                seen.add(signal_type)
                signals.append(self._build_signal(
                    source_event_id=event.feedback_id,
                    source_type="explicit",
                    session_id=event.session_id,
                    tenant_id=event.tenant_id,
                    mall_id=event.mall_id,
                    signal_type=signal_type,
                    severity=severity,
                    strategy_used=event.strategy_used,
                    playbook_used=event.playbook_used,
                    entity_types=[e.get("entity_type", "") for e in event.selected_entities],
                ))

        if not signals and event.feedback_type == FeedbackType.THUMBS_DOWN:
            signals.append(self._build_signal(
                source_event_id=event.feedback_id,
                source_type="explicit",
                session_id=event.session_id,
                tenant_id=event.tenant_id,
                mall_id=event.mall_id,
                signal_type=NormalizedSignalType.RELEVANCE_LOW,
                severity=0.50,
                strategy_used=event.strategy_used,
                playbook_used=event.playbook_used,
            ))

        return signals

    def normalize_implicit(self, event: ImplicitFeedbackEvent) -> list[NormalizedFeedbackSignal]:
        """Produce normalized signals from an implicit feedback event."""
        signals: list[NormalizedFeedbackSignal] = []
        seen: set[NormalizedSignalType] = set()

        for imp_signal in event.detected_signals:
            mappings = _IMPLICIT_SIGNAL_MAP.get(imp_signal, [])
            for signal_type, base_severity in mappings:
                if signal_type in seen:
                    continue
                seen.add(signal_type)
                severity = min(1.0, base_severity * event.confidence)
                signals.append(self._build_signal(
                    source_event_id=event.event_id,
                    source_type="implicit",
                    session_id=event.session_id,
                    tenant_id=event.tenant_id,
                    mall_id=event.mall_id,
                    signal_type=signal_type,
                    severity=severity,
                ))

        return signals

    def _build_signal(
        self,
        *,
        source_event_id: str,
        source_type: str,
        session_id: str,
        tenant_id: str,
        mall_id: str,
        signal_type: NormalizedSignalType,
        severity: float,
        strategy_used: str = "",
        playbook_used: str = "",
        entity_types: list[str] | None = None,
        semantic_tags: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> NormalizedFeedbackSignal:
        return NormalizedFeedbackSignal(
            signal_id=generate_feedback_id(),
            source_event_id=source_event_id,
            source_type=source_type,
            session_id=session_id,
            tenant_id=tenant_id,
            mall_id=mall_id,
            signal_type=signal_type,
            severity=severity,
            strategy_used=strategy_used,
            playbook_used=playbook_used,
            entity_types_involved=entity_types or [],
            semantic_tags_involved=semantic_tags or [],
            context=context or {},
        )
