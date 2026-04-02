#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Node Pipeline Evaluation — all test queries.

Combines the query runner (run_test_queries.py) with the LLM structural
pipeline auditor (audit_session.py) into a single end-to-end evaluation.

For every test query this script:
  1. Sends the query to the backend with debug=True
  2. Checks the simple response_mode + confidence_level match
  3. Runs the full 13-check LLM pipeline audit on each turn

Usage
─────
    # backend must be running first:
    python scripts/run_pipeline_evaluation.py
    python scripts/run_pipeline_evaluation.py --model gpt-4.1-mini
    python scripts/run_pipeline_evaluation.py --out test-results/
    python scripts/run_pipeline_evaluation.py --suite smoke     # smoke tests only
    python scripts/run_pipeline_evaluation.py --suite scenario  # scenario tests only
    python scripts/run_pipeline_evaluation.py --suite all       # default

Requirements
────────────
    pip install openai httpx
    BACKEND_OPENAI_API_KEY set in backend/.env (or env var OPENAI_API_KEY)
    Backend running at http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── resolve backend/ so we can import audit helpers ──────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ── Load .env ─────────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    env_path = _BACKEND_DIR / ".env"
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


# ── Test definitions ──────────────────────────────────────────────────────────

SMOKE_TESTS: list[tuple[str, str, str, str, str]] = [
    ("S-01",  "smoke-1",  "what movies are showing?",         "direct_factual",          "high"),
    ("S-02",  "smoke-2",  "i am here with my kids",           "context_acknowledgement", "high"),
    ("S-03",  "smoke-2",  "where can we eat?",                "guided_recommendation",   "high"),
    ("S-04",  "smoke-4",  "food and movies",                  "hybrid_plan",             "medium"),
    ("S-05",  "smoke-5",  "anything interesting here?",       "best_effort_shortlist",   "medium"),
    ("S-06",  "smoke-6",  "where is the prayer room?",        "direct_factual",          "high"),
    ("S-07",  "smoke-7",  "i am a bridesmaid",                "context_acknowledgement", "high"),
    ("S-08",  "smoke-8",  "something nice for my son",        "guided_recommendation",   "high"),
    ("S-09",  "smoke-9",  "asdf",                             "graceful_recovery",       "low"),
    ("S-10a", "smoke-1",  "with kid",                         "direct_factual",          "high"),
]

SCENARIO_TESTS: list[tuple[str, str, str, str, str]] = [
    # Scenario 1 — Movie Showtimes
    ("1.1", "sc1", "what movies are showing?",              "direct_factual",          "high"),
    ("1.2", "sc1", "show me movies",                        "guided_recommendation",   "high"),
    ("1.4", "sc1", "now showing",                           "direct_factual",          "high"),
    ("1.5", "sc1", "what's playing at the cinema?",         "direct_factual",          "high"),
    ("1.6", "sc1", "any kids movies today?",                "direct_factual",          "high"),

    # Scenario 2 — Family Day Out
    ("2.1",  "sc2",  "i am here with my family",            "context_acknowledgement", "high"),
    ("2.2",  "sc2b", "i am here with my kids",              "context_acknowledgement", "high"),
    ("2.3",  "sc2",  "where can we eat?",                   "guided_recommendation",   "high"),
    ("2.4",  "sc2",  "any activities for the kids?",        "guided_recommendation",   "high"),
    ("2.5",  "sc2",  "what movies are there?",              "direct_factual",          "high"),
    ("2.7",  "sc2",  "something quick for lunch",           "guided_recommendation",   "high"),

    # Scenario 3 — Gift Shopping
    ("3.1",  "sc3",  "i want to buy a gift",                "guided_recommendation",   "high"),
    ("3.2",  "sc3b", "i want to buy jackets",               "guided_recommendation",   "medium"),
    ("3.3",  "sc3b", "for my 5 year old son",               "guided_recommendation",   "high"),
    ("3.5",  "sc3b", "something affordable",                "guided_recommendation",   "medium"),
    ("3.6",  "sc3c", "gift for my girlfriend",              "guided_recommendation",   "high"),

    # Scenario 4 — Date Night
    ("4.1",  "sc4",  "i am here with my girlfriend",        "context_acknowledgement", "high"),
    ("4.2",  "sc4",  "suggest a nice dinner",               "guided_recommendation",   "high"),
    ("4.3",  "sc4b", "we want to catch a movie and then eat","hybrid_plan",            "high"),
    ("4.6",  "sc4c", "any romantic options here?",          "guided_recommendation",   "high"),

    # Scenario 5 — Quick Visit
    ("5.1",  "sc5",  "something quick to eat",              "guided_recommendation",   "high"),
    ("5.2",  "sc5b", "we are in a hurry",                   "context_acknowledgement", "high"),
    ("5.4",  "sc5c", "something quick before the movie",    "hybrid_plan",             "high"),

    # Scenario 6 — Cross-Intent: Movies + Food
    ("6.1",  "sc6",  "food and movies",                     "hybrid_plan",             "medium"),
    ("6.3",  "sc6b", "where can we eat after the movie?",   "hybrid_plan",             "high"),
    ("6.5",  "sc6c", "we want to watch a movie and grab dinner","hybrid_plan",         "high"),

    # Scenario 7 — Vague Exploration
    ("7.1",  "sc7",  "anything interesting here?",          "best_effort_shortlist",   "medium"),
    ("7.3",  "sc7",  "i'm bored",                           "best_effort_shortlist",   "medium"),
    ("7.4",  "sc7b", "it's my first time here",             "context_acknowledgement", "high"),
    ("7.7",  "sc7c", "surprise me",                         "best_effort_shortlist",   "medium"),

    # Scenario 8 — Broken Input
    ("8.1",  "sc8",  "asdf",                                "graceful_recovery",       "low"),
    ("8.3",  "sc8b", "what's the weather like?",            "graceful_recovery",       "low"),
    ("8.4",  "sc8c", "tell me a joke",                      "graceful_recovery",       "low"),
    ("8.6",  "sc8d", "a",                                   "graceful_recovery",       "low"),

    # Scenario 9 — Store Location
    ("9.1",  "sc9",  "where is Zara?",                      "direct_factual",          "high"),
    ("9.2",  "sc9b", "where is the prayer room?",           "direct_factual",          "high"),
    ("9.4",  "sc9c", "what are the mall opening hours?",    "direct_factual",          "high"),
    ("9.7",  "sc9d", "is Starbucks here?",                  "direct_factual",          "high"),

    # Scenario 10 — Constraint Refinement
    ("10.1", "sc10", "suggest some restaurants",            "guided_recommendation",   "high"),
    ("10.2", "sc10", "something cheaper",                   "guided_recommendation",   "medium"),
    ("10.6", "sc10b","suggest some stores for fashion",     "guided_recommendation",   "high"),
    ("10.7", "sc10b","something more affordable",           "guided_recommendation",   "medium"),

    # Scenario 11 — Wedding / Bridesmaid
    ("11.1", "sc11", "i am a bridesmaid",                   "context_acknowledgement", "high"),
    ("11.2", "sc11b","i am a bridesmaid shopping for the wedding","context_acknowledgement","high"),
    ("11.3", "sc11", "i need something elegant",            "guided_recommendation",   "high"),
    ("11.6", "sc11c","i am here for a wedding — i am the groom","context_acknowledgement","high"),

    # Scenario 12 — Brand Presence
    ("12.1", "sc12", "do you have H&M?",                    "direct_factual",          "high"),
    ("12.2", "sc12b","is Nike here?",                       "direct_factual",          "high"),

    # Scenario 13 — Mall Overview
    ("13.1", "sc13", "tell me about the mall",              "direct_factual",          "high"),
    ("13.2", "sc13b","what does this mall have?",           "direct_factual",          "high"),
    ("13.3", "sc13c","is this mall family friendly?",       "direct_factual",          "high"),
]

BASE_URL = "http://127.0.0.1:8000/api/chat"
TIMEOUT  = 45
_RUN_ID  = uuid.uuid4().hex[:8]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def send_query(session_id: str, message: str) -> dict[str, Any]:
    try:
        import httpx
    except ImportError:
        print("ERROR: 'httpx' package not installed. Run: pip install httpx", file=sys.stderr)
        sys.exit(1)

    payload = {"message": message, "session_id": session_id, "debug": True}
    resp = httpx.post(BASE_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── Pipeline audit integration ────────────────────────────────────────────────

def _import_audit_turn():
    """Import audit_turn from audit_session without running main()."""
    try:
        from scripts.audit_session import audit_turn  # type: ignore[import]
        return audit_turn
    except ImportError:
        # Fallback: direct path import
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "audit_session",
            _SCRIPT_DIR / "audit_session.py",
        )
        mod = importlib.util.load_from_spec(spec)  # type: ignore[attr-defined]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.audit_turn


def _load_audit_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "audit_session",
        _SCRIPT_DIR / "audit_session.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── Main evaluation loop ──────────────────────────────────────────────────────

def run_evaluation(
    tests: list[tuple[str, str, str, str, str]],
    label: str,
    api_key: str,
    model: str,
    audit_mod: Any,
) -> list[dict[str, Any]]:
    """
    Send all test queries, collect debug payloads, run pipeline audit per turn.

    Returns a list of rich result dicts, one per query.
    """
    results: list[dict[str, Any]] = []

    print(f"\n{'='*72}")
    print(f"  {label}  ({len(tests)} queries)")
    print(f"{'='*72}")

    turn_num = 0
    for tid, session_key, query, exp_mode, exp_conf in tests:
        unique_sid = f"{session_key}-{_RUN_ID}"
        turn_num += 1

        # ── 1. Send query ────────────────────────────────────────────────────
        try:
            t0 = time.time()
            data = send_query(unique_sid, query)
            elapsed_ms = round((time.time() - t0) * 1000)
        except Exception as exc:
            print(f"\n💥 [{tid}] {query!r}")
            print(f"   HTTP ERROR: {exc}")
            results.append({
                "id": tid, "session_key": session_key,
                "query": query,
                "expected_mode": exp_mode, "expected_conf": exp_conf,
                "http_status": "error", "http_error": str(exc),
                "pipeline_status": "skipped",
            })
            continue

        reply    = data.get("message", data.get("reply", ""))
        debug    = data.get("debug") or {}
        sources  = data.get("sources") or []

        actual_mode = debug.get("response_mode", "—")
        actual_conf = debug.get("confidence_level", "—")
        mode_ok     = actual_mode == exp_mode
        conf_ok     = actual_conf == exp_conf
        http_status = "pass" if (mode_ok and conf_ok) else "fail"

        mode_icon = "✓" if mode_ok else "✗"
        conf_icon = "✓" if conf_ok else "✗"
        print(f"\n[{tid}] {query!r}")
        print(f"   Mode: {actual_mode!r:35s} (expected {exp_mode!r}) {mode_icon}")
        print(f"   Conf: {actual_conf!r:35s} (expected {exp_conf!r}) {conf_icon}")
        print(f"   Flow: {debug.get('flow_type','—')} / playbook={debug.get('selected_playbook','—')}")
        print(f"   Nodes: {debug.get('node_count',0)} | Latency: {debug.get('total_latency_ms','—')}ms")
        print(f"   Pipeline audit … ", end="", flush=True)

        # ── 2. Run 13-check pipeline audit ──────────────────────────────────
        assistant_msg = {
            "content": reply,
            "debug":   debug,
            "sources": sources,
        }
        try:
            audit_result = audit_mod.audit_turn(
                turn_index=turn_num,
                user_msg=query,
                assistant_msg=assistant_msg,
                api_key=api_key,
                model=model,
            )
            n_issues = len(audit_result.get("issues", []))
            n_errors   = sum(1 for i in audit_result.get("issues", []) if i["severity"] == "error")
            n_warnings = sum(1 for i in audit_result.get("issues", []) if i["severity"] == "warning")
            audit_icon = "✅" if audit_result.get("pass") else "❌"
            print(f"{audit_icon} ({n_errors} err, {n_warnings} warn)")
            for iss in audit_result.get("issues", []):
                sev_icons = {"error": "🔴", "warning": "🟡", "info": "🔵"}
                print(f"     {sev_icons.get(iss['severity'],'⚪')} [{iss['check']}] {iss['message'][:80]}")
        except Exception as exc:
            print(f"💥 AUDIT ERROR: {exc}")
            audit_result = {
                "turn_index": turn_num,
                "user_message": query,
                "assistant_snippet": reply[:120],
                "intent": "",
                "intent_confidence": None,
                "message_kind": None,
                "response_mode": actual_mode,
                "confidence_level": actual_conf,
                "flow_type": debug.get("flow_type"),
                "chosen_strategy": debug.get("chosen_strategy"),
                "entity_count": len(debug.get("selected_entities", [])),
                "excluded_domains": debug.get("scene_summary", {}).get("excluded_domains", []),
                "topic_lock": debug.get("scene_summary", {}).get("topic_lock"),
                "node_count": debug.get("node_count", 0),
                "total_latency_ms": debug.get("total_latency_ms"),
                "check_results": {},
                "issues": [{"check": "inspector_error", "severity": "error", "message": str(exc)}],
                "pass": False,
            }

        results.append({
            "id":             tid,
            "session_key":    session_key,
            "query":          query,
            "expected_mode":  exp_mode,
            "expected_conf":  exp_conf,
            "actual_mode":    actual_mode,
            "actual_conf":    actual_conf,
            "mode_ok":        mode_ok,
            "conf_ok":        conf_ok,
            "http_status":    http_status,
            "elapsed_ms":     elapsed_ms,
            "reply_snippet":  reply[:120],
            "pipeline_status": "pass" if audit_result.get("pass") else "fail",
            "audit":          audit_result,
        })

    return results


# ── Report writer ─────────────────────────────────────────────────────────────

ALL_CHECKS = [
    "intent_routing", "companion_extract", "target_person", "target_gender",
    "retrieval_gap", "source_attribution", "response_mode",
    "flow_routing", "disengagement_misfire", "excluded_domains",
    "entity_pipeline", "topic_lock", "node_trace",
]

_CHECK_ICON = {"pass": "✅", "warning": "🟡", "error": "🔴"}
_SEV_ICON   = {"error": "🔴", "warning": "🟡", "info": "🔵"}


def write_report(
    smoke: list[dict],
    scenario: list[dict],
    model: str,
    out_dir: str,
) -> str:
    os.makedirs(out_dir, exist_ok=True)

    all_results = smoke + scenario
    now         = datetime.now(timezone.utc)
    ts          = now.strftime("%Y-%m-%d_%H-%M-%S")
    human       = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    total        = len(all_results)
    http_pass    = sum(1 for r in all_results if r.get("http_status") == "pass")
    http_fail    = sum(1 for r in all_results if r.get("http_status") == "fail")
    http_err     = sum(1 for r in all_results if r.get("http_status") == "error")
    pipe_pass    = sum(1 for r in all_results if r.get("pipeline_status") == "pass")
    pipe_fail    = sum(1 for r in all_results if r.get("pipeline_status") == "fail")
    pipe_skip    = sum(1 for r in all_results if r.get("pipeline_status") == "skipped")

    total_errors   = sum(
        len([i for i in r.get("audit", {}).get("issues", []) if i["severity"] == "error"])
        for r in all_results
    )
    total_warnings = sum(
        len([i for i in r.get("audit", {}).get("issues", []) if i["severity"] == "warning"])
        for r in all_results
    )
    avg_ms = round(
        sum(r.get("elapsed_ms", 0) for r in all_results if "elapsed_ms" in r)
        / max(1, sum(1 for r in all_results if "elapsed_ms" in r))
    )

    lines = [
        "# Cenomi Chatbot — Node Pipeline Evaluation",
        "",
        f"**Generated:** {human}  ",
        f"**Target:** `{BASE_URL}`  ",
        f"**Judge model:** `{model}`  ",
        f"**Run ID:** `{_RUN_ID}`",
        "",
        "## Summary",
        "",
        "### Simple Check (response_mode + confidence_level)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total queries | {total} |",
        f"| ✅ Mode+Conf match | {http_pass} ({round(http_pass/total*100) if total else 0}%) |",
        f"| ❌ Mode/Conf mismatch | {http_fail} |",
        f"| 💥 HTTP errors | {http_err} |",
        f"| Avg latency | {avg_ms} ms |",
        "",
        "### Pipeline Audit (13 checks via LLM inspector)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Turns audited | {total - pipe_skip} |",
        f"| ✅ Clean turns | {pipe_pass} |",
        f"| ❌ Turns with issues | {pipe_fail} |",
        f"| 🔴 Pipeline errors | {total_errors} |",
        f"| 🟡 Pipeline warnings | {total_warnings} |",
        f"| ⏭  Skipped (HTTP error) | {pipe_skip} |",
        "",
        "---",
        "",
    ]

    # ── Quick-reference table ────────────────────────────────────────────────
    lines += [
        "## Quick-Reference Table",
        "",
        "| # | ID | Query | Mode✓ | Conf✓ | Pipeline | Errors | Warnings | Latency |",
        "|---|----|-------|-------|-------|----------|--------|----------|---------|",
    ]
    for i, r in enumerate(all_results, 1):
        mode_icon  = "✅" if r.get("mode_ok") else "❌"
        conf_icon  = "✅" if r.get("conf_ok") else "❌"
        pipe_icon  = "✅" if r.get("pipeline_status") == "pass" else (
                      "⏭" if r.get("pipeline_status") == "skipped" else "❌"
                  )
        audit = r.get("audit", {})
        n_err  = len([x for x in audit.get("issues", []) if x["severity"] == "error"])
        n_warn = len([x for x in audit.get("issues", []) if x["severity"] == "warning"])
        lat    = f"{r.get('elapsed_ms','—')}ms"
        q_safe = r['query'].replace("|", "\\|")
        lines.append(
            f"| {i} | {r['id']} | `{q_safe}` "
            f"| {mode_icon} | {conf_icon} | {pipe_icon} "
            f"| {n_err} | {n_warn} | {lat} |"
        )
    lines.append("")

    # ── Mode/Conf failures ───────────────────────────────────────────────────
    mode_conf_fails = [r for r in all_results if r.get("http_status") == "fail"]
    if mode_conf_fails:
        lines += [
            "## Mode/Confidence Mismatches",
            "",
            "| ID | Query | Expected mode | Actual mode | Expected conf | Actual conf |",
            "|----|-------|--------------|-------------|--------------|-------------|",
        ]
        for r in mode_conf_fails:
            q_safe = r['query'].replace("|", "\\|")
            lines.append(
                f"| {r['id']} | `{q_safe}` "
                f"| `{r['expected_mode']}` | `{r.get('actual_mode','—')}` "
                f"| `{r['expected_conf']}` | `{r.get('actual_conf','—')}` |"
            )
        lines.append("")

    # ── Consolidated pipeline issues ─────────────────────────────────────────
    all_issues = [
        (r["id"], r["query"], iss)
        for r in all_results
        for iss in r.get("audit", {}).get("issues", [])
    ]
    if all_issues:
        lines += [
            "## All Pipeline Issues (Consolidated)",
            "",
            "| ID | Query | Severity | Check | Detail |",
            "|----|-------|----------|-------|--------|",
        ]
        for qid, qtext, iss in all_issues:
            sev_icon = _SEV_ICON.get(iss["severity"], "⚪")
            detail   = iss["message"].replace("|", "\\|")
            q_safe   = qtext[:40].replace("|", "\\|")
            lines.append(
                f"| {qid} | `{q_safe}` "
                f"| {sev_icon} {iss['severity']} "
                f"| `{iss['check']}` "
                f"| {detail} |"
            )
        lines.append("")

    # ── Per-turn detailed audit ──────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## Per-Turn Detailed Audit",
        "",
    ]
    for r in all_results:
        audit  = r.get("audit", {})
        h_icon = "✅" if r.get("http_status") == "pass" else "❌"
        p_icon = "✅" if r.get("pipeline_status") == "pass" else (
                  "⏭" if r.get("pipeline_status") == "skipped" else "❌"
                 )
        lines += [
            f"### [{r['id']}] {r['query']!r}  {h_icon} mode/conf  {p_icon} pipeline",
            "",
            f"**Session key:** `{r['session_key']}`  ",
            f"**Mode:** `{r.get('actual_mode','—')}` (expected `{r['expected_mode']}`)  ",
            f"**Conf:** `{r.get('actual_conf','—')}` (expected `{r['expected_conf']}`)  ",
            f"**Latency:** {r.get('elapsed_ms','—')} ms  ",
            f"**Reply:** {r.get('reply_snippet','')!r}",
            "",
        ]

        check_results = audit.get("check_results", {})
        if check_results:
            lines += [
                "**Pipeline checks:**",
                "",
                "| Check | Result | Notes |",
                "|-------|--------|-------|",
            ]
            for check_name in ALL_CHECKS:
                data       = check_results.get(check_name, {"result": "pass"})
                result_str = data.get("result", "pass")
                icon_c     = _CHECK_ICON.get(result_str, "⚪")
                reason     = data.get("reason", "").replace("|", "\\|")
                lines.append(f"| `{check_name}` | {icon_c} {result_str} | {reason} |")
            lines.append("")

        issues = audit.get("issues", [])
        if issues:
            lines.append("**Issues:**")
            lines.append("")
            for iss in issues:
                sev_icon = _SEV_ICON.get(iss["severity"], "⚪")
                lines.append(f"- {sev_icon} **[{iss['check']}]** {iss['message']}")
            lines.append("")
        else:
            lines.append("_No pipeline issues detected._")
            lines.append("")

        lines.append("---")
        lines.append("")

    # ── Write files ──────────────────────────────────────────────────────────
    md_path   = os.path.join(out_dir, f"pipeline_eval_{ts}.md")
    json_path = os.path.join(out_dir, f"pipeline_eval_{ts}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_out = {
        "generated_at": human,
        "run_id":       _RUN_ID,
        "judge_model":  model,
        "target":       BASE_URL,
        "summary": {
            "total":           total,
            "http_pass":       http_pass,
            "http_fail":       http_fail,
            "http_errors":     http_err,
            "pipeline_pass":   pipe_pass,
            "pipeline_fail":   pipe_fail,
            "pipeline_skip":   pipe_skip,
            "total_errors":    total_errors,
            "total_warnings":  total_warnings,
            "avg_latency_ms":  avg_ms,
        },
        "smoke_results":    smoke,
        "scenario_results": scenario,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    print(f"\n  📄  Markdown report : {md_path}")
    print(f"  🗂   JSON data       : {json_path}")
    return md_path


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Node pipeline evaluation for all test queries"
    )
    parser.add_argument(
        "--model", default=None,
        help="OpenAI judge model (default: value from .env or gpt-4.1-mini)",
    )
    parser.add_argument(
        "--out", default=str(_SCRIPT_DIR.parent.parent / "test-results"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--suite", choices=["smoke", "scenario", "all"], default="all",
        help="Which test suite to run (default: all)",
    )
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

    print("\nCenomi Chatbot — Node Pipeline Evaluation")
    print(f"Target     : {BASE_URL}")
    print(f"Judge model: {model}")
    print(f"Suite      : {args.suite}")
    print(f"Run ID     : {_RUN_ID}")

    # Load audit module
    audit_mod = _load_audit_module()

    smoke_results:    list[dict] = []
    scenario_results: list[dict] = []

    if args.suite in ("smoke", "all"):
        smoke_results = run_evaluation(
            SMOKE_TESTS, "SMOKE TESTS", api_key, model, audit_mod
        )

    if args.suite in ("scenario", "all"):
        scenario_results = run_evaluation(
            SCENARIO_TESTS, "SCENARIO TESTS", api_key, model, audit_mod
        )

    # ── Print summary ────────────────────────────────────────────────────────
    all_results = smoke_results + scenario_results
    total        = len(all_results)
    http_pass    = sum(1 for r in all_results if r.get("http_status") == "pass")
    pipe_pass    = sum(1 for r in all_results if r.get("pipeline_status") == "pass")
    total_errors = sum(
        len([i for i in r.get("audit", {}).get("issues", []) if i["severity"] == "error"])
        for r in all_results
    )
    total_warnings = sum(
        len([i for i in r.get("audit", {}).get("issues", []) if i["severity"] == "warning"])
        for r in all_results
    )

    print(f"\n{'─'*72}")
    print(f"  RESULTS")
    print(f"{'─'*72}")
    print(f"  Total queries   : {total}")
    print(f"  Mode/Conf pass  : {http_pass} / {total} ({round(http_pass/total*100) if total else 0}%)")
    print(f"  Pipeline clean  : {pipe_pass} / {total} ({round(pipe_pass/total*100) if total else 0}%)")
    print(f"  Pipeline errors : {total_errors}")
    print(f"  Pipeline warns  : {total_warnings}")
    print()

    write_report(smoke_results, scenario_results, model, args.out)


if __name__ == "__main__":
    main()
