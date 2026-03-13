"""
Observability logger — structured logging and trace capture.

Provides:
- Structured JSON logging for production
- Debug trace capture for the testing UI
- Pipeline turn summaries
"""

import json
import logging
from typing import Any

from app.config.settings import get_settings


def get_logger(name: str) -> logging.Logger:
    """Create a configured logger instance."""
    settings = get_settings()
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        level = getattr(logging, settings.log_level.upper(), logging.INFO)
        handler.setLevel(level)
        logger.setLevel(level)

        if settings.env == "production":
            formatter = logging.Formatter(
                json.dumps(
                    {
                        "time": "%(asctime)s",
                        "name": "%(name)s",
                        "level": "%(levelname)s",
                        "message": "%(message)s",
                    }
                )
            )
        else:
            formatter = logging.Formatter(
                "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
                datefmt="%H:%M:%S",
            )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


class DebugTracer:
    """Captures debug traces for a single conversation turn."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def trace(self, node: str, event: str, data: dict | None = None) -> None:
        self.events.append({"node": node, "event": event, "data": data or {}})

    def to_list(self) -> list[dict]:
        return self.events


def log_turn_summary(
    session_id: str,
    turn_id: str,
    intent_domain: str,
    strategy: str,
    playbook: str,
    latency_ms: float,
    warning_count: int,
) -> None:
    """Log a structured one-line summary of a completed turn."""
    logger = get_logger("concierge.turn")
    logger.info(
        "turn=%s session=%s intent=%s strategy=%s playbook=%s latency=%.1fms warnings=%d",
        turn_id,
        session_id,
        intent_domain,
        strategy,
        playbook or "none",
        latency_ms,
        warning_count,
    )
