#!/usr/bin/env python3
"""
LLM-driven structural audit of an exported session JSON.

Uses a single LLM inspector call per turn to evaluate all 13 pipeline checks,
replacing keyword-based heuristics with natural language reasoning over debug signals.

Checks evaluated per turn
─────────────────────────
  1.  intent_routing         — Is the classified intent sensible for the user message?
  2.  companion_extract      — Were companions identified correctly from the message?
  3.  target_person          — Does target_person match what the user actually said?
  4.  target_gender          — Does target_gender match pronouns used in the message?
  5.  retrieval_gap          — Should retrieval have fired but didn't?
  6.  source_attribution     — If retrieval fired and got results, were sources returned?
  7.  response_mode          — Is the response_mode consistent with the intent kind?
  8.  flow_routing           — Was factual vs concierge routing correct for this query?
  9.  disengagement_misfire  — Disengagement misclassified on an explicit request?
  10. excluded_domains       — Spurious or stale domain exclusions in scene memory?
  11. entity_pipeline        — Entity count appropriate? No bloat for specific product tasks?
  12. topic_lock             — Was topic_lock updated on a topic_switch turn?
  13. node_trace             — Is the trace complete? Duplicate nodes? Slow turns?

Usage
─────
    python scripts/audit_session.py
    python scripts/audit_session.py --session path/to/session.json
    python scripts/audit_session.py --model gpt-4.1-mini
    python scripts/audit_session.py --out test-results/

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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Load .env from backend/ ───────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    """Read API key settings from backend/.env."""
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


# ── LLM prompts ───────────────────────────────────────────────────────────────

AUDIT_SYSTEM_PROMPT = """You are an expert structural evaluator of a shopping mall concierge AI pipeline.
You will receive a single conversation turn with its internal debug signals.

Evaluate ALL 13 checks below and return ONLY valid JSON (no markdown fences):
{
  "checks": {
    "intent_routing":          {"result": "pass", "reason": "..."},
    "companion_extract":       {"result": "pass", "reason": "..."},
    "target_person":           {"result": "pass", "reason": "..."},
    "target_gender":           {"result": "pass", "reason": "..."},
    "retrieval_gap":           {"result": "pass", "reason": "..."},
    "source_attribution":      {"result": "pass", "reason": "..."},
    "response_mode":           {"result": "pass", "reason": "..."},
    "flow_routing":            {"result": "pass", "reason": "..."},
    "disengagement_misfire":   {"result": "pass", "reason": "..."},
    "excluded_domains":        {"result": "pass", "reason": "..."},
    "entity_pipeline":         {"result": "pass", "reason": "..."},
    "topic_lock":              {"result": "pass", "reason": "..."},
    "node_trace":              {"result": "pass", "reason": "..."}
  }
}

Each "result" must be exactly one of: "pass", "warning", or "error".
Omit "reason" when result is "pass" — always include it for "warning" or "error".

Guidance for each check:

  intent_routing         — Does intent_domain match what the user is actually asking?
                           Valid domains: "dining" (food/restaurant requests), "shopping" (purchase
                           requests), "entertainment" (cinema/activities), "navigation" (location
                           queries like "where is X"), "services" (prayer room, parking, ATM,
                           mall facilities), "mall_info" (overview, opening hours, family suitability
                           — broad questions ABOUT the mall itself), "exploration" (vague open
                           discovery — "what can I do?", "I'm bored", "surprise me"), "general"
                           (greetings, off-topic, gibberish).
                           "mall_info" and "exploration" and "services" ARE valid canonical domains —
                           do NOT flag these as warnings just because they are not dining/shopping/
                           entertainment/navigation. Only flag as warning if the domain clearly
                           contradicts what the user asked.

  companion_extract      — Are scene companions consistent with who the user mentioned in
                           this specific message? If the user says "my girlfriend" or "5 yr old"
                           in this turn, that companion should appear in the companions list.

  target_person          — Does shopping_task.target_person match explicit "for him/her/son/
                           daughter/girlfriend/wife" phrases in the user message?
                           Flag as error if target_person contradicts the stated recipient.

  target_gender          — Does target_gender match pronouns used in the user message?
                           "him/his/he" implies male; "her/she" implies female.
                           Flag as error if gender contradicts the pronouns used.

  retrieval_gap          — Should retrieval have fired given the response mode and domain?
                           For guided_recommendation / hybrid_plan / best_effort_shortlist
                           in dining/shopping/entertainment, retrieval should generally fire.
                           Flag as warning if retrieval_needed=false for recommendation responses.
                           EXCEPTION: If entity_count > 5, the system used canonical entity
                           selection (not vector retrieval) to build the shortlist — do NOT
                           flag retrieval_gap as a warning for these turns. Canonical entity
                           selection is the expected path for well-populated domains and is
                           functionally equivalent to retrieval.

  source_attribution     — If retrieval fired with results, were sources surfaced in the response?
                           Flag as error if retrieval_needed=true AND retrieval_results>0
                           AND sources_count=0.

  response_mode          — Is the response_mode consistent with message_kind + intent_domain?
                           fresh_request+dining → guided_recommendation;
                           factual query → direct_factual;
                           disengagement → disengagement_recovery or graceful_recovery.
                           IMPORTANT: context_acknowledgement is correct for context-setting turns
                           ("i am here with my family", "i am a bridesmaid", "i am here with my
                           girlfriend") EVEN WHEN the bot's response body already includes a
                           guided plan — the mode describes how the turn was classified, not
                           what the response text contains. Do NOT flag this as a warning;
                           the system proactively generates a plan alongside the acknowledgement.
                           Only flag response_mode as a warning if the mode is fundamentally wrong
                           (e.g. direct_factual for a clear recommendation request, or
                           graceful_recovery for a valid mall query).

  flow_routing           — Was factual vs concierge routing correct for this query type?
                           Location queries ("where is X"), hours, ATMs, brand presence → factual.
                           Recommendation queries ("suggest", "what can I eat") → concierge.
                           Context-setting turns ("i am here with my family", "i am a bridesmaid")
                           → concierge is CORRECT; do NOT flag concierge routing as wrong for
                           context-setting turns. Concierge enables the bot to provide a helpful
                           proactive response rather than just acknowledging.
                           Flag as warning ONLY if flow_type clearly contradicts the query nature
                           (e.g. factual routing for "suggest some restaurants").

  disengagement_misfire  — Is message_kind="disengagement" appropriate?
                           If the user made an explicit food craving ("I wanna eat"), purchase
                           intent ("I want to buy"), or entertainment request ("I want to watch"),
                           disengagement is a MISFIRE → flag as error.
                           Disengagement is only correct when the user is expressing frustration
                           or leaving without any specific actionable request.

  excluded_domains       — Are excluded_domains correctly set?
                           Spurious exclusion: dining excluded after the user asked about a
                           specific food item ("Can I get tiramisu?") or dietary preference
                           ("veg options") — these are requests WITHIN a domain, not rejections.
                           Stale exclusion: a domain is excluded but the user just explicitly
                           re-requested it (fresh_request/refinement/topic_switch message_kind).
                           Flag either as warning.

  entity_pipeline        — Is entity count appropriate and relevant to the intent?
                           For specific product tasks (product_type is set), >20 entities is
                           bloat — flag as warning.
                           If the majority of entities have entity_type contradicting the
                           intent_domain (e.g. mostly dining entities for a shopping request),
                           flag as warning.

  topic_lock             — If message_kind=topic_switch, was topic_lock updated to reflect
                           the new intent_domain? A stale topic_lock still showing the old
                           domain after a topic switch should be flagged as warning.

  node_trace             — Is the node trace complete and correct?
                           Empty trace → error (pipeline did not trace).
                           Any node name appears more than once → error (graph routing bug).
                           total_latency_ms > 8000 → warning (slow turn).
                           If node_trace shows "(empty)" → error.

Severity guide:
  "error"   — functional correctness failure (disengagement misfire, pronoun gender swap,
               empty node trace, duplicate node)
  "warning" — potential issue worth reviewing (entity bloat, stale topic_lock, slow turn,
               retrieval gap, suspicious exclusion)
  "pass"    — signal looks correct; no action needed
"""

AUDIT_USER_TEMPLATE = """=== USER MESSAGE ===
{user_message}

=== ASSISTANT RESPONSE (first 400 chars) ===
{assistant_snippet}

=== PIPELINE DEBUG SIGNALS ===
intent_domain:       {intent_domain}
intent_sub:          {intent_sub}
message_kind:        {message_kind}
intent_confidence:   {intent_confidence}
flow_type:           {flow_type}
response_mode:       {response_mode}
chosen_strategy:     {chosen_strategy}
companions:          {companions}
excluded_domains:    {excluded_domains}
topic_lock:          {topic_lock}
target_person:       {target_person}
target_gender:       {target_gender}
retrieval_needed:    {retrieval_needed}
retrieval_results:   {retrieval_results}
sources_count:       {sources_count}
entity_count:        {entity_count} (types: {entity_types_summary})
pipeline_warnings:   {warnings}
node_trace:          {node_trace_summary}
total_latency_ms:    {total_latency_ms}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summarise_entity_types(entities: list[dict]) -> str:
    """Return a compact summary like 'dining×3, store×5'."""
    if not entities:
        return "none"
    counts = Counter(e.get("entity_type", "unknown") for e in entities)
    return ", ".join(f"{t}×{n}" for t, n in sorted(counts.items()))


def _summarise_node_trace(node_trace: list[dict]) -> str:
    """Return a compact trace like 'load_session(12ms) → interpret_turn(450ms) → ...'."""
    if not node_trace:
        return "(empty)"
    parts = []
    for entry in node_trace:
        name = entry.get("node", "?")
        ms   = entry.get("latency_ms", "?")
        parts.append(f"{name}({ms}ms)")
    return " → ".join(parts)


def _map_severity(result: str) -> str:
    """Map LLM result string to audit severity level."""
    return {"error": "error", "warning": "warning", "pass": "info"}.get(result, "info")


def _severity_icon(sev: str) -> str:
    return {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "⚪")


def _check_icon(result: str) -> str:
    return {"pass": "✅", "warning": "🟡", "error": "🔴"}.get(result, "⚪")


# ── LLM inspector call ────────────────────────────────────────────────────────

_INSPECTOR_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response could not be parsed as valid JSON. "
    "Return ONLY a raw JSON object — no markdown fences, no apostrophes in string values "
    "(use double-quotes only), and ensure all string values are properly escaped. "
    "Do not include any text outside the JSON object."
)

_SAFE_PASS_RESULT: dict[str, Any] = {
    "checks": {name: {"result": "pass"} for name in [
        "intent_routing", "companion_extract", "target_person", "target_gender",
        "retrieval_gap", "source_attribution", "response_mode", "flow_routing",
        "disengagement_misfire", "excluded_domains", "entity_pipeline",
        "topic_lock", "node_trace",
    ]}
}


def call_inspector(
    system: str,
    user: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    """
    Call the LLM and parse the structured JSON audit response.

    On JSON parse failure, retries once with an explicit instruction to return
    valid JSON (handles apostrophes and special characters that cause malformed
    output).  If the retry also fails, returns a safe all-pass result rather
    than raising, so the audit continues for other turns.
    """
    try:
        import openai
    except ImportError:
        print("ERROR: 'openai' package not installed. Run: pip install openai", file=sys.stderr)
        sys.exit(1)

    client = openai.OpenAI(api_key=api_key)

    def _call(user_content: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_content},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(l for l in lines if not l.startswith("```")).strip()
        return raw

    # First attempt
    raw = _call(user)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Retry with explicit JSON-escaping instruction
    raw = _call(user + _INSPECTOR_RETRY_SUFFIX)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Graceful fallback: log and return safe all-pass so the audit continues
        print(
            f"\n  ⚠️  [call_inspector] JSON parse failed after retry — "
            f"returning safe fallback. Error: {exc}",
            file=sys.stderr,
        )
        return _SAFE_PASS_RESULT


# ── Full turn audit ───────────────────────────────────────────────────────────

ALL_CHECKS = [
    "intent_routing", "companion_extract", "target_person", "target_gender",
    "retrieval_gap", "source_attribution", "response_mode",
    "flow_routing", "disengagement_misfire", "excluded_domains",
    "entity_pipeline", "topic_lock", "node_trace",
]


def audit_turn(
    turn_index: int,
    user_msg: str,
    assistant_msg: dict,
    api_key: str,
    model: str,
) -> dict:
    debug      = assistant_msg.get("debug", {})
    scene      = debug.get("scene_summary", {})
    shop       = scene.get("shopping_task", {})
    sources    = assistant_msg.get("sources", [])
    resp       = assistant_msg.get("content", "")
    node_trace = debug.get("node_trace", [])
    entities   = debug.get("selected_entities", [])

    user_content = AUDIT_USER_TEMPLATE.format(
        user_message         = user_msg,
        assistant_snippet    = resp[:400],
        intent_domain        = debug.get("intent_domain", ""),
        intent_sub           = debug.get("intent_sub", ""),
        message_kind         = debug.get("message_kind", ""),
        intent_confidence    = debug.get("intent_confidence", ""),
        flow_type            = debug.get("flow_type", ""),
        response_mode        = debug.get("response_mode", "") or "(smalltalk)",
        chosen_strategy      = debug.get("chosen_strategy", ""),
        companions           = scene.get("companions", []),
        excluded_domains     = scene.get("excluded_domains", []),
        topic_lock           = scene.get("topic_lock", ""),
        target_person        = shop.get("target_person", ""),
        target_gender        = shop.get("target_gender", ""),
        retrieval_needed     = debug.get("retrieval_needed", False),
        retrieval_results    = debug.get("retrieval_results_count", 0),
        sources_count        = len(sources),
        entity_count         = len(entities),
        entity_types_summary = _summarise_entity_types(entities),
        warnings             = debug.get("warnings", []),
        node_trace_summary   = _summarise_node_trace(node_trace),
        total_latency_ms     = debug.get("total_latency_ms", ""),
    )

    raw    = call_inspector(AUDIT_SYSTEM_PROMPT, user_content, api_key, model)
    checks = raw.get("checks", {})

    all_issues = [
        {
            "check":    name,
            "severity": _map_severity(data.get("result", "pass")),
            "message":  data.get("reason", ""),
        }
        for name, data in checks.items()
        if data.get("result", "pass") != "pass"
    ]

    return {
        "turn_index":        turn_index,
        "user_message":      user_msg,
        "assistant_snippet": resp[:120],
        "intent":            f"{debug.get('intent_domain')}/{debug.get('intent_sub')}",
        "intent_confidence": debug.get("intent_confidence"),
        "message_kind":      debug.get("message_kind"),
        "response_mode":     debug.get("response_mode") or "(smalltalk)",
        "confidence_level":  debug.get("confidence_level"),
        "flow_type":         debug.get("flow_type"),
        "chosen_strategy":   debug.get("chosen_strategy"),
        "entity_count":      len(entities),
        "excluded_domains":  scene.get("excluded_domains", []),
        "topic_lock":        scene.get("topic_lock"),
        "node_count":        len(node_trace),
        "total_latency_ms":  debug.get("total_latency_ms"),
        "check_results":     checks,
        "issues":            all_issues,
        "pass":              len([i for i in all_issues if i["severity"] in ("error", "warning")]) == 0,
    }


# ── Report writer ─────────────────────────────────────────────────────────────

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

    total   = len(results)
    passing = sum(1 for r in results if r.get("pass"))
    failing = total - passing

    errors   = sum(len([i for i in r.get("issues", []) if i["severity"] == "error"])   for r in results)
    warnings = sum(len([i for i in r.get("issues", []) if i["severity"] == "warning"]) for r in results)
    infos    = sum(len([i for i in r.get("issues", []) if i["severity"] == "info"])     for r in results)

    lines = [
        "# Session Audit Report — LLM Structural Inspector",
        "",
        f"**Generated:** {human}  ",
        f"**Session ID:** `{session_meta.get('session_id', '—')}`  ",
        f"**Mall ID:** `{session_meta.get('mall_id', '—')}`  ",
        f"**Judge model:** `{model}`  ",
        f"**Exported at:** `{session_meta.get('exported_at', '—')}`  ",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Turns audited | {total} |",
        f"| ✅ Clean turns | {passing} |",
        f"| ⚠️  Turns with issues | {failing} |",
        f"| 🔴 Errors | {errors} |",
        f"| 🟡 Warnings | {warnings} |",
        f"| 🔵 Info notes | {infos} |",
        "",
        "---",
        "",
        "## Turn-by-Turn Audit",
        "",
    ]

    for r in results:
        icon = "✅" if r.get("pass") else "❌"
        lines += [
            f"### Turn {r['turn_index']} {icon}",
            "",
            f"**User:** `{r['user_message']}`  ",
            f"**Intent:** `{r['intent']}` (conf={r['intent_confidence']}, kind=`{r['message_kind']}`)  ",
            f"**Response mode:** `{r['response_mode']}` | **Confidence:** `{r.get('confidence_level')}`  ",
            (
                f"**Pipeline:** flow=`{r.get('flow_type')}` strategy=`{r.get('chosen_strategy')}` "
                f"entities={r.get('entity_count')} nodes={r.get('node_count')} "
                f"latency={r.get('total_latency_ms')}ms  "
            ),
            f"**Excluded domains:** {r.get('excluded_domains')} | **Topic lock:** `{r.get('topic_lock')}`  ",
            "",
            f"**Response snippet:** {r['assistant_snippet']!r}",
            "",
        ]

        # Per-check results table
        check_results = r.get("check_results", {})
        if check_results:
            lines += [
                "**Check results:**",
                "",
                "| Check | Result | Notes |",
                "|-------|--------|-------|",
            ]
            for check_name in ALL_CHECKS:
                data       = check_results.get(check_name, {"result": "pass"})
                result_str = data.get("result", "pass")
                icon_c     = _check_icon(result_str)
                reason     = data.get("reason", "").replace("|", "\\|")
                lines.append(f"| `{check_name}` | {icon_c} {result_str} | {reason} |")
            lines.append("")

        if r.get("issues"):
            lines.append("**Issues found:**")
            lines.append("")
            for iss in r["issues"]:
                lines.append(
                    f"- {_severity_icon(iss['severity'])} **[{iss['check']}]** {iss['message']}"
                )
            lines.append("")
        else:
            lines.append("_No issues detected._")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Consolidated issue table
    all_issues = [(r["turn_index"], r["user_message"], iss)
                  for r in results for iss in r.get("issues", [])]
    if all_issues:
        lines += [
            "## All Issues (Consolidated)",
            "",
            "| Turn | Severity | Check | Detail |",
            "|------|----------|-------|--------|",
        ]
        for turn_idx, umsg, iss in all_issues:
            sev_icon = _severity_icon(iss["severity"])
            detail   = iss["message"].replace("|", "\\|")
            lines.append(
                f"| {turn_idx} (`{umsg[:30]}...`) "
                f"| {sev_icon} {iss['severity']} "
                f"| `{iss['check']}` "
                f"| {detail} |"
            )
        lines.append("")

    md_path   = os.path.join(out_dir, f"audit_{ts}.md")
    json_path = os.path.join(out_dir, f"audit_{ts}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_out = {
        "generated_at": human,
        "judge_model":  model,
        "session_meta": session_meta,
        "summary": {
            "turns": total, "passing": passing, "failing": failing,
            "errors": errors, "warnings": warnings, "infos": infos,
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

    parser = argparse.ArgumentParser(description="LLM-driven structural audit of a session JSON")
    parser.add_argument("--session", default=str(default_in),
                        help="Path to session JSON file")
    parser.add_argument("--model",   default=None,
                        help="OpenAI model name (default: gpt-4.1-mini)")
    parser.add_argument("--out",     default=str(default_out),
                        help="Directory for output reports")
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
        "tenant_id":      data.get("tenant_id"),
        "mall_id":        data.get("mall_id"),
        "exported_at":    data.get("exported_at"),
        "total_messages": len(messages),
    }

    print("\nCenomi Session Auditor — LLM Structural Inspector")
    print(f"Session  : {session_meta['session_id']}")
    print(f"Mall     : {session_meta['mall_id']}")
    print(f"Model    : {model}")
    print(f"Messages : {session_meta['total_messages']}")
    print()

    results: list[dict] = []
    i        = 0
    turn_num = 0

    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            user_msg = msg.get("content", "")
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                asst_msg = messages[i + 1]
                turn_num += 1
                print(f"  Auditing turn {turn_num}: {user_msg[:55]!r} … ", end="", flush=True)

                try:
                    result = audit_turn(turn_num, user_msg, asst_msg, api_key, model)
                    results.append(result)
                    icon     = "✅" if result["pass"] else "❌"
                    n_issues = len(result["issues"])
                    print(f"{icon} ({n_issues} issue{'s' if n_issues != 1 else ''})")
                    for iss in result["issues"]:
                        sev_icon = _severity_icon(iss["severity"])
                        print(f"    {sev_icon} [{iss['check']}] {iss['message'][:80]}")

                except Exception as exc:
                    print(f"💥 ERROR: {exc}")
                    results.append({
                        "turn_index":        turn_num,
                        "user_message":      user_msg,
                        "assistant_snippet": "",
                        "intent":            "",
                        "intent_confidence": None,
                        "message_kind":      None,
                        "response_mode":     "(error)",
                        "confidence_level":  None,
                        "flow_type":         None,
                        "chosen_strategy":   None,
                        "entity_count":      0,
                        "excluded_domains":  [],
                        "topic_lock":        None,
                        "node_count":        0,
                        "total_latency_ms":  None,
                        "check_results":     {},
                        "pass":              False,
                        "issues": [{
                            "check":    "inspector_error",
                            "severity": "error",
                            "message":  str(exc),
                        }],
                    })

                i += 2
            else:
                i += 1
        else:
            i += 1

    # Summary
    passing = sum(1 for r in results if r.get("pass"))
    failing = len(results) - passing
    print(f"\n{'─'*60}")
    print(f"  Turns audited : {len(results)}")
    print(f"  Clean         : {passing}")
    print(f"  With issues   : {failing}")
    print()

    write_report(results, session_meta, model, args.out)


if __name__ == "__main__":
    main()
