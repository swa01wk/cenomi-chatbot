"""
Feedback endpoints — captures ratings, returns session feedback,
tenant summaries, and playbook performance.

Endpoints:
  POST /feedback                          — submit explicit feedback
  GET  /feedback/session/{session_id}     — all feedback for a session
  GET  /feedback/tenant-summary           — aggregated tenant metrics
  GET  /feedback/playbook-performance     — playbook-level breakdown
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.api import (
    FeedbackRequest,
    FeedbackResponse,
    PlaybookPerformanceResponse,
    SessionFeedbackResponse,
    TenantSummaryResponse,
)
from app.models.feedback import FeedbackType
from app.runtime import get_feedback_service, get_feedback_normalizer, get_session_tuning_engine

router = APIRouter()


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """
    Record explicit user feedback and trigger the normalization + session tuning pipeline.

    Flow:
      1. Store explicit event
      2. Normalize into signals
      3. Apply session tuning
    """
    svc = get_feedback_service()
    normalizer = get_feedback_normalizer()
    tuning_engine = get_session_tuning_engine()

    fb_type = (
        FeedbackType.THUMBS_UP
        if request.feedback_type == "thumbs_up"
        else FeedbackType.THUMBS_DOWN
    )

    event = await svc.record_explicit(
        session_id=request.session_id,
        tenant_id=request.tenant_id,
        mall_id=request.mall_id,
        turn_id=request.turn_id,
        user_message=request.user_message,
        assistant_response=request.assistant_response,
        feedback_type=fb_type,
        feedback_reasons=request.feedback_reasons,
        feedback_text=request.feedback_text,
        strategy_used=request.strategy_used,
        playbook_used=request.playbook_used,
        selected_entities=request.selected_entities,
        selected_context_blocks=request.selected_context_blocks,
    )

    signals = normalizer.normalize_explicit(event)
    await svc.record_normalized(signals)

    tuning_applied = False
    if signals:
        tuning_engine.apply_signals(request.session_id, request.mall_id, signals)
        tuning_applied = True

    return FeedbackResponse(
        feedback_id=event.feedback_id,
        status="recorded",
        session_tuning_applied=tuning_applied,
        normalized_signal_count=len(signals),
    )


@router.get("/feedback/session/{session_id}", response_model=SessionFeedbackResponse)
async def get_session_feedback(session_id: str):
    """Return all feedback data for a given session."""
    svc = get_feedback_service()
    tuning_engine = get_session_tuning_engine()

    explicit = svc.get_session_events(session_id)
    implicit = svc.get_session_implicit(session_id)
    normalized = svc.get_session_signals(session_id)
    snapshot = tuning_engine.get_snapshot(session_id)

    return SessionFeedbackResponse(
        session_id=session_id,
        explicit_events=[e.model_dump(mode="json") for e in explicit],
        implicit_events=[e.model_dump(mode="json") for e in implicit],
        normalized_signals=[s.model_dump(mode="json") for s in normalized],
        session_tuning=snapshot.model_dump(mode="json") if snapshot else {},
    )


@router.get("/feedback/tenant-summary", response_model=TenantSummaryResponse)
async def get_tenant_summary(
    tenant_id: str = "al_nakheel_plaza_28",
    mall_id: str = "al_nakheel_plaza_28",
):
    """Return aggregated feedback metrics and recommended adjustments."""
    from app.services.tenant_parameter_tuner import TenantParameterTuner

    svc = get_feedback_service()
    tuner = TenantParameterTuner()

    events = svc.get_all_events()
    signals = svc.get_all_normalized()

    aggregate = tuner.aggregate(events, signals, tenant_id, mall_id)
    adjustments = tuner.compute_adjustments(aggregate, signals)

    return TenantSummaryResponse(
        tenant_id=tenant_id,
        mall_id=mall_id,
        total_events=aggregate.total_events,
        overall_approval_rate=aggregate.overall_approval_rate,
        buckets=[b.model_dump(mode="json") for b in aggregate.buckets],
        recommended_adjustments=adjustments,
    )


@router.get("/feedback/playbook-performance", response_model=PlaybookPerformanceResponse)
async def get_playbook_performance(
    tenant_id: str = "al_nakheel_plaza_28",
    mall_id: str = "al_nakheel_plaza_28",
):
    """Return playbook-level performance breakdown and knowledge gaps."""
    from app.services.knowledge_gap_analyzer import KnowledgeGapAnalyzer
    from app.services.tenant_parameter_tuner import TenantParameterTuner

    svc = get_feedback_service()
    tuner = TenantParameterTuner()
    gap_analyzer = KnowledgeGapAnalyzer()

    events = svc.get_all_events()
    signals = svc.get_all_normalized()

    aggregate = tuner.aggregate(events, signals, tenant_id, mall_id)
    playbook_buckets = [
        b.model_dump(mode="json")
        for b in aggregate.buckets
        if b.dimension == "playbook_used"
    ]

    gap_report = gap_analyzer.analyze(events, signals, tenant_id, mall_id)

    return PlaybookPerformanceResponse(
        playbook_buckets=playbook_buckets,
        knowledge_gaps=[g.model_dump(mode="json") for g in gap_report.gaps],
    )
