"""
Chat endpoint — primary concierge interface.

Accepts user messages, runs the LangGraph concierge pipeline,
and returns the assistant response along with debug metadata.
"""

from fastapi import APIRouter, HTTPException

from app.models.api import ChatRequest, ChatResponse
from app.services.concierge import handle_chat

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        return await handle_chat(request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
