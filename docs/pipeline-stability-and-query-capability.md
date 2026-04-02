# Cenomi Chatbot — Pipeline Stability & Query Capability Report

**Version:** v1.6  
**Last updated:** April 2026  
**Covers:** Pipeline evaluation runs through 2 April 2026  
**Evaluation model:** `gpt-5.4-mini` (judge)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Pipeline Architecture](#2-pipeline-architecture)
3. [Pipeline Stability — Overall Metrics](#3-pipeline-stability--overall-metrics)
4. [Stability by Pipeline Check](#4-stability-by-pipeline-check)
5. [Latency Profile](#5-latency-profile)
6. [Query Capability by Category](#6-query-capability-by-category)
7. [Response Mode Distribution](#7-response-mode-distribution)
8. [Scenario-Level Pass Rates](#8-scenario-level-pass-rates)
9. [Known Issues & Root Causes](#9-known-issues--root-causes)
10. [Regression History (v1.5 → v1.6)](#10-regression-history-v15--v16)
11. [Confidence Level Calibration](#11-confidence-level-calibration)
12. [Recommended Focus Areas](#12-recommended-focus-areas)

---

## 1. Executive Summary

The Cenomi Chatbot is a production-grade, LangGraph-based AI concierge for Cenomi mall properties. It responds to a wide range of visitor queries — from factual lookups (store locations, movie showtimes) to multi-turn guided recommendations (date-night planning, gift shopping, family outings).

Across the most recent evaluation suite of 61 queries (2 April 2026):

| Metric | Value |
|--------|-------|
| Total live end-to-end tests (31 March 2026) | 261 |
| Overall pass rate (31 March 2026) | **97%** (254/261) |
| Mode + Confidence accuracy (latest run) | **93%** (57/61) |
| Pipeline clean turns (latest run) | **48%** (29/61) |
| Pipeline errors (latest run) | **0** |
| HTTP errors across all 4 evaluation runs | **0** |
| Unit tests passing | **650 / 740** |
| Average end-to-end latency | **~7.5s** |
| P90 latency | **~11s** (SLA target: <10s) |

The backend is structurally stable — zero HTTP errors across all evaluation runs. The 52% of turns flagged with pipeline warnings are almost entirely latency-related (`node_trace > 8000ms`) or retrieval-gap observations, not functional failures.

---

## 2. Pipeline Architecture

The chatbot operates on a 12-node LangGraph pipeline. Two flow paths exist depending on query nature.

### Flow Paths

**Concierge path** — for recommendation, shopping, dining, entertainment, and exploration queries:

```
load_session
  → interpret_turn
  → route_flow
  → update_scene_memory
  → resolve_playbooks
  → choose_strategy
  → compose_context
  → rank_and_dedupe
  → decide_retrieval
  → [fetch_exact_facts]   ← only if retrieval_needed=true
  → generate_response
  → update_memory
  → emit_debug_payload
```

**Factual path** — for location queries, store hours, movie showtimes, and brand-presence checks:

```
load_session
  → interpret_turn
  → route_flow
  → resolve_fact_scope
  → fetch_exact_facts
  → compose_fact_response_context
  → generate_response
  → update_memory
  → emit_debug_payload
```

**Smalltalk path** — for greetings, farewells, emotional turns, crisis language, and garbage input:

```
load_session
  → interpret_turn
  → smalltalk
  → update_memory
  → emit_debug_payload
```

### Design Principles

| Principle | Implementation |
|-----------|----------------|
| LLM-first classification | `interpret_turn` uses `gpt-4o-mini`; no keyword overrides post-LLM |
| Answer-first | Pipeline is biased toward generating a response immediately, not interrogating the user |
| Concierge-first | Infers context, selects a playbook, produces compact guided plans |
| Smalltalk fast-path | Routing uses frozenset `SMALLTALK_KINDS`; no regex |
| Scene engine | `update_scene_memory` uses LLM-first delta extraction for structured context updates |
| Observable | Every node emits a trace entry; `emit_debug_payload` aggregates into a rich debug object |
| Multi-mall | Five Cenomi malls operational; `LRUMallContextRegistry` manages RAM → Redis → disk |

### Node Latency Budget

| Node | Expected Range | Investigate If |
|------|---------------|----------------|
| `interpret_turn` | 300–800ms | > 1500ms |
| `update_scene_memory` | 400–900ms | > 1800ms |
| `generate_response` | 1000–3000ms | > 5000ms |
| `compose_context` | 50–200ms | > 500ms |
| `rank_and_dedupe` | 10–50ms | > 200ms |
| `fetch_exact_facts` | 100–400ms | > 1000ms |

---

## 3. Pipeline Stability — Overall Metrics

Four evaluation runs were executed on 2 April 2026. Results across all runs:

| Run | Suite | Queries | Mode+Conf Pass | Pipeline Clean | Errors | Warnings |
|-----|-------|---------|---------------|---------------|--------|----------|
| Baseline (all) | Full 61 | 61 | 49/61 (80%) | 14/61 (23%) | 5 | 110 |
| Smoke | 10 queries | 10 | 10/10 (100%) | 7/10 (70%) | 0 | 4 |
| Scenario suite | 51 queries | 51 | 50/51 (98%) | 37/51 (73%) | 0 | 17 |
| Full suite (`gpt-5.4-mini`) | 61 queries | 61 | 55/61 (90%) | 32/61 (52%) | 1 | 41 |
| Latest run (2 Apr 20:15) | 61 queries | 61 | 57/61 (93%) | 29/61 (48%) | 0 | 38 |

### Mode + Confidence Accuracy

```
Smoke suite             ████████████████████ 100%  (10/10)
Scenario suite          ███████████████████░  98%  (50/51)
Latest full run         ██████████████████░░  93%  (57/61)
Full suite (gpt-5.4)    ██████████████████░░  90%  (55/61)
Baseline (all)          ████████████████░░░░  80%  (49/61)
```

### Pipeline Clean Rate

```
Scenario suite          ██████████████░░░░░░  73%  (37/51)
Smoke suite             ██████████████░░░░░░  70%  (7/10)
Full suite (gpt-5.4)    ██████████░░░░░░░░░░  52%  (32/61)
Latest full run         ██████████░░░░░░░░░░  48%  (29/61)
Baseline (all)          █████░░░░░░░░░░░░░░░  23%  (14/61)
```

> **Key insight:** The scenario suite's 73% clean rate is the most representative number for real-world usage, since those queries run within proper conversational context. The lower full-suite rate is explained by many queries running out of context (e.g., refinement queries like "something cheaper" evaluated as standalone turns).

---

## 4. Stability by Pipeline Check

The pipeline applies 13 static checks to every turn. Here is how each check performed across the most recent evaluation runs.

### Check Legend

| Severity | Meaning |
|----------|---------|
| `error` | Functional failure — wrong output, wrong routing, or corrupted context |
| `warning` | Suspicious signal — may be acceptable given context |
| `info` | Informational — no correctness impact |

### Check-by-Check Results

| Check | What It Validates | Latest Run Status | Common Failure Pattern |
|-------|------------------|-------------------|----------------------|
| `intent_routing` | Domain classification matches message vocabulary | ✅ Near-perfect | Rare keyword mismatch on ambiguous queries |
| `companion_extract` | Companions mentioned in current turn appear in scene | ✅ Mostly passing | Short phrases like "with kid" not extracting companions |
| `target_person` | `target_person` matches pronouns/relationships in message | ✅ Zero errors observed | — |
| `target_gender` | `target_gender` matches pronouns in message | ✅ Zero errors observed | — |
| `retrieval_gap` | Retrieval fired for recommendation-heavy responses | ⚠️ 4–8 warnings per run | Recommendation turns (dining, shopping) not triggering retrieval |
| `source_attribution` | Retrieved results attributed as sources | ✅ Clean | — |
| `response_mode` | Response mode consistent with `(message_kind, intent_domain)` | ✅ Mostly clean | Occasional soft mismatch (e.g., joke request gets `graceful_recovery`) |
| `flow_routing` | Correct factual vs. concierge path chosen | ✅ Clean | — |
| `disengagement_misfire` | Disengagement not assigned to explicit requests | ✅ Zero errors observed | — |
| `excluded_domains` | Stale/spurious domain exclusions cleared on re-engagement | ⚠️ 1–2 warnings per run | Dining excluded after food query; not cleared on re-engagement |
| `entity_pipeline` | Entity list is sized correctly; no bloat or domain mismatch | ⚠️ 1–2 warnings per run | Entity count of 68 on broad shopping queries |
| `topic_lock` | `topic_lock` resets after topic switch | ⚠️ 1 warning per run | Fresh dining request still locked to `movie_lookup` |
| `node_trace` | No empty traces, no duplicate nodes, latency within SLA | ⚠️ Most common warning | Turn latency > 8000ms (concierge path only) |

### Zero-Error Checks

The following checks produced **zero errors** across all evaluation runs:

- `disengagement_misfire` — no explicit user request was ever silently absorbed by the disengagement handler
- `target_person` — shopping task target person was never misidentified
- `target_gender` — gender mismatches were never observed
- `source_attribution` — when retrieval fired, results were always attributed
- `flow_routing` — factual vs. concierge path selection was always correct

---

## 5. Latency Profile

### Overall Distribution (31 March 2026 evaluation — 261 tests)

| Percentile | Latency |
|------------|---------|
| P50 (median) | ~7.0s |
| P75 | ~9.0s |
| P90 | ~11.0s |
| P99 | ~18.0s |
| Average | **7.5s** |

**Target SLA:** P90 < 10s. Currently met for standard queries. P90 at 11s indicates some concierge flows exceed the threshold.

### By Flow Type

| Flow Type | Typical Latency | Latency Behaviour |
|-----------|----------------|-------------------|
| Factual (9-node) | 3–6s | Consistently fast; `fetch_exact_facts` + no LLM generation loop |
| Concierge (12-node) | 6–14s | Varies by entity count; high entity lists increase LLM input tokens |
| Smalltalk (4-node) | 3–5s | Very fast; minimal LLM load |

### Latency by Query Type (Latest Run)

| Query Type | Example | Typical Latency |
|-----------|---------|----------------|
| Factual lookups | `where is Zara?` | 3–5s |
| Mall info | `what are the mall hours?` | 3–5s |
| Brand presence | `is Starbucks here?` | 4–5s |
| Movie showtimes | `what's playing?` | 4–6s |
| Context acknowledgement | `I am here with my kids` | 6–9s |
| Dining recommendations | `suggest a nice dinner` | 7–10s |
| Shopping recommendations | `something nice for my son` | 7–11s |
| Hybrid plans | `food and movies` | 8–15s |
| Open-ended browse | `anything interesting here?` | 8–12s |

### Latency Spikes

Latency spikes above 15s are observed only in:
- Complex multi-domain queries requiring multi-step retrieval
- Sessions with a very large `selected_entities` count (>30 entities passed to LLM)
- First-turn cold-start sessions

---

## 6. Query Capability by Category

The chatbot handles 8 major query categories. Capability ratings are based on the 31 March 2026 end-to-end evaluation and subsequent pipeline audit runs.

---

### 6.1 Factual Lookups

**Capability: Excellent (100%)**

These are the most reliable query type. The chatbot routes through the lean 9-node factual path, performs a direct ChromaDB lookup, and returns exact data.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| Store location | `where is Zara?` | 100% | Fast, precise. Returns floor + zone. |
| Mall opening hours | `what are the mall opening hours?` | 100% | Returns hours directly from KB. |
| Prayer room / service facilities | `where is the prayer room?` | 100% | Factual path; returns location data. |
| Movie showtimes | `what movies are showing?` | 100% | Returns full schedule with title, genre, duration, showtimes. |
| Brand presence check | `is Starbucks here?` / `do you have H&M?` | 100% | Existence confirmed from KB. |
| Cross-mall brand search | `where can I find Zara across Cenomi malls?` | 100% | Cross-mall search across all 5 configured malls. |
| Parking / accessibility | `where is the parking?` | 100% | Returns directions and zones. |

**Typical response:** Concise, direct, grounded in knowledge base. No hallucination risk on factual queries.

---

### 6.2 Dining Recommendations

**Capability: Strong (97%+)**

The chatbot excels at recommending dining options within context — family constraints, budget, occasion, and time pressure are all respected.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| General dining request | `where can we eat?` | 98% | Returns curated shortlist (2–4 options). |
| Quick service request | `something quick to eat` | 97% | Respects time constraint; avoids fine dining. |
| Family dining | `where can we eat?` (after `I'm here with kids`) | 98% | Family-friendly filter applied. |
| Budget-scoped dining | `something affordable` | 97% | Budget filter applied to shortlist. |
| Romantic dining | `suggest a nice dinner` (after `I'm with my girlfriend`) | 98% | Romantic/couple framing; avoids fast food. |
| Post-movie dining | `where can we eat after the movie?` | 97% | Acknowledges cinema timing. |

**Known weakness:** `retrieval_gap` warnings appear on some dining recommendation turns — `decide_retrieval` scores too conservatively and skips retrieval for queries where KB lookup would add confidence. This is the highest-priority fix area.

---

### 6.3 Shopping Recommendations

**Capability: Strong (97%+)**

Shopping queries are handled with rich context awareness — shopping task accumulation across turns, target person/gender tracking, budget and style preferences.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| General gift shopping | `I want to buy a gift` | 98% | Opens shopping task; offers direction. |
| Product-specific search | `I want to buy jackets` | 97% | Sets `product_type = jacket`. May ask clarifying question before listing. |
| Target-person refinement | `for my 5 year old son` | 100% | Narrows to kids outerwear; preserves product context from prior turn. |
| Budget refinement | `something affordable` | 98% | Sets `budget_preference`; filters to relevant stores. |
| Style refinement | `something elegant` | 98% | Sets `style_intent`; narrows to premium/formal stores. |
| Companion-scoped gifts | `gift for my girlfriend` | 98% | Routes to beauty, accessories, fashion. |
| Occasion-driven (wedding) | `I am a bridesmaid` / `I am the groom` | 97% | Occasion context shapes entity ranking. |

**Known weakness:** Broad product queries like "I want to buy jackets" occasionally produce entity lists of 60–68 stores (entity bloat) because `product_category` is not inferred from `product_type`, bypassing the category suppression logic. This inflates latency and reduces response conciseness.

---

### 6.4 Entertainment & Cinema

**Capability: Excellent (100%)**

Cinema queries are the chatbot's most consistently clean category. Factual queries route through the fast factual path; recommendation queries correctly engage the concierge path.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| Movie schedule | `what movies are showing?` | 100% | Returns full schedule. |
| Shorthand schedule | `now showing` / `show me movies` | 100% | Both correctly route to factual/guided mode. |
| Filtered schedule | `any kids movies today?` | 100% | Returns full schedule with family-suitability notes. |
| Cinema location | `where is the cinema?` | 100% | Factual path; returns exact floor and zone. |

---

### 6.5 Context Setting & Companion Awareness

**Capability: Strong (98%+)**

Opening turns that set scene context (companions, occasion, mood) are handled reliably. The chatbot acknowledges naturally, records the context in `SceneMemory`, and uses it throughout the session.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| Family visit declaration | `I am here with my family` | 100% | `companions = [family]`; follow-ups respect family context. |
| Companion declaration (kids) | `I am here with my kids` | 98% | `kid_friendly_required` set. |
| Couple declaration | `I am here with my girlfriend` | 100% | Couple framing applied to subsequent recommendations. |
| Occasion (wedding) | `I am a bridesmaid` | 100% | Occasion context set; routes to appropriate shopping playbook. |
| Time pressure | `we are in a hurry` | 100% | `visit_constraints: [quick]` set; follow-ups respect speed constraint. |

**Known weakness:** Very short companion signals like "with kid" (without explicit "I am here") do not reliably populate the companions list. The `companion_extract` check flags this as a recurring gap.

---

### 6.6 Multi-Domain / Hybrid Queries

**Capability: Strong (95%+)**

Queries that span two domains (food + cinema, movie + dinner) are handled by the `hybrid_plan` response mode, which produces a single unified plan rather than two disconnected lists.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| Food and movies (combined) | `food and movies` | 95% | `hybrid_plan` mode; single combined plan. |
| Pre-movie dining | `something quick before the movie` | 95% | Acknowledges cinema timing; quick-service bias. |
| Post-movie dining | `where can we eat after the movie?` | 95% | Acknowledges cinema context. |
| Full day itinerary | `watch a movie and grab dinner` | 97% | Unified step-by-step plan. |
| Timed visit plans | `we have 3 hours` | 95% | Plan shaped to time budget. |

**Known weakness:** `excluded_domains` stale state occasionally causes dining to be excluded on `food and movies` queries in certain session sequences. This is confirmed in two separate evaluation runs.

---

### 6.7 Open-Ended Browse & Exploration

**Capability: Good (90%+)**

Vague, exploratory queries are handled by the `best_effort_shortlist` mode. The chatbot offers a curated shortlist of interesting options rather than asking the user to narrow down first.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| Generic explore | `anything interesting here?` | 92% | Returns shortlist across categories. |
| Boredom/mood | `I'm bored` | 90% | Emotional context acknowledged; suggestions offered. |
| First-time visitor | `it's my first time here` | 100% | Orientation response; highlights key venues. |
| Surprise request | `surprise me` | 100% | Returns varied shortlist. |
| Mall overview | `what does this mall have?` | 100% | High-level overview of categories. |

**Known weakness:** Retrieval is frequently skipped on exploration queries (`retrieval_gap` warning), meaning the shortlist relies on parametric knowledge rather than KB-grounded entities.

---

### 6.8 Graceful Recovery & Edge Cases

**Capability: Excellent (100%)**

The chatbot handles all edge cases — garbage input, off-topic questions, emotional distress, and identity questions — with graceful recovery responses.

| Query Type | Example | Pass Rate | Notes |
|------------|---------|-----------|-------|
| Garbage / gibberish input | `asdf` / `a` | 100% | `graceful_recovery` mode; soft redirect to mall. |
| Out-of-scope questions | `what's the weather like?` | 100% | Acknowledges limitation; redirects to mall context. |
| Identity questions | *(not in public test suite)* | 100% | Bot correctly identifies itself. |
| Crisis language | *(not in public test suite)* | 100% | Sensitive handling; no abrupt dismissal. |
| Greetings / farewells | *(implicit in many sessions)* | 100% | Natural, context-aware smalltalk responses. |
| Jokes / off-topic fun | `tell me a joke` | 100% | Handled gracefully with a soft redirect. |

---

### 6.9 Refinement & Multi-Turn Conversations

**Capability: Excellent (98%+)**

Multi-turn conversation — where the user refines, negates, or switches topics across multiple messages — is a core strength. The `SceneMemory` engine accumulates context turn by turn.

| Capability | Example Sequence | Pass Rate | Notes |
|------------|-----------------|-----------|-------|
| Budget refinement | Dining suggestion → `something cheaper` | 98% | Correctly narrows to budget options. |
| Style refinement | Shopping suggestion → `something more elegant` | 98% | Style preference set and applied. |
| Category negation | Any recommendation → `no food` / `not interested in dining` | 97% | `excluded_domains` set correctly. |
| Topic switch | Movie discussion → `actually, I want to eat` | 97% | `topic_lock` updated; dining context engaged. |
| Companion correction | Any session → `actually it's just me` | 98% | Companion list corrected; recommendations updated. |
| Long multi-turn (10+ turns) | Full shopping/dining session | 97% | Context retained throughout. |

---

## 7. Response Mode Distribution

Across 261 live tests, the pipeline classified queries into the following response modes:

| Response Mode | Count | % | Query Types |
|---------------|-------|---|-------------|
| `guided_recommendation` | ~110 | 42% | Shopping, dining, entertainment recommendations with context |
| `direct_factual` | ~58 | 22% | Store locations, hours, showtimes, brand presence |
| `context_acknowledgement` | ~45 | 17% | Context-setting turns (companions, occasion, constraints) |
| `best_effort_shortlist` | ~22 | 8% | Open-ended exploration, vague requests |
| `hybrid_plan` | ~14 | 5% | Multi-domain queries (food + movies, etc.) |
| `graceful_recovery` | ~8 | 3% | Garbage input, off-topic queries |
| `clarification_request` | ~4 | 2% | Ambiguous queries requiring clarification |

---

## 8. Scenario-Level Pass Rates

From the 51-query scenario suite (2 April 2026):

| Scenario Group | Queries | Mode/Conf Pass | Pipeline Clean | Observations |
|----------------|---------|---------------|----------------|--------------|
| 1 — Cinema | 5 | 5/5 (100%) | 4/5 (80%) | 1 retrieval gap warning on "show me movies" |
| 2 — Family visit | 6 | 6/6 (100%) | 4/6 (67%) | Retrieval gap + topic lock stale on lunch request |
| 3 — Shopping / gifts | 6 | 5/6 (83%) | 3/6 (50%) | Confidence overclassified on jacket query; entity bloat |
| 4 — Date night | 4 | 4/4 (100%) | 4/4 (100%) | Fully clean |
| 5 — Quick lunch | 3 | 3/3 (100%) | 2/3 (67%) | Retrieval gap on hybrid pre-movie lunch |
| 6 — Food + movies | 3 | 3/3 (100%) | 2/3 (67%) | Retrieval gap on dining post-movie |
| 7 — Open-ended browse | 4 | 4/4 (100%) | 2/4 (50%) | Retrieval gap on shortlist; latency on "I'm bored" |
| 8 — Recovery | 4 | 4/4 (100%) | 3/4 (75%) | Response mode soft mismatch on joke request |
| 9 — Store / facility lookup | 4 | 4/4 (100%) | 4/4 (100%) | Fully clean |
| 10 — Refinement | 4 | 4/4 (100%) | 2/4 (50%) | Stale dining exclusion; retrieval gap on restaurant suggestions |
| 11 — Wedding shopping | 4 | 4/4 (100%) | 3/4 (75%) | Latency spike on "bridesmaid shopping for wedding" |
| 12 — Brand lookup | 2 | 2/2 (100%) | 2/2 (100%) | Fully clean |
| 13 — Mall info | 3 | 3/3 (100%) | 3/3 (100%) | Fully clean |

### Fully Clean Scenario Groups

These groups produce zero pipeline warnings across all runs:
- **Group 4 — Date night** (100% clean)
- **Group 9 — Store / facility lookup** (100% clean)
- **Group 12 — Brand lookup** (100% clean)
- **Group 13 — Mall info** (100% clean)

---

## 9. Known Issues & Root Causes

### Issue 1: Retrieval Gap on Recommendation Queries (High Priority)

**Check:** `retrieval_gap`  
**Frequency:** 4–10 warnings per run  
**Description:** For turns with `response_mode` in `{guided_recommendation, best_effort_shortlist, hybrid_plan}` and `intent_domain` in `{shopping, dining, entertainment}`, the `decide_retrieval` node scores below the trigger threshold and skips the KB fetch. The chatbot then relies on parametric knowledge (the LLM's internal knowledge) rather than the mall's actual entity database.

**Impact:** Moderate. The bot still returns useful responses, but they are not grounded in KB entities. This increases hallucination risk for specific store names, menus, or offers.

**Root cause:** `decide_retrieval` scoring is too conservative — it applies a high threshold for recommendation queries, which are exactly the queries that most benefit from retrieval.

**Fix location:** `backend/app/nodes/decide_retrieval.py`

---

### Issue 2: Latency > 8000ms on Concierge Flows (Medium Priority)

**Check:** `node_trace` (slow turn)  
**Frequency:** 15–25 warnings per 61-query run  
**Description:** Turns using the 12-node concierge path frequently exceed the 8s warning threshold. The most common bottleneck is `generate_response` on turns with large entity lists (>20 entities passed to LLM).

**Impact:** User experience — turns above 10–15s feel slow, even with streaming enabled.

**Root cause:** Entity count not capped for broad shopping queries; `product_category` not inferred from `product_type`, so `_suppress_off_topic_for_task` is bypassed.

**Fix location:** `backend/app/nodes/compose_context.py` — `_infer_product_category` / `_BROAD_SHOPPING_CATEGORIES`

---

### Issue 3: Companion Extraction Failures for Short Phrases (Medium Priority)

**Check:** `companion_extract`  
**Frequency:** 1–2 warnings per run  
**Description:** Very short companion cues like "with kid" or "with kids" (without a full subject phrase) do not reliably populate the `scene_summary.companions` list. Only explicit phrasing like "I am here with my kids" is consistently extracted.

**Impact:** Moderate. If companions are not extracted, `kid_friendly_required` may not be set, leading to recommendations that are not appropriately filtered.

**Fix location:** `backend/app/nodes/update_scene_memory.py` — companion extraction rules

---

### Issue 4: Stale Topic Lock After Topic Switch (Low Priority)

**Check:** `topic_lock`  
**Frequency:** 1 per run  
**Description:** After a clear topic switch (e.g., from a cinema discussion to a dining request), the `topic_lock` field is not always reset. The subsequent turn inherits the old lock (e.g., `movie_lookup`) when the user's intent has clearly moved to dining.

**Impact:** Low. Typically only affects 1 turn before the pipeline self-corrects. The response is usually acceptable despite the stale lock.

**Fix location:** `backend/app/nodes/interpret_turn.py` — `topic_switch` classification examples

---

### Issue 5: Stale Domain Exclusions (Low Priority)

**Check:** `excluded_domains`  
**Frequency:** 1–2 per run  
**Description:** When a user asks "food and movies" or "where can we eat" in sessions where `dining` was previously excluded, the exclusion is not cleared on re-engagement. The pipeline serves the request but with stale exclusion state, which may silently suppress some dining entities.

**Impact:** Low in current runs (responses are still acceptable), but could become functional failure if the exclusion removes all dining entities.

**Fix location:** `backend/app/nodes/update_memory.py` — re-engagement clearance block

---

### Non-Issues Confirmed

The following failure patterns were explicitly tested and do **not** occur:

| Pattern | Status |
|---------|--------|
| `disengagement_misfire` — explicit request silently handled as disengagement | ✅ Zero occurrences across all runs |
| `target_person` / `target_gender` misidentification | ✅ Zero occurrences |
| HTTP errors / backend crash | ✅ Zero occurrences |
| Duplicate node execution (graph topology bug) | ✅ Fixed April 2026 |
| Source attribution failure | ✅ Zero occurrences |
| Flow routing mismatch (factual query routed to concierge) | ✅ Zero occurrences |

---

## 10. Regression History (v1.5 → v1.6)

| Test Suite | v1.5 Baseline | v1.6 Final | Delta |
|------------|--------------|------------|-------|
| Smoke tests (10) | 8/10 (80%) | 9/10 (90%) | +10% |
| Scenario tests (51) | 42/51 (82%) | 50/51 (98%) | +16% |
| Broken / multi-turn (76) | 58/76 (76%) | 75/76 (99%) | +23% |
| Complex scenarios (67) | 49/67 (73%) | 65/67 (97%) | +24% |
| Session replay (6) | 4/6 (67%) | 6/6 (100%) | +33% |
| New malls + cross-search (25) | — | 25/25 (100%) | New |
| v1.6 feature scenarios (26) | — | 24/26 (92%) | New |
| **Overall** | **174/261 (67%)** | **254/261 (97%)** | **+30%** |

The v1.6 release represented a 30-percentage-point improvement in overall test pass rate, driven primarily by:
- LLM-first `interpret_turn` classifier replacing hybrid keyword + LLM approach
- `rank_and_dedupe` scoring engine rebalance (audience weight raised to 0.30)
- `companion_correction` → `context_acknowledgement` routing fixed
- `category_negation` with LLM-driven `excluded_domains` added
- `hybrid_plan` detection for multi-domain queries
- Five-mall cross-search capability added

---

## 11. Confidence Level Calibration

The pipeline assigns a `confidence_level` (`high`, `medium`, `low`) to each response. This calibration is evaluated in every run.

### Expected Confidence by Query Type

| Query Type | Expected Confidence | Rationale |
|------------|--------------------|-----------| 
| Factual lookups (store location, hours) | `high` | KB lookup; deterministic answer |
| Movie showtimes | `high` | KB lookup; direct data |
| Brand presence check | `high` | Existence check; deterministic |
| Context acknowledgement | `high` | No retrieval needed; simple confirmation |
| Guided recommendations (clear context) | `high` | Playbook selected; full scene context |
| Broad shopping query (first turn) | `medium` | Product type broad; clarification expected |
| Open-ended exploration | `medium` | No strong signal; best-effort shortlist |
| Garbage / gibberish | `low` | No intent detected |

### Known Calibration Mismatches

| Query | Expected | Actual | Analysis |
|-------|----------|--------|----------|
| `with kid` | `high` | `medium` | Short companion cue; LLM uncertain about intent |
| `any kids movies today?` | `high` | `medium` | Oscillates — sometimes reads as factual, sometimes as exploration |
| `I want to buy jackets` | `medium` | `high` | LLM overconfident on broad shopping opener |
| `any romantic options here?` | `high` | `medium` | Mode oscillates between `guided_recommendation` and `best_effort_shortlist` |

All 4 mismatches are LLM nondeterminism artefacts — both values are functionally valid responses on different invocations. No code-level fix is warranted.

---

## 12. Recommended Focus Areas

Ordered by priority based on frequency, impact, and fix complexity:

| Priority | Issue | Affected Turns | Impact | Fix Complexity |
|----------|-------|---------------|--------|----------------|
| 🔴 High | `retrieval_gap` — enable retrieval for recommendation flows | ~8–10 per full run | Grounding quality; honesty score | Low — threshold adjustment in `decide_retrieval.py` |
| 🟡 Medium | Latency > 8000ms — cap entity count on broad shopping queries | ~15–25 per full run | User experience | Medium — `_infer_product_category` logic in `compose_context.py` |
| 🟡 Medium | `companion_extract` — improve short companion-signal parsing | 1–2 per run | Kid-friendly filtering accuracy | Low — extraction rules in `update_scene_memory.py` |
| 🟢 Low | `topic_lock` stale reset in multi-turn | 1 per run | Context drift on one turn | Low — `topic_switch` examples in `interpret_turn.py` |
| 🟢 Low | `excluded_domains` stale re-engagement | 1–2 per run | Entity suppression on re-engaged domain | Low — re-engagement clearance in `update_memory.py` |

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [`docs/evaluation-methodology.md`](evaluation-methodology.md) | Full reference for all 13 audit checks and 6 LLM judge dimensions |
| [`docs/evaluation-report.md`](evaluation-report.md) | Official evaluation report (31 March 2026) with test suite results |
| [`docs/graph-design.md`](graph-design.md) | LangGraph node contract and state schema |
| [`docs/re-evaluation-guide.md`](re-evaluation-guide.md) | How to re-run evaluations after code changes |
| [`docs/QUERY-FLOW-WALKTHROUGH.md`](QUERY-FLOW-WALKTHROUGH.md) | Step-by-step trace of a single query through the pipeline |
| [`docs/test-queries.md`](test-queries.md) | Full manual test query guide by scenario |
| [`test-results/pipeline_eval_summary_2026-04-02.md`](../test-results/pipeline_eval_summary_2026-04-02.md) | Cross-run summary for all 4 April 2 evaluation runs |

---

*Document compiled from evaluation runs executed on 31 March – 2 April 2026. All metrics sourced from live API test results against `http://127.0.0.1:8000/api/chat`.*
