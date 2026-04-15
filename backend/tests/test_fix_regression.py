"""
Regression tests for classification + pipeline bugs fixed in the fix/testing-bugs branch.

Each scenario maps directly to a real failure observed in exported conversations
(test_scenario.json).  Tests use the live /api/chat endpoint, one session per scenario.

Bugs covered
────────────
  BUG-01  Self-reinforcing disengagement loop
          crisis → positive thanks → every message thereafter classified as disengagement
  BUG-02  "Thanks" / gratitude classified as followup when bot just asked a question
  BUG-03  "Thanks Chatbot" and similar addressed-thanks classified as acknowledgement
  BUG-04  "Okay stop" / "stop" not in disengagement examples → treated as followup
  BUG-05  Bot-complaint messages ("Chatbot is not working", "not working") → acknowledgement
  BUG-06  "Binoo is pissed" / third-party anger → acknowledgement instead of disengagement
  BUG-07  Emotional distress + mood request ("I'm sad, give me a plan") → shopping/refinement
  BUG-08  Domain-change without transition words ("men's wear" while topic=dining) → wrong domain
  BUG-09  Smalltalk kind written to conversation_mode → next normal query sees stale mode
  BUG-10  Stream endpoint missing conversation_history → LLM blind to prior context

Prerequisites
─────────────
  Start the backend:
    cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Usage
─────
  python tests/test_fix_regression.py
  python tests/test_fix_regression.py --base-url http://localhost:8000
  python tests/test_fix_regression.py --scenario "BUG-01"
  python tests/test_fix_regression.py --verbose
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

MALL_ID   = "al_nakheel_plaza_28"
TENANT_ID = "al_nakheel_plaza_28"
BASE_URL  = "http://localhost:8000"
TIMEOUT   = 60.0


# ─────────────────────────────────────────────────────────────────────────────
# Data models  (identical to test_e2e_scenarios.py for compatibility)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    query:    str
    note:     str = ""
    must:     list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    debug:    dict[str, str] = field(default_factory=dict)


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
# Regression scenarios
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-01 — Self-reinforcing disengagement loop
    #
    # Reproduction: crisis message sets recent_mood=disengagement.  The next
    # turn's LLM classifier receives "Visitor's recent emotional state: disengagement
    # (factor this into classification)" and tags every subsequent message as
    # disengagement — even "Great! Amazing thanks", "food", "movies".
    # Expected: after disengagement_recovery is served, mood is cleared and the
    # next real query is answered normally.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-01 — Disengagement loop: crisis → positive thanks → normal queries recover",
        bug_id="BUG-01",
        turns=[
            Turn(
                query="Anything quick before movie?",
                note="Baseline: normal dining query → recommendations returned",
                must=["food court", "ground"],
                must_not=["apologies", "confusion", "sorry"],
                debug={"message_kind": "fresh_request", "intent_domain": "dining"},
            ),
            Turn(
                query="Suicide pls",
                note="Crisis → empathy + helpline, no mall recs",
                must=["reach out"],
                must_not=["restaurant", "cinema", "food court", "shop"],
                debug={"message_kind": "crisis"},
            ),
            Turn(
                query="Great! Amazing thanks",
                note="CRITICAL: positive gratitude after crisis — must be 'thanks', not 'disengagement'",
                must_not=["apologies for the confusion", "let's try again", "my bad"],
                debug={"message_kind": "thanks"},
            ),
            Turn(
                query="food",
                note="Normal query after loop — must NOT get apology/recovery response",
                must=["ground", "food"],
                must_not=["apologies for the confusion", "let's try again", "my bad", "sorry if"],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="Movies",
                note="Another normal query — pipeline must be fully recovered",
                must=["cinema", "muvi"],
                must_not=["apologies", "confusion", "sorry if"],
                debug={"intent_domain": "entertainment"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-02 — "Thanks" after bot question classified as followup
    #
    # When the bot ends its last message with a question or offer
    # ("Want me to narrow this down?") the LLM incorrectly treats "Thanks" as
    # an affirmative confirmation (followup) and runs the full pipeline again,
    # recycling the same recommendations.
    # Expected: "Thanks" is ALWAYS classified as "thanks" regardless of what the
    # bot just said.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-02 — Thanks after bot question → must be 'thanks', not 'followup'",
        bug_id="BUG-02",
        turns=[
            Turn(
                query="Perfume for my grandma",
                note="Setup: get a recommendation with a follow-up offer from the bot",
                must=["l'occitane", "ground"],
                must_not=[],
                debug={"message_kind": "fresh_request"},
            ),
            Turn(
                query="Thanks",
                note="CRITICAL: 'Thanks' after bot question → must route to smalltalk thanks pool, "
                     "NOT repeat recommendations",
                must=[],
                must_not=["l'occitane", "for your grandma", "main gallery"],
                debug={"message_kind": "thanks"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-03 — "Thanks Chatbot" classified as acknowledgement
    #
    # Addressed-thanks ("Thanks Chatbot", "Thank you bot") were landing in the
    # acknowledgement clarification path instead of the smalltalk thanks pool.
    # Expected: any message whose PRIMARY meaning is gratitude → "thanks".
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-03 — Addressed thanks ('Thanks Chatbot') → 'thanks', not 'acknowledgement'",
        bug_id="BUG-03",
        turns=[
            Turn(
                query="What dining options are here?",
                note="Setup: get a dining recommendation",
                must=["ground"],
                must_not=[],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="Thanks Chatbot",
                note="Addressed thanks → thanks smalltalk pool (NOT acknowledgement clarification)",
                must=[],
                must_not=["what specifically are you after", "what are you looking for"],
                debug={"message_kind": "thanks"},
            ),
            Turn(
                query="thanks a lot",
                note="Variant — casual gratitude → thanks",
                must=[],
                must_not=["what specifically", "what are you looking for"],
                debug={"message_kind": "thanks"},
            ),
            Turn(
                query="great thanks",
                note="Positive + gratitude → thanks",
                must=[],
                must_not=["apologies", "confusion"],
                debug={"message_kind": "thanks"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-04 — "Okay stop" / "stop" classified as followup
    #
    # "Okay stop", "ok stop", "stop" were not in the disengagement examples, so
    # the LLM treated them as affirmative follow-ups and ran the full pipeline,
    # sometimes hallucinating a response about a completely different topic.
    # Expected: stop variants → disengagement → empathetic recovery response.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-04 — 'Okay stop' / 'stop' → 'disengagement', not 'followup'",
        bug_id="BUG-04",
        turns=[
            Turn(
                query="Perfume options please",
                note="Setup: active topic = shopping/perfume",
                must=["ground"],
                must_not=[],
                debug={"intent_domain": "shopping"},
            ),
            Turn(
                query="Okay stop",
                note="'Okay stop' → disengagement recovery; must NOT recycle perfume recs",
                must=[],
                must_not=["l'occitane", "abdusamad", "perfume", "for your grandma"],
                debug={"message_kind": "disengagement"},
            ),
        ],
    ),

    Scenario(
        name="BUG-04b — 'stop' alone → 'disengagement'",
        bug_id="BUG-04",
        turns=[
            Turn(
                query="Food options",
                note="Setup: active topic = dining",
                must=[],
                must_not=[],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="stop",
                note="'stop' → disengagement",
                must=[],
                must_not=[],
                debug={"message_kind": "disengagement"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-05 — Bot-complaint messages classified as acknowledgement
    #
    # "Chatbot is not working", "not working", "this isn't helping" were landing
    # in the acknowledgement path which simply asks "Still thinking about X?".
    # This is tone-deaf — the user is frustrated.
    # Expected: bot-complaint messages → disengagement → empathetic response.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-05 — Bot complaint → 'disengagement', not 'acknowledgement'",
        bug_id="BUG-05",
        turns=[
            Turn(
                query="Show me shopping options",
                note="Setup: get into a topic",
                must=[],
                must_not=[],
                debug={"intent_domain": "shopping"},
            ),
            Turn(
                query="Chatbot is not working",
                note="Bot complaint → disengagement (must NOT say 'still thinking about shopping?')",
                must=[],
                must_not=["still thinking about shopping", "still thinking about"],
                debug={"message_kind": "disengagement"},
            ),
            Turn(
                query="Not working",
                note="Short complaint → disengagement",
                must=[],
                must_not=["still thinking about"],
                debug={"message_kind": "disengagement"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-06 — Third-party anger ("Binoo is pissed") classified as acknowledgement
    #
    # Expressions of anger/frustration about a person were landing in the
    # acknowledgement clarification path.
    # Expected: frustration/anger expressions → disengagement or emotional.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-06 — Third-party anger ('X is pissed') → disengagement/emotional",
        bug_id="BUG-06",
        turns=[
            Turn(
                query="What can we do here?",
                note="Setup: exploration query",
                must=[],
                must_not=[],
            ),
            Turn(
                query="Binoo is pissed",
                note="Anger expression → disengagement or emotional (NOT acknowledgement clarification)",
                must=[],
                must_not=["still thinking about", "what specifically are you after"],
                debug={"message_kind": "disengagement"},
            ),
            Turn(
                query="I'm pissed",
                note="Self-anger → disengagement",
                must=[],
                must_not=["still thinking about", "what specifically"],
                debug={"message_kind": "disengagement"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-07 — Emotional distress + mood request classified as shopping/refinement
    #
    # "I'm sad, give me a plan that will make me happy" was classified as
    # shopping/gift_recommendation/refinement because shopping was the active topic.
    # The LLM inherited the wrong domain instead of recognising the emotional state.
    # Expected: emotional kind → exploration/activity_suggestion → uplifting plan.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-07 — Emotional distress + plan request → 'emotional', not refinement",
        bug_id="BUG-07",
        turns=[
            Turn(
                query="Baby clothes please",
                note="Setup: active topic = shopping/children",
                must=[],
                must_not=[],
                debug={"intent_domain": "shopping"},
            ),
            Turn(
                query="I'm sad, give me a plan that will make me happy",
                note="CRITICAL: mood + activity request → emotional (NOT shopping refinement). "
                     "Response must be empathetic + suggest a feel-good visit plan.",
                must=[],
                must_not=["baby", "centrepoint", "mothercare", "children's"],
                debug={"message_kind": "emotional"},
            ),
        ],
    ),

    Scenario(
        name="BUG-07b — Standalone emotional distress",
        bug_id="BUG-07",
        turns=[
            Turn(
                query="I'm sad",
                note="Pure emotional → emotional kind → empathetic response",
                must=[],
                must_not=["baby", "shopping"],
                debug={"message_kind": "emotional"},
            ),
            Turn(
                query="cheer me up",
                note="Mood request → emotional + mood-plan response",
                must=[],
                must_not=[],
                debug={"message_kind": "emotional"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-08 — Domain switch without transition words → wrong domain lock
    #
    # "men's wear" when active_topic=dining was classified as dining/family_dining/followup
    # because no transition word ("instead", "forget that") was present.
    # Expected: when the message clearly belongs to a different domain from active_topic,
    # classify as topic_switch in the correct domain.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-08 — Domain switch without transition word → topic_switch",
        bug_id="BUG-08",
        turns=[
            Turn(
                query="Vegetarian food options",
                note="Setup: active topic = dining",
                must=["ground"],
                must_not=[],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="men's wear",
                note="Clear domain switch (dining → shopping) without transition word → topic_switch",
                must=[],
                must_not=["food court", "mcdonald", "restaurant", "vegetarian"],
                debug={"message_kind": "topic_switch", "intent_domain": "shopping"},
            ),
        ],
    ),

    Scenario(
        name="BUG-08b — Domain switch: shopping → entertainment",
        bug_id="BUG-08",
        turns=[
            Turn(
                query="Fashion stores please",
                note="Setup: active topic = shopping",
                must=[],
                must_not=[],
                debug={"intent_domain": "shopping"},
            ),
            Turn(
                query="movies",
                note="Clear domain switch (shopping → entertainment) without transition word",
                must=[],
                must_not=["zara", "h&m", "fashion"],
                debug={"message_kind": "topic_switch", "intent_domain": "entertainment"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-09 — Smalltalk kind written to conversation_mode → poisons next turn
    #
    # After a greeting/thanks/farewell/crisis smalltalk turn, conversation_mode
    # was saved as "greeting" (or "thanks", "crisis").  The next normal query's
    # intent classifier saw "Conversation mode: greeting" in context and could
    # misclassify it.
    # Expected: smalltalk turns do NOT overwrite conversation_mode.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-09 — After greeting, normal query classifies correctly",
        bug_id="BUG-09",
        turns=[
            Turn(
                query="hi",
                note="Greeting → smalltalk",
                must=[],
                must_not=[],
                debug={"message_kind": "greeting"},
            ),
            Turn(
                query="I need a good restaurant for dinner",
                note="Normal dining query after greeting → must get dining recommendations, NOT another greeting",
                must=["ground", "floor"],
                must_not=["hello", "welcome", "good to have you", "how can i help"],
                debug={"intent_domain": "dining", "message_kind": "fresh_request"},
            ),
        ],
    ),

    Scenario(
        name="BUG-09b — After crisis, normal query classifies correctly",
        bug_id="BUG-09",
        turns=[
            Turn(
                query="I want to end it all",
                note="Crisis → empathy response",
                must=["reach out"],
                must_not=["restaurant"],
                debug={"message_kind": "crisis"},
            ),
            Turn(
                query="What movies are showing?",
                note="Normal factual query after crisis → must get movie info, NOT another crisis response or apology",
                must=[],
                must_not=["apologies for the confusion", "let's try again", "reach out", "crisis"],
                debug={"intent_domain": "entertainment"},
            ),
        ],
    ),

    Scenario(
        name="BUG-09c — After thanks, normal query classifies correctly",
        bug_id="BUG-09",
        turns=[
            Turn(
                query="Thanks",
                note="Thanks smalltalk turn",
                must=[],
                must_not=[],
                debug={"message_kind": "thanks"},
            ),
            Turn(
                query="Where is the parking?",
                note="Factual query after thanks → must get parking info, NOT another thanks response",
                must=["parking"],
                must_not=["of course", "happy to help", "anytime"],
                debug={"intent_domain": "services"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # BUG-10 — Proactive follow-up suggestion after thanks
    #
    # When a conversation topic wraps up and the user says thanks, the bot should
    # acknowledge and suggest what to explore next rather than ending the exchange.
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="BUG-10 — Thanks after dining → proactive suggestion for next topic",
        bug_id="BUG-10",
        turns=[
            Turn(
                query="Quick food before the movie",
                note="Setup: dining topic established",
                must=["ground", "food"],
                must_not=[],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="Thanks",
                note="Thanks → must acknowledge AND suggest something new (shopping or entertainment), "
                     "must NOT repeat dining recommendations",
                must=[],
                must_not=["mcdonald", "herfy", "kudu", "popeyes", "food court"],
                debug={"message_kind": "thanks"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EXTRA — General gratitude variants should always route to thanks
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="EXTRA — All thanks variants → message_kind=thanks",
        bug_id="BUG-02/03",
        turns=[
            Turn(
                query="Food options",
                note="Setup",
                must=[],
                must_not=[],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="amazing thanks",
                note="Positive + thanks → thanks",
                must=[],
                must_not=[],
                debug={"message_kind": "thanks"},
            ),
            Turn(
                query="perfect thanks",
                note="Positive + thanks → thanks",
                must=[],
                must_not=[],
                debug={"message_kind": "thanks"},
            ),
            Turn(
                query="brilliant thank you",
                note="Positive + thank you → thanks",
                must=[],
                must_not=[],
                debug={"message_kind": "thanks"},
            ),
        ],
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # EXTRA — Disengagement variants should never bleed into real queries
    # ══════════════════════════════════════════════════════════════════════════
    Scenario(
        name="EXTRA — After disengagement recovery, real queries answer normally",
        bug_id="BUG-01",
        turns=[
            Turn(
                query="Show me food options",
                note="Setup: get dining topic going",
                must=["ground"],
                must_not=[],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="nevermind",
                note="Disengagement → recovery response",
                must=[],
                must_not=["mcdonald", "herfy", "kudu"],
                debug={"message_kind": "disengagement"},
            ),
            Turn(
                query="Dine",
                note="Fresh request after disengagement recovery → must get real dining answer",
                must=["ground", "floor"],
                must_not=["apologies for the confusion", "let's try again", "my bad"],
                debug={"intent_domain": "dining"},
            ),
            Turn(
                query="Movies",
                note="Topic switch after recovery → must get entertainment answer",
                must=["cinema", "muvi"],
                must_not=["apologies", "sorry if"],
                debug={"intent_domain": "entertainment"},
            ),
        ],
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Runner  (identical structure to test_e2e_scenarios.py)
# ─────────────────────────────────────────────────────────────────────────────

class Colors:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    GREEN  = "\033[32m"
    RED    = "\033[31m"
    YELLOW = "\033[33m"
    CYAN   = "\033[36m"


def c(code: str, text: str) -> str:
    return f"{code}{text}{Colors.RESET}"


async def check_server(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base_url}/api/health", timeout=5.0)
            return r.status_code == 200
    except Exception:
        return False


async def run_turn(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    turn: Turn,
    turn_idx: int,
    verbose: bool,
) -> TurnResult:
    start = time.perf_counter()
    response_text = ""
    debug_info: dict[str, Any] = {}
    failures: list[str] = []

    try:
        resp = await client.post(
            f"{base_url}/api/chat",
            json={
                "message":    turn.query,
                "session_id": session_id,
                "tenant_id":  TENANT_ID,
                "mall_id":    MALL_ID,
                "debug":      True,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        response_text = data.get("message", "")
        debug_raw = data.get("debug") or {}
        debug_info = {
            "message_kind":  debug_raw.get("message_kind", ""),
            "intent_domain": debug_raw.get("intent_domain", ""),
            "intent_sub":    debug_raw.get("intent_sub", ""),
            "chosen_strategy": debug_raw.get("chosen_strategy", ""),
            "recent_mood":   (debug_raw.get("scene_summary") or {}).get("recent_mood", ""),
        }
    except Exception as exc:
        failures.append(f"HTTP error: {exc}")

    elapsed = round((time.perf_counter() - start) * 1000)
    response_lower = response_text.lower()

    for kw in turn.must:
        if kw.lower() not in response_lower:
            failures.append(f"MUST contain {kw!r}")

    for kw in turn.must_not:
        if kw.lower() in response_lower:
            failures.append(f"MUST NOT contain {kw!r}")

    for field_name, expected in turn.debug.items():
        actual = str(debug_info.get(field_name, "")).lower()
        if expected.lower() not in actual:
            failures.append(
                f"DEBUG {field_name!r}: expected {expected!r}, got {actual!r}"
            )

    if verbose and not failures:
        snippet = response_text[:120].replace("\n", " ")
        print(f"    {c(Colors.DIM, f'→ {snippet}')}")

    return TurnResult(
        turn_idx=turn_idx,
        query=turn.query,
        note=turn.note,
        response=response_text,
        passed=len(failures) == 0,
        failures=failures,
        debug_info=debug_info,
        elapsed_ms=elapsed,
    )


async def run_scenario(
    client: httpx.AsyncClient,
    base_url: str,
    scenario: Scenario,
    verbose: bool,
) -> ScenarioResult:
    session_id = f"reg-{uuid.uuid4().hex[:12]}"
    turn_results: list[TurnResult] = []

    tag  = c(Colors.YELLOW, f"[{scenario.bug_id}]")
    name = c(Colors.BOLD + Colors.CYAN, scenario.name)
    print(f"\n{tag} {name}")
    print(f"  {c(Colors.DIM, f'session: {session_id}')}")

    for i, turn in enumerate(scenario.turns, 1):
        prefix = f"  Turn {i}/{len(scenario.turns)}"
        print(f"{prefix}  {c(Colors.DIM, turn.query[:60])} ", end="", flush=True)
        result = await run_turn(client, base_url, session_id, turn, i, verbose)
        turn_results.append(result)

        status = c(Colors.GREEN, "✓") if result.passed else c(Colors.RED, "✗ FAIL")
        print(f"{status}  {c(Colors.DIM, f'{result.elapsed_ms}ms')}")

        if not result.passed:
            for failure in result.failures:
                print(f"         {c(Colors.RED, '→')} {failure}")
            if verbose:
                snippet = result.response[:200].replace("\n", " ")
                print(f"         {c(Colors.DIM, f'Response: {snippet}')}")
        elif verbose and turn.note:
            print(f"         {c(Colors.DIM, turn.note)}")

    passed = sum(1 for r in turn_results if r.passed)
    failed = len(turn_results) - passed
    bar = c(Colors.GREEN, f"{passed} passed") + ", " + (
        c(Colors.RED, f"{failed} failed") if failed else c(Colors.DIM, "0 failed")
    )
    print(f"  {bar}")

    return ScenarioResult(
        name=scenario.name,
        bug_id=scenario.bug_id,
        turns=turn_results,
        total=len(turn_results),
        passed=passed,
        failed=failed,
    )


async def main(base_url: str, filter_name: str | None, verbose: bool) -> int:
    border = "═" * 72
    print(f"\n{c(Colors.BOLD, border)}")
    print(f"{c(Colors.BOLD, '  Cenomi Chatbot — Fix Regression Tests')}")
    print(f"{c(Colors.BOLD, border)}")
    print(f"  Target : {c(Colors.CYAN, base_url)}")
    print(f"  Mall   : {MALL_ID}")
    print(f"  Covers : BUG-01 through BUG-10 (disengagement loop, thanks, stop, emotional, etc.)")

    print(f"\n  Checking server... ", end="", flush=True)
    if not await check_server(base_url):
        print(c(Colors.RED, "OFFLINE"))
        print(f"\n  {c(Colors.RED, 'Server not reachable.')} Start it:")
        print(f"  {c(Colors.DIM, 'cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000')}")
        return 1
    print(c(Colors.GREEN, "online ✓"))

    scenarios_to_run = SCENARIOS
    if filter_name:
        scenarios_to_run = [
            s for s in SCENARIOS
            if filter_name.lower() in s.name.lower()
            or filter_name.lower() in s.bug_id.lower()
        ]
        if not scenarios_to_run:
            print(f"\n  {c(Colors.YELLOW, f'No scenarios matching {filter_name!r}')}")
            avail = ", ".join(f"{s.bug_id}" for s in SCENARIOS)
            print(f"  Available bug IDs: {avail}")
            return 1

    print(f"  Scenarios : {len(scenarios_to_run)}")
    print(f"  Turns     : {sum(len(s.turns) for s in scenarios_to_run)} total")

    all_results: list[ScenarioResult] = []
    async with httpx.AsyncClient() as client:
        for scenario in scenarios_to_run:
            result = await run_scenario(client, base_url, scenario, verbose)
            all_results.append(result)
            await asyncio.sleep(0.3)

    # ── Summary ──────────────────────────────────────────────────────────────
    total_turns  = sum(r.total  for r in all_results)
    total_passed = sum(r.passed for r in all_results)
    total_failed = sum(r.failed for r in all_results)

    print(f"\n{c(Colors.BOLD, border)}")
    print(f"{c(Colors.BOLD, '  SUMMARY')}")
    print(f"{c(Colors.BOLD, border)}")

    for r in all_results:
        status = c(Colors.GREEN, "✓") if r.failed == 0 else c(Colors.RED, f"✗ {r.failed} failed")
        tag    = c(Colors.YELLOW, f"[{r.bug_id}]")
        print(f"  {status}  {tag}  {r.name}")

    print(f"\n  Total turns : {total_turns}")
    print(f"  Passed      : {c(Colors.GREEN, str(total_passed))}")

    if total_failed:
        print(f"  Failed      : {c(Colors.RED, str(total_failed))}")
        print(f"\n  {c(Colors.RED + Colors.BOLD, '✗ REGRESSION DETECTED — one or more fixes are not working')}")
    else:
        print(f"  Failed      : {c(Colors.DIM, '0')}")
        print(f"\n  {c(Colors.GREEN + Colors.BOLD, '✓ ALL REGRESSION TESTS PASSED')}")

    print()
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Regression tests for fix/testing-bugs fixes"
    )
    parser.add_argument(
        "--base-url", default=BASE_URL,
        help=f"Backend base URL (default: {BASE_URL})"
    )
    parser.add_argument(
        "--scenario", default=None,
        help="Filter by scenario name or bug ID (e.g. 'BUG-01', 'thanks', 'disengagement')"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print bot response snippet for every turn"
    )
    args = parser.parse_args()

    exit_code = asyncio.run(main(args.base_url, args.scenario, args.verbose))
    sys.exit(exit_code)
