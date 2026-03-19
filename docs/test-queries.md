# Manual Test Query Guide

Organised by scenario. Each scenario maps to a real visitor situation the bot
handles today. Use the **Expected Response** column to validate live bot output —
it describes _what the response must do_, not the exact wording.

> **How to read the table**
> - `response_mode` — the mode the resolver should select (visible in debug panel under `response_mode`)
> - `confidence_level` — `high | medium | low` (debug panel: `confidence_level`)
> - **Must** — things the response is required to do
> - **Must NOT** — guardrails; immediate failure if violated

---

## Scenario 1 — Movie Showtime Lookup

> Visitor wants to know what is currently playing at the cinema.
> All queries should route to **factual flow** and return the actual movie schedule.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 1.1 | `what movies are showing?` | `direct_factual` | high | Must list currently showing movies with titles, genres, durations, and showtimes. Must NOT suggest restaurants or activities instead. |
| 1.2 | `show me movies` | `guided_recommendation` | high | Must display currently showing films. Must NOT ask for genre before showing any. Should offer to filter by genre/age after listing. |
| 1.3 | `what movies do we have` | `direct_factual` | high | Same as 1.2. Both queries normalize to the same canonical form. |
| 1.4 | `now showing` | `direct_factual` | high | Must return movie schedule without asking a clarifying question. Showtimes must be visible. |
| 1.5 | `what's playing at the cinema?` | `direct_factual` | high | Must list all movies currently showing. Format: title · genre · duration · showtimes. |
| 1.6 | `any kids movies today?` | `direct_factual` | high | Must return the full schedule AND highlight or note which films are family/child-suitable. Must NOT drop the schedule and only suggest kids activities. |
| 1.7 | `what time is [movie title] showing?` | `direct_factual` | high | Must answer with the specific showtime for that film. If not showing, must say so honestly without guessing. |

---

## Scenario 2 — Family Day Out (Context-Setting → Follow-Ups)

> Visitor opens with a context declaration ("I'm here with my family / kids") then
> asks follow-up questions. The first turn must acknowledge; follow-ups must stay in context.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 2.1 | `i am here with my family` | `context_acknowledgement` | high | Must acknowledge the family visit in one natural sentence. Must offer 2–4 next-step directions (e.g. dining, activities, shopping, movies). Must NOT immediately jump into a narrow product list. |
| 2.2 | `i am here with my kids` | `context_acknowledgement` | high | Same as 2.1. Scene must record `companions: [child]` and `audience: [family_friendly, kid_friendly]`. |
| 2.3 | *(after 2.1)* `where can we eat?` | `guided_recommendation` | high | Must suggest family-friendly dining options. Must NOT suggest romantic or adults-only restaurants. Should mention child-friendly features (kids menus, play areas). |
| 2.4 | *(after 2.1)* `any activities for the kids?` | `guided_recommendation` | high | Must focus on entertainment or activity options suitable for children. Must NOT return a flat store list. |
| 2.5 | *(after 2.1)* `what movies are there?` | `direct_factual` | high | Must return movie schedule. Should highlight or note family/animation titles. Must NOT replace the schedule with general kids activity suggestions. |
| 2.6 | *(after 2.5)* `with kid` | `direct_factual` | high | Must stay in movie context (topic_lock). Should add a note about which films are kid-suitable. Must NOT treat this as a new vague query and return a shopping shortlist. |
| 2.7 | *(after 2.1)* `something quick for lunch` | `guided_recommendation` | high | Must suggest quick-service, family-friendly food options. Must respect both the "family" and "quick" constraints simultaneously. |

---

## Scenario 3 — Gift Shopping (Multi-Turn Shopping Task)

> Visitor builds a shopping task across multiple turns.
> Each turn should refine the task without resetting it.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 3.1 | `i want to buy a gift` | `guided_recommendation` | high | Must ask or assume context and present gift-oriented stores. `shopping_task.product_type` should not yet be set to something specific. |
| 3.2 | `i want to buy jackets` | `guided_recommendation` | medium | Must ask a clarifying question (who for, what style?) before listing stores. `shopping_task.product_type = jacket`. Must NOT immediately dump all fashion stores — this is a broad opener. |
| 3.3 | *(after 3.2)* `for my 5 year old son` | `guided_recommendation` | high | Must refine to kids outerwear stores. `shopping_task.target_age = 5`, `product_category = kids_outerwear`. Must NOT reset the jacket context. |
| 3.4 | *(after 3.3)* `what's the price range?` | `guided_recommendation` | high | Must move `shopping_stage` to `price_guidance`. Should give a general price guidance or direct the visitor to in-store pricing. Must NOT hallucinate specific prices. |
| 3.5 | *(after 3.3)* `something affordable` | `guided_recommendation` | high | Must set `budget_preference = affordable`. Must return or refine toward budget-friendly kids fashion stores. Must NOT present luxury options. |
| 3.6 | `gift for my girlfriend` | `guided_recommendation` | high | Must suggest gift-appropriate stores (beauty, accessories, fashion) with a romantic/couple framing. Must NOT suggest kids stores. |
| 3.7 | *(after 3.6)* `something elegant` | `guided_recommendation` | high | Must refine to elegant / premium options. `style_intent` should include `elegant`. Must maintain the girlfriend gift context. |

---

## Scenario 4 — Date Night Planning

> Couple looking for a curated evening. Expects a guided, romantic plan — not a flat list.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 4.1 | `i am here with my girlfriend` | `context_acknowledgement` | high | Must acknowledge the couple context. Must offer 2–3 next-step directions (dinner, shopping, movie). Must NOT immediately return a restaurant list. |
| 4.2 | `suggest a nice dinner` | `guided_recommendation` | high | Must suggest romantic / couple-friendly dining. Should include atmosphere description. Must NOT suggest fast-food or kids-menu places as the primary recommendation. |
| 4.3 | `we want to catch a movie and then eat` | `hybrid_plan` | high | Must produce ONE unified plan: movie recommendation + nearby dining suggestion. Must NOT give two disconnected lists. Should be structured as steps: "Watch X at [cinema] then head to Y for dinner." |
| 4.4 | `a movie and dinner — what do you recommend?` | `hybrid_plan` | high | Same as 4.3. Must combine both intents into one answer. |
| 4.5 | *(after 4.2)* `something more affordable` | `guided_recommendation` | medium | Must refine to budget-friendlier options. Must NOT restart with a new restaurant search that ignores the romantic context. |
| 4.6 | `any romantic options here?` | `guided_recommendation` | high | Must suggest couple-friendly experiences — dining, experiences, or activities. Must lean into a warm, conversational tone. |

---

## Scenario 5 — Quick Visit / Time-Pressured

> Visitor is short on time and needs fast, concise answers.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 5.1 | `something quick to eat` | `guided_recommendation` | high | Must suggest quick-service food options (3 max). Must NOT return a full fine-dining list. Response must be concise. |
| 5.2 | `we are in a hurry` | `context_acknowledgement` | high | Must acknowledge the time constraint. Must set `visit_constraints: [quick]`. Must offer 2–3 fast options in different categories. |
| 5.3 | *(after 5.2)* `where can we grab a coffee?` | `guided_recommendation` | high | Must suggest cafes near the entrance or with fast service. Must respect the "quick" constraint from turn 5.2. Must NOT suggest sit-down restaurants. |
| 5.4 | `something quick before the movie` | `hybrid_plan` | high | Must produce a quick-bite recommendation that acknowledges the cinema timing context. Should note proximity to cinema where possible. |
| 5.5 | `a quick snack and then maybe browse` | `hybrid_plan` | medium | Must treat "snack" + "browse" as a light 2-step plan. Should not produce a long elaborate itinerary. |
| 5.6 | *(after any dining suggestion)* `something faster` | `guided_recommendation` | medium | Must treat this as a constraint refinement. Must NOT present the same restaurants again. Must acknowledge the refinement ("For something quicker...") and give faster alternatives. |

---

## Scenario 6 — Cross-Intent: Movies + Food

> Visitor explicitly wants both. Should produce a single combined plan, not two lists.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 6.1 | `food and movies` | `hybrid_plan` | medium | Must combine both into one answer. A plan like: "Here's how your evening could look: [movie option] then [food option]." Must NOT give a movie list followed by a separate restaurant list with no connection. |
| 6.2 | `movies and food` | `hybrid_plan` | medium | Same as 6.1. Order-independent. |
| 6.3 | `where can we eat after the movie?` | `hybrid_plan` | high | Must lead with dining options near or convenient to the cinema. The cinema/movie context must inform the answer. |
| 6.4 | `any good places to eat before the movie?` | `hybrid_plan` | high | Must suggest places appropriate for a pre-movie snack or light meal. Timing/proximity context must be in the response. |
| 6.5 | `we want to watch a movie and grab dinner` | `hybrid_plan` | high | Must produce a structured 2-step plan: movie → dinner. Must include specific suggestions for both. Must NOT answer only the movie or only the dinner part. |

---

## Scenario 7 — Vague Exploration / First Visit

> Visitor has no specific goal. Bot should offer a safe, diverse shortlist — not ask too many questions.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 7.1 | `anything interesting here?` | `best_effort_shortlist` | medium | Must offer 3–5 diverse options across categories (dining, shopping, entertainment). May end with ONE focused follow-up question. Must NOT ask multiple questions. |
| 7.2 | `what can I do here?` | `best_effort_shortlist` | medium | Must surface different activity types. Must NOT lock into one category (e.g. only list restaurants). |
| 7.3 | `i'm bored` | `best_effort_shortlist` | medium | Must interpret as an activity/entertainment request. Should offer a mix: cinema, shopping, dining. Tone should be warm and engaging. |
| 7.4 | `it's my first time here` | `context_acknowledgement` | high | Must acknowledge first-time visit. Should give a welcoming overview with 3–4 key highlights. Must NOT dump every store in the mall. |
| 7.5 | `what's good here?` | `best_effort_shortlist` | medium | Must suggest a curated mix — popular or well-regarded options across categories. Must NOT list every entity in the database. |
| 7.6 | `what else is there?` | `best_effort_shortlist` | medium | Must offer options not already mentioned in the conversation. Must NOT re-suggest places already covered. |
| 7.7 | `surprise me` | `best_effort_shortlist` | medium | Must give 3–4 varied suggestions with brief reasons. Tone should be enthusiastic. Must NOT ask "What are you looking for?" — it already knows the visitor wants a surprise. |

---

## Scenario 8 — Broken / Off-Topic Input

> Gibberish, keyboard mashing, or completely out-of-scope questions.
> Bot must recover gracefully without hallucinating.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 8.1 | `asdf` | `graceful_recovery` | low | Must NOT pretend to understand. Must explain 3–4 things it can help with (dining, shopping, movies, services). Should end with a short question. Must NOT hallucinate a store name. |
| 8.2 | `qwerty123` | `graceful_recovery` | low | Same as 8.1. |
| 8.3 | `what's the weather like?` | `graceful_recovery` | low | Must acknowledge it cannot help with weather. Must redirect to mall-relevant topics. Must NOT make up a weather report. |
| 8.4 | `tell me a joke` | `graceful_recovery` | low | Must not attempt to tell a joke. Must politely redirect to mall services. |
| 8.5 | `zrxqp mnbvc` | `graceful_recovery` | low | Same as 8.1. Must NOT produce a store name or recommendation out of thin air. |
| 8.6 | *(single character)* `a` | `graceful_recovery` | low | Must recognize this as too short to interpret. Must offer supported capabilities without guessing intent. |

---

## Scenario 9 — Store Location & Mall Services

> Visitor needs directions or service information. Expects a direct, factual answer — no fluff.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 9.1 | `where is Zara?` | `direct_factual` | high | Must answer with floor and zone. Format: "[Store] is on [Floor], [Zone]." Must NOT add shopping recommendations unless asked. |
| 9.2 | `where is the prayer room?` | `direct_factual` | high | Must give the floor and directions to the prayer room / musalla. Must be concise. |
| 9.3 | `where can I park?` | `direct_factual` | high | Must answer with parking level, entrances, or relevant parking guidance. |
| 9.4 | `what are the mall opening hours?` | `direct_factual` | high | Must state the opening and closing times clearly. Must NOT pad with restaurant recommendations. |
| 9.5 | `where is the ATM?` | `direct_factual` | high | Must give the location (floor/zone) of the nearest ATM. Must NOT suggest shopping while you're there. |
| 9.6 | `do you have a stroller rental?` | `direct_factual` | high | Must answer yes/no and give the location if yes. Must NOT speculate ("I think there might be..."). |
| 9.7 | `is Starbucks here?` | `direct_factual` | high | Must answer yes/no first, then give the floor/zone. Must NOT say "I believe" or hedge unnecessarily. |

---

## Scenario 10 — Constraint Refinement (Multi-Turn)

> Visitor receives a recommendation, then narrows it with a constraint.
> Bot must refine the existing result — NOT start over.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 10.1 | `suggest some restaurants` | `guided_recommendation` | high | Returns a shortlist of restaurants. This is the setup turn. |
| 10.2 | *(after 10.1)* `something cheaper` | `guided_recommendation` | medium | Must refine toward more affordable options. Must NOT return the same list or start fresh. Must open with "For something more affordable..." or equivalent. |
| 10.3 | *(after 10.1)* `something faster` | `guided_recommendation` | medium | Must refine toward quick-service options. Must acknowledge the constraint. Must NOT add new categories (like entertainment). |
| 10.4 | *(after 10.1)* `closer to the cinema` | `guided_recommendation` | medium | Must prioritise options near the cinema area. Must retain the dining context. |
| 10.5 | *(after 10.1)* `not too crowded` | `guided_recommendation` | medium | Must suggest quieter or less busy dining options. Must acknowledge the preference. |
| 10.6 | `suggest some stores for fashion` | `guided_recommendation` | high | Returns fashion store suggestions. Setup turn. |
| 10.7 | *(after 10.6)* `something more affordable` | `guided_recommendation` | medium | Must refine toward value/affordable fashion brands. Must NOT recommend luxury boutiques. The response must acknowledge the refinement. |
| 10.8 | *(after 10.6)* `something for teens` | `guided_recommendation` | medium | Must narrow to youth/teen-oriented fashion. The existing fashion context must be maintained. Must NOT switch to kids toys. |

---

## Scenario 11 — Wedding / Bridesmaid Shopping

> High-stakes scenario with a declared role. Bot must set the scenario correctly and
> NOT fall into family-shopping playbook or generic suggestions.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 11.1 | `i am a bridesmaid` | `context_acknowledgement` | high | Must acknowledge the bridesmaid role. Must set `scenario = wedding_related`, `user_role = bridesmaid`. Must offer wedding-relevant next steps: dress, accessories, beauty. Must NOT suggest family activities. |
| 11.2 | `i am a bridesmaid shopping for the wedding` | `context_acknowledgement` | high | Same as 11.1. Must NOT produce a family-shopping playbook response. |
| 11.3 | *(after 11.1)* `i need something elegant` | `guided_recommendation` | high | Must suggest elegant fashion or accessories. `style_intent` should include `elegant`. Must NOT suggest casual wear or fast fashion. |
| 11.4 | *(after 11.1)* `show me some accessories` | `guided_recommendation` | high | Must return accessories stores with a wedding/elegant framing. Must NOT drift to electronics or kids. |
| 11.5 | *(after 11.1)* `something affordable` | `guided_recommendation` | medium | Must refine elegantly-framed suggestions toward more affordable options. `scenario = wedding_related` must persist. Must NOT override the wedding scenario with a generic budget-shopping response. |
| 11.6 | `i am here for a wedding — i am the groom` | `context_acknowledgement` | high | Must acknowledge the groom role. Must suggest men's fashion, grooming, or accessories. Must NOT suggest women's fashion as the primary. |

---

## Scenario 12 — Cross-Mall Brand Search

> Visitor asks whether a brand exists here or at other Cenomi malls.
> Bot must answer based on real data and never guess availability.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 12.1 | `do you have H&M?` | `direct_factual` | high | Must answer yes/no. If yes: give the floor/zone. Must NOT suggest alternatives if the brand is present. |
| 12.2 | `is Nike here?` | `direct_factual` | high | Same as 12.1. Must NOT say "I think" or hedge. |
| 12.3 | `which of your malls has Zara?` | `direct_factual` | high | Must answer using cross-mall data. Must list which Cenomi mall(s) carry Zara. If none: must say so honestly without inventing availability. |
| 12.4 | `does any Cenomi mall have [brand]?` | `direct_factual` | high | Must check across all loaded malls. Must lead with the current mall if present, then list others. |
| 12.5 | `is Mango at Mall of Arabia too?` | `direct_factual` | high | Must check cross-mall data and give a direct yes/no + location. Must NOT hallucinate a floor if unavailable. |
| 12.6 | `do you have a brand I've never heard of` *(unknown brand)* | `direct_factual` | high | Must say the brand is not found in the current listing. Must NOT invent a floor or zone. Should suggest checking with the information desk. |

---

## Scenario 13 — Mall Overview & Information

> Visitor wants to understand the mall before diving into specific topics.

| # | Query | response_mode | confidence_level | Expected Response |
|---|-------|--------------|-----------------|-------------------|
| 13.1 | `tell me about the mall` | `direct_factual` | high | Must give a structured overview: zones/categories, practical info (hours, parking), 2–3 anchor highlights. Must NOT list every store. |
| 13.2 | `what does this mall have?` | `direct_factual` | high | Must describe the categories and key zones. Must be scan-friendly. Must NOT collapse to a single store name. |
| 13.3 | `is this mall family friendly?` | `direct_factual` | high | Must affirm/describe family amenities: kids areas, dining, prayer rooms, stroller access. Must be specific. |
| 13.4 | *(after 13.1)* `tell me more` | `direct_factual` | high | Must continue the mall overview — deeper detail on what was mentioned. Must NOT drift into a restaurant recommendation. topic_lock should remain `mall_info`. |
| 13.5 | *(after 13.1)* `what about parking?` | `direct_factual` | high | Must stay in mall-info context and answer the parking question directly. Must NOT suggest shopping while parking. |

---

## Debug Fields Checklist

When running any query through the bot with `BACKEND_DEBUG=true`, check these
fields in the debug panel to validate the resolver is working correctly:

| Debug field | Where to find it | What to verify |
|-------------|-----------------|----------------|
| `response_mode` | `evaluator_stub.response_mode` | Matches expected mode in tables above |
| `confidence_level` | `evaluator_stub.confidence_level` | `high` for specific intents; `medium` for vague; `low` only for broken input |
| `response_mode_reason` | `evaluator_stub.response_mode_reason` | Human-readable; explains why the mode was chosen |
| `fallback_applied` | `evaluator_stub.fallback_applied` | `true` only for `best_effort_shortlist` and `graceful_recovery` |
| `flow_type` | `evaluator_stub.flow_type` | `factual` for scenarios 1, 9, 12, 13; `concierge` for all others |
| `intent_domain` | `evaluator_stub.intent_domain` | Matches the query domain (entertainment, dining, shopping, etc.) |
| `message_kind` | `evaluator_stub.message_kind` | `context_setting` for scenarios 2.1, 4.1, 11.1; `followup` / `constraint_refinement` for refinement turns |
| `topic_lock` | `scene_summary.topic_lock` | Non-empty after scenario-specific turns; should persist on follow-ups |
| `playbook_used` | `evaluator_stub.playbook_used` | Should match scenario (e.g. `pb-date-plan` for scenario 4, `pb-family-visit` for scenario 2) |

---

## Quick Smoke Test (5-minute check)

Run these 10 queries in order in a fresh session. All should pass.

```
1.  "what movies are showing?"            → lists movies with showtimes
2.  "i am here with my kids"             → acknowledges, offers next steps
3.  "where can we eat?"                  → family-friendly dining suggestions
4.  "food and movies"                    → one combined plan
5.  "anything interesting here?"         → diverse 3-5 option shortlist
6.  "where is the prayer room?"          → floor/zone answer, no padding
7.  "i am a bridesmaid"                  → wedding context, elegant suggestions offered
8.  "something nice for my son"          → kids-appropriate shortlist
9.  "asdf"                               → graceful recovery, capabilities listed
10. (after query 1) "with kid"           → stays in movie context, notes kid-friendly films
```
