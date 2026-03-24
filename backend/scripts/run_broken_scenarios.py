#!/usr/bin/env python3
"""
Run the broken/vague/multi-turn scenario tests from docs/test-queries-broken.md
and write a detailed results report to test-results/.

Each scenario is run as a single continuous session so context accumulates
across turns exactly as it would in a real conversation.

Validates against:
  - response_mode (from debug)
  - confidence_level (from debug)
  - Must / Must NOT rules captured as evaluation notes

Results are written to test-results/broken_run_<timestamp>.{md,json}
"""
import json
import os
import time
import uuid
import httpx
from datetime import datetime, timezone

BASE_URL = "http://127.0.0.1:8000/api/chat"
TIMEOUT  = 45

_RUN_ID = uuid.uuid4().hex[:8]

# ---------------------------------------------------------------------------
# Scenario definitions — transcribed from docs/test-queries-broken.md
# Each entry: (turn_id, query, expected_mode, expected_confidence, validation_note)
# ---------------------------------------------------------------------------

SCENARIOS = [
    # ── Scenario 1: Jacket Refinement ────────────────────────────────────
    {
        "id": "S01",
        "label": "Jacket Refinement — Progressive Shopping (Required Flow A)",
        "area": "Progressive shopping refinement",
        "turns": [
            ("1.1", "i want to buy jackets",
             "guided_recommendation", "medium",
             "Must infer defaults (target=self, budget=mid_range, use_case=casual) and provide a direct recommendation. Must NOT ask a clarifying question. Must NOT list all jacket stores without reasoning."),
            ("1.2", "for my 5 year old son",
             "guided_recommendation", "high",
             "Must update context: category=kids_fashion, age=5, gender=boy. Must suggest stores with children's sections. Must NOT suggest adult fashion brands only."),
            ("1.3", "whats the price",
             "direct_factual", "medium",
             "Must acknowledge cannot confirm exact prices; give realistic range or direct to store. Must NOT make up specific prices. Must NOT ignore kids' jacket context."),
            ("1.4", "something affordable",
             "guided_recommendation", "high",
             "Must add budget=affordable. Must refine toward value stores. Must NOT reset child/age/jacket context. Must NOT suggest premium kids labels."),
        ],
    },

    # ── Scenario 2: Movie Refinement ──────────────────────────────────────
    {
        "id": "S02",
        "label": "Movie Refinement — Cinema Flow (Required Flow B)",
        "area": "Movies / cinema",
        "turns": [
            ("2.1", "show me movies",
             "guided_recommendation", "high",
             "Must display currently showing films. Must NOT ask for genre before showing any. Should offer to filter after listing."),
            ("2.2", "with kid",
             "guided_recommendation", "high",
             "Must re-filter for family/kids-appropriate films. Must NOT show horror, adult drama, or 18+ titles. Must set companions.has_child=true."),
            ("2.3", "anything action",
             "guided_recommendation", "medium",
             "Must find kid-appropriate action films (animated action, superhero). Must NOT show adult action films. Must flag if no kid-friendly action is showing."),
            ("2.4", "any other ones",
             "guided_recommendation", "medium",
             "Must show alternative kid-friendly films not already listed. Must NOT repeat same movies. Must NOT switch to dining or shopping."),
        ],
    },

    # ── Scenario 3: Mall Overview Continuity ──────────────────────────────
    {
        "id": "S03",
        "label": "Mall Overview Continuity — Discovery Mode (Required Flow C)",
        "area": "Mall overview / services",
        "turns": [
            ("3.1", "tell me about the mall",
             "direct_factual", "high",
             "Must give high-level overview: floors, zones, anchor stores, entertainment, dining. Must NOT ask 'what are you looking for?' — visitor in discovery mode."),
            ("3.2", "more about the mall",
             "direct_factual", "high",
             "Must expand on what was mentioned: hours, parking, accessibility, events. Must NOT repeat 3.1 content verbatim. Must NOT ask a clarifying question."),
            ("3.3", "what services do you have",
             "direct_factual", "high",
             "Must list mall services: prayer rooms, ATMs, lost & found, strollers, wheelchairs, wifi, etc. Must NOT confuse services with stores. Must NOT say 'I'm not sure' for basic facility questions."),
        ],
    },

    # ── Scenario 4: Bridesmaid ────────────────────────────────────────────
    {
        "id": "S04",
        "label": "Bridesmaid Scenario — Elegant + Budget (Required Flow D)",
        "area": "Progressive shopping refinement",
        "turns": [
            ("4.1", "im bridesmaid",
             "context_acknowledgement", "high",
             "Must acknowledge occasion and set occasion=wedding, role=bridesmaid. Must invite next direction. Must NOT dump a list of stores immediately."),
            ("4.2", "i need something elegant",
             "guided_recommendation", "high",
             "Must suggest formal/semi-formal women's fashion. Must set style=elegant. Must NOT suggest casual wear, sportswear, or fast fashion."),
            ("4.3", "not too expensive",
             "guided_recommendation", "high",
             "Must add budget=mid-range, eliminate luxury boutiques. Must retain elegant style filter. Must NOT swing to budget fast fashion. Must present stores balancing elegance and affordability."),
        ],
    },

    # ── Scenario 5: Family Quick Plan ─────────────────────────────────────
    {
        "id": "S05",
        "label": "Family Quick Plan — Time-Pressure Cross-Intent (Required Flow E)",
        "area": "Cross-intent planning",
        "turns": [
            ("5.1", "i am here with my family",
             "context_acknowledgement", "high",
             "Must acknowledge family context. Must set companions=family. Must offer natural next directions. Must NOT start listing stores immediately."),
            ("5.2", "something quick",
             "guided_recommendation", "high",
             "Must interpret 'quick' as time-constrained. Should lean toward fast-casual or quick entertainment. Must NOT recommend fine dining. Must NOT ask what kind of quick."),
            ("5.3", "near cinema",
             "guided_recommendation", "high",
             "Must add location proximity constraint. Must show options near cinema. Must NOT list options on opposite end without flagging. Must NOT forget family + quick context."),
        ],
    },

    # ── Scenario 6: Gift for Girlfriend ───────────────────────────────────
    {
        "id": "S06",
        "label": "Gift for Girlfriend — Elegant to Affordable Pivot",
        "area": "Progressive shopping refinement",
        "turns": [
            ("6.1", "i want to get something for my girlfriend",
             "context_acknowledgement", "medium",
             "Must set occasion=gift, recipient=girlfriend. Must prompt for type (fashion, accessories, fragrance, etc.). Must NOT immediately list all gift stores."),
            ("6.2", "something nice, not too much",
             "guided_recommendation", "medium",
             "Must interpret 'not too much' as budget=mid-range. Must NOT list luxury boutiques. Must suggest accessible gift options."),
            ("6.3", "she likes bags",
             "guided_recommendation", "high",
             "Must narrow to bags/handbags. Must hold budget=mid-range. Must NOT suggest luxury designer bags. Must surface mid-range bag stores."),
            ("6.4", "any with sales on",
             "guided_recommendation", "medium",
             "Must try to surface stores with active promotions. If unknown must say so honestly. Must NOT make up a sale. Must hold bag + mid-range + girlfriend context."),
        ],
    },

    # ── Scenario 7: Food Then Movie ────────────────────────────────────────
    {
        "id": "S07",
        "label": "Food Then Movie — Messy Wording Dual Intent",
        "area": "Cross-intent planning",
        "turns": [
            ("7.1", "food and maybe movie also",
             "hybrid_plan", "medium",
             "Must recognize dual intent: dining + cinema. Must NOT treat as pure dining. Should ask order or present plan for both. Must NOT demand clarification before doing anything."),
            ("7.2", "yeah food first then see",
             "guided_recommendation", "high",
             "'Then see' = user will decide movie later. Must focus on dining now. Must keep cinema as pending thread. Must NOT give full movie list here."),
            ("7.3", "something not too heavy, i hate waiting",
             "guided_recommendation", "high",
             "Must interpret as light food + fast service. Must filter toward fast-casual. Must NOT suggest buffets or long sit-downs. Must NOT ask for cuisine preference."),
            ("7.4", "ok after, what movies",
             "guided_recommendation", "high",
             "User ready for cinema thread. Must resume cinema intent. Must NOT forget 'already ate, now movie' context. Must NOT re-recommend restaurants."),
        ],
    },

    # ── Scenario 8: Kid Context Drop Mid-Flow ─────────────────────────────
    {
        "id": "S08",
        "label": "Kid Context Drop Mid-Movie Flow — Late Context Update",
        "area": "Family / child context-setting",
        "turns": [
            ("8.1", "whats showing at the cinema",
             "direct_factual", "high",
             "Must list currently showing films. Must NOT assume any audience filter yet."),
            ("8.2", "oh wait im with my 7 year old",
             "guided_recommendation", "high",
             "Must re-apply movie list with kid-appropriate filter. Must acknowledge context update. Must set companions.child_age=7. Must NOT re-ask 'what movies are you looking for?'"),
            ("8.3", "anything she would like",
             "guided_recommendation", "high",
             "Must surface age-appropriate films for a 7-year-old girl (animated, family adventure). Must NOT show violence-heavy action. Must NOT ask 'what does she like?' — make suggestions and offer to refine."),
            ("8.4", "ok we'll do that one, anything to eat before",
             "hybrid_plan", "high",
             "Must transition to dining while holding: selected film + 7-year-old companion. Must suggest family-friendly, quick dining. Must NOT suggest fine dining or adult-only atmospheres."),
        ],
    },

    # ── Scenario 9: Affordable Shoes Vague Opener ─────────────────────────
    {
        "id": "S09",
        "label": "Affordable Shoes — Vague Opener Progressive Refinement",
        "area": "Progressive shopping refinement",
        "turns": [
            ("9.1", "shoes",
             "guided_recommendation", "low",
             "Single-word query. Must ask minimal clarifying questions: who for, type, or budget. Must NOT dump all shoe stores. Must NOT assume adult/women's/men's."),
            ("9.2", "for me, casual",
             "guided_recommendation", "high",
             "Must set category=casual_shoes, infer adult. Should ask or prompt gender if ambiguous, or surface unisex options. Must NOT suggest formal shoes or kids' footwear."),
            ("9.3", "something not too pricey",
             "guided_recommendation", "high",
             "Must set budget=affordable. Must filter toward accessible footwear stores. Must NOT recommend high-end sneaker boutiques or designer brands."),
            ("9.4", "do they have like Nike or Adidas",
             "direct_factual", "high",
             "Must answer factually — which major sports brands are present. Must NOT confuse brand with store. Must hold affordable + casual context."),
        ],
    },

    # ── Scenario 10: Romantic Dinner Upgrade ──────────────────────────────
    {
        "id": "S10",
        "label": "Dinner for Two — Romantic Upgrade Mid-Flow",
        "area": "Dining / food",
        "turns": [
            ("10.1", "we want to eat",
             "guided_recommendation", "low",
             "'We' = 2+ people. Must ask or offer cuisine options. Must NOT ask 'how many people' — 'we' implies 2+."),
            ("10.2", "something nice, its kind of a special night",
             "guided_recommendation", "high",
             "Must register occasion signal. Must set occasion=special_evening. Must shift toward romantic, atmospheric dining. Must NOT suggest fast casual."),
            ("10.3", "not too loud, we want to talk",
             "guided_recommendation", "high",
             "Must add ambiance=quiet. Must filter out high-energy, loud restaurants. Must NOT re-suggest busy food courts."),
            ("10.4", "how long would a reservation take",
             "direct_factual", "medium",
             "Must respond helpfully: bot cannot make reservations, advise calling ahead. Must NOT fake a booking capability. Must NOT ignore quiet + special occasion context."),
        ],
    },

    # ── Scenario 11: School Wear Multiple Kids ────────────────────────────
    {
        "id": "S11",
        "label": "School Wear — Multiple Kids Different Ages",
        "area": "Family / child context-setting",
        "turns": [
            ("11.1", "i need school clothes for my kids",
             "guided_recommendation", "medium",
             "Must ask how many kids or what ages — 'kids' is plural. Must NOT dump all kids' fashion stores."),
            ("11.2", "one is 6 and one is 12",
             "guided_recommendation", "high",
             "Must register two child profiles: child_1.age=6, child_2.age=12. Must note 12-year-old may bridge kids/teen sizing. Must suggest stores covering both age groups or separate recommendations."),
            ("11.3", "something affordable, back to school budget",
             "guided_recommendation", "high",
             "Must apply budget=affordable across both profiles. Must NOT recommend premium kids brands. Must NOT forget either child."),
            ("11.4", "do you have any uniform stores",
             "direct_factual", "high",
             "Must answer honestly. If no dedicated uniform store present, say so and suggest alternatives. Must NOT fabricate a uniform store."),
        ],
    },

    # ── Scenario 12: Near Cinema Dining Triple Constraints ────────────────
    {
        "id": "S12",
        "label": "Near Cinema Dining — Budget + Speed + Proximity",
        "area": "Dining / food",
        "turns": [
            ("12.1", "i want to eat near the cinema",
             "guided_recommendation", "high",
             "Must apply location filter: near cinema. Must NOT list restaurants across the mall without flagging distance."),
            ("12.2", "something quick, movie starts in 40 mins",
             "guided_recommendation", "high",
             "Must add time-pressure context. Must prioritize fast-service venues. Must NOT suggest sit-down with 45+ min wait. Should note approximate time needed."),
            ("12.3", "and budget friendly",
             "guided_recommendation", "high",
             "Must add budget=affordable. Must filter out mid-upscale venues. Must hold location + time constraints simultaneously."),
            ("12.4", "just tell me the best one",
             "guided_recommendation", "high",
             "User wants ONE recommendation, not a list. Must give single clear pick with brief justification. Must NOT list 4-5 options again."),
        ],
    },

    # ── Scenario 13: ATM / Prayer Room / Services ─────────────────────────
    {
        "id": "S13",
        "label": "ATM / Prayer Room / Services — Factual Only",
        "area": "Mall overview / services",
        "turns": [
            ("13.1", "where is the ATM",
             "direct_factual", "high",
             "Must give ATM location. If multiple, list briefly. Must NOT give shopping recommendation. Must NOT say 'I'm not sure' if in known data."),
            ("13.2", "and the prayer room",
             "direct_factual", "high",
             "Must answer factually with prayer room location. Must NOT conflate with ATM answer. Must NOT give full services dump."),
            ("13.3", "do you have strollers",
             "direct_factual", "high",
             "Must answer whether stroller rental/lending is available. Must give customer service desk location if relevant. Must NOT confuse with recommendation query."),
        ],
    },

    # ── Scenario 14: Typo / Broken Input / Recovery ───────────────────────
    {
        "id": "S14",
        "label": "Typo / Broken Input / Recovery — Graceful Repair",
        "area": "Unsupported / broken / typo",
        "turns": [
            ("14.1", "Nkie shoes",
             "guided_recommendation", "medium",
             "Must interpret as 'Nike shoes'. Must NOT ask 'did you mean Nike?' in correcting tone. Should respond naturally as if it understood."),
            ("14.2", "asdf",
             "graceful_recovery", "low",
             "Completely unintelligible. Must gracefully explain what the bot can help with. Must NOT crash or give random response. Must NOT pretend to understand."),
            ("14.3", "sorry i meant sneakers",
             "guided_recommendation", "high",
             "Must pick up the correction. Must resume shopping intent for sneakers. Must NOT bring up 'asdf' confusion again."),
            ("14.4", "affordable ones",
             "guided_recommendation", "high",
             "Must add budget=affordable. Must NOT lose the sneaker context. Must surface accessible sneaker options."),
        ],
    },

    # ── Scenario 15: Unsupported Ask (Taxi / Delivery) ───────────────────
    {
        "id": "S15",
        "label": "Unsupported Ask — Taxi / Delivery / Online Order",
        "area": "Unsupported / broken",
        "turns": [
            ("15.1", "can you book me a taxi",
             "clarification_request", "high",
             "Must clearly state it cannot book taxis. Must offer alternatives if known. Must NOT pretend to book or say 'let me check'."),
            ("15.2", "what about online ordering, can you order food for me",
             "clarification_request", "high",
             "Must state it cannot place orders. Must NOT list food options as if that answers the question. May mention if any restaurants have their own online platforms."),
            ("15.3", "ok fine, just tell me where to eat then",
             "guided_recommendation", "high",
             "User accepts limitations and reverts to simple ask. Must respond normally and helpfully. Must NOT carry defensive tone from previous refusals."),
        ],
    },

    # ── Scenario 16: Wedding Shopping Twisted Wording ─────────────────────
    {
        "id": "S16",
        "label": "Wedding Shopping — Smart-Casual Tension",
        "area": "Progressive shopping refinement",
        "turns": [
            ("16.1", "looking for something for a wedding",
             "context_acknowledgement", "medium",
             "Must ask clarifying role: attending, in wedding party, buying a gift? Must NOT assume user is getting married."),
            ("16.2", "im attending, need an outfit, wedding but not too fancy",
             "guided_recommendation", "high",
             "Must interpret 'not too fancy' as smart-casual to semi-formal, not black-tie. Must set occasion=wedding_guest, style=smart_casual. Must NOT suggest ultra-formal gowns or ball gowns."),
            ("16.3", "something that works for after too",
             "guided_recommendation", "high",
             "User wants versatile outfit. Must refine toward smart-casual not occasion-specific. Must NOT ignore wedding context entirely."),
            ("16.4", "my budget is around 300",
             "guided_recommendation", "high",
             "Must apply budget=~300. Must keep smart-casual + versatile filter. Must NOT suggest budget fast-fashion or high-end luxury. Must frame around mid-range options."),
        ],
    },

    # ── Scenario 17: Friends Group — Not Crowded ─────────────────────────
    {
        "id": "S17",
        "label": "Friends Group — Quick, Not Crowded, Chill Vibe",
        "area": "Family / group context",
        "turns": [
            ("17.1", "im here with 3 friends, what can we do",
             "guided_recommendation", "medium",
             "Must register companions=friends, group_size≈4. Must offer directions: dining, entertainment, shopping. Must NOT give solo-visitor response."),
            ("17.2", "we want something quick, not crowded",
             "guided_recommendation", "high",
             "Must apply pace=quick, ambiance=not_crowded. Must avoid recommending peak-time food courts or busy spots. May note timing if relevant."),
            ("17.3", "maybe food, something we can share",
             "guided_recommendation", "high",
             "Must interpret 'share' as group-style dining or shared plates. Must filter toward restaurants for 4 people sharing. Must NOT suggest solo-format dining."),
            ("17.4", "anything with a chill vibe",
             "guided_recommendation", "high",
             "Must stack ambiance=relaxed/casual. Must NOT suggest loud bars or chaotic environments. Must hold group of 4 + quick + not crowded + shared dining."),
        ],
    },

    # ── Scenario 18: Topic Switching ─────────────────────────────────────
    {
        "id": "S18",
        "label": "Topic Switching — Movies → Shopping → Dessert",
        "area": "Topic continuity",
        "turns": [
            ("18.1", "what movies do you have",
             "direct_factual", "high",
             "Must list currently showing films. Standard cinema query."),
            ("18.2", "actually forget that, i want to shop something",
             "guided_recommendation", "medium",
             "Clear topic switch. Must fully close cinema topic. Must NOT carry any cinema context forward. Must ask or offer a direction for shopping."),
            ("18.3", "something cheaper",
             "guided_recommendation", "medium",
             "Ambiguous without prior shopping context. Must clarify: cheaper than what? Or ask what category. Must NOT guess randomly."),
            ("18.4", "i mean affordable brands, clothes",
             "guided_recommendation", "high",
             "Must now surface affordable clothing stores. Topic=fashion, budget=affordable. Must NOT re-open cinema topic."),
            ("18.5", "ok done, where can i get dessert",
             "guided_recommendation", "high",
             "Clear topic switch to dessert. Must close shopping topic. Must recommend dessert spots. Must NOT carry shopping or cinema context into dessert response."),
        ],
    },

    # ── Scenario 19: Context-Only Turns ───────────────────────────────────
    {
        "id": "S19",
        "label": "Context-Only Turns — Dinner Event, Elegant Outfit",
        "area": "Context-only turns",
        "turns": [
            ("19.1", "i have a dinner event tonight",
             "context_acknowledgement", "high",
             "Must acknowledge the event. Must NOT immediately list restaurants. Must ask or wait: dining, outfit, or both?"),
            ("19.2", "yeah im looking for something to wear",
             "guided_recommendation", "high",
             "Must open shopping for occasion wear. occasion=dinner_event, category=outfits. Must NOT suggest casual wear."),
            ("19.3", "something elegant, i want to look put together",
             "guided_recommendation", "high",
             "Must narrow to elegant, polished options. style=elegant. Must NOT suggest casualwear or sportswear."),
            ("19.4", "not too over the top though",
             "guided_recommendation", "high",
             "Must interpret as smart-elegant, not OTT/gown level. Must narrow toward sophisticated but understated fashion. Must NOT suggest formal ball gowns. Must hold occasion + elegant + not-OTT simultaneously."),
        ],
    },

    # ── Scenario 20: Rambling Query ───────────────────────────────────────
    {
        "id": "S20",
        "label": "Rambling Query — Multi-Fragment Worst-Case Input",
        "area": "Vague / broken / progressive refinement",
        "turns": [
            ("20.1", "something nice not too much maybe for kid",
             "context_acknowledgement", "low",
             "Multiple fragments: 'something nice' = quality, 'not too much' = budget, 'for kid' = child recipient. Must confirm interpretation. Must NOT dump 10 stores. Must ask one clarifying question max."),
            ("20.2", "like clothes or toy i dunno",
             "guided_recommendation", "medium",
             "Category still ambiguous. Must offer a direction or list two tracks: kids' clothing vs toy/gift. Must NOT ask more clarifying questions — make a choice and offer it."),
            ("20.3", "clothes, my son, 5",
             "guided_recommendation", "high",
             "Now fully specified: category=kids_clothing, child.gender=boy, child.age=5, budget=affordable. Must give clean recommendation. Must NOT re-ask clarified information."),
            ("20.4", "is there like a sale or something",
             "guided_recommendation", "medium",
             "Must look for promotional context. If known surface it. If unknown say so honestly. Must NOT fabricate a sale. Must hold all prior context."),
        ],
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def send_message(session_id: str, message: str, client: httpx.Client) -> dict:
    payload = {"message": message, "session_id": session_id, "debug": True}
    resp = client.post(BASE_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def extract_debug(data: dict) -> dict:
    debug = data.get("debug") or {}
    scene = debug.get("scene_summary") or {}
    return {
        "response_mode":    debug.get("response_mode", "—"),
        "confidence_level": debug.get("confidence_level", "—"),
        "flow_type":        debug.get("flow_type", "—"),
        "chosen_strategy":  debug.get("chosen_strategy", "—"),
        "constraint_stack": scene.get("constraint_stack", []),
        "topic_lock":       scene.get("topic_lock", "—"),
        "group_size":       scene.get("group_size", "—"),
        "occasion":         scene.get("occasion", "—"),
        "companions":       scene.get("companions", "—"),
        "budget":           scene.get("budget", "—"),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict) -> dict:
    sid     = scenario["id"]
    label   = scenario["label"]
    area    = scenario["area"]
    turns   = scenario["turns"]
    session = f"brk-{sid.lower()}-{_RUN_ID}"

    print(f"\n{'='*72}")
    print(f"  {sid}: {label}")
    print(f"  Area: {area}")
    print(f"  Session: {session}")
    print(f"{'='*72}")

    results = []
    client  = httpx.Client(timeout=TIMEOUT)

    for (turn_id, query, exp_mode, exp_conf, note) in turns:
        full_id = f"{sid}.{turn_id}"
        try:
            t0      = time.time()
            data    = send_message(session, query, client)
            elapsed = (time.time() - t0) * 1000
            reply   = data.get("reply", data.get("message", ""))
            dbg     = extract_debug(data)

            mode_ok = dbg["response_mode"]    == exp_mode
            conf_ok = dbg["confidence_level"] == exp_conf
            passed  = mode_ok and conf_ok

            results.append({
                "turn_id":          full_id,
                "query":            query,
                "status":           "PASS" if passed else "FAIL",
                "mode_ok":          mode_ok,
                "conf_ok":          conf_ok,
                "expected_mode":    exp_mode,
                "actual_mode":      dbg["response_mode"],
                "expected_conf":    exp_conf,
                "actual_conf":      dbg["confidence_level"],
                "flow_type":        dbg["flow_type"],
                "chosen_strategy":  dbg["chosen_strategy"],
                "constraint_stack": dbg["constraint_stack"],
                "topic_lock":       dbg["topic_lock"],
                "companions":       dbg["companions"],
                "occasion":         dbg["occasion"],
                "budget":           dbg["budget"],
                "latency_ms":       round(elapsed),
                "reply_snippet":    reply[:250] if reply else "",
                "validation_note":  note,
            })

            icon = "✅" if passed else "❌"
            print(f"\n{icon} [{full_id}] {query!r}")
            print(f"   Mode:       {dbg['response_mode']!r:30s} (expected {exp_mode!r}) {'✓' if mode_ok else '✗'}")
            print(f"   Confidence: {dbg['confidence_level']!r:30s} (expected {exp_conf!r}) {'✓' if conf_ok else '✗'}")
            print(f"   Latency:    {round(elapsed)}ms")
            print(f"   Reply:      {reply[:130]!r}")

        except Exception as exc:
            results.append({
                "turn_id":        full_id,
                "query":          query,
                "status":         "ERROR",
                "error":          str(exc),
                "expected_mode":  exp_mode,
                "expected_conf":  exp_conf,
                "validation_note": note,
            })
            print(f"\n💥 [{full_id}] {query!r}")
            print(f"   ERROR: {exc}")

    client.close()

    n_pass  = sum(1 for r in results if r["status"] == "PASS")
    n_fail  = sum(1 for r in results if r["status"] == "FAIL")
    n_error = sum(1 for r in results if r["status"] == "ERROR")
    pct     = round(n_pass / len(results) * 100) if results else 0
    print(f"\n  → {sid} result: {n_pass}/{len(results)} PASS ({pct}%)  |  fail={n_fail}  error={n_error}")

    return {
        "id":      sid,
        "label":   label,
        "area":    area,
        "session": session,
        "turns":   results,
        "n_pass":  n_pass,
        "n_fail":  n_fail,
        "n_error": n_error,
        "n_total": len(results),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_results(scenario_results: list) -> str:
    all_turns = [t for s in scenario_results for t in s["turns"]]
    total     = len(all_turns)
    passed    = sum(1 for t in all_turns if t["status"] == "PASS")
    failed    = sum(1 for t in all_turns if t["status"] == "FAIL")
    errors    = sum(1 for t in all_turns if t["status"] == "ERROR")
    pct       = round(passed / total * 100) if total else 0
    avg_ms    = round(
        sum(t.get("latency_ms", 0) for t in all_turns if "latency_ms" in t)
        / max(1, sum(1 for t in all_turns if "latency_ms" in t))
    )

    now_utc  = datetime.now(timezone.utc)
    ts_label = now_utc.strftime("%Y-%m-%d_%H-%M-%S")
    ts_human = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    script_dir  = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.normpath(os.path.join(script_dir, "..", "..", "test-results"))
    os.makedirs(results_dir, exist_ok=True)

    md_path   = os.path.join(results_dir, f"broken_run_{ts_label}.md")
    json_path = os.path.join(results_dir, f"broken_run_{ts_label}.json")

    # ── Markdown report ───────────────────────────────────────────────────
    lines = [
        "# Cenomi Chatbot — Broken / Vague / Multi-Turn Test Results",
        "",
        f"**Run date:** {ts_human}  ",
        f"**Source:** `docs/test-queries-broken.md`  ",
        f"**Target:** `{BASE_URL}`  ",
        f"**Session prefix:** `brk-*-{_RUN_ID}`",
        "",
        "> These scenarios test: context persistence, graceful degradation, intent repair,",
        "> and realistic user messiness (typos, vague queries, mid-conversation context drops).",
        "",
        "## Overall Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total turns | {total} |",
        f"| ✅ Passed | {passed} ({pct}%) |",
        f"| ❌ Failed | {failed} |",
        f"| 💥 Errors | {errors} |",
        f"| Avg latency | {avg_ms} ms |",
        "",
        "## Per-Scenario Summary",
        "",
        "| # | Scenario | Area | Turns | Pass | Fail | Error | Pass% |",
        "|---|----------|------|-------|------|------|-------|-------|",
    ]

    for s in scenario_results:
        pct_s = round(s["n_pass"] / s["n_total"] * 100) if s["n_total"] else 0
        lines.append(
            f"| {s['id']} | {s['label']} | {s['area']} "
            f"| {s['n_total']} | {s['n_pass']} | {s['n_fail']} | {s['n_error']} | {pct_s}% |"
        )

    # ── Failures table ────────────────────────────────────────────────────
    failures = [t for t in all_turns if t["status"] in ("FAIL", "ERROR")]
    if failures:
        lines += [
            "",
            "## Failures & Errors",
            "",
            "| Turn | Query | Expected mode | Actual mode | Expected conf | Actual conf |",
            "|------|-------|--------------|-------------|--------------|-------------|",
        ]
        for t in failures:
            q = t["query"][:65]
            if t["status"] == "FAIL":
                lines.append(
                    f"| {t['turn_id']} | `{q}` "
                    f"| `{t['expected_mode']}` | `{t['actual_mode']}` "
                    f"| `{t['expected_conf']}` | `{t['actual_conf']}` |"
                )
            else:
                err = str(t.get("error", ""))[:80]
                lines.append(
                    f"| {t['turn_id']} | `{q}` | — | 💥 ERROR | — | `{err}` |"
                )

    # ── Per-scenario detailed tables ──────────────────────────────────────
    for s in scenario_results:
        pct_s = round(s["n_pass"] / s["n_total"] * 100) if s["n_total"] else 0
        lines += [
            "",
            f"---",
            "",
            f"## {s['id']}: {s['label']}",
            "",
            f"**Area:** {s['area']}  ",
            f"**Session:** `{s['session']}`  ",
            f"**Result:** {s['n_pass']}/{s['n_total']} PASS ({pct_s}%)",
            "",
            "| Turn | Query | Status | Expected Mode | Actual Mode | Expected Conf | Actual Conf | Latency |",
            "|------|-------|--------|--------------|-------------|--------------|-------------|---------|",
        ]
        for t in s["turns"]:
            icon  = "✅" if t["status"] == "PASS" else ("❌" if t["status"] == "FAIL" else "💥")
            mode  = t.get("actual_mode",  t.get("error", "—"))[:30]
            conf  = t.get("actual_conf",  "—")
            lat   = f"{t.get('latency_ms', '—')}ms"
            q     = t["query"][:60]
            lines.append(
                f"| {t['turn_id']} | `{q}` | {icon} "
                f"| `{t['expected_mode']}` | `{mode}` "
                f"| `{t['expected_conf']}` | `{conf}` | {lat} |"
            )

        lines += ["", "**Validation rules:**", ""]
        for t in s["turns"]:
            note    = t.get("validation_note", "")
            snippet = t.get("reply_snippet", "")
            icon    = "✅" if t["status"] == "PASS" else ("❌" if t["status"] == "FAIL" else "💥")
            lines.append(f"**{t['turn_id']}** {icon} `{t['query'][:60]}`")
            lines.append(f"> *Rule:* {note}")
            if snippet:
                safe = snippet.replace("|", "\\|").replace("\n", " ")[:200]
                lines.append(f"> *Reply:* {safe}")
            lines.append("")

    # ── Integrity checklist ───────────────────────────────────────────────
    lines += [
        "",
        "---",
        "",
        "## Conversation Integrity Checklist",
        "",
        "Review results against these cross-scenario criteria:",
        "",
        "- [ ] **Context persistence** — Child age, companions, budget constraints survive across all turns in a session",
        "- [ ] **No premature recommendations** — Bot does not dump store lists on context-only turns (S04 turn 1, S05 turn 1, S19 turn 1)",
        "- [ ] **Graceful typo handling** — Typos (S14) resolved without shaming the user",
        "- [ ] **Gibberish handled** — Unintelligible input (S14.2) met with clarification_request, not a random answer",
        "- [ ] **Capability honesty** — Taxi/delivery/ordering (S15) refused cleanly; no hallucinated capabilities",
        "- [ ] **Topic switching** — Cinema → Shopping → Dessert (S18) transitions clean; no topic bleeding",
        "- [ ] **Budget guard** — Once budget is stated, premium options are filtered out for the rest of the session",
        "- [ ] **Kid filter persistence** — Child context applied from the turn it's set, not dropped on genre/type changes",
        "- [ ] **Single-pick decisions** — When user asks for 'the best one' (S12.4), bot picks ONE, not a list",
        "- [ ] **No hallucinated promotions/prices** — Sales and prices only stated if in known data",
    ]

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── JSON report ───────────────────────────────────────────────────────
    payload = {
        "run_timestamp":  ts_human,
        "run_id":         _RUN_ID,
        "source":         "docs/test-queries-broken.md",
        "target":         BASE_URL,
        "summary": {
            "total":          total,
            "passed":         passed,
            "failed":         failed,
            "errors":         errors,
            "pass_pct":       pct,
            "avg_latency_ms": avg_ms,
        },
        "scenarios": scenario_results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"\n  Results written to:")
    print(f"    📄 {md_path}")
    print(f"    🗂  {json_path}")
    return md_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nCenomi Chatbot — Broken / Vague / Multi-Turn Scenario Test Runner")
    print(f"Source : docs/test-queries-broken.md")
    print(f"Target : {BASE_URL}")
    print(f"Run ID : {_RUN_ID}")
    print(f"Scenarios : {len(SCENARIOS)}")
    total_turns = sum(len(s["turns"]) for s in SCENARIOS)
    print(f"Total turns : {total_turns}\n")

    all_results = []
    for scenario in SCENARIOS:
        result = run_scenario(scenario)
        all_results.append(result)

    all_turns = [t for s in all_results for t in s["turns"]]
    total  = len(all_turns)
    passed = sum(1 for t in all_turns if t["status"] == "PASS")
    failed = sum(1 for t in all_turns if t["status"] == "FAIL")
    errors = sum(1 for t in all_turns if t["status"] == "ERROR")

    print(f"\n{'='*72}")
    print(f"  OVERALL SUMMARY")
    print(f"{'='*72}")
    print(f"  Total turns : {total}")
    print(f"  Passed      : {passed} ({round(passed/total*100) if total else 0}%)")
    print(f"  Failed      : {failed}")
    print(f"  Errors      : {errors}")
    print(f"{'='*72}\n")

    for s in all_results:
        pct_s = round(s["n_pass"] / s["n_total"] * 100) if s["n_total"] else 0
        bar   = "█" * (pct_s // 10) + "░" * (10 - pct_s // 10)
        status = "✅" if pct_s == 100 else ("⚠️ " if pct_s >= 50 else "❌")
        print(f"  {s['id']}  {status} [{bar}] {pct_s:3d}%  {s['n_pass']}/{s['n_total']}  {s['label']}")

    print()
    write_results(all_results)
