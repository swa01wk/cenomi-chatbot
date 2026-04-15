"""
Client Feedback Regression Tests — CF-01 through CF-05.

Evaluates the five fixes shipped for client feedback items:
  CF-01  Services are now answered (was: bot deflected all service queries)
  CF-02  Mall context is correct per mall_id (no cross-contamination)
  CF-03  Graceful-recovery does not loop (failed domain excluded from suggestions)
  CF-04  "Cinema Level" never appears — replaced with "Upper Level"
  CF-05  "Gallery" / "Main Gallery" never appears — replaced with "Ground Floor"

Prerequisites:
  Start the backend first:
    cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Usage:
  python tests/test_client_feedback.py
  python tests/test_client_feedback.py --base-url http://localhost:8000
  python tests/test_client_feedback.py --scenario "CF-01"
  python tests/test_client_feedback.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MALL_ID = "al_nakheel_plaza_28"
TENANT_ID       = "al_nakheel_plaza_28"
BASE_URL        = "http://localhost:8000"
TIMEOUT         = 60.0


# ─────────────────────────────────────────────────────────────────────────────
# Data models  (same structure as test_fix_regression.py)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    query:    str
    note:     str = ""
    must:     list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    # Optional debug-field checks: {"intent_domain": "services"}
    debug:    dict[str, str] = field(default_factory=dict)
    # Override mall_id per-turn (used by CF-02)
    mall_id:  str = ""


@dataclass
class Scenario:
    name:   str
    bug_id: str
    turns:  list[Turn]


@dataclass
class TurnResult:
    turn_idx:   int
    query:      str
    note:       str
    response:   str
    passed:     bool
    failures:   list[str]
    debug_info: dict[str, Any]
    elapsed_ms: int


@dataclass
class ScenarioResult:
    name:    str
    bug_id:  str
    turns:   list[TurnResult]
    total:   int
    passed:  int
    failed:  int


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [

    # ══════════════════════════════════════════════════════════════════════════
    # CF-01 — Services are answered
    #
    # Before fix: service queries (ATM, lost & found, prayer room, WiFi,
    # general facilities) either produced graceful-recovery responses or were
    # deflected to the information desk with no actual location data.
    # After fix:  the dedicated MALL SERVICES section is in the LLM context;
    #             expanded keywords route service queries to service_lookup;
    #             _lookup_service_details returns all matching services.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="CF-01 — Service queries are answered with actual data",
        bug_id="CF-01",
        turns=[
            Turn(
                query="Where is the ATM?",
                note="ATM is a named service — must give floor/location, not just redirect",
                must=["atm", "ground"],
                must_not=["i don't know", "cannot help", "i'm not sure"],
                debug={"intent_domain": "services"},
            ),
            Turn(
                query="Is there a lost and found?",
                note="Lost & Found service — must confirm existence and give location",
                must=["lost"],
                must_not=["i don't have", "unsupported", "i'm unable"],
                debug={"intent_domain": "services"},
            ),
            Turn(
                query="Do you have a prayer room?",
                note="Prayer room is in mall_profile.facilities — must give floor",
                must=["prayer", "ground"],
                must_not=["i don't have that", "cannot assist", "unsupported"],
                debug={"intent_domain": "services"},
            ),
            Turn(
                query="What facilities does the mall have?",
                note="General facilities list — must mention multiple services",
                must=["wheelchair", "atm"],
                must_not=["i don't know", "cannot help"],
                # intent may be 'services' or 'mall_info' — both are correct routing
            ),
            Turn(
                query="Is there WiFi here?",
                note="WiFi is in amenities — must not flatly refuse",
                must=[],
                must_not=["i'm unable to help", "unsupported", "that's outside"],
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # CF-02 — Mall context is correct per mall_id
    #
    # Each turn uses a different mall_id.  The response must contain context
    # specific to that mall and must NOT bleed in stores or city names from
    # other malls.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="CF-02 — Mall context is isolated per mall_id",
        bug_id="CF-02",
        turns=[
            Turn(
                query="What is this mall?",
                note="al_nakheel_plaza_28 = Al Nakheel Plaza, Buraidah",
                must=["nakheel", "buraidah"],
                must_not=["jeddah", "riyadh", "al ahsa"],
                mall_id="al_nakheel_plaza_28",
            ),
            Turn(
                query="What is this mall?",
                note="al_nakheel_plaza_13 = Mall of Arabia, Jeddah",
                must=["jeddah"],
                must_not=["buraidah"],
                mall_id="al_nakheel_plaza_13",
            ),
            Turn(
                query="What is this mall?",
                note="al_nakheel_plaza_27 = Al Nakheel Mall, Riyadh",
                must=["riyadh"],
                must_not=["buraidah", "jeddah"],
                mall_id="al_nakheel_plaza_27",
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # CF-03 — Graceful recovery does not loop
    #
    # When the bot cannot handle a query it enters graceful_recovery and
    # suggests alternative topics.  Before fix: the failed domain was always
    # included in the suggestion list, creating a loop.
    # After fix: the current domain is excluded from recovery suggestions.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="CF-03 — Recovery suggestions exclude the domain that just failed",
        bug_id="CF-03",
        turns=[
            Turn(
                query="Tell me a joke",
                note="Off-topic (general) → graceful recovery must suggest mall topics, not offer to tell jokes",
                must=[],
                # Bot may quote the query back in a polite refusal — check it doesn't OFFER jokes
                must_not=["here's a joke", "let me tell you a joke", "want to hear a joke", "sure, here"],
            ),
            Turn(
                query="Tell me another joke",
                note="Repeat off-topic — recovery must not offer to tell jokes",
                must=[],
                must_not=["here's another joke", "let me tell you another", "sure, here"],
            ),
            Turn(
                query="What can I do here?",
                note="Re-engage with real mall query — must give useful exploration, not an apology",
                must=["ground floor"],
                must_not=["apologies", "confusion", "unable to help"],
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # CF-04 — "Cinema Level" never appears in responses
    #
    # The canonical data and all prompts now use "Upper Level" instead.
    # Every response mentioning the cinema floor must say "Upper Level".
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="CF-04 — 'Cinema Level' replaced by 'Upper Level' in all responses",
        bug_id="CF-04",
        turns=[
            Turn(
                query="Where is Muvi Cinema?",
                note="Direct cinema location — must say upper level, never cinema level",
                must=["upper level"],
                must_not=["cinema level"],
                # intent may be 'entertainment' or 'navigation' — both are valid; content is what matters
            ),
            Turn(
                query="What floor is the cinema on?",
                note="Floor inquiry — must use upper level terminology",
                must=["upper level"],
                must_not=["cinema level"],
            ),
            Turn(
                query="Tell me about the mall",
                note="Mall overview — floor names must not include 'Cinema Level'",
                must=[],
                must_not=["cinema level"],
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # CF-05 — "Gallery" / "Main Gallery" never appears in responses
    #
    # Zone name "Main Gallery" has been replaced with "Ground Floor" throughout
    # al_nakheel_plaza_28 data and all prompt examples.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="CF-05 — 'Main Gallery' / 'Gallery' replaced by 'Ground Floor' in all responses",
        bug_id="CF-05",
        turns=[
            Turn(
                query="Where is Zara?",
                note="Store location — must say ground floor, never main gallery",
                must=["ground floor"],
                must_not=["main gallery", " gallery"],
            ),
            Turn(
                query="Where is the prayer room?",
                note="Facility location — must say ground floor, never main gallery",
                must=["ground floor"],
                must_not=["main gallery", " gallery"],
            ),
            Turn(
                query="Tell me about the mall",
                note="Overview — must not mention gallery",
                must=[],
                must_not=["main gallery", " gallery"],
            ),
            Turn(
                query="Where can I find perfumes?",
                note="Category search — must say ground floor, never main gallery",
                must=["ground floor"],
                must_not=["main gallery", " gallery"],
            ),
        ],
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

async def send_turn(
    client: httpx.AsyncClient,
    base_url: str,
    message: str,
    session_id: str | None,
    mall_id: str,
) -> tuple[str, str, dict[str, Any]]:
    """
    POST /api/chat and return (response_text, session_id, debug_dict).
    """
    payload: dict[str, Any] = {
        "message": message,
        "mall_id": mall_id,
        "tenant_id": mall_id,
        "debug": True,
    }
    if session_id:
        payload["session_id"] = session_id

    resp = await client.post(
        f"{base_url}/api/chat",
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    response_text = data.get("message", "")
    new_session_id = data.get("session_id", session_id or "")
    debug_info: dict[str, Any] = data.get("debug", {}) or {}

    return response_text, new_session_id, debug_info


def evaluate_turn(
    turn: Turn,
    response: str,
    debug_info: dict[str, Any],
) -> list[str]:
    """Return list of failure reasons (empty = pass)."""
    failures: list[str] = []
    resp_lower = response.lower()

    for kw in turn.must:
        if kw.lower() not in resp_lower:
            failures.append(f"MISSING must-have keyword: '{kw}'")

    for kw in turn.must_not:
        if kw.lower() in resp_lower:
            failures.append(f"FORBIDDEN keyword found: '{kw}'")

    for field_name, expected_value in turn.debug.items():
        actual = str(debug_info.get(field_name, "")).lower()
        if expected_value.lower() not in actual:
            failures.append(
                f"DEBUG MISMATCH {field_name}: expected '{expected_value}', got '{actual}'"
            )

    return failures


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_scenario(
    scenario: Scenario,
    base_url: str,
    verbose: bool = False,
) -> ScenarioResult:
    session_id: str | None = None
    turn_results: list[TurnResult] = []

    async with httpx.AsyncClient() as client:
        for idx, turn in enumerate(scenario.turns):
            # CF-02 uses per-turn mall_id; reset session for each turn so
            # context doesn't bleed between malls.
            mall_id = turn.mall_id or DEFAULT_MALL_ID
            if turn.mall_id:
                session_id = None  # fresh session per mall

            start = time.monotonic()
            try:
                response, session_id, debug_info = await send_turn(
                    client, base_url, turn.query, session_id, mall_id
                )
                elapsed = int((time.monotonic() - start) * 1000)
                failures = evaluate_turn(turn, response, debug_info)
                passed = len(failures) == 0
            except Exception as exc:
                elapsed = int((time.monotonic() - start) * 1000)
                response = ""
                debug_info = {}
                failures = [f"REQUEST ERROR: {exc}"]
                passed = False

            result = TurnResult(
                turn_idx=idx,
                query=turn.query,
                note=turn.note,
                response=response,
                passed=passed,
                failures=failures,
                debug_info=debug_info,
                elapsed_ms=elapsed,
            )
            turn_results.append(result)

            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] Turn {idx + 1}: {turn.query[:60]!r}  ({elapsed} ms)")
            if not passed or verbose:
                for f in failures:
                    print(f"         ✗ {f}")
                if verbose and response:
                    print(f"         Response: {response[:200]!r}")

    total  = len(turn_results)
    passed = sum(1 for r in turn_results if r.passed)
    failed = total - passed
    return ScenarioResult(
        name=scenario.name,
        bug_id=scenario.bug_id,
        turns=turn_results,
        total=total,
        passed=passed,
        failed=failed,
    )


async def run_all(
    base_url: str,
    filter_name: str | None = None,
    verbose: bool = False,
) -> int:
    """Run all (or filtered) scenarios. Returns exit code (0=all pass)."""
    selected = SCENARIOS
    if filter_name:
        selected = [s for s in SCENARIOS if filter_name.lower() in s.name.lower() or filter_name.upper() == s.bug_id]
    if not selected:
        print(f"No scenarios match '{filter_name}'")
        return 1

    print(f"\nRunning {len(selected)} client-feedback scenario(s) against {base_url}\n")
    print("=" * 70)

    all_results: list[ScenarioResult] = []
    for scenario in selected:
        print(f"\n{scenario.bug_id}: {scenario.name}")
        print("-" * 70)
        result = await run_scenario(scenario, base_url, verbose=verbose)
        all_results.append(result)
        summary = f"  → {result.passed}/{result.total} turns passed"
        if result.failed:
            summary += f"  ({result.failed} FAILED)"
        print(summary)

    # ── Grand summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    total_scenarios = len(all_results)
    passed_scenarios = sum(1 for r in all_results if r.failed == 0)
    total_turns   = sum(r.total  for r in all_results)
    passed_turns  = sum(r.passed for r in all_results)

    for r in all_results:
        status = "PASS" if r.failed == 0 else "FAIL"
        print(f"  [{status}] {r.bug_id}: {r.passed}/{r.total} turns  — {r.name}")

    print()
    print(f"Scenarios : {passed_scenarios}/{total_scenarios} passed")
    print(f"Turns     : {passed_turns}/{total_turns} passed")

    return 0 if passed_scenarios == total_scenarios else 1


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Client Feedback Regression Tests")
    parser.add_argument("--base-url", default=BASE_URL, help="Backend base URL")
    parser.add_argument("--scenario", default=None, help="Filter by scenario name or ID (e.g. CF-01)")
    parser.add_argument("--verbose", action="store_true", help="Print full response text on failures")
    args = parser.parse_args()

    exit_code = asyncio.run(run_all(args.base_url, args.scenario, args.verbose))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
