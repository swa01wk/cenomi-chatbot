"""
Async quality evaluator — LLM-as-judge scoring for completed chat turns.

Fires as a fire-and-forget background task after every chat turn when
BACKEND_ENABLE_EVALUATOR=true. Never blocks the user-facing response.

Scoring dimensions
──────────────────
  intent_alignment      Did the response address the classified intent?
  constraint_adherence  Were scene constraints (budget, pace, companions) respected?
  honesty               Did the response avoid hallucinating offers/prices/facts?
  conciseness           Was the entity count within cap and the response compact?

Each dimension is scored 1–5. Scores are averaged into an overall_score.

Output
──────
Results are written to:
    backend/data/evaluations/{session_id}-{turn_id}.json

The format mirrors the existing feedback records so the same tooling
(knowledge_gap_analyzer, tenant_parameter_tuner) can ingest them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EVAL_DIR = Path(__file__).resolve().parents[2] / "data" / "evaluations"

_JUDGE_SYSTEM_PROMPT = """You are an objective evaluator for a mall concierge AI assistant.
You will be given a structured context about a single conversation turn and the assistant's response.
Score the response on four dimensions, each from 1 (very poor) to 5 (excellent).

Return ONLY valid JSON in this exact format:
{
  "intent_alignment": <1-5>,
  "constraint_adherence": <1-5>,
  "honesty": <1-5>,
  "conciseness": <1-5>,
  "overall_score": <float average>,
  "notes": "<one sentence summary>"
}"""

_JUDGE_USER_TEMPLATE = """Turn context:
- intent_domain: {intent_domain}
- message_kind: {message_kind}
- strategy_used: {strategy_used}
- response_mode: {response_mode}
- scene_companions: {companions}
- scene_budget: {budget}
- scene_constraints: {constraints}
- entity_cap: {entity_cap}
- entities_returned: {entities_count}
- retrieval_used: {retrieval_used}
- warnings: {warnings}

Assistant response:
{response_text}

Scoring guide:
- intent_alignment (1-5): Does the response directly address the intent ({intent_domain}/{message_kind})?
- constraint_adherence (1-5): Are all scene constraints respected? (budget={budget}, constraints={constraints})
- honesty (1-5): Are there any invented prices, offers, or store names not grounded in context?
- conciseness (1-5): Is the entity count ({entities_count}/{entity_cap} cap) and response length appropriate?"""


def _build_judge_user_message(
    evaluator_stub: dict[str, Any],
    response_text: str,
) -> str:
    scene = evaluator_stub.get("scene_summary", {})
    return _JUDGE_USER_TEMPLATE.format(
        intent_domain=evaluator_stub.get("intent_domain", ""),
        message_kind=evaluator_stub.get("message_kind", ""),
        strategy_used=evaluator_stub.get("strategy_used", ""),
        response_mode=evaluator_stub.get("response_mode", ""),
        companions=scene.get("companions", []),
        budget=scene.get("budget", ""),
        constraints=scene.get("visit_constraints", []),
        entity_cap=evaluator_stub.get("entity_cap", 5),
        entities_count=evaluator_stub.get("entities_count", 0),
        retrieval_used=evaluator_stub.get("retrieval_used", False),
        warnings=evaluator_stub.get("warning_count", 0),
        response_text=response_text[:1200],  # cap to avoid token overflow
    )


async def evaluate_turn(
    evaluator_stub: dict[str, Any],
    response_text: str,
    session_id: str,
    turn_id: str,
) -> None:
    """
    Score a completed turn asynchronously using an LLM-as-judge.

    This function is designed to be run via asyncio.create_task() and
    therefore must never raise — all errors are logged and suppressed.
    """
    t0 = time.perf_counter()
    try:
        scores = await _run_judge(evaluator_stub, response_text)
        _persist(scores, evaluator_stub, session_id, turn_id, t0)
    except Exception:
        logger.warning("Quality evaluator failed for turn %s", turn_id, exc_info=True)


async def _run_judge(
    evaluator_stub: dict[str, Any],
    response_text: str,
) -> dict[str, Any]:
    """Call the LLM judge and parse the JSON scores."""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.config.settings import get_settings
    settings = get_settings()

    llm = ChatOpenAI(
        model=settings.openai_model,
        temperature=0.0,
        api_key=settings.openai_api_key,
    )

    user_content = _build_judge_user_message(evaluator_stub, response_text)
    messages = [
        SystemMessage(content=_JUDGE_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    response = await llm.ainvoke(messages)
    raw_text: str = response.content if hasattr(response, "content") else str(response)

    # Extract JSON from the response (handles markdown code fences if present)
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        lines = json_text.splitlines()
        json_text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    scores: dict[str, Any] = json.loads(json_text)

    # Validate expected keys
    for key in ("intent_alignment", "constraint_adherence", "honesty", "conciseness"):
        if key not in scores:
            raise ValueError(f"Judge response missing key: {key!r}")

    # Recompute overall_score from dimensions in case judge mis-calculated
    dims = [scores["intent_alignment"], scores["constraint_adherence"],
            scores["honesty"], scores["conciseness"]]
    scores["overall_score"] = round(sum(dims) / len(dims), 2)

    return scores


def _persist(
    scores: dict[str, Any],
    evaluator_stub: dict[str, Any],
    session_id: str,
    turn_id: str,
    t0: float,
) -> None:
    """Write evaluation record to disk."""
    _EVAL_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "session_id": session_id,
        "turn_id": turn_id,
        "mall_id": evaluator_stub.get("mall_id", ""),
        "evaluated_at": time.time(),
        "evaluation_latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        "scores": scores,
        # Lightweight context snapshot for offline analysis
        "context": {
            "intent_domain": evaluator_stub.get("intent_domain", ""),
            "message_kind": evaluator_stub.get("message_kind", ""),
            "strategy_used": evaluator_stub.get("strategy_used", ""),
            "response_mode": evaluator_stub.get("response_mode", ""),
            "playbook_used": evaluator_stub.get("playbook_used", ""),
            "flow_type": evaluator_stub.get("flow_type", ""),
            "entity_cap": evaluator_stub.get("entity_cap", 5),
            "entities_count": evaluator_stub.get("entities_count", 0),
            "retrieval_used": evaluator_stub.get("retrieval_used", False),
            "total_latency_ms": evaluator_stub.get("total_latency_ms", 0),
            "warning_count": evaluator_stub.get("warning_count", 0),
        },
    }

    filename = f"{session_id}-{turn_id}.json"
    out_path = _EVAL_DIR / filename
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    logger.info(
        "Quality eval saved — turn=%s score=%.2f path=%s",
        turn_id,
        scores.get("overall_score", 0.0),
        out_path,
    )
