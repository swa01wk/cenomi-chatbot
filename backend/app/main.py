"""
Cenomi Mall Concierge — FastAPI Application Entrypoint
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.api.session import router as session_router
from app.api.stream import router as stream_router
from app.config.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    from app import runtime

    mall_ids = settings.get_mall_id_list()
    await runtime.initialize(mall_ids)
    print(
        f"[startup] Cenomi Concierge ready — "
        f"malls={mall_ids} env={settings.env}"
    )
    yield
    await runtime.shutdown()
    print("[shutdown] Cenomi Concierge shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cenomi Mall Concierge",
        version="0.1.0",
        description="AI-powered multi-mall concierge platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api", tags=["health"])
    app.include_router(chat_router, prefix="/api", tags=["chat"])
    app.include_router(stream_router, prefix="/api", tags=["chat"])
    app.include_router(session_router, prefix="/api", tags=["session"])
    app.include_router(feedback_router, prefix="/api", tags=["feedback"])

    return app


app = create_app()
