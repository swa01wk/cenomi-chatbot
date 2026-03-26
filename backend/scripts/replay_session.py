#!/usr/bin/env python3
"""
Option B — Replay a session JSON against the live server and diff the results.

Replays every user message from an exported session file through the live API
in a fresh session, then compares the new responses against the saved ones
across:
  - intent_domain / intent_sub
  - response_mode
  - confidence_level
  - retrieval_needed
  - fallback_applied
  - response text (semantic similarity via word-overlap heuristic)

Output: markdown diff report + JSON written to test-results/.

Usage
─────
    python scripts/replay_session.py
    python scripts/replay_session.py --session path/to/session.json
    python scripts/replay_session.py --mall-id al_nakheel_plaza_13
    python scripts/replay_session.py --out test-results/
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

import httpx

BASE_URL = "http://127.0.0.1:8000/api/chat"
TIMEOUT  = 60

# ── Helpers ───────────────────────────────────────────────────────────────────

def send_message(session_id: str, message: str, mall_id: str) -> dict[str, Any]:
    payload = {
        "message":    message,
        "session_id": session_id,
        "mall_id":    mall_id,
        "debug":      True,
    }
    resp = httpx.post(BASE_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _word_overlap(a: str, b: str) -> float:
    """Rough semantic similarity: Jaccard over unique word sets."""
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _extract_debug(data: dict) -> dict:
    debug = data.get("debug") or {}
    scene = debug.get("scene_summary") or {}
    return {
        "intent_domain":    debug.get("intent_domain", "—"),
        "intent_sub":       debug.get("intent_sub", "—"),
        "response_mode":    debug.get("response_mode", "") or "(smalltalk)",
        "confidence_level": debug.get("confidence_level", "—"),
        "retrieval_needed": debug.get("retrieval_needed", False),
        "fallback_applied": debug.get("fallback_applied", False),
        "flow_type":        debug.get("flow_type", "—"),
        "companions":       scene.get("companions", []),
        "budget":           scene.get("budget", ""),
        "target_person":    scene.get("shopping_task", {}).get("target_person", ""),
        "total_latency_ms": debug.get("total_latency_ms", 0),
    }


# ── Field comparison ──────────────────────────────────────────────────────────

COMPARED_FIELDS = [
    "intent_domain",
    "intent_sub",
    "response_mode",
    "confidence_level",
    "retrieval_needed",
    "fallback_applied",
]

INFORMATIONAL_FIELDS = [
    "flow_type",
    "companions",
    "budget",
    "target_person",
]


def compare_turn(
    turn_num: int,
    user_msg: str,
    saved_reply: str,
    saved_dbg: dict,
    new_reply: str,
    new_dbg: dict,
    latency_ms: int,
) -> dict:
    field_diffs = []
    for field in COMPARED_FIELDS:
        old_val = saved_dbg.get(field)
        new_val = new_dbg.get(field)
        if old_val != new_val:
            field_diffs.append({
                "field": field,
                "saved": old_val,
                "new":   new_val,
            })

    info_changes = []
    for field in INFORMATIONAL_FIELDS:
        old_val = saved_dbg.get(field)
        new_val = new_dbg.get(field)
        if old_val != new_val:
            info_changes.append({
                "field": field,
                "saved": old_val,
                "new":   new_val,
            })

    overlap = _word_overlap(saved_reply, new_reply)

    status = "MATCH" if not field_diffs else "DIFF"

    return {
        "turn":            turn_num,
        "user_message":    user_msg,
        "status":          status,
        "field_diffs":     field_diffs,
        "info_changes":    info_changes,
        "response_overlap": round(overlap, 3),
        "saved_reply_snippet": saved_reply[:140],
        "new_reply_snippet":   new_reply[:140],
        "saved_debug":     saved_dbg,
        "new_debug":       new_dbg,
        "latency_ms":      latency_ms,
    }


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(
    turns: list[dict],
    session_meta: dict,
    run_id: str,
    out_dir: str,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    now   = datetime.now(timezone.utc)
    ts    = now.strftime("%Y-%m-%d_%H-%M-%S")
    human = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    total   = len(turns)
    matched = sum(1 for t in turns if t["status"] == "MATCH")
    diffs   = total - matched
    avg_lat = round(sum(t["latency_ms"] for t in turns) / max(1, total))

    lines = [
        "# Session Replay & Diff Report",
        "",
        f"**Generated:** {human}  ",
        f"**Original session:** `{session_meta.get('session_id', '—')}`  ",
        f"**Replay session prefix:** `{run_id}`  ",
        f"**Mall ID:** `{session_meta.get('mall_id', '—')}`  ",
        f"**Target:** `{BASE_URL}`  ",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Turns replayed | {total} |",
        f"| ✅ Full match | {matched} |",
        f"| ⚠️  Field diffs | {diffs} |",
        f"| Avg replay latency | {avg_lat} ms |",
        "",
        "---",
        "",
        "## Turn-by-Turn Comparison",
        "",
        "| Turn | User message | Status | Intent (saved→new) | Mode (saved→new) | Conf (saved→new) | Overlap |",
        "|------|-------------|--------|-------------------|-----------------|-----------------|---------|",
    ]

    for t in turns:
        icon = "✅" if t["status"] == "MATCH" else "⚠️"
        sd = t["saved_debug"]
        nd = t["new_debug"]
        intent_col = (
            f"`{sd['intent_domain']}`"
            if sd["intent_domain"] == nd["intent_domain"]
            else f"~~`{sd['intent_domain']}`~~ → `{nd['intent_domain']}`"
        )
        mode_col = (
            f"`{sd['response_mode']}`"
            if sd["response_mode"] == nd["response_mode"]
            else f"~~`{sd['response_mode']}`~~ → `{nd['response_mode']}`"
        )
        conf_col = (
            f"`{sd['confidence_level']}`"
            if sd["confidence_level"] == nd["confidence_level"]
            else f"~~`{sd['confidence_level']}`~~ → `{nd['confidence_level']}`"
        )
        overlap_pct = f"{round(t['response_overlap'] * 100)}%"
        umsg = t["user_message"][:40].replace("|", "\\|")
        lines.append(
            f"| {t['turn']} | `{umsg}` | {icon} {t['status']} "
            f"| {intent_col} | {mode_col} | {conf_col} | {overlap_pct} |"
        )

    lines += ["", "---", "", "## Detailed Turn Diffs", ""]

    for t in turns:
        icon = "✅" if t["status"] == "MATCH" else "⚠️"
        lines += [
            f"### Turn {t['turn']} {icon}  `{t['user_message']}`",
            "",
        ]

        if t["field_diffs"]:
            lines.append("**Field differences (affect routing/mode):**")
            lines.append("")
            lines.append("| Field | Saved | Replayed |")
            lines.append("|-------|-------|---------|")
            for d in t["field_diffs"]:
                lines.append(f"| `{d['field']}` | `{d['saved']}` | `{d['new']}` |")
            lines.append("")
        else:
            lines.append("_All routing fields match._  ")
            lines.append("")

        if t["info_changes"]:
            lines.append("**Context/memory changes (informational):**")
            lines.append("")
            lines.append("| Field | Saved | Replayed |")
            lines.append("|-------|-------|---------|")
            for d in t["info_changes"]:
                lines.append(f"| `{d['field']}` | `{d['saved']}` | `{d['new']}` |")
            lines.append("")

        overlap_pct = round(t["response_overlap"] * 100)
        lines += [
            f"**Response text overlap:** {overlap_pct}%  ",
            f"**Saved reply:** {t['saved_reply_snippet']!r}  ",
            f"**New reply:**   {t['new_reply_snippet']!r}  ",
            f"**Replay latency:** {t['latency_ms']} ms  ",
            "",
            "---",
            "",
        ]

    md_path   = os.path.join(out_dir, f"replay_{ts}.md")
    json_path = os.path.join(out_dir, f"replay_{ts}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    json_out = {
        "generated_at": human,
        "run_id":       run_id,
        "session_meta": session_meta,
        "target":       BASE_URL,
        "summary": {
            "total": total, "matched": matched, "diffs": diffs,
            "avg_latency_ms": avg_lat,
        },
        "turns": turns,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    print(f"\n  📄  Markdown report : {md_path}")
    print(f"  🗂   JSON data       : {json_path}")
    return md_path


# ── Server health check ───────────────────────────────────────────────────────

def wait_for_server(max_wait: int = 30) -> bool:
    health_url = BASE_URL.replace("/api/chat", "/api/health")
    deadline = time.time() + max_wait
    print(f"  Checking server at {health_url} …", end="", flush=True)
    while time.time() < deadline:
        try:
            r = httpx.get(health_url, timeout=5)
            if r.status_code == 200:
                print(" ✓ ready")
                return True
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(2)
    print(" ✗ not available")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    script_dir  = Path(__file__).resolve().parent
    repo_root   = script_dir.parent.parent
    default_in  = repo_root / "test_scenario.json"
    default_out = repo_root / "test-results"

    parser = argparse.ArgumentParser(description="Replay a session JSON against the live server")
    parser.add_argument("--session", default=str(default_in))
    parser.add_argument("--mall-id", default=None,
                        help="Override mall ID (default: from session file)")
    parser.add_argument("--out", default=str(default_out))
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    if not args.no_wait and not wait_for_server():
        print("ERROR: Server not available. Start it first.", file=sys.stderr)
        sys.exit(1)

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"ERROR: session file not found: {session_path}", file=sys.stderr)
        sys.exit(1)

    with open(session_path, encoding="utf-8") as f:
        data = json.load(f)

    messages = data.get("messages", [])
    mall_id  = args.mall_id or data.get("mall_id", "al_nakheel_plaza_28")
    run_id   = uuid.uuid4().hex[:8]

    session_meta = {
        "session_id":     data.get("session_id"),
        "mall_id":        mall_id,
        "exported_at":    data.get("exported_at"),
        "total_messages": len(messages),
    }

    print("\nCenomi Session Replay & Diff")
    print(f"Original session : {session_meta['session_id']}")
    print(f"Mall ID          : {mall_id}")
    print(f"Replay session   : replay-{run_id}")
    print(f"Messages         : {len(messages)}")
    print()

    replay_session_id = f"replay-{run_id}"
    turn_results: list[dict] = []
    turn_num = 0

    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            user_msg = msg.get("content", "")
            # Find the paired saved assistant message
            if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                saved_asst = messages[i + 1]
                turn_num += 1

                saved_reply = saved_asst.get("content", "")
                saved_raw_dbg = saved_asst.get("debug", {})
                saved_scene   = saved_raw_dbg.get("scene_summary", {})
                saved_dbg = {
                    "intent_domain":    saved_raw_dbg.get("intent_domain", "—"),
                    "intent_sub":       saved_raw_dbg.get("intent_sub", "—"),
                    "response_mode":    saved_raw_dbg.get("response_mode", "") or "(smalltalk)",
                    "confidence_level": saved_raw_dbg.get("confidence_level", "—"),
                    "retrieval_needed": saved_raw_dbg.get("retrieval_needed", False),
                    "fallback_applied": saved_raw_dbg.get("fallback_applied", False),
                    "flow_type":        saved_raw_dbg.get("flow_type", "—"),
                    "companions":       saved_scene.get("companions", []),
                    "budget":           saved_scene.get("budget", ""),
                    "target_person":    saved_scene.get("shopping_task", {}).get("target_person", ""),
                    "total_latency_ms": saved_raw_dbg.get("total_latency_ms", 0),
                }

                print(f"  ► Turn {turn_num}: {user_msg[:60]!r} … ", end="", flush=True)

                try:
                    t0 = time.time()
                    new_data  = send_message(replay_session_id, user_msg, mall_id)
                    latency   = round((time.time() - t0) * 1000)
                    new_reply = new_data.get("reply", new_data.get("message", ""))
                    new_dbg   = _extract_debug(new_data)

                    result = compare_turn(
                        turn_num, user_msg,
                        saved_reply, saved_dbg,
                        new_reply, new_dbg,
                        latency,
                    )
                    turn_results.append(result)

                    icon = "✅" if result["status"] == "MATCH" else "⚠️"
                    print(f"{icon} ({latency}ms)")

                    if result["field_diffs"]:
                        for d in result["field_diffs"]:
                            print(f"       {d['field']}: {d['saved']!r} → {d['new']!r}")

                except Exception as exc:
                    print(f"💥 ERROR: {exc}")
                    turn_results.append({
                        "turn": turn_num, "user_message": user_msg,
                        "status": "ERROR", "error": str(exc),
                        "field_diffs": [], "info_changes": [],
                        "response_overlap": 0, "latency_ms": 0,
                        "saved_reply_snippet": "", "new_reply_snippet": "",
                        "saved_debug": saved_dbg, "new_debug": {},
                    })

                i += 2
            else:
                i += 1
        else:
            i += 1

    matched = sum(1 for t in turn_results if t["status"] == "MATCH")
    diffs   = sum(1 for t in turn_results if t["status"] == "DIFF")
    errors  = sum(1 for t in turn_results if t["status"] == "ERROR")

    print(f"\n{'─'*60}")
    print(f"  Turns replayed : {len(turn_results)}")
    print(f"  Full match     : {matched}")
    print(f"  Field diffs    : {diffs}")
    print(f"  Errors         : {errors}")
    print()

    write_report(turn_results, session_meta, run_id, args.out)


if __name__ == "__main__":
    main()
