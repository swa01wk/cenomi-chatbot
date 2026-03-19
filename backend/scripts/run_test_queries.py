#!/usr/bin/env python3
"""
Run the smoke-test queries from docs/test-queries.md and print a results report.
Writes detailed results to the project-level test-results/ folder.
"""
import json
import os
import time
import uuid
import httpx
from datetime import datetime, timezone

BASE_URL = "http://127.0.0.1:8000/api/chat"
TIMEOUT = 30

# Unique suffix per test run so sessions are never contaminated by prior runs.
_RUN_ID = uuid.uuid4().hex[:8]

# ---------------------------------------------------------------------------
# Test definitions: (id, session_id, message, expected_mode, expected_confidence)
# ---------------------------------------------------------------------------
SMOKE_TESTS = [
    ("S-01", "smoke-1", "what movies are showing?",         "direct_factual",          "high"),
    ("S-02", "smoke-2", "i am here with my kids",           "context_acknowledgement", "high"),
    ("S-03", "smoke-2", "where can we eat?",                "guided_recommendation",   "high"),
    ("S-04", "smoke-4", "food and movies",                  "hybrid_plan",             "medium"),
    ("S-05", "smoke-5", "anything interesting here?",       "best_effort_shortlist",   "medium"),
    ("S-06", "smoke-6", "where is the prayer room?",        "direct_factual",          "high"),
    ("S-07", "smoke-7", "i am a bridesmaid",                "context_acknowledgement", "high"),
    ("S-08", "smoke-8", "something nice for my son",        "guided_recommendation",   "high"),
    ("S-09", "smoke-9", "asdf",                             "graceful_recovery",       "low"),
    ("S-10a", "smoke-1", "with kid",                        "direct_factual",          "high"),  # follow-up to S-01
]

SCENARIO_TESTS = [
    # Scenario 1 — Movie Showtimes
    ("1.1", "sc1", "what movies are showing?",              "direct_factual", "high"),
    ("1.2", "sc1", "show me movies",                        "guided_recommendation", "high"),
    ("1.4", "sc1", "now showing",                           "direct_factual", "high"),
    ("1.5", "sc1", "what's playing at the cinema?",         "direct_factual", "high"),
    ("1.6", "sc1", "any kids movies today?",                "direct_factual", "high"),

    # Scenario 2 — Family Day Out
    ("2.1", "sc2", "i am here with my family",              "context_acknowledgement", "high"),
    ("2.2", "sc2b", "i am here with my kids",               "context_acknowledgement", "high"),
    ("2.3", "sc2", "where can we eat?",                     "guided_recommendation",   "high"),
    ("2.4", "sc2", "any activities for the kids?",          "guided_recommendation",   "high"),
    ("2.5", "sc2", "what movies are there?",                "direct_factual",          "high"),
    ("2.7", "sc2", "something quick for lunch",             "guided_recommendation",   "high"),

    # Scenario 3 — Gift Shopping
    ("3.1", "sc3", "i want to buy a gift",                  "guided_recommendation",   "high"),
    ("3.2", "sc3b", "i want to buy jackets",                "guided_recommendation",   "medium"),
    ("3.3", "sc3b", "for my 5 year old son",                "guided_recommendation",   "high"),
    ("3.5", "sc3b", "something affordable",                 "guided_recommendation",   "high"),
    ("3.6", "sc3c", "gift for my girlfriend",               "guided_recommendation",   "high"),

    # Scenario 4 — Date Night
    ("4.1", "sc4", "i am here with my girlfriend",          "context_acknowledgement", "high"),
    ("4.2", "sc4", "suggest a nice dinner",                 "guided_recommendation",   "high"),
    ("4.3", "sc4b", "we want to catch a movie and then eat","hybrid_plan",             "high"),
    ("4.6", "sc4c", "any romantic options here?",           "guided_recommendation",   "high"),

    # Scenario 5 — Quick Visit
    ("5.1", "sc5", "something quick to eat",                "guided_recommendation",   "high"),
    ("5.2", "sc5b", "we are in a hurry",                    "context_acknowledgement", "high"),
    ("5.4", "sc5c", "something quick before the movie",     "hybrid_plan",             "high"),

    # Scenario 6 — Cross-Intent: Movies + Food
    ("6.1", "sc6", "food and movies",                       "hybrid_plan",             "medium"),
    ("6.3", "sc6b", "where can we eat after the movie?",    "hybrid_plan",             "high"),
    ("6.5", "sc6c", "we want to watch a movie and grab dinner", "hybrid_plan",         "high"),

    # Scenario 7 — Vague Exploration
    ("7.1", "sc7", "anything interesting here?",            "best_effort_shortlist",   "medium"),
    ("7.3", "sc7", "i'm bored",                             "best_effort_shortlist",   "medium"),
    ("7.4", "sc7b", "it's my first time here",              "context_acknowledgement", "high"),
    ("7.7", "sc7c", "surprise me",                          "best_effort_shortlist",   "medium"),

    # Scenario 8 — Broken Input
    ("8.1", "sc8", "asdf",                                  "graceful_recovery",       "low"),
    ("8.3", "sc8b", "what's the weather like?",             "graceful_recovery",       "low"),
    ("8.4", "sc8c", "tell me a joke",                       "graceful_recovery",       "low"),
    ("8.6", "sc8d", "a",                                    "graceful_recovery",       "low"),

    # Scenario 9 — Store Location
    ("9.1", "sc9", "where is Zara?",                        "direct_factual",          "high"),
    ("9.2", "sc9b", "where is the prayer room?",            "direct_factual",          "high"),
    ("9.4", "sc9c", "what are the mall opening hours?",     "direct_factual",          "high"),
    ("9.7", "sc9d", "is Starbucks here?",                   "direct_factual",          "high"),

    # Scenario 10 — Constraint Refinement
    ("10.1", "sc10", "suggest some restaurants",            "guided_recommendation",   "high"),
    ("10.2", "sc10", "something cheaper",                   "guided_recommendation",   "medium"),
    ("10.6", "sc10b", "suggest some stores for fashion",    "guided_recommendation",   "high"),
    ("10.7", "sc10b", "something more affordable",          "guided_recommendation",   "medium"),

    # Scenario 11 — Wedding/Bridesmaid
    ("11.1", "sc11", "i am a bridesmaid",                   "context_acknowledgement", "high"),
    ("11.2", "sc11b", "i am a bridesmaid shopping for the wedding", "context_acknowledgement", "high"),
    ("11.3", "sc11", "i need something elegant",            "guided_recommendation",   "high"),
    ("11.6", "sc11c", "i am here for a wedding — i am the groom", "context_acknowledgement", "high"),

    # Scenario 12 — Cross-Mall Brand Search
    ("12.1", "sc12", "do you have H&M?",                   "direct_factual",          "high"),
    ("12.2", "sc12b", "is Nike here?",                     "direct_factual",          "high"),

    # Scenario 13 — Mall Overview
    ("13.1", "sc13", "tell me about the mall",             "direct_factual",          "high"),
    ("13.2", "sc13b", "what does this mall have?",         "direct_factual",          "high"),
    ("13.3", "sc13c", "is this mall family friendly?",     "direct_factual",          "high"),
]


def send_message(session_id: str, message: str) -> dict:
    unique_session = f"{session_id}-{_RUN_ID}"
    payload = {"message": message, "session_id": unique_session, "debug": True}
    resp = httpx.post(BASE_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def extract_debug(data: dict) -> dict:
    debug = data.get("debug") or {}
    scene = debug.get("scene_summary") or {}
    return {
        "response_mode":    debug.get("response_mode", "—"),
        "confidence_level": debug.get("confidence_level", "—"),
        "flow_type":        debug.get("flow_type", "—"),
        "playbook_used":    debug.get("selected_playbook", "—"),
        "fallback_applied": debug.get("fallback_applied", "—"),
        "intent_domain":    debug.get("intent_domain", "—"),
        "message_kind":     debug.get("message_kind", "—"),
        "chosen_strategy":  debug.get("chosen_strategy", "—"),
        "topic_lock":       scene.get("topic_lock", "—"),
    }


def run_tests(tests: list, label: str) -> list:
    results = []
    client = httpx.Client(timeout=TIMEOUT)

    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    for (tid, session, query, exp_mode, exp_conf) in tests:
        try:
            t0 = time.time()
            data = send_message(session, query)
            elapsed = (time.time() - t0) * 1000
            reply = data.get("reply", data.get("message", ""))

            dbg = extract_debug(data)
            mode_ok = dbg["response_mode"] == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf

            status = "PASS" if (mode_ok and conf_ok) else "FAIL"

            results.append({
                "id": tid,
                "query": query,
                "status": status,
                "mode_ok": mode_ok,
                "conf_ok": conf_ok,
                "expected_mode": exp_mode,
                "actual_mode":   dbg["response_mode"],
                "expected_conf": exp_conf,
                "actual_conf":   dbg["confidence_level"],
                "flow_type":     dbg["flow_type"],
                "playbook_used": dbg["playbook_used"],
                "topic_lock":    dbg["topic_lock"],
                "latency_ms":    round(elapsed),
                "reply_snippet": reply[:120] if reply else "",
            })

            icon = "✅" if status == "PASS" else "❌"
            print(f"\n{icon} [{tid}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:30s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:30s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Flow/PB:    {dbg['flow_type']} / {dbg['playbook_used']}")
            print(f"   Reply:      {reply[:120]!r}")
            print(f"   Latency:    {round(elapsed)}ms")

        except Exception as exc:
            results.append({
                "id": tid, "query": query, "status": "ERROR",
                "error": str(exc),
            })
            print(f"\n💥 [{tid}] {query!r}")
            print(f"   ERROR: {exc}")

    client.close()
    return results


def print_summary(smoke_results: list, scenario_results: list) -> None:
    all_results = smoke_results + scenario_results

    total = len(all_results)
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    failed = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors = sum(1 for r in all_results if r.get("status") == "ERROR")

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  Total  : {total}")
    print(f"  Passed : {passed} ({round(passed/total*100)}%)")
    print(f"  Failed : {failed}")
    print(f"  Errors : {errors}")

    if failed or errors:
        print(f"\n  --- Failures/Errors ---")
        for r in all_results:
            if r.get("status") in ("FAIL", "ERROR"):
                icon = "❌" if r["status"] == "FAIL" else "💥"
                print(f"  {icon} [{r['id']}] {r['query']!r}")
                if r["status"] == "FAIL":
                    print(f"       mode:  expected={r['expected_mode']!r}  actual={r['actual_mode']!r}")
                    print(f"       conf:  expected={r['expected_conf']!r}  actual={r['actual_conf']!r}")
                else:
                    print(f"       error: {r.get('error')}")

    print()


def write_results(smoke_results: list, scenario_results: list) -> str:
    """
    Write test results to test-results/ as both a human-readable Markdown
    summary and a machine-readable JSON file.

    Returns the path to the Markdown file.
    """
    all_results = smoke_results + scenario_results
    total   = len(all_results)
    passed  = sum(1 for r in all_results if r.get("status") == "PASS")
    failed  = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors  = sum(1 for r in all_results if r.get("status") == "ERROR")
    pct     = round(passed / total * 100) if total else 0
    avg_ms  = round(
        sum(r.get("latency_ms", 0) for r in all_results if "latency_ms" in r)
        / max(1, sum(1 for r in all_results if "latency_ms" in r))
    )

    now_utc = datetime.now(timezone.utc)
    ts_label = now_utc.strftime("%Y-%m-%d_%H-%M-%S")
    ts_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Resolve test-results/ relative to the repo root (two levels up from scripts/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.normpath(os.path.join(script_dir, "..", "..", "test-results"))
    os.makedirs(results_dir, exist_ok=True)

    md_path   = os.path.join(results_dir, f"run_{ts_label}.md")
    json_path = os.path.join(results_dir, f"run_{ts_label}.json")

    # ── Markdown report ────────────────────────────────────────────────
    lines = [
        "# Cenomi Chatbot — Test Query Results",
        "",
        f"**Run date:** {ts_human}  ",
        f"**Target:** `{BASE_URL}`  ",
        f"**Session prefix:** `{_RUN_ID}`",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total queries | {total} |",
        f"| ✅ Passed | {passed} ({pct}%) |",
        f"| ❌ Failed | {failed} |",
        f"| 💥 Errors | {errors} |",
        f"| Avg latency | {avg_ms} ms |",
        "",
    ]

    if failed or errors:
        lines += [
            "## Failures",
            "",
            "| ID | Query | Expected mode | Actual mode | Expected conf | Actual conf |",
            "|----|-------|--------------|-------------|--------------|-------------|",
        ]
        for r in all_results:
            if r.get("status") in ("FAIL", "ERROR"):
                if r["status"] == "FAIL":
                    lines.append(
                        f"| {r['id']} | `{r['query']}` "
                        f"| `{r['expected_mode']}` | `{r['actual_mode']}` "
                        f"| `{r['expected_conf']}` | `{r['actual_conf']}` |"
                    )
                else:
                    lines.append(
                        f"| {r['id']} | `{r['query']}` "
                        f"| — | ERROR | — | `{r.get('error', '')}` |"
                    )
        lines.append("")

    # Detailed per-test table
    lines += [
        "## All Results",
        "",
        "| # | ID | Query | Status | Mode | Conf | Flow | Playbook | Latency |",
        "|---|----|-------|--------|------|------|------|----------|---------|",
    ]

    # Smoke tests section
    lines.append(f"| | | **SMOKE TESTS** | | | | | | |")
    for i, r in enumerate(smoke_results, 1):
        icon = "✅" if r.get("status") == "PASS" else ("❌" if r.get("status") == "FAIL" else "💥")
        mode = r.get("actual_mode", r.get("error", "—"))
        conf = r.get("actual_conf", "—")
        flow = r.get("flow_type", "—")
        pb   = r.get("playbook_used", "—") or "—"
        lat  = f"{r.get('latency_ms', '—')}ms"
        lines.append(
            f"| {i} | {r['id']} | `{r['query']}` "
            f"| {icon} | `{mode}` | `{conf}` | {flow} | {pb} | {lat} |"
        )

    # Scenario tests section
    lines.append(f"| | | **SCENARIO TESTS** | | | | | | |")
    for i, r in enumerate(scenario_results, 1):
        icon = "✅" if r.get("status") == "PASS" else ("❌" if r.get("status") == "FAIL" else "💥")
        mode = r.get("actual_mode", r.get("error", "—"))
        conf = r.get("actual_conf", "—")
        flow = r.get("flow_type", "—")
        pb   = r.get("playbook_used", "—") or "—"
        lat  = f"{r.get('latency_ms', '—')}ms"
        lines.append(
            f"| {i} | {r['id']} | `{r['query']}` "
            f"| {icon} | `{mode}` | `{conf}` | {flow} | {pb} | {lat} |"
        )

    # Reply snippets for passing tests (useful reference)
    lines += [
        "",
        "## Reply Snippets",
        "",
        "| ID | Query | Reply (first 120 chars) |",
        "|----|-------|------------------------|",
    ]
    for r in all_results:
        snippet = r.get("reply_snippet", "")
        if snippet:
            safe = snippet.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {r['id']} | `{r['query']}` | {safe} |")

    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── JSON dump (full structured data) ──────────────────────────────
    payload = {
        "run_timestamp": ts_human,
        "run_id": _RUN_ID,
        "target": BASE_URL,
        "summary": {
            "total": total, "passed": passed, "failed": failed,
            "errors": errors, "pass_pct": pct, "avg_latency_ms": avg_ms,
        },
        "smoke_tests": smoke_results,
        "scenario_tests": scenario_results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"\n  Results written to:")
    print(f"    📄 {md_path}")
    print(f"    🗂  {json_path}")
    return md_path


if __name__ == "__main__":
    print("\nCenomi Chatbot — Test Query Runner")
    print(f"Target: {BASE_URL}\n")

    smoke_results    = run_tests(SMOKE_TESTS,    "SMOKE TESTS (10 queries)")
    scenario_results = run_tests(SCENARIO_TESTS, "SCENARIO TESTS (50+ queries)")

    print_summary(smoke_results, scenario_results)
    write_results(smoke_results, scenario_results)
