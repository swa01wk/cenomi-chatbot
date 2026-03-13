"""
Node execution tracing — automatic latency + output tracking for graph nodes.

Usage:
    @traced_node("my_node")
    async def my_node(state: ConciergeState) -> dict:
        ...
        return {"field": value, "_trace_summary": "what happened"}

Nodes may include two special keys in their return dict:
  _trace_summary  — short human-readable description of the outcome
  _trace_warnings — list[str] of non-fatal issues encountered

Both are consumed by the decorator and removed before the dict
reaches LangGraph's state merge.
"""

from __future__ import annotations

import functools
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from app.models.state import NodeTraceEntry


def traced_node(name: str):
    """Decorator that wraps an async graph node with tracing instrumentation."""

    def decorator(fn: Callable[..., Coroutine[Any, Any, dict]]) -> Callable[..., Coroutine[Any, Any, dict]]:
        @functools.wraps(fn)
        async def wrapper(state: Any) -> dict:
            t0 = time.perf_counter()
            started = datetime.now(timezone.utc).isoformat()

            try:
                result = await fn(state)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                return {
                    "node_trace": [
                        NodeTraceEntry(
                            node=name,
                            started_at=started,
                            latency_ms=round(elapsed_ms, 2),
                            summary=f"FAILED: {exc}",
                            warnings=[str(exc)],
                        )
                    ],
                    "warnings": [f"[{name}] {exc}"],
                }

            elapsed_ms = (time.perf_counter() - t0) * 1000
            trace_summary = result.pop("_trace_summary", f"{name} completed")
            trace_warnings: list[str] = result.pop("_trace_warnings", [])

            result.setdefault("node_trace", [])
            result["node_trace"].append(
                NodeTraceEntry(
                    node=name,
                    started_at=started,
                    latency_ms=round(elapsed_ms, 2),
                    summary=trace_summary,
                    output_keys=[k for k in result if k != "node_trace"],
                    warnings=trace_warnings,
                )
            )

            if trace_warnings:
                result.setdefault("warnings", [])
                result["warnings"].extend(f"[{name}] {w}" for w in trace_warnings)

            return result

        return wrapper

    return decorator
