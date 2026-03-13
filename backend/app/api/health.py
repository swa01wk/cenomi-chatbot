"""Health check endpoint."""

from fastapi import APIRouter

from app.config.settings import get_settings
from app.runtime import is_initialized

router = APIRouter()


@router.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "mall_id": settings.mall_id,
        "env": settings.env,
        "runtime_initialized": is_initialized(),
    }
