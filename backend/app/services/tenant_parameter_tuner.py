"""
Tenant parameter tuner — adjusts tenant config based on aggregated feedback.

Unlike session tuning (ephemeral, per-conversation), tenant tuning produces
persistent changes to the mall's TenantConfig.  These changes are applied
at the TENANT mutability tier.

Aggregation → Tuning flow:
  1. Aggregate normalized signals across sessions (by strategy, playbook, etc.)
  2. Identify patterns that exceed threshold (e.g., >30% negative on a strategy)
  3. Compute safe parameter adjustments
  4. Apply via tenant_params.apply_feedback_update()

Safe boundaries:
  - Float params: clamp to [0.0, 1.0] or [0.0, 5.0] per Pydantic field
  - Int params: clamp to field min/max
  - String params: only allowed values
  - Max adjustment per cycle: ±0.15 for floats, ±2 for ints
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from app.models.feedback import (
    AggregationBucket,
    FeedbackEvent,
    FeedbackType,
    NormalizedFeedbackSignal,
    NormalizedSignalType,
    TenantFeedbackAggregate,
)
from app.models.tenant import Mutability, TenantConfig
from app.services.tenant_params import apply_feedback_update
from app.utils.ids import generate_feedback_id

logger = logging.getLogger(__name__)

_MAX_FLOAT_DELTA = 0.15
_NEGATIVE_THRESHOLD = 0.30  # trigger tuning when >30% negative in a bucket


# ═══════════════════════════════════════════════════════════════════════════
# Tuning rules — map dominant signal types to parameter adjustments
# ═══════════════════════════════════════════════════════════════════════════


_TENANT_TUNING_RULES: dict[NormalizedSignalType, list[dict[str, Any]]] = {
    NormalizedSignalType.SPECIFICITY_LOW: [
        {"group": "context_weights", "param": "prioritize_exact_entity_examples", "delta": +0.10},
        {"group": "context_weights", "param": "prioritize_semantic_tags", "delta": +0.08},
    ],
    NormalizedSignalType.VERBOSITY_HIGH: [
        {"group": "response_shape", "param": "recommendation_density", "delta": +0.10},
        {"group": "response_shape", "param": "location_detail_level", "set_value": "floor_only"},
    ],
    NormalizedSignalType.STRATEGY_MISMATCH: [
        {"group": "clarification", "param": "answer_first_bias", "delta": +0.08},
        {"group": "clarification", "param": "ambiguity_tolerance", "delta": +0.05},
    ],
    NormalizedSignalType.POOR_RECOMMENDATION_QUALITY: [
        {"group": "ranking", "param": "favor_diversity_vs_confidence", "delta": +0.10},
        {"group": "ranking", "param": "favor_anchor_entities", "delta": -0.05},
    ],
    NormalizedSignalType.INCORRECT_SCENE: [
        {"group": "clarification", "param": "assumption_aggressiveness", "delta": -0.10},
        {"group": "clarification", "param": "correction_sensitivity", "delta": +0.08},
    ],
    NormalizedSignalType.TONE_PROBLEM: [
        {"group": "tone", "param": "warmth", "delta": +0.10},
        {"group": "tone", "param": "directness", "delta": +0.05},
    ],
    NormalizedSignalType.RELEVANCE_LOW: [
        {"group": "context_weights", "param": "prioritize_current_topic", "delta": +0.10},
    ],
}


class TenantParameterTuner:
    """
    Aggregates feedback across sessions and tunes tenant parameters.
    """

    # ── aggregation ──────────────────────────────────────────────────

    def aggregate(
        self,
        events: list[FeedbackEvent],
        signals: list[NormalizedFeedbackSignal],
        tenant_id: str = "al_nakheel_plaza_28",
        mall_id: str = "al_nakheel_plaza_28",
    ) -> TenantFeedbackAggregate:
        """
        Build a TenantFeedbackAggregate from raw events and normalized signals.

        Slices by: strategy_used, playbook_used, signal_type, entity_type.
        """
        total = len(events)
        up = sum(1 for e in events if e.feedback_type == FeedbackType.THUMBS_UP)
        down = total - up
        approval = up / total if total else 0.0

        buckets: list[AggregationBucket] = []

        # by strategy
        buckets.extend(self._bucket_by(events, signals, "strategy_used"))
        # by playbook
        buckets.extend(self._bucket_by(events, signals, "playbook_used"))
        # by signal type
        buckets.extend(self._bucket_by_signal(signals))

        return TenantFeedbackAggregate(
            tenant_id=tenant_id,
            mall_id=mall_id,
            total_events=total,
            overall_approval_rate=round(approval, 3),
            buckets=buckets,
            generated_at=datetime.utcnow(),
        )

    # ── tuning ───────────────────────────────────────────────────────

    def compute_adjustments(
        self,
        aggregate: TenantFeedbackAggregate,
        signals: list[NormalizedFeedbackSignal],
    ) -> list[dict[str, Any]]:
        """
        Analyze aggregate and return a list of recommended parameter changes.

        Each change: {"group": ..., "param": ..., "value": ..., "reason": ...}
        """
        signal_counts: Counter[NormalizedSignalType] = Counter()
        for s in signals:
            signal_counts[s.signal_type] += 1

        total = len(signals) or 1
        adjustments: list[dict[str, Any]] = []

        for signal_type, count in signal_counts.most_common():
            ratio = count / total
            if ratio < _NEGATIVE_THRESHOLD:
                continue

            rules = _TENANT_TUNING_RULES.get(signal_type, [])
            for rule in rules:
                if "delta" in rule:
                    scaled_delta = min(_MAX_FLOAT_DELTA, rule["delta"] * (ratio / _NEGATIVE_THRESHOLD))
                    adjustments.append({
                        "group": rule["group"],
                        "param": rule["param"],
                        "delta": round(scaled_delta, 4),
                        "reason": f"{signal_type.value} at {ratio:.0%} ({count}/{total})",
                    })
                elif "set_value" in rule:
                    adjustments.append({
                        "group": rule["group"],
                        "param": rule["param"],
                        "value": rule["set_value"],
                        "reason": f"{signal_type.value} at {ratio:.0%} ({count}/{total})",
                    })

        return adjustments

    def apply_adjustments(
        self,
        config: TenantConfig,
        adjustments: list[dict[str, Any]],
    ) -> TenantConfig:
        """
        Apply computed adjustments to a TenantConfig.

        Uses apply_feedback_update() which respects mutability tiers.
        """
        patched = config
        for adj in adjustments:
            group = adj["group"]
            param = adj["param"]

            if "value" in adj:
                new_value = adj["value"]
            elif "delta" in adj:
                current = config.param_value(group, param)
                if isinstance(current, (int, float)):
                    new_value = round(current + adj["delta"], 4)
                    new_value = max(0.0, min(1.0 if isinstance(current, float) else 10, new_value))
                else:
                    continue
            else:
                continue

            patched = apply_feedback_update(
                patched, group, param, new_value,
                caller_tier=Mutability.TENANT,
            )
            logger.info(
                "Tenant tuning: %s.%s → %s (%s)",
                group, param, new_value, adj.get("reason", ""),
            )

        return patched

    # ── helpers ───────────────────────────────────────────────────────

    def _bucket_by(
        self,
        events: list[FeedbackEvent],
        signals: list[NormalizedFeedbackSignal],
        field: str,
    ) -> list[AggregationBucket]:
        groups: dict[str, list[FeedbackEvent]] = defaultdict(list)
        for e in events:
            val = getattr(e, field, "") or "unknown"
            groups[val].append(e)

        signal_by_event: dict[str, list[NormalizedFeedbackSignal]] = defaultdict(list)
        for s in signals:
            signal_by_event[s.source_event_id].append(s)

        buckets: list[AggregationBucket] = []
        for val, group_events in groups.items():
            up = sum(1 for e in group_events if e.feedback_type == FeedbackType.THUMBS_UP)
            total = len(group_events)
            down = total - up

            neg_reasons: Counter[str] = Counter()
            sig_types: Counter[str] = Counter()
            for e in group_events:
                for r in e.feedback_reasons:
                    neg_reasons[r.value] += 1
                for s in signal_by_event.get(e.feedback_id, []):
                    sig_types[s.signal_type.value] += 1

            buckets.append(AggregationBucket(
                dimension=field,
                dimension_value=val,
                total_feedback=total,
                thumbs_up=up,
                thumbs_down=down,
                approval_rate=round(up / total, 3) if total else 0.0,
                top_negative_reasons=[r for r, _ in neg_reasons.most_common(5)],
                top_signals=[s for s, _ in sig_types.most_common(5)],
                sample_size=total,
            ))

        return buckets

    def _bucket_by_signal(
        self,
        signals: list[NormalizedFeedbackSignal],
    ) -> list[AggregationBucket]:
        groups: dict[str, list[NormalizedFeedbackSignal]] = defaultdict(list)
        for s in signals:
            groups[s.signal_type.value].append(s)

        buckets: list[AggregationBucket] = []
        for sig_type, group_signals in groups.items():
            buckets.append(AggregationBucket(
                dimension="signal_type",
                dimension_value=sig_type,
                total_feedback=len(group_signals),
                thumbs_up=0,
                thumbs_down=len(group_signals),
                approval_rate=0.0,
                top_negative_reasons=[],
                top_signals=[sig_type],
                sample_size=len(group_signals),
            ))
        return buckets
