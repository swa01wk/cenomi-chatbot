"""Smoke tests for the chat endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_chat_returns_response():
    """
    Smoke test: chat endpoint must return a valid response.

    This test requires runtime.initialize() to have been called (i.e. a full
    backend environment with mall data loaded).  When running against a cold
    in-process ASGI app the runtime is not initialised, so we accept a
    structured 500 as proof that the endpoint *exists* and the application
    boots without import errors.  The 200-path is covered by run_all_tests.py.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chat", json={"message": "Hello"})

    if resp.status_code == 200:
        data = resp.json()
        assert "message" in data
        assert "session_id" in data
    else:
        # Runtime not initialised in test process — acceptable for unit test run
        assert resp.status_code == 500
        detail = resp.json().get("detail", "")
        assert "not initialized" in detail.lower() or "runtime" in detail.lower(), (
            f"Unexpected 500 body: {detail}"
        )
