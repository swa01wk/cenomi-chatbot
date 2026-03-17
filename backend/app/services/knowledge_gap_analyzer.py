"""
Knowledge gap analyzer — identifies missing playbooks, tags, and entity enrichments.

Scans normalized feedback signals and explicit events to detect patterns
that suggest the semantic intelligence or playbook library is incomplete.

Detection heuristics:
  1. Frequent "missing_playbook" signals → recommend new playbook
  2. High "poor_recommendation_quality" on specific entity types → enrichment gap
  3. Repeated "incorrect_entity_type" → missing semantic tags
  4. User messages with no playbook match → candidate new scenarios
  5. Clusters of "not_relevant" + free-text mentioning unknown topics
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from app.models.feedback import (
    FeedbackEvent,
    FeedbackType,
    KnowledgeGap,
    KnowledgeGapReport,
    NormalizedFeedbackSignal,
    NormalizedSignalType,
)
from app.utils.ids import generate_feedback_id

logger = logging.getLogger(__name__)

_MIN_EVIDENCE = 3  # minimum occurrences before reporting a gap


class KnowledgeGapAnalyzer:
    """
    Analyzes accumulated feedback to surface gaps in the knowledge base.

    Call `analyze()` periodically (e.g., nightly batch, or after N events)
    to produce a KnowledgeGapReport.
    """

    def analyze(
        self,
        events: list[FeedbackEvent],
        signals: list[NormalizedFeedbackSignal],
        tenant_id: str = "al_nakheel_plaza_28",
        mall_id: str = "al_nakheel_plaza_28",
    ) -> KnowledgeGapReport:
        gaps: list[KnowledgeGap] = []

        gaps.extend(self._detect_missing_playbooks(events, signals))
        gaps.extend(self._detect_underperforming_entities(events, signals))
        gaps.extend(self._detect_missing_semantic_tags(events, signals))
        gaps.extend(self._detect_unresolved_topics(events))

        gaps.sort(key=lambda g: {"critical": 0, "high": 1, "medium": 2, "low": 3}[g.priority])

        report = KnowledgeGapReport(
            report_id=generate_feedback_id(),
            tenant_id=tenant_id,
            mall_id=mall_id,
            gaps=gaps,
            total_feedback_analyzed=len(events) + len(signals),
            generated_at=datetime.utcnow(),
        )

        logger.info(
            "Knowledge gap report: %d gaps from %d events + %d signals",
            len(gaps), len(events), len(signals),
        )
        return report

    # ── gap detection methods ────────────────────────────────────────

    def _detect_missing_playbooks(
        self,
        events: list[FeedbackEvent],
        signals: list[NormalizedFeedbackSignal],
    ) -> list[KnowledgeGap]:
        """Detect scenarios where no playbook matched but users had clear intent."""
        no_playbook_events = [e for e in events if not e.playbook_used and e.feedback_type == FeedbackType.THUMBS_DOWN]
        if len(no_playbook_events) < _MIN_EVIDENCE:
            return []

        topic_counter: Counter[str] = Counter()
        example_queries: dict[str, list[str]] = defaultdict(list)

        for e in no_playbook_events:
            topic = e.strategy_used or "unmatched"
            topic_counter[topic] += 1
            if len(example_queries[topic]) < 5:
                example_queries[topic].append(e.user_message[:200])

        gaps: list[KnowledgeGap] = []
        for topic, count in topic_counter.most_common():
            if count < _MIN_EVIDENCE:
                continue
            gaps.append(KnowledgeGap(
                gap_type="missing_playbook",
                description=f"No playbook matched for strategy '{topic}' — {count} negative events",
                evidence_count=count,
                example_queries=example_queries.get(topic, []),
                recommended_action=f"Create a new scenario playbook for '{topic}' conversations",
                priority="high" if count >= 10 else "medium",
            ))

        return gaps

    def _detect_underperforming_entities(
        self,
        events: list[FeedbackEvent],
        signals: list[NormalizedFeedbackSignal],
    ) -> list[KnowledgeGap]:
        """Find entity types that consistently get poor recommendation quality."""
        entity_neg: Counter[str] = Counter()
        entity_total: Counter[str] = Counter()

        for sig in signals:
            if sig.signal_type == NormalizedSignalType.POOR_RECOMMENDATION_QUALITY:
                for et in sig.entity_types_involved:
                    if et:
                        entity_neg[et] += 1

        for e in events:
            for ent in e.selected_entities:
                et = ent.get("entity_type", "")
                if et:
                    entity_total[et] += 1

        gaps: list[KnowledgeGap] = []
        for et, neg_count in entity_neg.most_common():
            total = entity_total.get(et, neg_count)
            neg_rate = neg_count / total if total else 0
            if neg_count >= _MIN_EVIDENCE and neg_rate > 0.3:
                gaps.append(KnowledgeGap(
                    gap_type="underperforming_entity_type",
                    description=(
                        f"Entity type '{et}' has {neg_rate:.0%} negative recommendation rate "
                        f"({neg_count}/{total})"
                    ),
                    evidence_count=neg_count,
                    recommended_action=f"Review and enrich semantic profiles for '{et}' entities",
                    priority="high" if neg_rate > 0.5 else "medium",
                ))

        return gaps

    def _detect_missing_semantic_tags(
        self,
        events: list[FeedbackEvent],
        signals: list[NormalizedFeedbackSignal],
    ) -> list[KnowledgeGap]:
        """Detect when users repeatedly ask about concepts without matching tags."""
        incorrect_entity_signals = [
            s for s in signals
            if s.signal_type == NormalizedSignalType.INCORRECT_ENTITY_TYPE
        ]
        if len(incorrect_entity_signals) < _MIN_EVIDENCE:
            return []

        return [KnowledgeGap(
            gap_type="missing_semantic_tag",
            description=(
                f"{len(incorrect_entity_signals)} incorrect-entity-type signals detected — "
                "semantic tag coverage may be insufficient"
            ),
            evidence_count=len(incorrect_entity_signals),
            recommended_action="Audit semantic enrichment rules and add missing tags",
            priority="medium",
        )]

    def _detect_unresolved_topics(
        self,
        events: list[FeedbackEvent],
    ) -> list[KnowledgeGap]:
        """Find user queries that repeatedly produce negative feedback."""
        negative_events = [e for e in events if e.feedback_type == FeedbackType.THUMBS_DOWN]
        if len(negative_events) < _MIN_EVIDENCE:
            return []

        keyword_counter: Counter[str] = Counter()
        for e in negative_events:
            words = set(e.user_message.lower().split())
            stop = {"i", "a", "the", "is", "in", "to", "for", "and", "or", "my", "me", "it", "do", "can", "what", "where", "how"}
            meaningful = words - stop
            for w in meaningful:
                if len(w) > 3:
                    keyword_counter[w] += 1

        gaps: list[KnowledgeGap] = []
        for keyword, count in keyword_counter.most_common(10):
            if count < _MIN_EVIDENCE:
                continue
            examples = [
                e.user_message[:200] for e in negative_events
                if keyword in e.user_message.lower()
            ][:5]

            gaps.append(KnowledgeGap(
                gap_type="frequent_unresolved_topic",
                description=f"Keyword '{keyword}' appears in {count} negative feedback events",
                evidence_count=count,
                example_queries=examples,
                recommended_action=f"Investigate whether '{keyword}' needs a dedicated context block or playbook",
                priority="low" if count < 5 else "medium",
            ))

        return gaps
