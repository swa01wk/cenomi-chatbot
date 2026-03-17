"""Health check endpoint."""

from fastapi import APIRouter

from app.config.settings import get_settings
from app.runtime import get_loaded_mall_ids, is_initialized

router = APIRouter()


@router.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "mall_ids": get_loaded_mall_ids() if is_initialized() else settings.get_mall_id_list(),
        "env": settings.env,
        "runtime_initialized": is_initialized(),
    }
