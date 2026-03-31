"""Smoke tests for the API health endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # v1.6: single mall_id replaced by mall_ids list (all 5 configured malls)
    assert "mall_ids" in data
    assert isinstance(data["mall_ids"], list)
    assert len(data["mall_ids"]) >= 1
