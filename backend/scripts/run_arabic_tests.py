#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arabic test runner — translates all English test queries to Arabic then runs
every suite against the chatbot with language="ar" in the request payload.

Translations are cached to arabic_translations_cache.json alongside this
script so repeated runs are instant (no extra API calls).

Usage
─────
    # translate + run all suites
    python scripts/run_arabic_tests.py

    # skip re-translation (use cache only, fail if a query is missing)
    python scripts/run_arabic_tests.py --no-translate

    # run a single suite
    python scripts/run_arabic_tests.py --suite smoke
    python scripts/run_arabic_tests.py --suite broken
    python scripts/run_arabic_tests.py --suite complex
    python scripts/run_arabic_tests.py --suite session
    python scripts/run_arabic_tests.py --suite new-malls
    python scripts/run_arabic_tests.py --suite v16

Requirements
────────────
    pip install openai httpx          (both already in backend deps)
    BACKEND_OPENAI_API_KEY or OPENAI_API_KEY set in backend/.env or env
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

import httpx

# ---------------------------------------------------------------------------
# Path setup — make sure backend/ is importable
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_CACHE_PATH  = _SCRIPT_DIR / "arabic_translations_cache.json"

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Import all test data from the existing runner (single source of truth).
from scripts.run_all_tests import (  # noqa: E402
    SMOKE_TESTS,
    SCENARIO_TESTS,
    BROKEN_TESTS,
    COMPLEX_TESTS,
    SESSION_TESTS,
    NEW_MALL_SMOKE_TESTS,
    V16_FEATURE_TESTS,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL   = "http://127.0.0.1:8000/api/chat"
TIMEOUT    = 60
_RUN_ID    = uuid.uuid4().hex[:8]
_BATCH_SZ  = 50          # queries per OpenAI translation call
_MODEL     = "gpt-4o-mini"

# Queries that are intentionally malformed / non-linguistic.  They are sent
# to the backend as-is (no translation) because they test graceful recovery
# from garbage input — Arabic detection is irrelevant for these.
_PASSTHROUGH_QUERIES: frozenset[str] = frozenset({
    "asdf",
    "a",
    "Nkie shoes",   # deliberate typo — kept in English to test typo handling
})

# ---------------------------------------------------------------------------
# .env loader (mirrors run_pipeline_evaluation.py)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Translation cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict[str, str]:
    """Load existing English→Arabic translation cache from disk."""
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    _CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ---------------------------------------------------------------------------
# OpenAI batch translation
# ---------------------------------------------------------------------------

def _translate_batch(queries: list[str], api_key: str) -> list[str]:
    """
    Translate a list of English queries to colloquial Saudi Arabic via OpenAI.
    Returns translations in the same order as the input.
    """
    from openai import OpenAI  # local import — only needed when translating

    client = OpenAI(api_key=api_key)
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries))
    system_msg = (
        "You are a translator for a Saudi mall chatbot test suite. "
        "Translate the following numbered English queries into natural, colloquial "
        "Saudi Arabic as a mall visitor would actually say them. "
        "Transliterate brand names into Arabic script as Arabs commonly write them: "
        "Zara→زارا, Nike→نايك, Starbucks→ستاربكس, Adidas→أديداس, "
        "McDonald's→ماكدونالدز, H&M→اتش اند ام, Sephora→سيفورا, "
        "Zara→زارا, Cenomi→سينومي. "
        "Keep mall IDs (like al_nakheel_plaza_28) and technical system terms unchanged. "
        "Output ONLY a valid JSON array of translated strings, in exactly the same "
        "order as the input. No extra keys, no numbering."
    )
    user_msg = f"Queries to translate:\n{numbered}\n\nOutput JSON array:"

    response = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or "{}"
    # The model may wrap in {"translations": [...]} or return a bare array.
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        results = parsed
    elif isinstance(parsed, dict):
        # Accept any single list value
        for v in parsed.values():
            if isinstance(v, list):
                results = v
                break
        else:
            raise ValueError(f"Unexpected JSON shape from translation model: {raw[:200]}")
    else:
        raise ValueError(f"Unexpected JSON type from translation model: {type(parsed)}")

    if len(results) != len(queries):
        raise ValueError(
            f"Translation count mismatch: expected {len(queries)}, got {len(results)}"
        )
    return [str(r) for r in results]


def translate_queries(
    queries: list[str],
    cache: dict[str, str],
    api_key: str,
    no_translate: bool,
) -> dict[str, str]:
    """
    Ensure every query in `queries` has a cached Arabic translation.
    Passthrough queries are copied verbatim.  If `no_translate` is True,
    missing queries raise an error instead of calling OpenAI.

    Returns the updated cache (also mutates `cache` in-place).
    """
    # Passthrough queries are always mapped to themselves.
    for q in queries:
        if q in _PASSTHROUGH_QUERIES:
            cache[q] = q

    missing = [q for q in queries if q not in cache]
    if not missing:
        return cache

    if no_translate:
        raise SystemExit(
            f"--no-translate set but {len(missing)} queries are not cached.\n"
            "Run without --no-translate first to populate the cache."
        )

    if not api_key:
        raise SystemExit(
            "No OpenAI API key found. Set BACKEND_OPENAI_API_KEY in backend/.env "
            "or export OPENAI_API_KEY before running."
        )

    print(f"\n  Translating {len(missing)} queries to Arabic …")
    for batch_start in range(0, len(missing), _BATCH_SZ):
        batch = missing[batch_start : batch_start + _BATCH_SZ]
        print(f"    Batch {batch_start // _BATCH_SZ + 1}: {len(batch)} queries …", end=" ", flush=True)
        translated = _translate_batch(batch, api_key)
        for en, ar in zip(batch, translated):
            cache[en] = ar
        print("done")

    _save_cache(cache)
    print(f"  Cache saved → {_CACHE_PATH}")
    return cache

# ---------------------------------------------------------------------------
# Convert test tuples to Arabic
# ---------------------------------------------------------------------------

def _ar_5(tests: list[tuple], cache: dict[str, str]) -> list[tuple]:
    """Replace the query field (index 2) in 5-tuples with its Arabic translation."""
    return [
        (tid, session, cache[query], mode, conf)
        for tid, session, query, mode, conf in tests
    ]


def _ar_6(tests: list[tuple], cache: dict[str, str]) -> list[tuple]:
    """Replace the query field (index 2) in 6-tuples with its Arabic translation."""
    return [
        (tid, session, cache[query], mode, conf, mall_id)
        for tid, session, query, mode, conf, mall_id in tests
    ]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def send_message(session_id: str, message: str, mall_id: str = "al_nakheel_plaza_28") -> dict:
    unique_session = f"{session_id}-ar-{_RUN_ID}"
    payload = {
        "message": message,
        "session_id": unique_session,
        "mall_id": mall_id,
        "debug": True,
        "language": "ar",
    }
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
        "context_depth":    scene.get("turn_count", "—"),
    }

# ---------------------------------------------------------------------------
# Suite runners
# ---------------------------------------------------------------------------

def run_suite(tests: list[tuple], label: str) -> list[dict]:
    results: list[dict] = []
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    for row in tests:
        tid, session, query, exp_mode, exp_conf = row
        try:
            t0 = time.time()
            data = send_message(session, query)
            elapsed = (time.time() - t0) * 1000
            reply = data.get("reply", data.get("message", ""))
            dbg = extract_debug(data)

            mode_ok = dbg["response_mode"] == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf
            status  = "PASS" if (mode_ok and conf_ok) else "FAIL"

            results.append({
                "id":            tid,
                "query":         query,
                "status":        status,
                "mode_ok":       mode_ok,
                "conf_ok":       conf_ok,
                "expected_mode": exp_mode,
                "actual_mode":   dbg["response_mode"],
                "expected_conf": exp_conf,
                "actual_conf":   dbg["confidence_level"],
                "flow_type":     dbg["flow_type"],
                "playbook_used": dbg["playbook_used"],
                "topic_lock":    dbg["topic_lock"],
                "latency_ms":    round(elapsed),
                "reply_snippet": reply[:140] if reply else "",
            })

            icon = "✅" if status == "PASS" else "❌"
            print(f"\n{icon} [{tid}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:35s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:35s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Flow/PB:    {dbg['flow_type']} / {dbg['playbook_used']}")
            print(f"   Reply:      {reply[:140]!r}")
            print(f"   Latency:    {round(elapsed)}ms")

        except Exception as exc:
            results.append({
                "id": tid, "query": query,
                "status": "ERROR", "error": str(exc),
            })
            print(f"\n💥 [{tid}] {query!r}")
            print(f"   ERROR: {exc}")

    return results


def run_suite_with_mall(tests: list[tuple], label: str) -> list[dict]:
    """Runner for Suite 5 which carries mall_id as a 6th tuple element."""
    results: list[dict] = []
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    for row in tests:
        tid, session, query, exp_mode, exp_conf, mall_id = row
        try:
            t0 = time.time()
            data = send_message(session, query, mall_id=mall_id)
            elapsed = (time.time() - t0) * 1000
            reply = data.get("reply", data.get("message", ""))
            dbg = extract_debug(data)

            mode_ok = dbg["response_mode"] == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf
            status  = "PASS" if (mode_ok and conf_ok) else "FAIL"

            results.append({
                "id":            tid,
                "query":         query,
                "mall_id":       mall_id,
                "status":        status,
                "mode_ok":       mode_ok,
                "conf_ok":       conf_ok,
                "expected_mode": exp_mode,
                "actual_mode":   dbg["response_mode"],
                "expected_conf": exp_conf,
                "actual_conf":   dbg["confidence_level"],
                "flow_type":     dbg["flow_type"],
                "playbook_used": dbg["playbook_used"],
                "topic_lock":    dbg["topic_lock"],
                "latency_ms":    round(elapsed),
                "reply_snippet": reply[:140] if reply else "",
            })

            icon = "✅" if status == "PASS" else "❌"
            print(f"\n{icon} [{tid}] [{mall_id}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:35s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:35s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Flow/PB:    {dbg['flow_type']} / {dbg['playbook_used']}")
            print(f"   Reply:      {reply[:140]!r}")
            print(f"   Latency:    {round(elapsed)}ms")

        except Exception as exc:
            results.append({
                "id": tid, "query": query, "mall_id": mall_id,
                "status": "ERROR", "error": str(exc),
            })
            print(f"\n💥 [{tid}] [{mall_id}] {query!r}")
            print(f"   ERROR: {exc}")

    return results

# ---------------------------------------------------------------------------
# Summary + file output
# ---------------------------------------------------------------------------

def print_summary(all_suites: dict[str, list[dict]]) -> None:
    all_results = [r for results in all_suites.values() for r in results]
    total  = len(all_results)
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    failed = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors = sum(1 for r in all_results if r.get("status") == "ERROR")

    print(f"\n{'='*70}")
    print(f"  SUMMARY  (language=ar)")
    print(f"{'='*70}")
    print(f"  Total  : {total}")
    print(f"  Passed : {passed} ({round(passed/total*100) if total else 0}%)")
    print(f"  Failed : {failed}")
    print(f"  Errors : {errors}")

    for suite_name, results in all_suites.items():
        s_total  = len(results)
        s_passed = sum(1 for r in results if r.get("status") == "PASS")
        print(f"\n  {suite_name}: {s_passed}/{s_total} passed")

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


def write_results(all_suites: dict[str, list[dict]]) -> str:
    all_results = [r for results in all_suites.values() for r in results]
    total  = len(all_results)
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    failed = sum(1 for r in all_results if r.get("status") == "FAIL")
    errors = sum(1 for r in all_results if r.get("status") == "ERROR")
    pct    = round(passed / total * 100) if total else 0
    avg_ms = round(
        sum(r.get("latency_ms", 0) for r in all_results if "latency_ms" in r)
        / max(1, sum(1 for r in all_results if "latency_ms" in r))
    )

    now_utc  = datetime.now(timezone.utc)
    ts_label = now_utc.strftime("%Y-%m-%d_%H-%M-%S")
    ts_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    results_dir = _BACKEND_DIR.parent / "test-results"
    results_dir.mkdir(exist_ok=True)

    md_path   = results_dir / f"run_arabic_{ts_label}.md"
    json_path = results_dir / f"run_arabic_{ts_label}.json"

    # ── Markdown report ────────────────────────────────────────────────────
    lines = [
        "# Cenomi Chatbot — Arabic Test Suite Results",
        "",
        f"**Run date:** {ts_human}  ",
        f"**Target:** `{BASE_URL}`  ",
        f"**Language:** `ar` (Arabic — colloquial Saudi)  ",
        f"**Session prefix:** `{_RUN_ID}`  ",
        f"**Translation cache:** `{_CACHE_PATH.name}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total queries | {total} |",
        f"| ✅ Passed | {passed} ({pct}%) |",
        f"| ❌ Failed | {failed} |",
        f"| 💥 Errors | {errors} |",
        f"| Avg latency | {avg_ms} ms |",
        "",
        "## Suite Breakdown",
        "",
        "| Suite | Queries | Passed | Failed | Errors |",
        "|-------|---------|--------|--------|--------|",
    ]
    for suite_name, results in all_suites.items():
        s_total  = len(results)
        s_passed = sum(1 for r in results if r.get("status") == "PASS")
        s_failed = sum(1 for r in results if r.get("status") == "FAIL")
        s_errors = sum(1 for r in results if r.get("status") == "ERROR")
        lines.append(f"| {suite_name} | {s_total} | {s_passed} | {s_failed} | {s_errors} |")
    lines.append("")

    if failed or errors:
        lines += [
            "## Failures",
            "",
            "| ID | Query (Arabic) | Expected mode | Actual mode | Expected conf | Actual conf |",
            "|----|----------------|--------------|-------------|--------------|-------------|",
        ]
        for r in all_results:
            if r.get("status") in ("FAIL", "ERROR"):
                if r["status"] == "FAIL":
                    lines.append(
                        f"| {r['id']} | `{r['query'][:60]}` "
                        f"| `{r['expected_mode']}` | `{r['actual_mode']}` "
                        f"| `{r['expected_conf']}` | `{r['actual_conf']}` |"
                    )
                else:
                    lines.append(
                        f"| {r['id']} | `{r['query'][:60]}` "
                        f"| — | ERROR | — | `{r.get('error', '')[:60]}` |"
                    )
        lines.append("")

    # Per-suite tables
    for suite_name, results in all_suites.items():
        lines += [
            f"## {suite_name}",
            "",
            "| # | ID | Query (Arabic) | Status | Mode | Conf | Flow | Latency |",
            "|---|----|----------------|--------|------|------|------|---------|",
        ]
        for i, r in enumerate(results, 1):
            icon = "✅" if r.get("status") == "PASS" else ("❌" if r.get("status") == "FAIL" else "💥")
            mode = r.get("actual_mode", r.get("error", "—"))[:30]
            conf = r.get("actual_conf", "—")
            flow = r.get("flow_type", "—") or "—"
            lat  = f"{r.get('latency_ms', '—')}ms"
            q    = r["query"][:55]
            lines.append(f"| {i} | {r['id']} | `{q}` | {icon} | `{mode}` | `{conf}` | {flow} | {lat} |")
        lines.append("")

    # Reply snippets
    lines += [
        "## Reply Snippets",
        "",
        "| ID | Query (Arabic) | Reply (first 140 chars) |",
        "|----|----------------|------------------------|",
    ]
    for r in all_results:
        snippet = r.get("reply_snippet", "")
        if snippet:
            safe_q = r["query"][:50].replace("|", "\\|")
            safe_r = snippet.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {r['id']} | `{safe_q}` | {safe_r} |")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    # ── JSON dump ──────────────────────────────────────────────────────────
    json_payload = {
        "run_timestamp": ts_human,
        "run_id":        _RUN_ID,
        "language":      "ar",
        "target":        BASE_URL,
        "summary": {
            "total": total, "passed": passed, "failed": failed,
            "errors": errors, "pass_pct": pct, "avg_latency_ms": avg_ms,
        },
        "suites": {name: results for name, results in all_suites.items()},
    }
    json_path.write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n  Results written to:")
    print(f"    📄 {md_path}")
    print(f"    🗂  {json_path}")
    return str(md_path)

# ---------------------------------------------------------------------------
# Server health check
# ---------------------------------------------------------------------------

def wait_for_server(max_wait: int = 120) -> bool:
    health_url = BASE_URL.replace("/api/chat", "/api/health")
    deadline = time.time() + max_wait
    print(f"  Waiting for server at {health_url} …", end="", flush=True)
    while time.time() < deadline:
        try:
            r = httpx.get(health_url, timeout=5)
            if r.status_code == 200:
                print(" ✓ ready")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(3)
    print(" ✗ timed out")
    return False

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Cenomi chatbot test suites in Arabic (language=ar)"
    )
    parser.add_argument(
        "--suite",
        choices=["smoke", "broken", "complex", "session", "new-malls", "v16", "all"],
        default="all",
        help="Which suite(s) to run (default: all)",
    )
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="Use cache only; error if any query is missing from the cache",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Skip server health-check wait",
    )
    args = parser.parse_args()

    print("\nCenomi Chatbot — Arabic Test Suite Runner")
    print(f"Target : {BASE_URL}")
    print(f"Run ID : {_RUN_ID}")
    print(f"Language: ar (language=ar sent on every request)")

    # ── 1. Collect all unique English queries needed for this run ──────────
    all_5tuple_suites: list[list[tuple]] = []
    all_6tuple_suites: list[list[tuple]] = []

    if args.suite in ("smoke", "all"):
        all_5tuple_suites.append(SMOKE_TESTS)
    if args.suite in ("smoke", "all"):  # scenarios are always bundled with smoke
        all_5tuple_suites.append(SCENARIO_TESTS)
    if args.suite in ("broken", "all"):
        all_5tuple_suites.append(BROKEN_TESTS)
    if args.suite in ("complex", "all"):
        all_5tuple_suites.append(COMPLEX_TESTS)
    if args.suite in ("session", "all"):
        all_5tuple_suites.append(SESSION_TESTS)
    if args.suite in ("new-malls", "all"):
        all_6tuple_suites.append(NEW_MALL_SMOKE_TESTS)
    if args.suite in ("v16", "all"):
        all_5tuple_suites.append(V16_FEATURE_TESTS)

    unique_queries: list[str] = list(
        dict.fromkeys(
            row[2]
            for suite in all_5tuple_suites + all_6tuple_suites
            for row in suite
        )
    )

    # ── 2. Translate (or load from cache) ─────────────────────────────────
    cache = _load_cache()
    cache = translate_queries(
        unique_queries, cache,
        api_key=_get_api_key(),
        no_translate=args.no_translate,
    )

    # ── 3. Check server ───────────────────────────────────────────────────
    if not args.no_wait:
        if not wait_for_server():
            print("\nServer did not become ready in time. Aborting.")
            sys.exit(1)

    # ── 4. Run suites ──────────────────────────────────────────────────────
    all_suites: dict[str, list[dict]] = {}

    if args.suite in ("smoke", "all"):
        smoke_r    = run_suite(_ar_5(SMOKE_TESTS, cache),    "SMOKE TESTS (AR) — test-queries.md")
        scenario_r = run_suite(_ar_5(SCENARIO_TESTS, cache), "SCENARIO TESTS (AR) — test-queries.md")
        all_suites["test-queries.md (smoke) [ar]"]     = smoke_r
        all_suites["test-queries.md (scenarios) [ar]"] = scenario_r

    if args.suite in ("broken", "all"):
        broken_r = run_suite(_ar_5(BROKEN_TESTS, cache), "BROKEN / MULTI-TURN (AR) — test-queries-broken.md")
        all_suites["test-queries-broken.md [ar]"] = broken_r

    if args.suite in ("complex", "all"):
        complex_r = run_suite(_ar_5(COMPLEX_TESTS, cache), "COMPLEX SCENARIOS (AR) — test-queries-complex.md")
        all_suites["test-queries-complex.md [ar]"] = complex_r

    if args.suite in ("session", "all"):
        session_r = run_suite(_ar_5(SESSION_TESTS, cache), "SESSION SCENARIO (AR) — session-4647af8c1ae742e3")
        all_suites["session-scenario.json [ar]"] = session_r

    if args.suite in ("new-malls", "all"):
        new_mall_r = run_suite_with_mall(
            _ar_6(NEW_MALL_SMOKE_TESTS, cache),
            "SUITE 5 (AR) — NEW MALL SMOKE + 5-MALL CROSS SEARCH",
        )
        all_suites["suite5-new-malls [ar]"] = new_mall_r

    if args.suite in ("v16", "all"):
        v16_r = run_suite(_ar_5(V16_FEATURE_TESTS, cache), "SUITE 6 (AR) — v1.6 FEATURE SCENARIOS")
        all_suites["suite6-v16-features [ar]"] = v16_r

    print_summary(all_suites)
    write_results(all_suites)
