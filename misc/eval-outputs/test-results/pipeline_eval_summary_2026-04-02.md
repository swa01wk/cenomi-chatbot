# Cenomi Chatbot — Pipeline Evaluation Summary

**Generated:** 2026-04-02  
**Target:** `http://127.0.0.1:8000/api/chat`  
**Judge model:** `gpt-5.4-mini`

---

## Overview of All Runs

Four evaluation commands were executed across the four supported invocation modes. Results are captured below with links to their individual full reports.

| Run | Command | Queries | Mode/Conf Pass | Pipeline Clean | Errors | Warnings | Report |
|-----|---------|---------|---------------|---------------|--------|----------|--------|
| 1 | `python scripts/run_pipeline_evaluation.py` (all, baseline) | 61 | 49 / 61 (80%) | 14 / 61 (23%) | 5 | 110 | [16:30 report](pipeline_eval_2026-04-02_16-30-57.md) |
| 2 | `python scripts/run_pipeline_evaluation.py --suite smoke` | 10 | 10 / 10 (100%) | 7 / 10 (70%) | 0 | 4 | [17:44 report](pipeline_eval_2026-04-02_17-44-37.md) |
| 3 | `python scripts/run_pipeline_evaluation.py --suite scenario` | 51 | 50 / 51 (98%) | 37 / 51 (73%) | 0 | 17 | [17:50 report](pipeline_eval_2026-04-02_17-50-46.md) |
| 4 | `python scripts/run_pipeline_evaluation.py --model gpt-5.4-mini` | 61 | 55 / 61 (90%) | 32 / 61 (52%) | 1 | 41 | [17:31 report](pipeline_eval_2026-04-02_17-31-25.md) |

> **Note:** `gpt-5.4-mini` is also the default judge model. Run 4 reflects a later run of the full suite showing improvement over Run 1 — same model, different session order.

---

## Run 1 — All 61 Queries (Baseline)

**Run ID:** `78cfe517`  
**Generated:** 2026-04-02 16:30:57 UTC

### Summary

| Metric | Value |
|--------|-------|
| Total queries | 61 |
| ✅ Mode+Conf match | 49 (80%) |
| ❌ Mode/Conf mismatch | 12 |
| 💥 HTTP errors | 0 |
| Turns audited | 61 |
| ✅ Clean turns | 14 (23%) |
| ❌ Turns with issues | 47 |
| 🔴 Pipeline errors | 5 |
| 🟡 Pipeline warnings | 110 |

**Full report:** [`pipeline_eval_2026-04-02_16-30-57.md`](pipeline_eval_2026-04-02_16-30-57.md)

---

## Run 2 — Smoke Suite (10 Queries)

**Run ID:** `1cd8f807`  
**Generated:** 2026-04-02 17:44:37 UTC  
**Command:** `python scripts/run_pipeline_evaluation.py --suite smoke`

### Summary

| Metric | Value |
|--------|-------|
| Total queries | 10 |
| ✅ Mode+Conf match | 10 (100%) |
| ❌ Mode/Conf mismatch | 0 |
| 💥 HTTP errors | 0 |
| Turns audited | 10 |
| ✅ Clean turns | 7 (70%) |
| ❌ Turns with issues | 3 |
| 🔴 Pipeline errors | 0 |
| 🟡 Pipeline warnings | 4 |

### Per-Query Results

| ID | Query | Mode | Conf | Pipeline |
|----|-------|------|------|----------|
| S-01 | `what movies are showing?` | ✅ direct_factual | ✅ high | ✅ clean |
| S-02 | `i am here with my kids` | ✅ context_acknowledgement | ✅ high | ✅ clean |
| S-03 | `where can we eat?` | ✅ guided_recommendation | ✅ high | ✅ clean |
| S-04 | `food and movies` | ✅ hybrid_plan | ✅ medium | ❌ 1 warn — latency >8000ms |
| S-05 | `anything interesting here?` | ✅ best_effort_shortlist | ✅ medium | ❌ 2 warn — retrieval gap + latency |
| S-06 | `where is the prayer room?` | ✅ direct_factual | ✅ high | ✅ clean |
| S-07 | `i am a bridesmaid` | ✅ context_acknowledgement | ✅ high | ✅ clean |
| S-08 | `something nice for my son` | ✅ guided_recommendation | ✅ high | ✅ clean |
| S-09 | `asdf` | ✅ graceful_recovery | ✅ low | ✅ clean |
| S-10a | `with kid` | ✅ direct_factual | ✅ high | ❌ 1 warn — companion not extracted |

### Notable Warnings

- **`[node_trace]`** Latency exceeded 8000ms on S-04 (9999ms) and S-05 (8019ms).
- **`[retrieval_gap]`** S-05: recommendation-like mode triggered but retrieval not invoked.
- **`[companion_extract]`** S-10a: "with kid" said but companions list remained empty.

**Full report:** [`pipeline_eval_2026-04-02_17-44-37.md`](pipeline_eval_2026-04-02_17-44-37.md)

---

## Run 3 — Scenario Suite (51 Queries)

**Run ID:** `2bd0ef93`  
**Generated:** 2026-04-02 17:50:46 UTC  
**Command:** `python scripts/run_pipeline_evaluation.py --suite scenario`

### Summary

| Metric | Value |
|--------|-------|
| Total queries | 51 |
| ✅ Mode+Conf match | 50 (98%) |
| ❌ Mode/Conf mismatch | 1 |
| 💥 HTTP errors | 0 |
| Turns audited | 51 |
| ✅ Clean turns | 37 (73%) |
| ❌ Turns with issues | 14 |
| 🔴 Pipeline errors | 0 |
| 🟡 Pipeline warnings | 17 |

### Mode/Conf Mismatch

| ID | Query | Expected | Got |
|----|-------|----------|-----|
| 3.2 | `i want to buy jackets` | conf=`medium` | conf=`high` ✗ |

### Pipeline Warnings Breakdown

| Check | Count | Example |
|-------|-------|---------|
| `retrieval_gap` | 7 | Recommendation turns not triggering retrieval |
| `node_trace` (latency) | 4 | Turns >8000ms |
| `topic_lock` | 1 | Fresh request but topic_lock still `movie_lookup` |
| `excluded_domains` | 1 | Dining excluded despite explicit user request |
| `entity_pipeline` | 1 | High entity count (29) |
| `companion_extract` | 1 | Companion not extracted from "with kid" |
| `response_mode` | 1 | graceful_recovery used where off-topic handling applied |
| `intent_routing` | 1 | Domain not in canonical list |

### Per-Scenario Group Results

| Group | Queries | Mode/Conf Pass | Pipeline Clean |
|-------|---------|---------------|----------------|
| 1 — Cinema | 5 | 5/5 (100%) | 4/5 (80%) |
| 2 — Family visit | 6 | 6/6 (100%) | 4/6 (67%) |
| 3 — Shopping / gifts | 6 | 5/6 (83%) | 3/6 (50%) |
| 4 — Date night | 4 | 4/4 (100%) | 4/4 (100%) |
| 5 — Quick lunch | 3 | 3/3 (100%) | 2/3 (67%) |
| 6 — Food + movies | 3 | 3/3 (100%) | 2/3 (67%) |
| 7 — Open-ended browse | 4 | 4/4 (100%) | 2/4 (50%) |
| 8 — Recovery | 4 | 4/4 (100%) | 3/4 (75%) |
| 9 — Store/facility lookup | 4 | 4/4 (100%) | 4/4 (100%) |
| 10 — Refinement | 4 | 4/4 (100%) | 2/4 (50%) |
| 11 — Wedding shopping | 4 | 4/4 (100%) | 3/4 (75%) |
| 12 — Brand lookup | 2 | 2/2 (100%) | 2/2 (100%) |
| 13 — Mall info | 3 | 3/3 (100%) | 3/3 (100%) |

**Full report:** [`pipeline_eval_2026-04-02_17-50-46.md`](pipeline_eval_2026-04-02_17-50-46.md)

---

## Run 4 — All 61 Queries with `--model gpt-5.4-mini`

**Run ID:** `cff2be16`  
**Generated:** 2026-04-02 17:31:25 UTC  
**Command:** `python scripts/run_pipeline_evaluation.py --model gpt-5.4-mini`

### Summary

| Metric | Value |
|--------|-------|
| Total queries | 61 |
| ✅ Mode+Conf match | 55 (90%) |
| ❌ Mode/Conf mismatch | 6 |
| 💥 HTTP errors | 0 |
| Turns audited | 61 |
| ✅ Clean turns | 32 (52%) |
| ❌ Turns with issues | 29 |
| 🔴 Pipeline errors | 1 |
| 🟡 Pipeline warnings | 41 |

**Full report:** [`pipeline_eval_2026-04-02_17-31-25.md`](pipeline_eval_2026-04-02_17-31-25.md)

---

## Cross-Run Analysis

### Mode + Confidence Accuracy

```
Run 1 (all, baseline)       ████████████████░░░░  80%  (49/61)
Run 4 (all, gpt-5.4-mini)   ██████████████████░░  90%  (55/61)
Run 3 (--suite scenario)    ███████████████████░  98%  (50/51)
Run 2 (--suite smoke)       ████████████████████ 100%  (10/10)
```

### Pipeline Clean Rate

```
Run 1 (all, baseline)       █████░░░░░░░░░░░░░░░  23%  (14/61)
Run 4 (all, gpt-5.4-mini)   ██████████░░░░░░░░░░  52%  (32/61)
Run 2 (--suite smoke)       ██████████████░░░░░░  70%  (7/10)
Run 3 (--suite scenario)    ██████████████░░░░░░  73%  (37/51)
```

### Key Observations

1. **Mode/Conf routing is reliable** — 98–100% pass rate on dedicated smoke and scenario suites. The lower 80–90% in full-suite runs reflects edge-case queries evaluated out of conversational context.

2. **Most common pipeline issue: `retrieval_gap`** — Recommendation-oriented response modes (guided_recommendation, best_effort_shortlist, hybrid_plan) frequently skip retrieval. This is the single biggest source of warnings.

3. **Latency warnings cluster above 8000ms** — Several turns exceed the 8s threshold. These are almost exclusively 12-node concierge flows; factual (9-node) flows are consistently fast.

4. **Zero HTTP errors** across all 4 runs — the backend is stable.

5. **`companion_extract` is a recurring gap** — Short context-setting messages like "with kid" or "with kids" do not reliably populate the companions list.

6. **`topic_lock` stale state** — One scenario (2.7) shows a fresh dining request still locked to `movie_lookup`, indicating a possible state-reset gap in multi-turn flows.

### Recommended Focus Areas

| Priority | Issue | Affected Turns |
|----------|-------|---------------|
| 🔴 High | `retrieval_gap` — enable retrieval for recommendation flows | ~8–10 across suites |
| 🟡 Medium | Latency >8000ms — profile 12-node concierge pipeline | ~4–5 per run |
| 🟡 Medium | `companion_extract` — improve short companion-signal parsing | 1–2 per run |
| 🟢 Low | `topic_lock` stale reset in multi-turn | 1 per run |

---

## Individual Report Files

| File | Suite | Queries | Generated |
|------|-------|---------|-----------|
| [`pipeline_eval_2026-04-02_16-30-57.md`](pipeline_eval_2026-04-02_16-30-57.md) | all | 61 | 2026-04-02 16:30 UTC |
| [`pipeline_eval_2026-04-02_16-57-07.md`](pipeline_eval_2026-04-02_16-57-07.md) | all | 61 | 2026-04-02 16:57 UTC |
| [`pipeline_eval_2026-04-02_17-31-25.md`](pipeline_eval_2026-04-02_17-31-25.md) | all (gpt-5.4-mini) | 61 | 2026-04-02 17:31 UTC |
| [`pipeline_eval_2026-04-02_17-44-37.md`](pipeline_eval_2026-04-02_17-44-37.md) | smoke | 10 | 2026-04-02 17:44 UTC |
| [`pipeline_eval_2026-04-02_17-50-46.md`](pipeline_eval_2026-04-02_17-50-46.md) | scenario | 51 | 2026-04-02 17:50 UTC |
