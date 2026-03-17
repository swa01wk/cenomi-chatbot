"""
Application settings loaded from environment variables.

All configuration flows through this single module.
Tenant-specific overrides are handled separately via TenantConfig models.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Server ---
    env: str = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "debug"
    frontend_origin: str = "http://localhost:5173"

    # --- LLM ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_temperature: float = 0.3

    # --- Mall ---
    mall_id: str = "al_nakheel_plaza_28"
    mall_name: str = "Al Nakheel Plaza"

    # --- Retrieval ---
    vector_store_type: str = "chroma"
    embedding_model: str = "text-embedding-3-small"

    # --- Observability ---
    enable_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "cenomi-concierge"

    # --- Feedback ---
    feedback_storage: str = "local"

    model_config = {"env_prefix": "BACKEND_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
