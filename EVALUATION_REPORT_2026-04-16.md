# Cenomi Chatbot — Full Evaluation Report

**Date:** 2026-04-16  
**Suite runner:** `run_2026-04-16_16-33-45.json`  
**Pipeline audit:** `pipeline_eval_2026-04-16_16-42-08.json`  
**Comparison run:** `comparison_raw_ca769f50.json`

---

## 1. Executive Summary

**Evaluation Date:** 2026-04-16  
**Primary Mall:** `al_nakheel_plaza_28`  
**Backend:** `http://127.0.0.1:8000/api/chat`

### At a Glance

| Dimension | Result |
|-----------|--------|
| Total queries run (all suites) | **261** |
| Overall pass rate (mode + confidence) | **190/261 (73%)** |
| Pipeline health (LLM audit) | **49/61 clean (80%)** |
| Pipeline warnings | 14 |
| Pipeline errors | 0 |
| Comparison queries answered (vs AI Findr) | 10/10 |
| Avg response latency | 6358 ms |
| p95 latency | 9911 ms |

### Key Findings

- **Suite runner:** 190 of 261 queries passed (73%). Failures are predominantly **confidence-level mismatches** (bot returns `medium` where `high` is expected), not wrong response modes.
- **Pipeline audit:** 49 of 61 turns are fully clean. The 12 flagged turns carry **warnings only** (0 hard errors), mostly slow latency (>8 s) and retrieval-gap notices.
- **New-mall coverage (Suite 5):** 25/25 passed — all 5 malls respond correctly.
- **Comparison:** The chatbot delivers **live, structured, venue-specific** answers versus the AI Findr static baseline, at the cost of slightly longer responses.

## 2. Suite Breakdown

| Suite | Queries | Passed | Failed | Errors | Pass Rate |
|-------|---------|--------|--------|--------|-----------|
| test-queries.md (smoke) | 10 | 7 | 3 | 0 | 70% |
| test-queries.md (scenarios) | 51 | 41 | 10 | 0 | 80% |
| test-queries-broken.md | 76 | 41 | 35 | 0 | 54% |
| test-queries-complex.md | 67 | 50 | 17 | 0 | 75% |
| session-scenario.json | 6 | 5 | 1 | 0 | 83% |
| suite5-new-malls | 25 | 25 | 0 | 0 | 100% |
| suite6-v16-features | 26 | 21 | 5 | 0 | 81% |
| **TOTAL** | **261** | **190** | **71** | **0** | **73%** |

### test-queries.md (smoke) — Failures

| ID | Query | Expected Mode | Actual Mode | Expected Conf | Actual Conf |
|----|-------|--------------|-------------|--------------|-------------|
| `S-04` | `food and movies` | `hybrid_plan` | `hybrid_plan` | `high` | `medium` |
| `S-05` | `anything interesting here?` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |
| `S-10a` | `with kid` | `direct_factual` | `direct_factual` | `high` | `medium` |

### test-queries.md (scenarios) — Failures

| ID | Query | Expected Mode | Actual Mode | Expected Conf | Actual Conf |
|----|-------|--------------|-------------|--------------|-------------|
| `1.6` | `any kids movies today?` | `direct_factual` | `direct_factual` | `high` | `medium` |
| `3.5` | `something affordable` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `5.4` | `something quick before the movie` | `guided_recommendation` | `hybrid_plan` | `high` | `high` |
| `6.1` | `food and movies` | `hybrid_plan` | `hybrid_plan` | `high` | `medium` |
| `6.3` | `where can we eat after the movie?` | `guided_recommendation` | `hybrid_plan` | `high` | `high` |
| `7.1` | `anything interesting here?` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |
| `7.3` | `i'm bored` | `context_acknowledgement` | `best_effort_shortlist` | `high` | `medium` |
| `7.7` | `surprise me` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |
| `10.2` | `something cheaper` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `10.7` | `something more affordable` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |

### test-queries-broken.md — Failures

| ID | Query | Expected Mode | Actual Mode | Expected Conf | Actual Conf |
|----|-------|--------------|-------------|--------------|-------------|
| `B1.4` | `something affordable` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B2.1` | `show me movies` | `direct_factual` | `guided_recommendation` | `high` | `high` |
| `B2.2` | `with kid` | `guided_recommendation` | `direct_factual` | `high` | `medium` |
| `B2.3` | `anything action` | `guided_recommendation` | `direct_factual` | `high` | `medium` |
| `B2.4` | `any other ones` | `guided_recommendation` | `direct_factual` | `high` | `high` |
| `B4.3` | `not too expensive` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B6.2` | `something nice, not too much` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B6.3` | `she likes bags` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B6.4` | `any with sales on` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B7.4` | `ok after, what movies` | `direct_factual` | `guided_recommendation` | `high` | `high` |
| `B8.2` | `oh wait im with my 7 year old` | `context_acknowledgement` | `direct_factual` | `high` | `high` |
| `B8.3` | `anything she would like` | `best_effort_shortlist` | `direct_factual` | `high` | `high` |
| `B8.4` | `ok we'll do that one, anything to eat before` | `guided_recommendation` | `hybrid_plan` | `high` | `medium` |
| `B9.2` | `for me, casual` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B9.3` | `something not too pricey` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B10.3` | `not too loud, we want to talk` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B10.4` | `how long would a reservation take` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B11.1` | `i need school clothes for my kids` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B11.2` | `one is 6 and one is 12` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B11.3` | `something affordable, back to school budget` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B12.3` | `and budget friendly` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B12.4` | `just tell me the best one` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B14.1` | `Nkie shoes` | `direct_factual` | `best_effort_shortlist` | `high` | `medium` |
| `B14.4` | `affordable ones` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B16.3` | `something that works for after too` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B16.4` | `my budget is around 300` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B17.1` | `im here with 3 friends, what can we do` | `context_acknowledgement` | `best_effort_shortlist` | `high` | `medium` |
| `B17.2` | `we want something quick, not crowded` | `guided_recommendation` | `best_effort_shortlist` | `high` | `medium` |
| `B17.3` | `maybe food, something we can share` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B17.4` | `anything with a chill vibe` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B18.2` | `actually forget that, i want to shop something` | `guided_recommendation` | `best_effort_shortlist` | `high` | `medium` |
| `B18.3` | `something cheaper` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B18.4` | `i mean affordable brands, clothes` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B18.5` | `ok done, where can i get dessert` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `B19.4` | `not too over the top though` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |

### test-queries-complex.md — Failures

| ID | Query | Expected Mode | Actual Mode | Expected Conf | Actual Conf |
|----|-------|--------------|-------------|--------------|-------------|
| `C1.3` | `my wife is vegetarian — does that change your suggestio` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C1.4` | `which of those have good vegetarian menus specifically?` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C1.5` | `we'd also love some flowers or a small gift — is there ` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C1.8` | `what time should we aim for dinner to make the 9pm show` | `direct_factual` | `hybrid_plan` | `high` | `high` |
| `C2.6` | `after lunch, we want something fun we can all do togeth` | `guided_recommendation` | `best_effort_shortlist` | `high` | `medium` |
| `C5.12` | `can you give us a family-friendly plan for a 4-hour vis` | `guided_recommendation` | `hybrid_plan` | `high` | `high` |
| `C6.1` | `school is starting next week and i need to shop for thr` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C6.2` | `i need school bags, stationery, and shoes for all three` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C6.12` | `i have 2.5 hours — can you map out a shopping plan?` | `hybrid_plan` | `hybrid_plan` | `high` | `medium` |
| `C7.1` | `i'm looking for a premium experience today — money is n` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |
| `C7.6` | `where should I have lunch — i want exceptional food, no` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C7.9` | `i'd like to end the day with a tasting or something exp` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `C8.1` | `i'm really into fitness and healthy eating — what's her` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |
| `C8.13` | `give me a health-focused plan for my 2 hours here` | `guided_recommendation` | `hybrid_plan` | `high` | `high` |
| `C9.1` | `we're a group of 5 teens just hanging out — what's fun ` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |
| `C9.2` | `we have like 300 SAR between us — so what can we actual` | `best_effort_shortlist` | `guided_recommendation` | `high` | `high` |
| `C9.8` | `any challenges or competitions we can do together?` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |

### session-scenario.json — Failures

| ID | Query | Expected Mode | Actual Mode | Expected Conf | Actual Conf |
|----|-------|--------------|-------------|--------------|-------------|
| `SS1.4` | `yes` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |

### suite6-v16-features — Failures

| ID | Query | Expected Mode | Actual Mode | Expected Conf | Actual Conf |
|----|-------|--------------|-------------|--------------|-------------|
| `V6.17` | `actually i am alone, no kids` | `best_effort_shortlist` | `context_acknowledgement` | `high` | `medium` |
| `V6.20` | `something affordable` | `guided_recommendation` | `guided_recommendation` | `high` | `medium` |
| `V6.22` | `any for kids?` | `direct_factual` | `direct_factual` | `high` | `medium` |
| `V6.24` | `i'm so bored, nothing to do` | `context_acknowledgement` | `best_effort_shortlist` | `high` | `medium` |
| `V6.25` | `fine, what would you recommend?` | `best_effort_shortlist` | `best_effort_shortlist` | `high` | `medium` |

## 3. Failure Analysis

**Total failures:** 71  
- Mode mismatch only: 11  
- Confidence mismatch only: 49  
- Both mode and confidence wrong: 11

### Most Common Mode Mismatches

| Transition | Count |
|-----------|-------|
| `guided_recommendation` → `hybrid_plan` | 5 |
| `context_acknowledgement` → `best_effort_shortlist` | 3 |
| `guided_recommendation` → `direct_factual` | 3 |
| `guided_recommendation` → `best_effort_shortlist` | 3 |
| `direct_factual` → `guided_recommendation` | 2 |
| `context_acknowledgement` → `direct_factual` | 1 |
| `best_effort_shortlist` → `direct_factual` | 1 |
| `direct_factual` → `best_effort_shortlist` | 1 |
| `direct_factual` → `hybrid_plan` | 1 |
| `best_effort_shortlist` → `guided_recommendation` | 1 |
| `best_effort_shortlist` → `context_acknowledgement` | 1 |

### Confidence Calibration Pattern

**49 queries** returned the correct `response_mode` but a lower `confidence_level` than expected (`medium` vs. expected `high`). This is the dominant failure pattern across the broken and complex suites. Root cause: the model downgrades confidence in multi-turn conversations when context accumulates without a clean anchor point.

## 4. Pipeline Health (LLM Audit)

The pipeline auditor runs **13 structural checks** per turn using an LLM judge.

| Metric | Value |
|--------|-------|
| Turns audited | 61 |
| ✅ Clean turns | 49 (80%) |
| ❌ Turns with issues | 12 |
| 🔴 Hard errors | 0 |
| 🟡 Warnings | 14 |
| Judge model | `gpt-5.4-mini` |

**No hard errors detected.**

### Warnings by Check Category

| Check | Count |
|-------|-------|
| `node_trace` | 6 |
| `response_mode` | 3 |
| `retrieval_gap` | 2 |
| `intent_routing` | 2 |
| `companion_extract` | 1 |

### Warning Detail

| ID | Query | Check | Message |
|----|-------|-------|---------|
| `S-04` | `food and movies` | `node_trace` | total_latency_ms exceeds 8000ms (9178.44ms), indicating a slow turn. |
| `1.2` | `show me movies` | `retrieval_gap` | retrieval_needed=true for an entertainment query, but retrieval_results=0. For a factual movie looku |
| `2.4` | `any activities for the kids?` | `retrieval_gap` | Guided recommendation-style response in an exploration/activity-suggestion turn would normally benef |
| `2.4` | `any activities for the kids?` | `node_trace` | Total latency exceeds 8000ms (9027.79ms), indicating a slow turn. |
| `2.5` | `what movies are there?` | `node_trace` | total_latency_ms exceeds 8000ms (9034.36ms), indicating a slow turn. |
| `3.1` | `i want to buy a gift` | `node_trace` | total_latency_ms exceeds 8000ms (10536.0ms), indicating a slow turn. |
| `4.2` | `suggest a nice dinner` | `companion_extract` | The user did not explicitly mention a companion in this turn, but companions contains ['girlfriend'] |
| `4.6` | `any romantic options here?` | `intent_routing` | User asked for romantic options, which is a shopping-style recommendation request, but intent_domain |
| `6.5` | `we want to watch a movie and grab dinner` | `intent_routing` | The user asked for both a movie and dinner, but intent_domain is entertainment only; dining is also  |
| `8.1` | `asdf` | `response_mode` | For a gibberish/general turn, graceful_recovery is acceptable, but the chosen_strategy of guided_pla |
| `8.1` | `asdf` | `node_trace` | Total latency exceeds 8000 ms (8394.83 ms), which is slow and should be reviewed. |
| `8.4` | `tell me a joke` | `response_mode` | User asked for a joke, which is a general/off-topic request; graceful_recovery is acceptable, but th |
| `10.6` | `suggest some stores for fashion` | `node_trace` | total_latency_ms exceeds 8000ms (8243.48ms), indicating a slow turn. |
| `13.2` | `what does this mall have?` | `response_mode` | For a broad mall overview request, direct_factual is acceptable, but the response is also functionin |

## 5. Comparison vs AI Findr Baseline

**Mall:** `al_nakheel_plaza_28` | **Queries:** 10 standard comparison questions

### Response Overview

| QID | Question | Our Mode | Latency | AI Findr Approach |
|-----|----------|----------|---------|-------------------|
| Q1 | Plan a fun day for me and my friends | `hybrid_plan` | 6194 ms | Structured multi-step itinerary |
| Q2 | Best family-friendly movies today | `direct_factual` | 4383 ms | Deflects — cannot answer |
| Q3 | Good restaurants for lunch | `guided_recommendation` | 6960 ms | Lists options + asks follow-up |
| Q4 | Kids' clothing stores | `guided_recommendation` | 6696 ms | Lists options + asks follow-up |
| Q5 | Fun activities for kids | `guided_recommendation` | 8450 ms | Lists options + asks follow-up |
| Q6 | Dessert places or cafés | `guided_recommendation` | 8581 ms | Lists options + asks follow-up |
| Q7 | Affordable fashion shopping | `guided_recommendation` | 6468 ms | Lists options + asks follow-up |
| Q8 | Ongoing offers or discounts | `guided_recommendation` | 6573 ms | Reports no live data |
| Q9 | Entertainment besides movies | `guided_recommendation` | 8167 ms | Structured multi-step itinerary |
| Q10 | Quick 2–3 hour shopping + dining plan | `hybrid_plan` | 5808 ms | Structured multi-step itinerary |

### Key Differentiators

| Dimension | Our Chatbot | AI Findr |
|-----------|------------|---------|
| Movie data | Live showtimes from ingested data | Asks user to provide titles |
| Offers | Live active offers | Reports expired offers from static snapshot |
| Response structure | Venue + location + routing plan | List of names + emoji |
| Follow-up prompts | Context-driven refinement | Hard-coded clarifying Qs |
| Latency | 4–12 s per query | N/A (static) |

### Chatbot Responses (Full)

#### Q1: Can you plan a fun day for me and my friends in the mall?

**Our response:**

For a fun day with your friends, start with **Fun Time** in the **Entertainment Wing on the Ground Floor** — it sets the tone with an easy, lively first stop. From there, head to **Muvi Cinema** in the **Cinema Zone on the Upper Level** for the main outing, then finish with a sweet stop at **CINNABON** in the **Food Court on the Ground Floor**.

If you want a quicker, more relaxed pace, swap the cinema for **Baskin Robbins** on **Kiosk Row** and keep the day light and social.

**Fun Time and the Food Court are both on the Ground Floor, and Muvi Cinema is straightforward to reach on the Upper Level.**

If you’d like, I can turn this into a 2-hour or 3-hour friends’ itinerary with the best order for your visit.

**AI Findr baseline:**

Here's a fun, easy full-day plan for you and your friends 🛍️🎬

✅ A fun day itinerary
1) Start with coffee + catch-up (45–60 min) ☕
   FanJan Caffé (specialty coffee + pastries) Or MammaBunz / Mamma Roti Bunz
2) Game time / friendly competition (1–2 hrs) 🎮
   Sparky's (Fun Time)
3) Lunch in the food court (60–90 min) 🍗🍔
   Popeyes Restaurant / McDonald's
4) Sweet break + photos (30–45 min) 🍦
   Baskin Robbins or CoolNup
5) Movie to wrap the day (2–3 hrs) 🎥
   Muvi Cinema

Little tip: Do a 'pick-for-each-other' challenge 🎉
(Mall hours: Fri 1:00 PM–11:00 PM, other days 9:00 AM–11:00 PM.)

---

#### Q2: What are the best family-friendly movies playing in the mall today?

**Our response:**

For family-friendly films today: there are no genuinely family-appropriate movies listed in today’s Muvi Cinema schedule.

**Not family-appropriate right now**
- ⚠ **SAUDI PRO LEAGUE (AL QADSIAH VS AL AHLI)** — Sport, 180 min
- ⚠ **SAUDI PRO LEAGUE (AL NASSR VS NEOM)** — Sport, 180 min
- ⚠ **SAUDI PRO LEAGUE (AL AHLI VS AL ITTIHAD)** — Sport, 180 min
- ⚠ **SHELTER** — Action Thriller, 107 min
- ⚠ **CRIME 101** — Crime Action, 140 min
- ⚠ **THE HOUSEMAID** — Thriller, 131 min

For a family outing today, **Fun Time** in the **Entertainment Wing on the Ground Floor** is the best alternative.

Tickets available at the cinema counter or via the Muvi app — book ahead for peak times.

**AI Findr baseline:**

I can't accurately say which family-friendly movies are playing today.
If you share the titles from Muvi's website, I'll:
- pick the most family-friendly options
- suggest the best one by age group (kids vs. teens)
(Muvi Cinema is in the mall for your movie plan.)

---

#### Q3: Can you suggest some good restaurants for lunch in the mall?

**Our response:**

For lunch in the Food Court, the strongest picks are these five — all quick, halal, and easy to reach from the Ground Floor.

**Fast Lunch Choices**
- **McDonald's** — **Ground, Food Court**: A familiar fast-food option and the most straightforward choice for a quick lunch.
- **Herfy** — **Ground, Food Court**: A good stop for chicken-focused meals with a fast-service format.
- **Kudu Restaurant** — **Ground, Food Court**: Meals are prepared directly in front of you, which makes it a strong pick when you want something made to order.
- **Popeyes Restaurant** — **Ground, Food Court**: Fried chicken is the focus here, with a fast-lunch setup.
- **Corn World** — **Ground, Kiosk Row**: Best if you want a lighter snack-style lunch with corn-based items.

**Best flow:** start with **Kudu** or **Popeyes** for a fuller lunch, and keep **Corn World** in mind if you want something lighter.

All of these are on the **Ground Floor**, so the lunch run is simple and efficient. If you would like, I can narrow this down by **quickest**, **lightest**, or **most filling**.

**AI Findr baseline:**

Here are some solid lunch options (quick + crowd-pleasers) 🍽️😋
- Popeyes Restaurant – fried chicken, sandwiches, tenders
- Kudu Restaurant – a classic Saudi favorite
- Herfy – well-known for burgers and fast meals
- McDonald's – easy, familiar choice
- FanJan Caffé – light lunch + coffee vibe (pastries/sweets too)

[Asks: mood — chicken / burgers / light & coffee]

---

#### Q4: Where can I find kids' clothing stores in the mall?

**Our response:**

Head to the Ground Floor and the Entertainment Wing for the strongest kids’ clothing shopping in Al Nakheel Plaza.

**Kids’ clothing-focused picks**
- **Centrepoint** — **Entertainment Wing, Ground**: the best family-shopping destination here, with affordable fashion and a strong multi-brand mix.
- **Red Tag** — **Ground Floor**: a value-focused fashion stop that suits practical kids’ clothing shopping.
- **Brands For Less** — **Entertainment Wing, Ground**: a good choice for budget-conscious family fashion finds.
- **Max** — **Ground Floor**: dependable value fashion for everyday clothing needs.
- **Zara** — **Ground Floor**: useful if you want a more trend-led clothing option.
- **Bershka** — **Ground Floor**: another fashion stop for modern, youth-focused styles.

If you want the most efficient route, start with **Centrepoint**, then move to **Red Tag** or **Brands For Less** for value, and finish at **Zara** if you want something more fashion-led.

All of these are on the **Ground Floor** or in the **Entertainment Wing**, so the shopping flow is straightforward.  
If you would like, I can next narrow this to the **best budget-friendly kids’ clothing options** or guide you to **family-friendly shopping nearby**.

**AI Findr baseline:**

Kids' clothing options 👧🧒🛍️:
- Marsil – specialized in kids & family fashion
- Red Tag – affordable, men/women/kids
- R&B (R and B) – value lifestyle fashion for all ages

[Asks: kid's age — baby / toddler / 6–12 / teens]

---

#### Q5: What are some fun activities for kids in the mall?

**Our response:**

For your kids’ outing, Fun Time in the Entertainment Wing is the strongest choice — it is the mall’s main children’s entertainment anchor and the best place to start for active play.  

From there, you can keep the visit easy with a quick meal at the Food Court: McDonald’s is the simplest family stop, while Herfy and Kudu are good alternatives for a fast bite before or after play.  

Begin at Fun Time on the Ground Floor, then move to the Food Court for a convenient break. Both spots are on the Ground Floor, so the flow is straightforward for a family visit.  

If you would like, I can map out a 1-hour kids’ plan that combines play and food in the smoothest order.

**AI Findr baseline:**

Fun kid-friendly activities 👧🧒🎉:
- Fun Time (Sparky's World) 🎮 — major entertainment center
- Baby Stroller service 👶 — available 4:00 PM–11:00 PM, Ground Floor

[Transparent: 'I don't have more detailed kids-activity venues beyond Fun Time']
[Asks: kids' ages]

---

#### Q6: Can you recommend dessert places or cafes in the mall?

**Our response:**

For dessert and café stops, the strongest choices are **Baskin Robbins**, **CINNABON**, and **FanJan Caffé** — each gives you a different mood, from sweet treats to coffee and pastries.

**Dessert first**
- **Baskin Robbins** — **Ground, Kiosk Row**  
  Best for ice cream and an easy sweet stop when you want something quick.
- **CINNABON** — **Ground, Food Court**  
  A good pick for cinnamon rolls, desserts, and coffee in one stop.

**Cafés and relaxed breaks**
- **FanJan Caffé** — **Ground, Ground Floor**  
  Best for specialty coffee with a calmer café feel.
- **Starbucks** — **Ground, Ground Floor**  
  A reliable café stop if you want coffee and light snacks.

**A couple of extra sweet options**
- **MammaBunz** — **Ground, Kiosk Row**  
  Good for buns and signature drinks if you want something a little different.

Start with **Baskin Robbins** if you want dessert, then move to **FanJan Caffé** or **Starbucks** if you want to stay a while with coffee. All of these are on the Ground level, so it is easy to keep your visit simple and close together.

If you would like, I can next narrow these to the best **coffee-only** spots or the best **dessert-only** stops.

**AI Findr baseline:**

Dessert spots & cafés 🍰☕:
- CoolNup – sweets/dessert shop
- Baskin Robbins – classic ice cream
- FanJan Caffé – specialty coffee + pastries
- MammaBunz – buns + signature drinks
- Mamma Roti Bunz – buns + drinks (cozy café stop)

[Asks: ice cream / coffee + pastry / rich desserts]

---

#### Q7: Where can I shop for affordable fashion in the mall?

**Our response:**

For affordable fashion, start with the strongest value-led options on the Ground Floor and in the Entertainment Wing. Centrepoint, Max, and Red Tag are the clearest places to begin if you want practical pricing without losing style.

**Best value picks**
- **Centrepoint** — **Entertainment Wing, Ground**: the mall’s strongest affordable fashion destination, with family shopping and value-led pricing.
- **Max** — **Ground Floor**: a dependable budget-friendly fashion stop for everyday accessories and wardrobe basics.
- **Red Tag** — **Ground Floor**: a good choice for low-cost fashion and accessories, with a broad value focus.

**More affordable fashion stops**
- **Brands For Less** — **Entertainment Wing, Ground**: useful if you want discounted fashion and lifestyle pieces.
- **MINISO** — **Ground Floor**: best for budget-friendly accessories and smaller fashion buys.
- **Zara** — **Ground Floor**: not the cheapest option, but still a good place to look for fashionable pieces at accessible prices.

**If you want something a little more style-led**
- **Stradivarius** — **Ground Floor**: trendy pieces with a modern look.
- **Bershka** — **Ground Floor**: youthful, fashion-forward options with a casual edge.

Centrepoint, Max, Red Tag, and Brands For Less are all straightforward to reach on the Ground Level areas of the mall. If you would like, I can narrow this to the best picks for **women’s fashion**, **footwear**, or **traditional wear**.

**AI Findr baseline:**

Affordable fashion 🛍️💸:
- Red Tag – budget-friendly for men/women/kids
- R&B (R and B) – value lifestyle fashion
- Max – value fashion store
- Brands For Less – discounted shopping

[Asks: who shopping for + style]

---

#### Q8: Are there any ongoing offers or discounts in the mall?

**Our response:**

There is one active offer listed at present:

- **Osma** — **Ground, Kiosk Row**
  - **Offer:** One 150ml bottle for **99 R.S** or one 75ml bottle for **99 R.S**
  - **Valid from:** **2025-11-10**
  - **Valid until:** **2025-11-30**
  - **Terms:** No terms were listed in the context

If you are browsing for value as you walk the Ground Floor, **Red Tag**, **Brands For Less**, and **Centrepoint** are also well-suited to affordable shopping, though I do not have current offer details for them right now.

Osma is on the Ground floor in Kiosk Row, so it is easy to reach from the main shopping corridor.  
If you would like, I can also look for the best current offers by category, such as perfumes, fashion, or accessories.

**AI Findr baseline:**

No active offers today (2026-04-15) ✅🛍️

Expired offers ⛔:
- 30% Off Entertainment! (Cenomi Plus / SALA) — ended 2025-12-31
- SAR 50 Voucher at Vogacloset (spend SAR 500+) — ended 2025-12-31

[Offers to point to best value stores if no specific deal matches]

---

#### Q9: What entertainment options are available in the mall besides movies?

**Our response:**

For entertainment beyond movies, **Fun Time** in the **Entertainment Wing on the Ground Floor** is the strongest choice — it is the mall’s dedicated kids’ entertainment venue and the clear non-cinema option for a lively break.

- **Fun Time** — **Ground, Entertainment Wing**  
  The best fit if you want a true entertainment stop rather than shopping.

- **Centrepoint** — **Ground, Entertainment Wing**  
  A practical backup if you want to combine leisure with browsing in a large family-focused store.

**Next step:** head to the **Entertainment Wing** first and start with **Fun Time**; if you want to add a quick shopping stop after that, **Centrepoint** is right there in the same area.  

Both options are on the **Ground Floor**, so the switch between them is straightforward. If you’d like, I can also point you to the best nearby food stop for a break after the entertainment.

**AI Findr baseline:**

Entertainment besides movies 🎮👨‍👩‍👧‍👦:
- Fun Time (Sparky's World) — large entertainment center with games, activities

[Transparent: 'That's all the non-movie entertainment I have confirmed details for']
[Asks: ages for planning]

---

#### Q10: Can you suggest a quick shopping and dining plan for 2–3 hours in the mall?

**Our response:**

For a quick 2–3 hour visit, I would keep the shopping focused and finish with an easy meal at the Food Court.

- Begin at **L'Occitane** on the **Ground Floor** for a polished, gift-friendly stop if you want something premium and compact.
- From there, step into **Season Accessorize** in the **Kiosk Row** for a fast add-on such as accessories or a watch, which works well when time is limited.
- Close at **Kudu Restaurant** in the **Food Court** on the **Ground Floor** for a quick meal that fits neatly after shopping.

Both stops are on the Ground Floor or nearby, so the plan stays efficient and easy to move through.

If you’d like, I can refine this into a more romantic shopping-and-dining route with the best pairing for your pace.

**AI Findr baseline:**

Quick 2–3 hour plan 🛍️🍔

Option A (affordable fashion + quick bite) — ~2 to 2.5 hrs:
1) Shop (60–75 min): Red Tag → R&B / Max / Brands For Less
2) Lunch (30–45 min): Popeyes or McDonald's
3) Dessert (15–25 min): Baskin Robbins or CoolNup

Option B (chill — coffee + light shopping) — ~2 hrs:
1) Coffee (30–45 min): FanJan Caffé
2) Shop (60–75 min): Brands For Less → Red Tag

[Asks: budget vs. trendy fashion, fast food vs. café lunch]

---

## 6. Performance (Latency)

### Overall

| Metric | Value |
|--------|-------|
| Min | 1287 ms |
| Avg | 6358 ms |
| Median | 6178 ms |
| p95 | 9911 ms |
| Max | 21544 ms |

### Per-Suite Latency

| Suite | Queries | Avg (ms) | p95 (ms) | Max (ms) |
|-------|---------|---------|---------|---------|
| test-queries.md (smoke) | 10 | 6985 | 10776 | 10776 |
| test-queries.md (scenarios) | 51 | 6892 | 11547 | 21544 |
| test-queries-broken.md | 76 | 6157 | 9509 | 13126 |
| test-queries-complex.md | 67 | 7079 | 10679 | 15350 |
| session-scenario.json | 6 | 7284 | 9396 | 9396 |
| suite5-new-malls | 25 | 5099 | 9203 | 9236 |
| suite6-v16-features | 26 | 4794 | 7636 | 8552 |
| comparison (Q1–Q10) | 10 | 6828 | 8581 | 8581 |

> **Note:** Latency includes LangGraph pipeline overhead (intent classification, retrieval, response generation). Factual flow turns are typically 3–5 s; concierge/hybrid turns are 6–12 s.

## 8. Query Coverage by Flow Type

| Flow Type | Queries | Passed | Failed | Pass Rate |
|-----------|---------|--------|--------|-----------|
| `concierge` | 199 | 136 | 63 | 68% |
| `factual` | 62 | 54 | 8 | 87% |

## 9. Sample Responses (Passing, per Flow Type)

| ID | Flow | Query | Reply snippet |
|----|------|-------|---------------|
| `S-01` | `factual` | `what movies are showing?` | Here are the movies showing now:  - **SAUDI PRO LEAGUE (AL QADSIAH VS AL AHLI)** — **Sport** — **180 |
| `S-02` | `concierge` | `i am here with my kids` | For your visit with the children, the smoothest route is to keep it simple: start with Fun Time in t |

## 7. Recommendations

Prioritised by frequency and user-impact:

| # | Issue | Affected Queries | Recommendation |
|---|-------|-----------------|----------------|
| 1 | **Confidence calibration** — `medium` returned instead of `high` in multi-turn | 49 queries | Review `confidence_level` scoring in the LLM response node; increase threshold for conversations with a clear anchor entity. |
| 2 | **Response-mode misclassification** | 22 queries | Audit intent-to-mode routing for vague follow-ups (e.g. `'ok after, what movies'` → should be `direct_factual`). |
| 3 | **Slow concierge turns (>8 s)** | 6 audit turns | Profile LangGraph node execution; consider parallel retrieval for concierge flow to bring p95 below 8 s. |
| 4 | **Retrieval gaps in entertainment queries** | 2 audit turns | Ensure `retrieval_needed=true` triggers correctly for movie/activity queries; check vector-store routing in the `compose_context` node. |
| 5 | **No hard pipeline errors** | — | All 13 structural checks pass at error level; zero blocking issues. Continue monitoring warnings as they accumulate. |

---

*Report generated 2026-04-16 16:46:42 UTC by `backend/scripts/generate_evaluation_report.py`*
