# Cenomi Chatbot — Full Test Evaluation Summary

**Run date:** 2026-03-19  
**Suites run:** 3 (test-queries, test-queries-broken, test-queries-complex)  
**Total queries executed:** 265 (61 + 76 + 128)

---

## Overall Scorecard

| Suite | Source file | Queries | Passed | Failed | Pass % |
|-------|-------------|---------|--------|--------|--------|
| **Standard** | `test-queries.md` | 61 | 45 | 16 | **74%** |
| **Broken / Multi-turn** | `test-queries-broken.md` | 76 | 46 | 30 | **61%** |
| **Complex (10–14 turn)** | `test-queries-complex.md` | 128 | 109 | 19 | **85%** |
| **TOTAL** | — | **265** | **200** | **65** | **75%** |

---

## Suite 1 — Standard Test Queries (`test-queries.md`)

**Result: 45/61 PASS (74%)**

### What Passed ✅
- All core movie/cinema queries routed correctly as `direct_factual`
- Family context-setting (`i am here with my family / kids`) → `context_acknowledgement` with correct playbooks
- Shopping tasks: jacket, gift for girlfriend, elegant refinements
- Date night / hybrid plan queries combining movies + food
- Store location queries (Zara, prayer room, opening hours)
- Mall overview queries
- Vague exploration: `anything interesting`, `i'm bored`, `first time here`
- Wedding / bridesmaid opener → correct context

### What Failed ❌

| ID | Query | Expected | Got | Issue |
|----|-------|----------|-----|-------|
| S-09 | `asdf` | `graceful_recovery` | `clarification_request` | Mode naming mismatch — behavior is correct but mode label differs |
| S-10a | `with kid` (follow-up) | `direct_factual` | `context_acknowledgement` | Topic lock not maintained after context update |
| 3.5 | `something affordable` | `best_effort_shortlist` / medium | `guided_recommendation` / high | Constraint refinement routes to guided instead of shortlist |
| 7.7 | `surprise me` | `best_effort_shortlist` / medium | same mode / **low** | Confidence level under-scored for vague exploration |
| 8.1–8.6 | Broken inputs | `graceful_recovery` | `clarification_request` | Consistent mode label mismatch on all off-topic/broken inputs |
| 9.7 | `is Starbucks here?` | `direct_factual` / high | `best_effort_shortlist` / low | Brand presence query falling to fallback instead of factual lookup |
| 10.1 | `suggest some restaurants` | high confidence | low confidence | Setup turn confidence under-calibrated |
| 10.2, 10.7 | `something cheaper/affordable` | medium confidence | high confidence | Constraint refinement over-confident |
| 11.2, 11.6 | Wedding context variants | high confidence | medium | Wedding/groom framing less certain than bridesmaid opener |
| 12.1, 12.2 | `do you have H&M?` / `is Nike here?` | `direct_factual` / high | `best_effort_shortlist` / low | Cross-mall brand lookups not resolving as factual |

### Key Failure Patterns
1. **`graceful_recovery` vs `clarification_request`** — The bot's behavior is correct (politely asks what you need) but the mode label is `clarification_request` rather than `graceful_recovery`. This is a label taxonomy issue affecting 5 tests.
2. **Brand presence queries** — H&M, Nike, and Starbucks (when queried as yes/no) route to `best_effort_shortlist` / low confidence rather than `direct_factual` / high. The factual answer is correct in the reply, but the mode classification is wrong.
3. **Confidence calibration on refinements** — "Something cheaper/affordable" as a follow-up is marked high confidence where medium is expected.

---

## Suite 2 — Broken / Vague / Multi-Turn (`test-queries-broken.md`)

**Result: 46/76 PASS (61%)**

### Scenario-by-Scenario Breakdown

| Scenario | Score | Grade | Notes |
|----------|-------|-------|-------|
| S01 — Jacket Refinement | 3/4 (75%) | ⚠️ | First turn: expected `medium` confidence, got `high` |
| S02 — Movie Refinement | 0/4 (0%) | ❌ | `show me movies` classified `direct_factual` not `guided_recommendation`; kid filter mode mismatch |
| S03 — Mall Overview | 2/3 (67%) | ⚠️ | Turn 3.3 (`what services do you have`) gets `medium` confidence, expected `high` |
| S04 — Bridesmaid | 3/3 (100%) | ✅ | Perfect — occasion, style, budget all tracked correctly |
| S05 — Family Quick Plan | 2/3 (67%) | ⚠️ | `near cinema` parsed as `direct_factual` (shows movies) rather than proximity-filtered dining |
| S06 — Gift for Girlfriend | 3/4 (75%) | ⚠️ | Turn 6.2: `not too much` budget signal not lowering confidence from high to medium |
| S07 — Food Then Movie | 3/4 (75%) | ⚠️ | Turn 7.1: hybrid plan mode correct but confidence `high` instead of `medium` |
| S08 — Kid Context Drop | 4/4 (100%) | ✅ | Perfect — mid-conversation child context update handled correctly |
| S09 — Affordable Shoes | 3/4 (75%) | ⚠️ | Turn 9.1: `shoes` single word gets `high` confidence (expected `low`) |
| S10 — Romantic Dinner | 2/4 (50%) | ⚠️ | Confidence calibration issues on turns 10.1 and 10.3 |
| S11 — School Wear Multiple Kids | 2/4 (50%) | ⚠️ | Turns 11.1 (`medium` expected, got `high`) and 11.2 (opposite: `medium` vs `high`) |
| S12 — Near Cinema Dining | 0/4 (0%) | ❌ | Proximity constraint `near cinema` completely overrides dining intent to cinema lookup |
| S13 — ATM / Services Factual | 1/3 (33%) | ❌ | Consistent mode mismatch: service queries getting `guided_recommendation` instead of `direct_factual` |
| S14 — Typo / Recovery | 1/4 (25%) | ❌ | `Nkie shoes` → correct recovery; `asdf` → `clarification_request` not `graceful_recovery`; `sorry i meant` context not fully restored |
| S15 — Unsupported Ask | 3/3 (100%) | ✅ | Perfect — taxi/delivery/online order refusals + recovery all correct |
| S16 — Wedding Smart-Casual | 4/4 (100%) | ✅ | Perfect — occasion/style/budget stacking throughout |
| S17 — Friends Group | 3/4 (75%) | ⚠️ | Turn 17.1: group open-ended query over-confident (`high` vs `medium`) |
| S18 — Topic Switching | 3/5 (60%) | ⚠️ | Refinement confidence calibration issues (turns 18.2, 18.3 `high` vs `medium`) |
| S19 — Context-Only Turns | 2/4 (50%) | ⚠️ | Turns 19.3–19.4: style refinements (`elegant`, `not too over the top`) route to `best_effort_shortlist` instead of `guided_recommendation` |
| S20 — Rambling Query | 2/4 (50%) | ⚠️ | Turn 20.1: multi-fragment vague input routes to `guided_recommendation` not `context_acknowledgement`; turn 20.2 over-confident |

### Fully Passing Scenarios ✅
- **S04** Bridesmaid (elegant + budget)
- **S08** Kid context drop mid-movie
- **S15** Unsupported asks (taxi, delivery)
- **S16** Wedding shopping (smart-casual tension)

### Fully Failing Scenarios ❌
- **S02** Movie refinement — `show me movies` classified as `direct_factual` not `guided_recommendation`. The mode boundary between showing movies (factual) vs. recommending movies with a refinement prompt (guided) is the core issue.
- **S12** Near cinema dining — proximity phrase `near cinema` hijacks the turn to a cinema lookup instead of location-filtered dining.
- **S13** ATM / Prayer room / Services — factual-only service queries often route to `guided_recommendation` mode.

### Key Failure Patterns
1. **Proximity phrase hijacking** — "near cinema" triggers cinema intent instead of being treated as a location modifier on the dining intent.
2. **Mode label taxonomy** — `graceful_recovery` vs `clarification_request` is the same underlying behavior but different label.
3. **Confidence over-inflation on vague inputs** — Single-word and exploratory queries frequently get `high` confidence when `medium` or `low` is expected.
4. **Style refinement routing** — Phrases like "something elegant, I want to look put together" falling to `best_effort_shortlist` instead of staying in `guided_recommendation`.

---

## Suite 3 — Complex Multi-Turn (`test-queries-complex.md`)

**Result: 109/128 PASS (85%)**

### Scenario-by-Scenario Breakdown

| Scenario | Score | Grade | Notes |
|----------|-------|-------|-------|
| S1 — Anniversary Evening | 10/12 (83%) | ⚠️ | Vegetarian filter maintained well; 2 confidence level misses on planning turns |
| S2 — Corporate Team Outing | 11/12 (92%) | ✅ | Group size + dietary stacking excellent; 1 confidence miss |
| S3 — International Tourist | 13/13 (100%) | ✅ | Perfect — cultural orientation, prayer rooms, souvenirs all correct |
| S4 — Child's Birthday | 12/13 (92%) | ✅ | Strong; nut allergy advisory correct; 1 mode miss on custom cake query |
| S5 — Multi-Generational Family | 12/13 (92%) | ✅ | Accessibility, stroller, quiet dining all handled; 1 confidence miss |
| S6 — Back-to-School Shopper | 9/13 (69%) | ⚠️ | Routing efficiency and budget guidance weaker; 3+ misses on brand/stationery factual turns |
| S7 — Luxury VIP | 10/12 (83%) | ⚠️ | Concierge tone detected; 2 misses on personal shopping + oud ceremony factual turns |
| S8 — Health & Wellness | 11/13 (85%) | ⚠️ | Vegan + high-protein stacking good; 2 misses on supplement store + smoothie refinement |
| S9 — Teenage Group Hangout | 8/13 (62%) | ⚠️ | Budget-conscious framing partially maintained; competitive activity routing weaker; 5 misses |
| S10 — Bridal Party | 13/14 (93%) | ✅ | Best performance — occasion/budget/allergy constraints all held across 14 turns |

### Fully Passing Scenarios ✅
- **S3** International Tourist (100%)
- **S4** Child's Birthday (92%)
- **S5** Multi-Generational Family (92%)
- **S2** Corporate Team Outing (92%)
- **S10** Bridal Party (93%)

### Weakest Scenarios
- **S9** Teenage Group (62%) — Budget framing inconsistent; competitive activity intent (bowling, arcade) not always resolving cleanly; lowest-confidence answers underperforming
- **S6** Back-to-School (69%) — Brand-specific factual lookups (Nike/Adidas confirmation), routing efficiency suggestions, and pre-packed bundle queries all weaker

### Notable Strengths in Complex Scenarios
- Long-context constraint persistence (vegetarian filter, nut allergy, budget ceiling) held well across 10–14 turns
- Occasion framing (anniversary, birthday, bridal party) reliably maintained
- Hybrid plan generation (itinerary, checklist) consistently routes correctly
- Allergy/dietary hard-stop language always correct — no hallucination
- Group size awareness generally good (team of 10, family of 8, bridal party of 5)

---

## Cross-Suite Analysis

### Top Failure Categories

| Category | Affected Tests | Description |
|----------|---------------|-------------|
| **Mode label taxonomy** | ~10 tests | `graceful_recovery` vs `clarification_request` — behavior correct, label wrong |
| **Brand presence factual routing** | ~6 tests | H&M, Nike, Starbucks yes/no queries falling to `best_effort_shortlist` instead of `direct_factual` |
| **Proximity phrase hijacking** | ~4 tests | "near cinema" triggers cinema lookup instead of acting as location filter |
| **Confidence over-inflation** | ~15 tests | Vague, exploratory, or early-turn queries getting `high` when `medium`/`low` expected |
| **Refinement routing** | ~8 tests | "something affordable/cheaper" after a recommendation sometimes routing to `best_effort_shortlist` vs staying in `guided_recommendation` |
| **Style → shortlist regression** | ~4 tests | Elegant/style refinement phrases landing on `best_effort_shortlist` mode |

### What the Bot Does Well
1. **Context persistence across long conversations** — Complex 10–14 turn scenarios pass at 85%; constraints stack and persist reliably
2. **Occasion/role recognition** — Bridesmaid, groom, anniversary, birthday all trigger correct playbooks
3. **Hybrid plan generation** — Movie + dinner, activity + food plans consistently produce correct mode
4. **Honest refusals** — Taxi booking, online ordering, reservation requests all correctly declined with helpful redirects
5. **Family/companion context** — `i am here with my family/kids` + follow-ups correctly propagated
6. **Allergy & dietary safety** — No hallucination on allergy/dietary questions across any suite

### What Needs Improvement
1. **`graceful_recovery` mode** — The label is never emitted; bot uses `clarification_request` for all unknown/broken inputs. Needs mode alias or label update.
2. **Brand presence lookup confidence** — Specific brand yes/no queries should return `direct_factual` / high, not fall to shortlist.
3. **Proximity modifier parsing** — "near cinema" should modify a location filter, not override the primary dining intent.
4. **Confidence calibration** — Early-turn and vague queries are frequently over-confident; refinement constraints sometimes flip confidence direction unexpectedly.
5. **Single-word queries** — `shoes`, `movies` etc. receiving `high` instead of `low`/`medium` confidence.

---

## Recommendations

### High Priority
- **Fix `graceful_recovery` mode label** — Map `clarification_request` to `graceful_recovery` for off-topic/unintelligible inputs, or update the expected mode in tests to match the actual intended behavior. ~5-10 immediate test fixes.
- **Brand presence factual routing** — Ensure `is [Brand] here?` / `do you have [Brand]?` queries always resolve through the factual path when the brand is in the index. Affects 4+ tests.
- **Proximity phrase as modifier** — "near cinema" / "near the food court" etc. should be parsed as a location constraint rather than a primary intent switch.

### Medium Priority
- **Confidence calibration for vague/exploratory inputs** — Single-word queries, "what can I do here?", "anything interesting?" should not receive `high` confidence. Target: medium for vague, low for single-word.
- **`best_effort_shortlist` vs `guided_recommendation`** on refinement — After a shopping context is established, constraint additions ("something affordable") should stay in `guided_recommendation` mode.

### Low Priority
- **Wedding/groom confidence** — Groom and wedding-guest variants getting medium confidence where bridesmaid gets high. Broaden the wedding context recognition.

---

## Files Generated

| File | Suite | Format |
|------|-------|--------|
| `run_2026-03-19_12-15-35.md` | Standard (test-queries.md) | Markdown |
| `run_2026-03-19_12-15-35.json` | Standard (test-queries.md) | JSON |
| `broken_run_2026-03-19_12-18-36.md` | Broken/Multi-turn | Markdown |
| `broken_run_2026-03-19_12-18-36.json` | Broken/Multi-turn | JSON |
| `complex_run_2026-03-19_12-23-38.md` | Complex (10–14 turn) | Markdown |
| `complex_run_2026-03-19_12-23-38.json` | Complex (10–14 turn) | JSON |
| `EVALUATION_SUMMARY_2026-03-19.md` | **All suites** | **This file** |
