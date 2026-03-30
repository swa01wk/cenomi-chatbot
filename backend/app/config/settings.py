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
    # Separate model for intent classification, scene extraction, and routing
    # escape-hatch calls.  Defaults to gpt-4o-mini which is ~30x cheaper per
    # token than gpt-4o and accurate enough for structured classification tasks.
    # Set BACKEND_CLASSIFIER_MODEL in .env to override.
    classifier_model: str = "gpt-4o-mini"

    # --- Mall ---
    # Comma-separated list of mall IDs to load at startup.
    # Example: "al_nakheel_plaza_28,al_nakheel_plaza_13"
    mall_ids: str = "al_nakheel_plaza_28"

    def get_mall_id_list(self) -> list[str]:
        return [m.strip() for m in self.mall_ids.split(",") if m.strip()]

    # --- Retrieval ---
    vector_store_type: str = "chroma"
    embedding_model: str = "text-embedding-3-small"
    chroma_persist_dir: str = "data/chroma"
    pinecone_api_key: str = ""
    qdrant_url: str = ""

    # --- Multi-mall memory management ---
    # Max number of MallContextLoader objects kept in Python RAM simultaneously.
    # Least-recently-used malls are evicted when this limit is exceeded.
    # Evicted malls are restored from Redis (if available) or reloaded from disk.
    mall_cache_size: int = 5
    # TTL (seconds) for serialized MallContextLoader in Redis (Tier 2 cache).
    # Restoring an evicted mall from Redis is ~50-100× faster than disk + normalization.
    mall_ctx_redis_ttl: int = 3600
    # TTL (seconds) for vector search result cache in Redis.
    # Repeated identical queries skip the OpenAI embedding call entirely.
    vector_cache_ttl: int = 900

    # --- Redis ---
    # Set to a valid Redis URL (e.g. "redis://localhost:6379/0") to enable
    # Redis-backed session persistence. Empty string = in-memory LRU store.
    redis_url: str = ""
    # TTL in seconds for Redis session keys (default: 30 minutes).
    redis_session_ttl: int = 1800

    # --- LangGraph Checkpointer ---
    # When True, each graph turn is snapshot-persisted for replay/debugging.
    # Uses MemorySaver in dev; AsyncRedisSaver when redis_url is also set.
    enable_checkpointer: bool = False

    # --- Quality Evaluator ---
    # When True, an async LLM-as-judge scorer fires after every chat turn.
    # Results are written to backend/data/evaluations/. Never blocks responses.
    enable_evaluator: bool = False

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
