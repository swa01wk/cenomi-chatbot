#!/usr/bin/env python3
"""
Option A — Static offline audit of an exported session JSON.

Loads a session file (default: test_scenario.json at repo root) and runs
a battery of heuristic checks against every assistant turn without making
any network calls.

Checks performed per turn
─────────────────────────
  1. intent_routing     — Is the classified intent sensible for the user message?
  2. companion_extract  — Were companions identified correctly from the message?
  3. target_person      — Does target_person match what the user actually said?
  4. target_gender      — Does target_gender match pronouns used in the message?
  5. retrieval_gap      — Should retrieval have fired but didn't?
  6. source_attribution — If retrieval fired and got results, were sources returned?
  7. response_mode      — Is the response_mode consistent with the intent kind?

Usage
─────
    python scripts/audit_session.py
    python scripts/audit_session.py --session path/to/session.json
    python scripts/audit_session.py --out test-results/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Heuristic rule tables ─────────────────────────────────────────────────────

# Keywords that strongly signal a particular intent domain
INTENT_SIGNALS: dict[str, list[str]] = {
    "shopping":      ["buy", "purchase", "shop", "jacket", "shoes", "bag", "clothes",
                      "outfit", "shirt", "dress", "gift", "present", "affordable"],
    "dining":        ["eat", "food", "hungry", "restaurant", "grab", "lunch", "dinner",
                      "breakfast", "snack", "drink", "cafe", "meal"],
    "entertainment": ["movie", "film", "cinema", "watch", "show", "play", "game",
                      "activity", "fun", "entertainment"],
    "factual":       ["where is", "where are", "hours", "opening", "atm", "prayer",
                      "parking", "how to get", "directions"],
    "general":       ["hey", "hi", "hello", "help", "what can", "what do you"],
}

# Companion keywords
COMPANION_SIGNALS: dict[str, list[str]] = {
    "child":      ["kid", "child", "son", "daughter", "baby", "toddler",
                   "year old", "years old"],
    "girlfriend": ["girlfriend", "girl friend"],
    "boyfriend":  ["boyfriend", "boy friend"],
    "wife":       ["wife", "spouse"],
    "husband":    ["husband", "spouse"],
    "friend":     ["friend", "mate", "buddy"],
    "family":     ["family", "parents", "grandparent"],
    "partner":    ["partner", "significant other"],
}

# Pronoun → implied gender for referred subject
MALE_PRONOUNS   = ["him", "his", "he"]
FEMALE_PRONOUNS = ["her", "she"]

# Intent domains that should generally trigger retrieval
RETRIEVAL_DOMAINS = {"shopping", "dining", "entertainment"}

# Expected response_mode for intent message_kind combinations
MODE_EXPECTATIONS: dict[str, str] = {
    "fresh_request:general":       "best_effort_shortlist",
    "context_setting:general":     "context_acknowledgement",
    "context_setting:shopping":    "context_acknowledgement",
    "context_setting:dining":      "context_acknowledgement",
    "fresh_request:shopping":      "guided_recommendation",
    "fresh_request:dining":        "guided_recommendation",
    "fresh_request:entertainment": "guided_recommendation",
    "fresh_request:factual":       "direct_factual",
    "followup:shopping":           "guided_recommendation",
    "followup:dining":             "guided_recommendation",
    "constraint_refinement:shopping": "guided_recommendation",
    "constraint_refinement:dining":   "guided_recommendation",
}

# ── Check functions ───────────────────────────────────────────────────────────

def check_intent_routing(user_msg: str, debug: dict) -> list[dict]:
    """Verify the classified intent domain matches message content."""
    issues = []
    msg_lower = user_msg.lower()
    classified = debug.get("intent_domain", "")

    matched_signals = {
        domain: any(kw in msg_lower for kw in kws)
        for domain, kws in INTENT_SIGNALS.items()
    }
    strongly_matched = [d for d, hit in matched_signals.items() if hit]

    if strongly_matched and classified not in strongly_matched:
        issues.append({
            "check": "intent_routing",
            "severity": "warning",
            "message": (
                f"Intent classified as '{classified}' but message signals "
                f"{strongly_matched}. Consider routing to: {strongly_matched[0]}"
            ),
        })
    return issues


def check_companion_extract(user_msg: str, debug: dict) -> list[dict]:
    """Verify companions list reflects what user explicitly mentioned."""
    issues = []
    msg_lower = user_msg.lower()
    scene = debug.get("scene_summary", {})
    companions = scene.get("companions", [])

    # Check for companions mentioned in this specific message
    for comp_type, signals in COMPANION_SIGNALS.items():
        if any(sig in msg_lower for sig in signals):
            if comp_type not in companions:
                issues.append({
                    "check": "companion_extract",
                    "severity": "warning",
                    "message": (
                        f"User mentioned '{comp_type}' in this turn but "
                        f"scene.companions={companions!r} does not include it."
                    ),
                })

    # Check for companions injected without evidence in current message
    spurious = []
    for comp in companions:
        if comp not in COMPANION_SIGNALS:
            continue
        signals = COMPANION_SIGNALS[comp]
        # Only flag if no prior message would have set this either
        if not any(sig in msg_lower for sig in signals):
            spurious.append(comp)
    # We don't flag spurious here because they could have come from earlier turns

    return issues


def check_target_person(user_msg: str, debug: dict, prior_companions: list[str]) -> list[dict]:
    """Verify target_person in shopping_task is consistent with user's words."""
    issues = []
    msg_lower = user_msg.lower()
    scene = debug.get("scene_summary", {})
    shopping = scene.get("shopping_task", {})
    target = shopping.get("target_person", "")

    # If user says "for him" or "for my son/kid" → target should be 'child' or 'son', not 'boyfriend'
    if any(kw in msg_lower for kw in ["for him", "for my son", "for my kid", "for the kid", "for my child"]):
        if target not in ("child", "son", "boy", "self") and target != "":
            issues.append({
                "check": "target_person",
                "severity": "error",
                "message": (
                    f"User said 'for him/son/kid' but shopping_task.target_person='{target}'. "
                    "Expected 'child' or similar."
                ),
            })

    if any(kw in msg_lower for kw in ["for her", "for my daughter", "for my girlfriend", "for my wife"]):
        if target not in ("daughter", "girlfriend", "wife", "girl", "self") and target != "":
            issues.append({
                "check": "target_person",
                "severity": "error",
                "message": (
                    f"User said 'for her/daughter/girlfriend' but shopping_task.target_person='{target}'."
                ),
            })

    return issues


def check_target_gender(user_msg: str, debug: dict) -> list[dict]:
    """Verify target_gender matches pronouns used in the message."""
    issues = []
    msg_lower = user_msg.lower()
    scene = debug.get("scene_summary", {})
    shopping = scene.get("shopping_task", {})
    gender = shopping.get("target_gender", "")

    male_signals   = [p for p in MALE_PRONOUNS   if p in msg_lower.split()]
    female_signals = [p for p in FEMALE_PRONOUNS if p in msg_lower.split()]

    if male_signals and gender in ("female", "girl", "woman"):
        issues.append({
            "check": "target_gender",
            "severity": "error",
            "message": (
                f"User used male pronoun(s) {male_signals} but "
                f"shopping_task.target_gender='{gender}'."
            ),
        })

    if female_signals and gender in ("male", "boy", "man"):
        issues.append({
            "check": "target_gender",
            "severity": "error",
            "message": (
                f"User used female pronoun(s) {female_signals} but "
                f"shopping_task.target_gender='{gender}'."
            ),
        })

    return issues


def check_retrieval_gap(debug: dict) -> list[dict]:
    """Flag turns where retrieval was skipped despite a recommendation-heavy intent."""
    issues = []
    domain    = debug.get("intent_domain", "")
    mode      = debug.get("response_mode", "")
    retrieved = debug.get("retrieval_needed", False)
    results   = debug.get("retrieval_results_count", 0)

    recommendation_modes = {"guided_recommendation", "hybrid_plan", "best_effort_shortlist"}

    if domain in RETRIEVAL_DOMAINS and mode in recommendation_modes and not retrieved:
        issues.append({
            "check": "retrieval_gap",
            "severity": "warning",
            "message": (
                f"Intent domain='{domain}' with response_mode='{mode}' "
                "typically benefits from retrieval, but retrieval_needed=False. "
                "Recommendations may rely on parametric knowledge only."
            ),
        })
    return issues


def check_source_attribution(debug: dict, sources: list) -> list[dict]:
    """If retrieval fired and returned results, verify sources were surfaced."""
    issues = []
    retrieved = debug.get("retrieval_needed", False)
    results   = debug.get("retrieval_results_count", 0)
    n_sources = len(sources)

    if retrieved and results > 0 and n_sources == 0:
        issues.append({
            "check": "source_attribution",
            "severity": "error",
            "message": (
                f"retrieval_needed=True with {results} result(s) returned, "
                "but message.sources is empty — retrieved content not attributed to user."
            ),
        })
    return issues


def check_response_mode(user_msg: str, debug: dict) -> list[dict]:
    """Validate response_mode against (message_kind, intent_domain) expectations."""
    issues = []
    kind   = debug.get("message_kind", "")
    domain = debug.get("intent_domain", "")
    mode   = debug.get("response_mode", "")

    if not mode:  # smalltalk path — no standard mode expected
        return issues

    key = f"{kind}:{domain}"
    expected = MODE_EXPECTATIONS.get(key)

    if expected and mode != expected:
        issues.append({
            "check": "response_mode",
            "severity": "info",
            "message": (
                f"For kind='{kind}' + domain='{domain}', expected mode "
                f"'{expected}' but got '{mode}'."
            ),
        })
    return issues


def check_response_hallucination(user_msg: str, assistant_response: str,
                                 debug: dict) -> list[dict]:
    """
    Light heuristic: flag if response references a pronoun/entity that
    contradicts the user's stated context.
    """
    issues = []
    msg_lower  = user_msg.lower()
    resp_lower = assistant_response.lower()
    scene      = debug.get("scene_summary", {})
    companions = scene.get("companions", [])

    # If user mentioned 'girlfriend' but response says 'boyfriend'
    if "girlfriend" in msg_lower and "boyfriend" in resp_lower:
        issues.append({
            "check": "response_hallucination",
            "severity": "error",
            "message": (
                "Response uses 'boyfriend' but user mentioned 'girlfriend'. "
                "Pronoun hallucination in generated text."
            ),
        })

    # If user said 'him'/'his' (male child) but response says 'she'/'her'/'girl'
    msg_words = set(msg_lower.split())
    if ("him" in msg_words or "his" in msg_words) and (
        " she " in resp_lower or "her " in resp_lower or "girl" in resp_lower
    ):
        issues.append({
            "check": "response_hallucination",
            "severity": "warning",
            "message": (
                "Response references female pronouns/gender, but user referred to the "
                "shopping target with male pronouns ('him'/'his')."
            ),
        })

    return issues


# ── Full turn audit ───────────────────────────────────────────────────────────

def audit_turn(
    turn_index: int,
    user_msg: str,
    assistant_msg: dict,
    prior_companions: list[str],
) -> dict:
    debug   = assistant_msg.get("debug", {})
    sources = assistant_msg.get("sources", [])
    resp    = assistant_msg.get("content", "")

    all_issues: list[dict] = []
    all_issues += check_intent_routing(user_msg, debug)
    all_issues += check_companion_extract(user_msg, debug)
    all_issues += check_target_person(user_msg, debug, prior_companions)
    all_issues += check_target_gender(user_msg, debug)
    all_issues += check_retrieval_gap(debug)
    all_issues += check_source_attribution(debug, sources)
    all_issues += check_response_mode(user_msg, debug)
    all_issues += check_response_hallucination(user_msg, resp, debug)

    scene    = debug.get("scene_summary", {})
    shopping = scene.get("shopping_task", {})

    return {
        "turn_index":      turn_index,
        "user_message":    user_msg,
        "assistant_snippet": resp[:120],
        "intent":          f"{debug.get('intent_domain')}/{debug.get('intent_sub')}",
        "intent_confidence": debug.get("intent_confidence"),
        "message_kind":    debug.get("message_kind"),
        "response_mode":   debug.get("response_mode") or "(smalltalk)",
        "confidence_level": debug.get("confidence_level"),
        "companions":      scene.get("companions", []),
        "target_person":   shopping.get("target_person"),
        "target_gender":   shopping.get("target_gender"),
        "retrieval_needed": debug.get("retrieval_needed"),
        "retrieval_results": debug.get("retrieval_results_count"),
        "sources_count":   len(sources),
        "issues":          all_issues,
        "pass":            len([i for i in all_issues if i["severity"] in ("error", "warning")]) == 0,
    }


# ── Report writer ─────────────────────────────────────────────────────────────

def _severity_icon(sev: str) -> str:
    return {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "⚪")


def write_report(results: list[dict], session_meta: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    now   = datetime.now(timezone.utc)
    ts    = now.strftime("%Y-%m-%d_%H-%M-%S")
    human = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    total   = len(results)
    passing = sum(1 for r in results if r["pass"])
    failing = total - passing

    errors   = sum(len([i for i in r["issues"] if i["severity"] == "error"])   for r in results)
    warnings = sum(len([i for i in r["issues"] if i["severity"] == "warning"]) for r in results)
    infos    = sum(len([i for i in r["issues"] if i["severity"] == "info"])     for r in results)

    lines = [
        "# Session Audit Report — Static Offline Analysis",
        "",
        f"**Generated:** {human}  ",
        f"**Session ID:** `{session_meta.get('session_id', '—')}`  ",
        f"**Mall ID:** `{session_meta.get('mall_id', '—')}`  ",
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
        icon = "✅" if r["pass"] else "❌"
        lines += [
            f"### Turn {r['turn_index']} {icon}",
            "",
            f"**User:** `{r['user_message']}`  ",
            f"**Intent:** `{r['intent']}` (conf={r['intent_confidence']}, kind=`{r['message_kind']}`)  ",
            f"**Response mode:** `{r['response_mode']}` | **Confidence:** `{r['confidence_level']}`  ",
            f"**Companions:** {r['companions']}  ",
            f"**Target person/gender:** `{r['target_person']}` / `{r['target_gender']}`  ",
            f"**Retrieval:** needed={r['retrieval_needed']} results={r['retrieval_results']} sources={r['sources_count']}  ",
            "",
            f"**Response snippet:** {r['assistant_snippet']!r}",
            "",
        ]

        if r["issues"]:
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
    all_issues = [(r["turn_index"], r["user_message"], iss) for r in results for iss in r["issues"]]
    if all_issues:
        lines += [
            "## All Issues (Consolidated)",
            "",
            "| Turn | Severity | Check | Detail |",
            "|------|----------|-------|--------|",
        ]
        for turn_idx, umsg, iss in all_issues:
            sev_icon = _severity_icon(iss["severity"])
            detail = iss["message"].replace("|", "\\|")
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
        "generated_at":  human,
        "session_meta":  session_meta,
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

    parser = argparse.ArgumentParser(description="Static offline audit of a session JSON")
    parser.add_argument("--session", default=str(default_in),
                        help="Path to session JSON file")
    parser.add_argument("--out", default=str(default_out),
                        help="Directory for output reports")
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"ERROR: session file not found: {session_path}", file=sys.stderr)
        sys.exit(1)

    with open(session_path, encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    session_meta = {
        "session_id":  data.get("session_id"),
        "tenant_id":   data.get("tenant_id"),
        "mall_id":     data.get("mall_id"),
        "exported_at": data.get("exported_at"),
        "total_messages": len(messages),
    }

    print("\nCenomi Session Auditor — Static Offline Analysis")
    print(f"Session  : {session_meta['session_id']}")
    print(f"Mall     : {session_meta['mall_id']}")
    print(f"Messages : {session_meta['total_messages']}")
    print()

    results: list[dict] = []
    prior_companions: list[str] = []

    # Pair user→assistant turns
    i = 0
    turn_num = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            user_msg = msg.get("content", "")
            # Find the next assistant message
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                asst_msg = messages[i + 1]
                turn_num += 1
                result = audit_turn(turn_num, user_msg, asst_msg, prior_companions)
                results.append(result)

                # Update prior companions for next iteration
                scene = asst_msg.get("debug", {}).get("scene_summary", {})
                prior_companions = scene.get("companions", [])

                icon = "✅" if result["pass"] else "❌"
                n_issues = len(result["issues"])
                print(f"{icon} Turn {turn_num}: {user_msg[:60]!r}")
                if result["issues"]:
                    for iss in result["issues"]:
                        sev_icon = _severity_icon(iss["severity"])
                        print(f"    {sev_icon} [{iss['check']}] {iss['message'][:80]}")
                i += 2
            else:
                i += 1
        else:
            i += 1

    # Summary
    passing = sum(1 for r in results if r["pass"])
    failing = len(results) - passing
    print(f"\n{'─'*60}")
    print(f"  Turns audited : {len(results)}")
    print(f"  Clean         : {passing}")
    print(f"  With issues   : {failing}")
    print()

    write_report(results, session_meta, args.out)


if __name__ == "__main__":
    main()
