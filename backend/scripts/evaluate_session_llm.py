#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NOTE: Run this script using the backend virtualenv:
#   cd backend && source .venv/bin/activate && python3 scripts/evaluate_session_llm.py
"""
Option C — LLM-as-judge quality scoring for an exported session JSON.

Scores every assistant turn on five dimensions using GPT:
  intent_alignment       Did the response address the intent correctly?
  constraint_adherence   Were companions, budget, and scene constraints respected?
  honesty                No hallucinated stores/prices/facts?
  conciseness            Appropriate length and entity count?
  context_retention      Did the bot carry forward earlier conversation context?

Each dimension is scored 1–5. An overall_score (average) is computed.
A full annotated report is written to test-results/ as both markdown and JSON.

Usage
─────
    python scripts/evaluate_session_llm.py
    python scripts/evaluate_session_llm.py --session path/to/session.json
    python scripts/evaluate_session_llm.py --model gpt-4o
    python scripts/evaluate_session_llm.py --out test-results/

Requirements
────────────
    pip install openai
    BACKEND_OPENAI_API_KEY set in backend/.env (or env var OPENAI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Load .env from backend/ ───────────────────────────────────────────────────

def _load_env() -> str:
    """Read BACKEND_OPENAI_API_KEY and BACKEND_OPENAI_MODEL from .env."""
    script_dir = Path(__file__).resolve().parent
    env_path   = script_dir.parent / ".env"
    env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


_ENV = _load_env()

def _get_api_key() -> str:
    return (
        _ENV.get("BACKEND_OPENAI_API_KEY")
        or os.environ.get("BACKEND_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )

def _get_model(override: str | None) -> str:
    if override:
        return override
    return _ENV.get("BACKEND_OPENAI_MODEL") or "gpt-4.1-mini"


# ── Judge prompts ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an objective evaluator for a shopping mall concierge AI.
You will receive a single conversation turn with full context and the assistant's response.
Score the response on FIVE dimensions, each 1–5 (1=very poor, 5=excellent).

Return ONLY valid JSON in this exact format (no markdown fences):
{
  "intent_alignment": <1-5>,
  "constraint_adherence": <1-5>,
  "honesty": <1-5>,
  "conciseness": <1-5>,
  "context_retention": <1-5>,
  "overall_score": <float average of the five>,
  "strengths": "<one sentence>",
  "weaknesses": "<one sentence, or 'None' if none>",
  "verdict": "pass" | "needs_improvement" | "fail"
}

Scoring guide:
  intent_alignment     — Does the response directly address what the user asked?
  constraint_adherence — Are companions (kids, girlfriend), budget, and stated constraints respected?
  honesty              — Are all stores/facts grounded in context? No invented prices or availability?
  conciseness          — Is the response appropriately sized? Not bloated or too terse?
  context_retention    — Does the response correctly use context from earlier in the conversation?

Verdict rules:
  overall >= 4.0  → "pass"
  overall >= 3.0  → "needs_improvement"
  overall < 3.0   → "fail"
"""

USER_TEMPLATE = """=== CONVERSATION HISTORY (up to this turn) ===
{history}

=== CURRENT TURN ===
User: {user_message}

=== TURN METADATA ===
  intent_domain:    {intent_domain}
  intent_sub:       {intent_sub}
  message_kind:     {message_kind}
  response_mode:    {response_mode}
  confidence_level: {confidence_level}
  companions:       {companions}
  budget:           {budget}
  target_person:    {target_person}
  target_gender:    {target_gender}
  retrieval_needed: {retrieval_needed}
  retrieval_results:{retrieval_results}
  sources_count:    {sources_count}
  warnings:         {warnings}

=== ASSISTANT RESPONSE ===
{assistant_response}
"""


# ── OpenAI call ───────────────────────────────────────────────────────────────

def call_judge(
    system: str,
    user: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    """Call OpenAI chat completions with the judge prompt."""
    try:
        import openai
    except ImportError:
        print("ERROR: 'openai' package not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    raw = resp.choices[0].message.content.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.startswith("```")).strip()

    scores: dict[str, Any] = json.loads(raw)

    # Recompute overall from the 5 dimensions
    dims = [
        scores["intent_alignment"],
        scores["constraint_adherence"],
        scores["honesty"],
        scores["conciseness"],
        scores["context_retention"],
    ]
    scores["overall_score"] = round(sum(dims) / len(dims), 2)

    # Auto-verdict based on overall
    if scores["overall_score"] >= 4.0:
        scores["verdict"] = "pass"
    elif scores["overall_score"] >= 3.0:
        scores["verdict"] = "needs_improvement"
    else:
        scores["verdict"] = "fail"

    return scores


# ── Turn evaluator ────────────────────────────────────────────────────────────

def build_history_text(messages: list[dict], up_to_index: int) -> str:
    """Build a compact conversation history string up to (not including) up_to_index."""
    lines = []
    for m in messages[:up_to_index]:
        role = m.get("role", "")
        content = m.get("content", "")[:300]
        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            lines.append(f"Assistant: {content}")
    return "\n".join(lines) if lines else "(start of conversation)"


def evaluate_turn(
    turn_num: int,
    user_msg: str,
    assistant_msg: dict,
    messages: list[dict],
    user_msg_index: int,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    debug   = assistant_msg.get("debug", {})
    scene   = debug.get("scene_summary", {})
    shop    = scene.get("shopping_task", {})
    sources = assistant_msg.get("sources", [])

    history = build_history_text(messages, user_msg_index)

    user_content = USER_TEMPLATE.format(
        history          = history,
        user_message     = user_msg,
        intent_domain    = debug.get("intent_domain", ""),
        intent_sub       = debug.get("intent_sub", ""),
        message_kind     = debug.get("message_kind", ""),
        response_mode    = debug.get("response_mode", "") or "(smalltalk)",
        confidence_level = debug.get("confidence_level", ""),
        companions       = scene.get("companions", []),
        budget           = scene.get("budget", ""),
        target_person    = shop.get("target_person", ""),
        target_gender    = shop.get("target_gender", ""),
        retrieval_needed = debug.get("retrieval_needed", False),
        retrieval_results= debug.get("retrieval_results_count", 0),
        sources_count    = len(sources),
        warnings         = debug.get("warnings", []),
        assistant_response = assistant_msg.get("content", "")[:800],
    )

    t0 = time.perf_counter()
    scores = call_judge(SYSTEM_PROMPT, user_content, api_key, model)
    judge_latency_ms = round((time.perf_counter() - t0) * 1000)

    return {
        "turn":             turn_num,
        "user_message":     user_msg,
        "assistant_snippet": assistant_msg.get("content", "")[:120],
        "intent":           f"{debug.get('intent_domain')}/{debug.get('intent_sub')}",
        "response_mode":    debug.get("response_mode", "") or "(smalltalk)",
        "confidence_level": debug.get("confidence_level", ""),
        "scores":           scores,
        "judge_latency_ms": judge_latency_ms,
    }


# ── Report writer ─────────────────────────────────────────────────────────────

def _score_bar(score: float | int) -> str:
    """Simple visual bar for a 1–5 score."""
    filled = round(score)
    return "█" * filled + "░" * (5 - filled) + f" {score}/5"


def _verdict_icon(verdict: str) -> str:
    return {"pass": "✅", "needs_improvement": "🟡", "fail": "❌"}.get(verdict, "⚪")


def write_report(
    results: list[dict],
    session_meta: dict,
    model: str,
    out_dir: str,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    now   = datetime.now(timezone.utc)
    ts    = now.strftime("%Y-%m-%d_%H-%M-%S")
    human = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Aggregate scores
    all_scores = [r["scores"] for r in results if "scores" in r]
    dims = ["intent_alignment", "constraint_adherence", "honesty", "conciseness", "context_retention"]
    avg_by_dim: dict[str, float] = {}
    for d in dims:
        vals = [s[d] for s in all_scores if d in s]
        avg_by_dim[d] = round(sum(vals) / len(vals), 2) if vals else 0.0
    overall_avg = round(sum(s["overall_score"] for s in all_scores) / len(all_scores), 2) if all_scores else 0.0

    passing         = sum(1 for r in results if r.get("scores", {}).get("verdict") == "pass")
    needs_improve   = sum(1 for r in results if r.get("scores", {}).get("verdict") == "needs_improvement")
    failing         = sum(1 for r in results if r.get("scores", {}).get("verdict") == "fail")
    errors          = sum(1 for r in results if "error" in r)

    lines = [
        "# Session Quality Evaluation Report — LLM-as-Judge",
        "",
        f"**Generated:** {human}  ",
        f"**Session ID:** `{session_meta.get('session_id', '—')}`  ",
        f"**Mall ID:** `{session_meta.get('mall_id', '—')}`  ",
        f"**Judge model:** `{model}`  ",
        "",
        "## Overall Scores",
        "",
        "| Dimension | Avg Score | Bar |",
        "|-----------|-----------|-----|",
    ]
    for d in dims:
        lines.append(f"| `{d}` | {avg_by_dim[d]} | {_score_bar(avg_by_dim[d])} |")
    lines += [
        f"| **overall_score** | **{overall_avg}** | {_score_bar(overall_avg)} |",
        "",
        "## Verdict Summary",
        "",
        "| Verdict | Count |",
        "|---------|-------|",
        f"| ✅ Pass | {passing} |",
        f"| 🟡 Needs improvement | {needs_improve} |",
        f"| ❌ Fail | {failing} |",
        f"| 💥 Errors | {errors} |",
        "",
        "---",
        "",
        "## Turn-by-Turn Scores",
        "",
        "| Turn | User message | Overall | Intent | Constrt | Honesty | Concise | Context | Verdict |",
        "|------|-------------|---------|--------|---------|---------|---------|---------|---------|",
    ]

    for r in results:
        sc = r.get("scores", {})
        if "error" in r:
            lines.append(f"| {r['turn']} | `{r['user_message'][:40]}` | 💥 ERROR | — | — | — | — | — | — |")
            continue
        umsg = r["user_message"][:40].replace("|", "\\|")
        lines.append(
            f"| {r['turn']} | `{umsg}` "
            f"| **{sc.get('overall_score', '—')}** "
            f"| {sc.get('intent_alignment', '—')} "
            f"| {sc.get('constraint_adherence', '—')} "
            f"| {sc.get('honesty', '—')} "
            f"| {sc.get('conciseness', '—')} "
            f"| {sc.get('context_retention', '—')} "
            f"| {_verdict_icon(sc.get('verdict', ''))} {sc.get('verdict', '')} |"
        )

    lines += ["", "---", "", "## Detailed Turn Analysis", ""]

    for r in results:
        sc = r.get("scores", {})
        icon = _verdict_icon(sc.get("verdict", "")) if sc else "💥"
        lines += [
            f"### Turn {r['turn']} {icon}  `{r['user_message']}`",
            "",
            f"**Intent:** `{r.get('intent', '—')}` | **Mode:** `{r.get('response_mode', '—')}` | **Conf:** `{r.get('confidence_level', '—')}`  ",
            "",
        ]

        if "error" in r:
            lines += [f"**Error:** {r['error']}", "", "---", ""]
            continue

        lines += [
            "| Dimension | Score | Bar |",
            "|-----------|-------|-----|",
        ]
        for d in dims:
            lines.append(f"| `{d}` | {sc.get(d, '—')} | {_score_bar(sc.get(d, 0))} |")
        lines += [
            f"| **overall_score** | **{sc.get('overall_score', '—')}** | {_score_bar(sc.get('overall_score', 0))} |",
            "",
            f"**Strengths:** {sc.get('strengths', '—')}  ",
            f"**Weaknesses:** {sc.get('weaknesses', '—')}  ",
            "",
            f"**Assistant response:**  ",
            f"> {r['assistant_snippet']}",
            "",
            "---",
            "",
        ]

    md_path   = os.path.join(out_dir, f"llm_eval_{ts}.md")
    json_path = os.path.join(out_dir, f"llm_eval_{ts}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_out = {
        "generated_at": human,
        "session_meta": session_meta,
        "judge_model":  model,
        "summary": {
            "total": len(results),
            "passing": passing,
            "needs_improvement": needs_improve,
            "failing": failing,
            "errors": errors,
            "overall_avg": overall_avg,
            "avg_by_dimension": avg_by_dim,
        },
        "turns": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    print(f"\n  📄  Markdown report : {md_path}")
    print(f"  🗂   JSON data       : {json_path}")
    return md_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    script_dir  = Path(__file__).resolve().parent
    repo_root   = script_dir.parent.parent
    default_in  = repo_root / "test_scenario.json"
    default_out = repo_root / "test-results"

    parser = argparse.ArgumentParser(description="LLM-as-judge evaluation of a session JSON")
    parser.add_argument("--session", default=str(default_in))
    parser.add_argument("--model",   default=None, help="OpenAI model name")
    parser.add_argument("--out",     default=str(default_out))
    args = parser.parse_args()

    api_key = _get_api_key()
    if not api_key:
        print(
            "ERROR: No OpenAI API key found.\n"
            "Set BACKEND_OPENAI_API_KEY in backend/.env or export OPENAI_API_KEY.",
            file=sys.stderr,
        )
        sys.exit(1)

    model = _get_model(args.model)

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"ERROR: session file not found: {session_path}", file=sys.stderr)
        sys.exit(1)

    with open(session_path, encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    session_meta = {
        "session_id":     data.get("session_id"),
        "mall_id":        data.get("mall_id"),
        "exported_at":    data.get("exported_at"),
        "total_messages": len(messages),
    }

    print("\nCenomi Session Evaluator — LLM-as-Judge")
    print(f"Session  : {session_meta['session_id']}")
    print(f"Mall     : {session_meta['mall_id']}")
    print(f"Model    : {model}")
    print(f"Messages : {len(messages)}")
    print()

    results: list[dict] = []
    turn_num = 0
    i = 0

    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            user_msg = msg.get("content", "")
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                asst_msg = messages[i + 1]
                turn_num += 1
                print(f"  Evaluating turn {turn_num}: {user_msg[:55]!r} … ", end="", flush=True)

                try:
                    result = evaluate_turn(
                        turn_num, user_msg, asst_msg,
                        messages, i, api_key, model,
                    )
                    results.append(result)
                    sc = result["scores"]
                    icon = _verdict_icon(sc.get("verdict", ""))
                    print(f"{icon} overall={sc.get('overall_score')} ({result['judge_latency_ms']}ms)")

                except Exception as exc:
                    print(f"💥 ERROR: {exc}")
                    results.append({
                        "turn": turn_num, "user_message": user_msg,
                        "error": str(exc),
                    })

                i += 2
            else:
                i += 1
        else:
            i += 1

    # Summary
    all_sc = [r["scores"]["overall_score"] for r in results if "scores" in r]
    avg = round(sum(all_sc) / len(all_sc), 2) if all_sc else 0
    passing = sum(1 for r in results if r.get("scores", {}).get("verdict") == "pass")

    print(f"\n{'─'*60}")
    print(f"  Turns evaluated : {len(results)}")
    print(f"  Overall avg     : {avg}/5")
    print(f"  Passing (≥4.0)  : {passing}/{len(results)}")
    print()

    write_report(results, session_meta, model, args.out)


if __name__ == "__main__":
    main()
