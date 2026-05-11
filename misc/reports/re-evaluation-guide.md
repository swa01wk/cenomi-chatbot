# Re-Evaluation Guide — Adaptive Concierge Engine

> Use this guide after implementing the adaptive concierge changes to validate regressions
> and confirm new behavior. Cross-reference with the baseline: `test-results/EVALUATION_SUMMARY_2026-03-20.md`.

---

## Baseline (Before Changes)

| Suite | Source File | Queries | Pass % |
|---|---|---|---|
| Standard | `test-queries.md` | 61 | **98%** (60/61) |
| Broken / Multi-turn | `test-queries-broken.md` | 76 | **93%** (71/76) |
| Complex (10–14 turn) | `test-queries-complex.md` | 128 | **99%** (127/128) |
| **TOTAL** | — | **265** | **97%** (258/265) |

---

## What Changed and Why It Affects Tests

### Change 1 — Scene Defaults (`update_scene_memory.py`)

When a shopping query is underspecified (e.g. `"I need a jacket"`), the system now fills
missing `ShoppingTask` fields with defaults instead of asking a clarifying question:

- `target_person = "self"`
- `budget_preference = "mid_range"`
- `use_case = "casual"`

Defaults are only applied when the field is still empty after extraction. If any context
already exists in the conversation, it is used instead — no question is asked.

**Impact on tests:** Direct conflict with `test-queries-broken.md` S01.1.1.

---

### Change 2 — Ranking Weights (`rank_and_dedupe.py`)

Audience fit weight raised from `0.15 → 0.30`. A hard post-scoring penalty (0.2× multiplier)
is applied when audience fit is below 0.2 and the scene has an active audience requirement
(kid/family/budget context). Premium stores are de-ranked for budget queries.

**Impact on tests:** No test directly checks ranking scores. Could improve S08 (Kid Context Drop, currently 2/4).

---

### Change 3 — Response Format (`concierge_prompt.py` + `generate_response.py`)

Responses now follow a 4-part structure:

1. PRIMARY RECOMMENDATION — best option + 1-line why
2. SECONDARY OPTION — fallback + 1-line why
3. ACTION PLAN — where to go first, what to do next
4. OPTIONAL FOLLOW-UP — only if genuinely needed

Entity caps updated: max 2–3 recommendations for guided/contextual queries (was 5–6).
Category dump queries (`"what cafes are here?"`) are unaffected — still list all.

**Impact on tests:** Tests check `response_mode` and `confidence_level`, not prose structure.
Low regression risk. Verify category-list queries still return full lists.

---

### Change 4 — Playbook Tone Mapping + Cross-Domain Continuity (`generate_response.py`)

- Playbook → tone instructions added (e.g. family playbook → simple language, quick decisions).
- Cross-domain block: when `topic_history` spans domains AND companions are set, the prompt
  now explicitly carries the audience constraint into the new domain (e.g. family with kid →
  food must be kid-friendly, not fine-dining).

**Impact on tests:** Behavioral improvements. Could improve S02 (Movie Refinement, 2/4) and S08 (Kid Context Drop, 2/4).

---

## Test Expectation That Must Be Updated Before Running

**File:** `docs/test-queries-broken.md`  
**Scenario:** S01 — Jacket Refinement, Turn 1.1  
**Query:** `"i want to buy jackets"`

| | Before | After |
|---|---|---|
| **Must** | Ask a clarifying question: who is it for, or what kind? | Infer defaults (target=self, budget=mid, use_case=casual) and provide a direct recommendation without asking a clarifying question. |
| **Must NOT** | Immediately list all jacket stores | List stores with no reasoning; ask multiple questions |
| **response_mode** | `guided_recommendation` | `guided_recommendation` (unchanged) |
| **confidence_level** | `medium` | `medium` (unchanged) |

> This is not a regression — it is an intentional behavior change mandated by the spec.
> If you run without updating the expectation, the test will report a false failure.

---

## Recommended Testing Order

### Step 1 — Run Broken Suite First (highest impact)

```
docs/test-queries-broken.md   →   76 queries
```

This is the most impacted suite. S01, S02, and S08 are all in the blast radius of the changes.

**Target:** Maintain ≥ 93% (71/76). Changes should improve S02 and S08; S01 needs updated expectation.

Key scenarios to watch:

| Scenario | Current | Expected After Changes |
|---|---|---|
| S01 — Jacket Refinement | 4/4 ✅ | 4/4 ✅ (after expectation update) |
| S02 — Movie Refinement | 2/4 ⚠️ | 3–4/4 (cross-domain block should help) |
| S08 — Kid Context Drop | 2/4 ⚠️ | 3–4/4 (audience weight raise should help) |
| S16 — Wedding Smart-Casual | 3/4 ⚠️ | 3/4 (known trade-off, unrelated to changes) |

---

### Step 2 — Selective Run on Standard Suite

```
docs/test-queries.md   →   only these scenarios
```

| Scenario | Queries | Why |
|---|---|---|
| Scenario 2 — Family Day Out | 2.1–2.5 | Tests companion carry-forward and scene acknowledgment |
| Scenario 3 — Shopping / Jacket | 3.1–3.6 | Directly tests shopping defaults and refinement |
| Scenario 5 — Dining with Constraints | 5.x | Tests constraint + companion context |

Skip: factual lookups (Scenarios 1, 7, 8), services (Scenario 9), mall info (Scenario 10).
These are untouched by the changes.

**Target:** Maintain 98% (60/61). The 1 existing failure was a network timeout — unrelated.

---

### Step 3 — Skip Complex Suite Unless Regressions Found Above

```
docs/test-queries-complex.md   →   128 queries — run only if Steps 1–2 show regressions
```

Complex scenarios test long-horizon topic lock and sequential awareness — logic untouched by
the 4 changes. The only failure (S9.9.2 — budget group hangout) is unrelated to the changes.

If Step 1 or Step 2 produces unexpected failures, run the complex suite to check for
cross-domain continuity regressions (most likely in S1 Anniversary, S4 Birthday, S5 Multi-Gen).

---

## Known Issues That Will NOT Be Fixed by These Changes

These failures exist in the baseline and are unrelated to the 4 proposed changes:

| ID | Query | Root Cause |
|---|---|---|
| S02.2.3–4 | `anything action` / `any other ones` | Topic-lock not carried through very short refinement phrases |
| S08.8.3 | `anything she would like` | Pronoun-only query misclassified by LLM |
| S08.8.4 | `ok we'll do that one, anything to eat before` | Non-canonical "before" pattern not in `_PLANNING_SIGNALS` |
| S16.16.4 | `my budget is around 300` | Budget declarations always MEDIUM (known trade-off) |
| S9.9.2 | `we have 300 SAR between us — what can we afford?` | LLM classifies as `general` intent with MEDIUM confidence |

Do not count these as regressions in the post-change run.

---

## Pass Rate Targets After Changes

| Suite | Baseline | Target | Notes |
|---|---|---|---|
| Standard | 98% | ≥ 98% | No regressions; updated S01.1.1 expectation |
| Broken / Multi-turn | 93% | ≥ 95% | S02 and S08 should improve |
| Complex | 99% | ≥ 99% | No expected impact |
