"""Smoke tests for the chat endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.api import ChatResponse


@pytest.mark.asyncio
async def test_chat_returns_response():
    fake_response = ChatResponse(session_id="test-session", message="Hi there!")

    with patch(
        "app.api.chat.handle_chat", new_callable=AsyncMock, return_value=fake_response
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/chat", json={"message": "Hello"})

    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert data["message"] == "Hi there!"
    assert "session_id" in data
    assert data["session_id"] == "test-session"
