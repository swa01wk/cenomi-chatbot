# Chatbot Comparison Report: AI Findr vs. Our New Implementation
**Mall:** Al Nakheel Plaza (ID: `al_nakheel_plaza_28`)  
**Date:** 2026-04-16  
**Test scope:** 10 representative user queries covering planning, movies, dining, shopping, kids, and offers  

---

## Executive Summary

| Dimension | AI Findr | New Implementation |
|---|---|---|
| **Response Style** | Conversational, emoji-rich, warm | Structured, markdown-formatted, professional |
| **Coverage (avg. venues listed)** | 4–5 per query | 3–6 per query |
| **Floor/Zone Accuracy** | Partial | High (explicit floor + zone labels) |
| **Context Contamination** | None observed | 1 case (Q10 assumed "couple" context) |
| **Live Data (Movies)** | Defers to Muvi website | Retrieves live schedule (no family-filter) |
| **Offers/Discounts** | Shows expired offers transparently | Returns "no active offers" — accurate but brief |
| **Follow-up Prompts** | Consistent (asks for age/mood/vibe) | Intermittent |
| **Overall Tone** | Friendly concierge assistant | Efficient mall guide |

---

## Query-by-Query Comparison

---

### Q1 — Can you plan a fun day for me and my friends in the mall?

#### AI Findr
```
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

Little tip: Do a "pick-for-each-other" challenge 🎉
(Mall hours: Fri 1:00 PM–11:00 PM, other days 9:00 AM–11:00 PM.)
```

#### New Implementation
```
For a fun day with your friends, start with Fun Time in the Entertainment Wing on the Ground Floor.

A simple plan to follow:
- Begin at Fun Time for the main entertainment stop
- From there, head to Muvi Cinema (Cinema Zone, Upper Level) for a film
- Close at the Food Court with McDonald's or CINNABON

Secondary swap:
- Baskin Robbins in Kiosk Row for a lighter finish
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Itinerary structure | Full 5-step plan with time estimates | 3-step outline | AI Findr |
| Venue variety | 6 venues across coffee, games, food, dessert, cinema | 4 venues | AI Findr |
| Creative engagement | "Pick-for-each-other" challenge tip | None | AI Findr |
| Floor/location info | Absent | Partial (floor + zone) | New Impl |
| Mall hours included | Yes | No | AI Findr |
| Personalization prompt | Yes (asks vibe + arrival time) | Yes (offers tighter route) | Tie |

**Verdict:** AI Findr gives a richer, more actionable plan. The new implementation is clean but lacks depth and creative engagement.

---

### Q2 — What are the best family-friendly movies playing in the mall today?

#### AI Findr
```
I can't accurately say which family-friendly movies are playing today.
If you share the titles from Muvi's website, I'll:
- pick the most family-friendly options
- suggest the best one by age group (kids vs. teens)
(Muvi Cinema is in the mall for your movie plan.)
```

#### New Implementation
```
Here are the movies showing now:
- SAUDI PRO LEAGUE (AL QADSIAH VS AL AHLI) — Sport — 180 min
- SAUDI PRO LEAGUE (AL NASSR VS NEOM) — Sport — 180 min
- SAUDI PRO LEAGUE (AL AHLI VS AL ITTIHAD) — Sport — 180 min
- SHELTER — Action Thriller — 107 min
- CRIME 101 — Crime Action — 140 min
- THE HOUSEMAID — Thriller — 131 min

If you would like, I can help narrow these by genre.
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Live data retrieval | No (defers to website) | Yes (retrieves schedule) | New Impl |
| Family-friendly filtering | N/A (no data) | **None applied** — all 6 listed are sports/thriller | AI Findr |
| Response appropriateness | Honest limitation + helpful redirect | Lists violent/adult films for a "family-friendly" query | AI Findr |
| Showtimes included | N/A | Missing ("Not listed in the provided schedule") | Tie |

**Verdict:** New implementation retrieves live movie data (a clear improvement), but **critically fails to filter for family-appropriate content**. Listing Sport and Thriller films in response to a "family-friendly movies" query is a UX regression. A genre/age suitability filter or at minimum a note on appropriateness is needed.

> ⚠️ **Bug:** The new impl's movie response does not apply content suitability filtering for family queries.

---

### Q3 — Can you suggest some good restaurants for lunch in the mall?

#### AI Findr
```
Here are some solid lunch options (quick + crowd-pleasers) 🍽️😋
- Popeyes Restaurant – fried chicken, sandwiches, tenders
- Kudu Restaurant – a classic Saudi favorite
- Herfy – well-known for burgers and fast meals
- McDonald's – easy, familiar choice
- FanJan Caffé – light lunch + coffee vibe (pastries/sweets too)

[Asks: mood — chicken / burgers / light & coffee]
```

#### New Implementation
```
For lunch in the Food Court, all quick, halal, easy on the Ground Floor.
Food Court:
- McDonald's — Ground, Food Court
- Herfy — Ground, Food Court
- Kudu Restaurant — Ground, Food Court
- Popeyes Restaurant — Ground, Food Court

Light Bite Alternative:
- Corn World — Ground, Kiosk Row (snack-style)

[Offers to narrow by quickest/budget/family]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Number of options | 5 (including FanJan Caffé for café-style) | 5 (includes Corn World as a snack) | Tie |
| Cuisine variety framing | Chicken, burgers, light café | Chicken, fast food, snack | AI Findr |
| Floor/zone info | Absent | Present (all Ground Floor) | New Impl |
| Tone | Warm + descriptive | Efficient + structured | Depends on brand voice |
| Sit-down vs. quick distinction | Mentions mood-based separation | All fast food only | AI Findr |

**Verdict:** Comparable quality. AI Findr's tone is warmer and includes a café option (FanJan Caffé). New implementation wins on location precision.

---

### Q4 — Where can I find kids' clothing stores in the mall?

#### AI Findr
```
Kids' clothing options 👧🧒🛍️:
- Marsil – specialized in kids & family fashion
- Red Tag – affordable, men/women/kids
- R&B (R and B) – value lifestyle fashion for all ages

[Asks: kid's age — baby / toddler / 6–12 / teens]
```

#### New Implementation
```
Kids' Clothing / Family Fashion:
- Centrepoint — Ground, Entertainment Wing (best family stop)
- Red Tag — Ground, Ground Floor
- Max — Ground, Ground Floor
- Brands For Less — Ground, Entertainment Wing
- Zara — Ground, Ground Floor
- Bershka — Ground, Ground Floor

Best route: start with Centrepoint, then Max or Red Tag
[Offers to narrow by boys'/girls'/baby]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Number of stores | 3 | 6 | New Impl |
| Dedicated kids stores (Marsil) | Yes | No (Marsil not listed) | AI Findr |
| Floor/zone precision | Absent | Full floor + wing info | New Impl |
| Routing suggestion | None | Best route provided | New Impl |
| Personalization prompt | Yes | Yes | Tie |

**Verdict:** New implementation provides more comprehensive coverage with location details. However, AI Findr correctly surfaces **Marsil** (a specialist kids' fashion store) which is missing from the new impl's output.

---

### Q5 — What are some fun activities for kids in the mall?

#### AI Findr
```
Fun kid-friendly activities 👧🧒🎉:
- Fun Time (Sparky's World) 🎮 — major entertainment center
- Baby Stroller service 👶 — available 4:00 PM–11:00 PM, Ground Floor

[Transparent: "I don't have more detailed kids-activity venues beyond Fun Time"]
[Asks: kids' ages]
```

#### New Implementation
```
For kids, Fun Time in the Entertainment Wing is the strongest choice.
- Fun Time — Entertainment Wing, Ground Floor
- After play: McDonald's or Herfy in the Food Court for a family break

Begin at Fun Time → Food Court
[Offers kids visit sequence]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Core entertainment accuracy | Correct (Fun Time/Sparky's) | Correct (Fun Time) | Tie |
| Practical family info | Baby Stroller service details | Not mentioned | AI Findr |
| Data transparency | Explicit about limited data | Implicit | AI Findr |
| Post-activity flow | Not included | Includes dining follow-up | New Impl |

**Verdict:** AI Findr's Baby Stroller service tip is a notable practical detail for families that the new implementation misses. New implementation adds a helpful dining sequence.

---

### Q6 — Can you recommend dessert places or cafes in the mall?

#### AI Findr
```
Dessert spots & cafés 🍰☕:
- CoolNup – sweets/dessert shop
- Baskin Robbins – classic ice cream
- FanJan Caffé – specialty coffee + pastries
- MammaBunz – buns + signature drinks
- Mamma Roti Bunz – buns + drinks (cozy café stop)

[Asks: ice cream / coffee + pastry / rich desserts]
```

#### New Implementation
```
For desserts, Baskin Robbins in the Ground Floor Kiosk Row is your dedicated stop.
- Baskin Robbins — Ground, Kiosk Row

[Offers to guide to a coffee stop nearby]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Number of options | 5 | **1** | AI Findr |
| Café coverage | FanJan Caffé, MammaBunz, Mamma Roti Bunz | None retrieved | AI Findr |
| Location precision | None | Ground, Kiosk Row | New Impl |
| Response completeness | Full coverage | Severely limited | AI Findr |

**Verdict:** This is a **significant regression**. The new implementation returns only 1 dessert option (Baskin Robbins) while AI Findr correctly surfaces 5 venues. CoolNup, FanJan Caffé, MammaBunz, and Mamma Roti Bunz are all missing from the new implementation's retrieval for this query.

> ⚠️ **Issue:** Dessert/café retrieval appears under-indexed in the new implementation for Al Nakheel Plaza 28.

---

### Q7 — Where can I shop for affordable fashion in the mall?

#### AI Findr
```
Affordable fashion 🛍️💸:
- Red Tag – budget-friendly for men/women/kids
- R&B (R and B) – value lifestyle fashion
- Max – value fashion store
- Brands For Less – discounted shopping

[Asks: who shopping for + style]
```

#### New Implementation
```
For budget shopping:
- Red Tag — Ground Floor (budget-friendly, value focus)
- Max — Ground Floor (affordable, family-friendly)
- Centrepoint — Entertainment Wing (largest value destination)
- Brands For Less — Entertainment Wing (budget lifestyle finds)
- Nawaem — Kiosk Row (traditional wear, mid-range)

[Offers to narrow to women's/footwear/traditional]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Stores listed | 4 | 5 | New Impl |
| Location info | None | Full floor + wing | New Impl |
| Practical context per store | Brief descriptors | Richer descriptors | New Impl |
| Traditional wear option | No | Nawaem (Kiosk Row) | New Impl |
| R&B mentioned | Yes | No | AI Findr |

**Verdict:** New implementation is stronger here with better location detail and more options. AI Findr mentions R&B which is absent from the new response.

---

### Q8 — Are there any ongoing offers or discounts in the mall?

#### AI Findr
```
No active offers today (2026-04-15) ✅🛍️

Expired offers ⛔:
- 30% Off Entertainment! (Cenomi Plus / SALA) — ended 2025-12-31
- SAR 50 Voucher at Vogacloset (spend SAR 500+) — ended 2025-12-31

[Offers to point to best value stores if no specific deal matches]
```

#### New Implementation
```
I do not see any active offers or discounts listed in the current mall information.
You can check with individual stores for the latest promotions.
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Accuracy (no live offers) | Correct | Correct | Tie |
| Transparency on expired offers | Lists them for reference | None shown | AI Findr |
| Recovery suggestion | Points to value stores | Deflects to stores | Tie |
| Brevity | Moderate | Very brief | New Impl (if brevity is desired) |

**Verdict:** Both are accurate. AI Findr provides more context (expired offers as reference) which sets better expectations. New implementation is acceptably brief but feels slightly dismissive.

---

### Q9 — What entertainment options are available in the mall besides movies?

#### AI Findr
```
Entertainment besides movies 🎮👨‍👩‍👧‍👦:
- Fun Time (Sparky's World) — large entertainment center with games, activities

[Transparent: "That's all the non-movie entertainment I have confirmed details for"]
[Asks: ages for planning]
```

#### New Implementation
```
For entertainment beyond movies:
- Fun Time — Entertainment Wing, Ground Floor (dedicated kids' entertainment)
- Centrepoint — Entertainment Wing (family-friendly backup for leisure browsing)

Start with Fun Time, then Centrepoint.
[Offers nearby food stop pairing]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Accuracy of primary venue | Correct (Fun Time/Sparky's) | Correct (Fun Time) | Tie |
| Data transparency | Explicitly limited | Implicit | AI Findr |
| Secondary recommendation | None | Centrepoint (shopping stretch) | New Impl |
| Post-visit flow | None | Food stop pairing | New Impl |

**Verdict:** New implementation offers a more complete visit flow. Centrepoint as an "entertainment" backup is a reasonable stretch, though primarily a shopping destination.

---

### Q10 — Can you suggest a quick shopping and dining plan for 2–3 hours in the mall?

#### AI Findr
```
Quick 2–3 hour plan 🛍️🍔

Option A (affordable fashion + quick bite) — ~2 to 2.5 hrs:
1) Shop (60–75 min): Red Tag → R&B / Max / Brands For Less
2) Lunch (30–45 min): Popeyes or McDonald's
3) Dessert (15–25 min): Baskin Robbins or CoolNup

Option B (chill — coffee + light shopping) — ~2 hrs:
1) Coffee (30–45 min): FanJan Caffé
2) Shop (60–75 min): Brands For Less → Red Tag

[Asks: budget vs. trendy fashion, fast food vs. café lunch]
```

#### New Implementation
```
For a quick couple's visit [❌ question didn't mention "couple"]:
- Begin at COACH, Ground Floor — polished gift-worthy browse
- Then Swarovski or Tous (Ground Floor) for jewellery
- Close at Food Court with Cinnabon / Kudu / Baskin Robbins

[Offers to tighten into a 2-hour plan]
```

#### Analysis

| Criterion | AI Findr | New Impl | Winner |
|---|---|---|---|
| Two plan variants (A/B) | Yes (fashion + café options) | No | AI Findr |
| Time breakdowns per segment | Yes | No | AI Findr |
| Context appropriateness | Generic (correct) | **"Couple's visit" — wrong** | AI Findr |
| Venue relevance | Value stores (Red Tag, Max) | Premium stores (COACH, Swarovski) for budget-neutral query | AI Findr |
| Dessert variety in plan | 2 options | 3 options | New Impl |

**Verdict:** Significant regression. The new implementation incorrectly interprets a neutral "shopping and dining" query as a "couple's visit" and recommends premium jewellery stores (COACH, Swarovski, Tous) which are irrelevant to a generic 2–3 hour planning request. This is likely a **context contamination issue** — possibly from session state leaking in adjacent queries.

> ⚠️ **Bug:** Session context ("couple's visit") appears to bleed into Q10, producing off-topic premium recommendations. Needs investigation into context isolation between independent queries.

---

## Overall Scorecard

| Query | AI Findr | New Impl | Winner |
|---|---|---|---|
| Q1 — Fun day plan | ★★★★★ | ★★★☆☆ | AI Findr |
| Q2 — Family movies | ★★★☆☆ | ★★☆☆☆ | AI Findr |
| Q3 — Lunch restaurants | ★★★★☆ | ★★★★☆ | Tie |
| Q4 — Kids' clothing | ★★★☆☆ | ★★★★☆ | New Impl |
| Q5 — Kids' activities | ★★★★☆ | ★★★☆☆ | AI Findr |
| Q6 — Desserts & cafes | ★★★★★ | ★★☆☆☆ | AI Findr |
| Q7 — Affordable fashion | ★★★☆☆ | ★★★★☆ | New Impl |
| Q8 — Offers/discounts | ★★★★☆ | ★★★☆☆ | AI Findr |
| Q9 — Entertainment (non-movies) | ★★★☆☆ | ★★★★☆ | New Impl |
| Q10 — 2–3 hour plan | ★★★★★ | ★★☆☆☆ | AI Findr |
| **Totals** | **6 wins** | **3 wins** | **AI Findr** |

---

## Key Strengths of New Implementation

1. **Location precision** — Consistently provides floor and zone info (e.g., "Ground, Entertainment Wing") absent in AI Findr responses.
2. **Markdown formatting** — Clean structured output with headers and bullet points; better suited for rendering in the frontend.
3. **Broader fashion coverage** — Q4 and Q7 both return more stores with routing guidance.
4. **Live movie data** — Actually retrieves the current cinema schedule (though filtering is missing).
5. **Post-visit flow suggestions** — Consistently proposes "what to do after" (e.g., Food Court after Fun Time).

---

## Key Issues in New Implementation

### 1. Dessert/Café Under-retrieval (Q6)
- **Impact:** High — only 1 venue returned vs. 5 in AI Findr.
- **Root cause:** CoolNup, FanJan Caffé, MammaBunz, and Mamma Roti Bunz are not being surfaced for dessert/café intent queries at Al Nakheel Plaza 28.
- **Fix:** Review vector index coverage for café/dessert category tenants; check if these stores are correctly tagged in the tenant data.

### 2. Family-Movie Filter Missing (Q2)
- **Impact:** High — lists 3 sports events and 3 thriller/crime films for a "family-friendly" query.
- **Root cause:** Movie retrieval returns the raw schedule without applying age/content suitability filtering.
- **Fix:** Add genre-filtering logic in the movie recommendation node when the intent is `family_movies` or when companion context includes children.

### 3. Context Contamination — "Couple's Visit" (Q10)
- **Impact:** Medium-High — completely wrong target audience assumed for a generic query.
- **Root cause:** Session state from a prior unrelated query (possibly couple-context from earlier in the test session) leaked into Q10's context window.
- **Fix:** Validate that fresh sessions start with a clean slate. Review `scene.companions` and `scene.visit_type` initialization in `clean_context_builder`.

### 4. Incomplete Offers Response (Q8)
- **Impact:** Low — technically accurate but loses trust-building opportunity.
- **Improvement:** Show expired offers as background context, similar to AI Findr's approach, to show the system is aware of what exists even if nothing is live.

### 5. Mall Hours Missing from Planning Queries (Q1, Q10)
- **Impact:** Low — but useful for visit planning.
- **Improvement:** Include operating hours in itinerary/planning responses.

---

## Recommendations

| Priority | Action |
|---|---|
| 🔴 High | Fix dessert/café retrieval completeness for Al Nakheel Plaza 28 |
| 🔴 High | Add family-content filtering to movie recommendation node |
| 🔴 High | Audit context isolation between independent sessions in the test harness |
| 🟡 Medium | Improve offers response to surface expired offers as historical context |
| 🟡 Medium | Add mall hours to planning-type responses (itinerary, 2–3 hour plans) |
| 🟢 Low | Re-check Marsil and R&B presence in the Al Nakheel Plaza 28 tenant index |

---

*Report generated: 2026-04-16 | Tested against: `al_nakheel_plaza_28` | Backend: `http://localhost:8000`*
