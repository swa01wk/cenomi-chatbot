"""
Session management endpoints.

Provides session inspection and reset capabilities
for the concierge chat pipeline.
"""

from fastapi import APIRouter, HTTPException

from app.models.api import (
    SessionDetailResponse,
    SessionResetRequest,
    SessionResetResponse,
)
from app.runtime import get_session_store

router = APIRouter()


@router.post("/session/reset", response_model=SessionResetResponse)
async def reset_session(request: SessionResetRequest):
    store = get_session_store()
    was_reset = store.reset(request.session_id)

    if not was_reset:
        store.get_or_create(request.session_id, request.mall_id)

    return SessionResetResponse(
        session_id=request.session_id,
        status="reset",
    )


@router.get("/session/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str):
    store = get_session_store()
    session = store.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionDetailResponse(
        session_id=session.session_id,
        mall_id=session.mall_id,
        turn_count=session.turn_count,
        scene=session.scene.model_dump(),
        messages=[m.model_dump() for m in session.messages[-20:]],
    )
