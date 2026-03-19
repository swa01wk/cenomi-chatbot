# Broken, Vague & Multi-Turn Test Scenarios

> Production-grade QA scenarios for the mall concierge chatbot.  
> Designed to stress **context persistence, graceful degradation, intent repair, and realistic user messiness**.
>
> **How to read this file**
> - Each scenario has 3–5 turns.
> - Each turn has a **User query**, the expected `response_mode`, `confidence_level`, and **Must / Must NOT** validation rules.
> - An **Expected Behavior Summary** closes each scenario with state-tracking notes.
> - Queries are written as real users type — typos, fragments, and run-ons included.
> - Validate against Must/Must NOT rules, **not** exact wording.

---

## Scenario 1 — Jacket Refinement (Required Flow A)

> Parent wants kids' jackets. Budget emerges after seeing prices. Classic progressive narrowing.

**Primary Area:** Progressive shopping refinement  
**Why It Matters:** Tests whether the bot holds child-age context across budget refinement, and whether it avoids re-opening the full shopping universe mid-flow.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 1.1 | `i want to buy jackets` | `guided_recommendation` | medium | Must ask a clarifying question: who is it for, or what kind? Must NOT immediately list all jacket stores. Must NOT assume gender or age. |
| 1.2 | `for my 5 year old son` | `guided_recommendation` | high | Must update active context: `category = kids_fashion`, `age = 5`, `gender = boy`. Must suggest stores with children's sections. Must NOT suggest adult fashion brands only. |
| 1.3 | `whats the price` | `direct_factual` | medium | Must acknowledge it cannot confirm exact live prices but give a realistic range or direct to the store. Must NOT make up specific price tags. Must NOT ignore the kids' jacket context. |
| 1.4 | `something affordable` | `guided_recommendation` | high | Must add `budget = affordable` modifier. Must refine suggestions toward value stores, not premium kids labels. Must NOT reset the child/age/jacket context. Must NOT suggest stores already known to be premium. |

**Expected Behavior Summary:**
- After 1.2: active topic = kids fashion, `companions.child_age = 5`, `category = jackets`
- After 1.4: budget modifier added; suggestions must narrow, not expand
- Must avoid: reopening full shopping universe, losing child context, listing adult menswear, hallucinating prices

---

## Scenario 2 — Movie Refinement (Required Flow B)

> Visitor arrives wanting to see movies. Kid context drops mid-flow. Genre preference follows.

**Primary Area:** Movies / cinema  
**Why It Matters:** Tests genre + age-appropriateness stacking, and whether the bot keeps kid-friendly filter active after the companion context is set.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 2.1 | `show me movies` | `guided_recommendation` | high | Must display currently showing films. Must NOT ask for genre before showing any. Should offer to filter by genre/age after listing. |
| 2.2 | `with kid` | `guided_recommendation` | high | Must immediately re-filter for family/kids-appropriate films. Must NOT show horror, adult drama, or 18+ titles. Must set `companions.has_child = true`. |
| 2.3 | `anything action` | `guided_recommendation` | medium | Must find kid-appropriate action films (animated action, superhero, etc.). Must NOT show adult action films just because user said "action." Must flag if no kid-friendly action is currently showing. |
| 2.4 | `any other ones` | `guided_recommendation` | medium | Must show alternative kid-friendly films that were not already listed. Must NOT repeat the same movies from 2.3. Must NOT switch topic to dining or shopping. |

**Expected Behavior Summary:**
- After 2.2: `companions.has_child = true`, filter = family-appropriate
- After 2.3: genre = action, still filtered by kid-appropriate
- After 2.4: continuity — bot must show new options, not re-list old ones
- Must avoid: removing kid filter when "action" is specified, showing duplicate films, switching topic

---

## Scenario 3 — Mall Overview Continuity (Required Flow C)

> Visitor explores what the mall offers at a high level. Follow-up questions drill into services.

**Primary Area:** Mall overview / services  
**Why It Matters:** Tests whether the bot maintains the "discovery" mode as the visitor progressively narrows, rather than switching to a specific recommendation track too early.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 3.1 | `tell me about the mall` | `direct_factual` | high | Must give a high-level overview: floors, key zones, anchor stores, entertainment, dining. Must NOT immediately ask "what are you looking for?" — visitor is in discovery mode. |
| 3.2 | `more about the mall` | `direct_factual` | high | Must expand on what was mentioned: hours, parking, accessibility, events if any. Must NOT repeat the same content from 3.1 verbatim. Must NOT ask a clarifying question — visitor wants more info, not a redirect. |
| 3.3 | `what services do you have` | `direct_factual` | high | Must list mall services: prayer rooms, ATMs, lost & found, strollers, wheelchairs, customer service desk, wifi, etc. Must NOT confuse services with stores. Must NOT say "I'm not sure" for basic facility questions. |

**Expected Behavior Summary:**
- After 3.1: active topic = mall overview, mode = factual/discovery
- After 3.2: topic continues, response deepens — no topic switch
- After 3.3: services sub-topic, must answer factually from known data
- Must avoid: switching to a recommendation mode, asking redundant clarifying questions, repeating 3.1 content in 3.2

---

## Scenario 4 — Bridesmaid Scenario (Required Flow D)

> Woman is a bridesmaid and needs an elegant outfit on a budget. Context and tone both matter.

**Primary Area:** Progressive shopping refinement  
**Why It Matters:** Tests whether the bot holds both a style constraint (elegant) and a budget constraint (not too expensive) simultaneously, without defaulting to either luxury or fast-fashion.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 4.1 | `im bridesmaid` | `context_acknowledgement` | high | Must acknowledge the occasion and set `occasion = wedding`, `role = bridesmaid`. Must invite next direction: outfit? accessories? Must NOT immediately dump a list of stores. |
| 4.2 | `i need something elegant` | `guided_recommendation` | high | Must suggest formal/semi-formal women's fashion options. Must set `style = elegant`. Must NOT suggest casual wear, sportswear, or fast fashion. |
| 4.3 | `not too expensive` | `guided_recommendation` | high | Must add `budget = mid-range`, eliminate premium luxury boutiques. Must retain the elegant style filter — must NOT swing to budget fast fashion as the first suggestion. Must present stores that balance elegance and affordability. |

**Expected Behavior Summary:**
- After 4.1: `occasion = wedding`, `role = bridesmaid`, awaiting next intent
- After 4.2: `style = elegant`, category = women's formalwear
- After 4.3: budget modifier applied; intersection of elegant + affordable
- Must avoid: suggesting H&M-tier or luxury-only options, losing the occasion framing, suggesting menswear

---

## Scenario 5 — Family Quick Plan (Required Flow E)

> Family arrives, needs something fast near cinema. Time-pressure is the key constraint.

**Primary Area:** Cross-intent planning  
**Why It Matters:** Tests whether the bot can generate a quick, co-located plan (food + possible cinema) without over-engineering the response when the user says "quick."

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 5.1 | `i am here with my family` | `context_acknowledgement` | high | Must acknowledge family context. Must set `companions = family`. Must offer natural next directions: dining, entertainment, shopping. Must NOT start listing stores immediately. |
| 5.2 | `something quick` | `guided_recommendation` | high | Must interpret "quick" as time-constrained. Should lean toward fast-casual dining or quick-to-browse entertainment. Must NOT recommend sit-down fine dining. Must NOT ask what kind of quick — just narrow and confirm. |
| 5.3 | `near cinema` | `guided_recommendation` | high | Must add location proximity constraint. Must show dining or activity options physically near the cinema. Must NOT list options on the opposite end of the mall without flagging it. Must NOT forget the family + quick context. |

**Expected Behavior Summary:**
- After 5.1: `companions = family`
- After 5.2: `pace = quick`, suggest fast-casual, quick activities
- After 5.3: `proximity = near_cinema`; all results must be geographically filtered
- Must avoid: recommending fine dining, losing quick-pace context, dropping family modifier

---

## Scenario 6 — Gift for Girlfriend (Elegant → Affordable Pivot)

> Man buying a gift for girlfriend starts vague, wants something nice, pivots when a price range is implied.

**Primary Area:** Progressive shopping refinement  
**Why It Matters:** Tests how the bot handles a vague romantic shopping context and a surprise budget pivot mid-conversation.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 6.1 | `i want to get something for my girlfriend` | `context_acknowledgement` | medium | Must set `occasion = gift`, `recipient = girlfriend`. Must ask or prompt for type: fashion, accessories, fragrance, dining experience? Must NOT immediately list all gift stores. |
| 6.2 | `something nice, not too much` | `guided_recommendation` | medium | "Not too much" signals budget caution. Must interpret as `budget = mid-range`. Must NOT list luxury boutiques. Must suggest accessible gift options: accessories, perfume, casual fashion. |
| 6.3 | `she likes bags` | `guided_recommendation` | high | Must narrow to bags/handbags. Must hold `budget = mid-range` — do NOT suggest luxury designer bags. Must surface mid-range bag stores. |
| 6.4 | `any with sales on` | `guided_recommendation` | medium | Must try to surface stores with active promotions or sales. If unknown, must say so honestly. Must NOT make up a sale offer. Must hold the bag + mid-range + girlfriend context. |

**Expected Behavior Summary:**
- After 6.1: `occasion = gift`, `recipient = girlfriend`
- After 6.2: `budget = mid-range`, style = accessible/nice
- After 6.3: `category = handbags`, budget constraint still active
- After 6.4: promotion filter applied; honesty required if data unavailable
- Must avoid: suggesting luxury designer brands, switching to clothing without reason, hallucinating promotions

---

## Scenario 7 — Food Then Movie, Messy Wording

> Visitor wants food and maybe a movie but phrases it awkwardly. Bot must handle the ambiguity cleanly.

**Primary Area:** Cross-intent planning  
**Why It Matters:** Real users rarely say "I want a hybrid dining and cinema plan" — they say things like this. Tests intent parsing under natural ambiguity.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 7.1 | `food and maybe movie also` | `hybrid_plan` | medium | Must recognize dual intent: dining + cinema. Must NOT treat it as pure dining. Should ask or clarify order (food first, then movie?) or present a simple plan for both. Must NOT demand clarification before doing anything. |
| 7.2 | `yeah food first then see` | `guided_recommendation` | high | "Then see" = user will decide on movie later. Must focus on dining recommendations now. Must keep cinema as a pending thread. Must NOT give a full movie list here — user hasn't asked yet. |
| 7.3 | `something not too heavy, i hate waiting` | `guided_recommendation` | high | Must interpret as: light food + fast service. Must filter toward fast-casual or quick-service dining. Must NOT suggest buffets or long sit-downs. Must NOT ask for cuisine preference — user is clearly in a hurry. |
| 7.4 | `ok after, what movies` | `guided_recommendation` | high | User is now ready for the cinema thread. Must resume cinema intent, suggest currently showing films. Must NOT forget the "we already ate, now movie" context. Must NOT re-recommend restaurants. |

**Expected Behavior Summary:**
- After 7.1: dual intent stored; primary = dining, secondary = cinema pending
- After 7.3: `pace = quick`, `food_weight = light`
- After 7.4: cinema thread resumes; dining thread closes
- Must avoid: asking redundant clarification questions, switching topic prematurely, re-listing food options after user moves to cinema

---

## Scenario 8 — "I'm With My Kid" Context Drop, Movie Mid-Flow

> Visitor asks about movies generically, then drops child context without re-stating the movie intent.

**Primary Area:** Family / child context-setting  
**Why It Matters:** Tests whether the bot correctly applies a mid-conversation context update to a previously established topic, rather than treating it as a new subject.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 8.1 | `whats showing at the cinema` | `direct_factual` | high | Must list currently showing films. Must NOT assume any audience filter yet. |
| 8.2 | `oh wait im with my 7 year old` | `guided_recommendation` | high | Must re-apply the movie list with a kid-appropriate filter. Must acknowledge the context update. Must set `companions.child_age = 7`. Must NOT re-ask "what movies are you looking for?" — the topic is already established. |
| 8.3 | `anything she would like` | `guided_recommendation` | high | Must surface age-appropriate films for a 7-year-old girl (animated, family adventure, etc.). Must NOT show action films with violence. Must NOT ask "what does she like?" — bot should make reasonable suggestions and offer to refine. |
| 8.4 | `ok we'll do that one, anything to eat before` | `hybrid_plan` | high | Must transition to dining while holding: (a) the selected film, (b) the 7-year-old companion. Must suggest family-friendly, quick dining before the movie. Must NOT suggest fine dining or adult-only atmospheres. |

**Expected Behavior Summary:**
- After 8.2: `companions.child_age = 7`, movie filter = family-appropriate, topic stays cinema
- After 8.3: gender hint active, narrowing toward girl-friendly films
- After 8.4: dining sub-intent opens; cinema stays as anchor; family + pre-movie timing context applied
- Must avoid: losing child context, showing adult films, recommending formal restaurants

---

## Scenario 9 — Affordable Shoes, Vague Opener

> User wants shoes, but everything about it is vague until gradually clarified.

**Primary Area:** Progressive shopping refinement  
**Why It Matters:** Tests the bot's ability to progressively narrow without losing patience or switching topic, even when user inputs are minimal.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 9.1 | `shoes` | `guided_recommendation` | low | Single-word query. Must ask minimal clarifying questions: who for, type, or budget. Must NOT dump all shoe stores. Must NOT assume adult/women's/men's. |
| 9.2 | `for me, casual` | `guided_recommendation` | high | Must set `category = casual_shoes`, infer adult. Should ask or prompt gender if still ambiguous, or surface unisex/broad options. Must NOT suggest formal shoes or kids' footwear. |
| 9.3 | `something not too pricey` | `guided_recommendation` | high | Must set `budget = affordable`. Must filter toward accessible footwear stores. Must NOT recommend high-end sneaker boutiques or designer brands. |
| 9.4 | `do they have like Nike or Adidas` | `direct_factual` | high | Must answer factually — which major sports brands are present. Must NOT confuse brand with store. Must hold affordable + casual context — if flagship stores are present, note they carry full price range. |

**Expected Behavior Summary:**
- After 9.2: `category = casual_shoes`, `for = self`
- After 9.3: `budget = affordable`, luxury stores filtered out
- After 9.4: factual brand-availability answer; budget context should color how the answer is framed
- Must avoid: listing luxury sneaker boutiques, losing casual filter, hallucinating brand presence

---

## Scenario 10 — Dinner for Two, Romantic Upgrade

> Couple wants dinner. Starts simple, then romantic framing emerges. Occasion discovered mid-flow.

**Primary Area:** Dining / food  
**Why It Matters:** Tests whether the bot gracefully upgrades its recommendation register when an occasion is revealed mid-flow, without losing prior conversation.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 10.1 | `we want to eat` | `guided_recommendation` | low | "We" = at least 2 people. Must ask or offer cuisine options. Must NOT ask "how many people" — "we" already implies 2+. |
| 10.2 | `something nice, its kind of a special night` | `guided_recommendation` | high | Must register the occasion signal. Must set `occasion = special_evening`. Must shift toward romantic, atmospheric dining. Must NOT suggest fast casual. |
| 10.3 | `not too loud, we want to talk` | `guided_recommendation` | high | Must add `ambiance = quiet`. Must filter out high-energy, loud restaurants. Must NOT re-suggest busy food courts. |
| 10.4 | `how long would a reservation take` | `direct_factual` | medium | Must respond helpfully: bot cannot make reservations, advise calling ahead. Must NOT fake a booking capability. Must NOT ignore the quiet + special occasion context when framing the answer. |

**Expected Behavior Summary:**
- After 10.2: `occasion = special_evening`, `companions = partner/couple`
- After 10.3: `ambiance = quiet`, recommendation register = romantic
- After 10.4: honest factual answer about booking; bot must stay in-character
- Must avoid: suggesting loud or casual dining after quiet constraint, pretending to book reservations, ignoring the occasion framing

---

## Scenario 11 — School Wear for Child, Multiple Kids

> Parent shopping for school wear for two kids of different ages. Complexity in managing two profiles.

**Primary Area:** Family / child context-setting  
**Why It Matters:** Tests whether the bot can hold two simultaneous child profiles and not conflate recommendations for a 6-year-old with those for a 12-year-old.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 11.1 | `i need school clothes for my kids` | `guided_recommendation` | medium | Must ask how many kids or what ages, since "kids" is plural. Must NOT dump all kids' fashion stores. |
| 11.2 | `one is 6 and one is 12` | `guided_recommendation` | high | Must register two child profiles: `child_1.age = 6`, `child_2.age = 12`. Must note that 12-year-old may bridge kids/teen sizing. Must suggest stores that cover both age groups, or separate recommendations for each. |
| 11.3 | `something affordable, back to school budget` | `guided_recommendation` | high | Must apply `budget = affordable` across both profiles. Must NOT recommend premium kids brands. Must NOT forget either child. |
| 11.4 | `do you have any uniform stores` | `direct_factual` | high | Must answer honestly. If no dedicated uniform store is present, must say so and suggest alternatives. Must NOT fabricate a uniform store. |

**Expected Behavior Summary:**
- After 11.2: two child profiles held simultaneously; no conflation
- After 11.3: budget applied to both profiles
- After 11.4: factual question — honest answer required
- Must avoid: conflating the two children's ages, hallucinating a store, losing budget context

---

## Scenario 12 — "Near Cinema" Dining, Budget + Speed

> Visitor wants to eat near the cinema, quickly, on a budget. Three simultaneous constraints.

**Primary Area:** Dining / food  
**Why It Matters:** Tests triple-constraint handling: location proximity, time pressure, and budget — all applied to a dining recommendation simultaneously.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 12.1 | `i want to eat near the cinema` | `guided_recommendation` | high | Must apply location filter: near cinema. Must NOT list restaurants across the mall without flagging distance. |
| 12.2 | `something quick, movie starts in 40 mins` | `guided_recommendation` | high | Must add time-pressure context. Must prioritize fast-service venues. Must NOT suggest sit-down restaurants with 45+ min typical wait. Should note approximate time needed. |
| 12.3 | `and budget friendly` | `guided_recommendation` | high | Must add `budget = affordable`. Must filter out mid-upscale venues. Must hold location + time constraints. Must NOT give a premium fast-casual recommendation as the first choice. |
| 12.4 | `just tell me the best one` | `guided_recommendation` | high | User wants ONE recommendation, not a list. Must give a single clear pick with brief justification. Must NOT list 4-5 options again. |

**Expected Behavior Summary:**
- After 12.1: `proximity = near_cinema`
- After 12.2: `pace = quick`, time-critical flag active
- After 12.3: `budget = affordable`, all three constraints must be simultaneously active
- After 12.4: decisiveness required — single recommendation
- Must avoid: listing far-away restaurants, suggesting slow-service venues, giving a list when user asks for one pick

---

## Scenario 13 — ATM / Prayer Room / Services Query

> Visitor needs practical mall services, not shopping or dining. Factual-only scenario.

**Primary Area:** Mall overview / services  
**Why It Matters:** Tests whether the bot can cleanly answer practical service questions without veering into recommendations, and without pretending to know things it doesn't.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 13.1 | `where is the ATM` | `direct_factual` | high | Must give ATM location. If multiple, list them briefly. Must NOT give a shopping recommendation. Must NOT say "I'm not sure" if this is in known data. |
| 13.2 | `and the prayer room` | `direct_factual` | high | Must answer factually with prayer room location. Must NOT conflate with ATM answer. Must NOT give a general "here are all services" dump — user asked a specific question. |
| 13.3 | `do you have strollers` | `direct_factual` | high | Must answer whether stroller rental/lending is available. Must give location of customer service desk if relevant. Must NOT confuse this with a recommendation query. |

**Expected Behavior Summary:**
- All three turns: factual, services-mode — no drift into recommendations
- Must avoid: redirecting to shopping or dining, giving uncertain answers for known facts, overwhelming with a full services list when asked specific questions

---

## Scenario 14 — Typo / Broken Input / Recovery

> User makes a typo, sends a near-nonsense query, then recovers. Bot must handle gracefully.

**Primary Area:** Unsupported / broken / typo  
**Why It Matters:** Real users mistype constantly. The bot must attempt to recover without embarrassing the user or failing entirely.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 14.1 | `Nkie shoes` | `guided_recommendation` | medium | Must interpret as "Nike shoes." Must NOT ask "did you mean Nike?" in a way that makes the user feel corrected. Should respond naturally as if it understood. |
| 14.2 | `asdf` | `graceful_recovery` | low | Completely unintelligible. Must gracefully explain what the bot can help with (dining, shopping, movies, services). Must NOT crash, error, or give a random response. Must NOT pretend to understand. |
| 14.3 | `sorry i meant sneakers` | `guided_recommendation` | high | Must pick up the correction. Must resume shopping intent for sneakers. Should not bring up the "asdf" confusion again. |
| 14.4 | `affordable ones` | `guided_recommendation` | high | Must add `budget = affordable`. Must NOT lose the sneaker context. Must surface accessible sneaker options. |

**Expected Behavior Summary:**
- After 14.1: typo corrected gracefully, Nike/sneaker category inferred
- After 14.2: graceful failure — ask for clarification
- After 14.3: context restored naturally; no lingering confusion
- After 14.4: budget modifier stacked on sneaker context
- Must avoid: shaming the user for typos, giving random responses to gibberish, losing context after recovery

---

## Scenario 15 — Unsupported Ask (Taxi, Delivery, Online Order)

> User asks for services the mall concierge cannot provide. Bot must decline clearly without being unhelpful.

**Primary Area:** Unsupported / broken  
**Why It Matters:** Hallucinating capabilities (fake booking, fake delivery) is a critical failure mode. Bot must gracefully say no and redirect.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 15.1 | `can you book me a taxi` | `clarification_request` | high | Must clearly state it cannot book taxis. Must offer alternatives if known (ride-share app suggestion, drop-off/pick-up point info). Must NOT pretend to book or say "let me check." |
| 15.2 | `what about online ordering, can you order food for me` | `clarification_request` | high | Must state it cannot place orders. Must NOT list food options as if that answers the question. May mention if any restaurants have their own online platforms. |
| 15.3 | `ok fine, just tell me where to eat then` | `guided_recommendation` | high | User accepts the limitations and reverts to a simple ask. Must respond normally and helpfully. Must NOT carry a defensive tone from the previous refusals. |

**Expected Behavior Summary:**
- After 15.1 and 15.2: clean refusals, no hallucinated capability
- After 15.3: bot resumes normal helpful mode; no residual awkwardness
- Must avoid: pretending to book or order, giving evasive non-answers, over-apologizing in a way that derails the conversation

---

## Scenario 16 — Wedding Shopping, Twisted Wording

> Visitor is shopping for a wedding. Phrasing is vague and slightly contradictory.

**Primary Area:** Progressive shopping refinement  
**Why It Matters:** "Wedding but not too fancy" is a real tension — the bot must navigate it without defaulting to either extreme.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 16.1 | `looking for something for a wedding` | `context_acknowledgement` | medium | Must ask clarifying role: attending, in the wedding party, buying a gift? Must NOT assume the user is getting married. |
| 16.2 | `im attending, need an outfit, wedding but not too fancy` | `guided_recommendation` | high | Must interpret "not too fancy" as smart-casual to semi-formal, not black-tie. Must set `occasion = wedding_guest`, `style = smart_casual`. Must NOT suggest ultra-formal gowns or ball gowns. |
| 16.3 | `something that works for after too` | `guided_recommendation` | high | User wants a versatile outfit. Must refine toward smart-casual that is not occasion-specific. Must NOT ignore the wedding context entirely. |
| 16.4 | `my budget is around 300` | `guided_recommendation` | high | Must apply `budget = ~300`. Must keep the smart-casual + versatile filter. Must NOT suggest budget fast-fashion or high-end luxury. Must frame around mid-range options. |

**Expected Behavior Summary:**
- After 16.2: `occasion = wedding_guest`, `style = smart_casual` (not black-tie)
- After 16.3: versatility added as style modifier
- After 16.4: `budget = mid-range (~300)`, all three constraints active
- Must avoid: suggesting ball gowns, suggesting very casual clothing, losing occasion framing

---

## Scenario 17 — "With Friends, Quick, Not Crowded" — Group Social Plan

> A group of friends wants something quick and low-key. Energy and pace are the dominant signals.

**Primary Area:** Family / group context  
**Why It Matters:** Group context without a clear primary intent. Tests whether the bot can surface a reasonable plan from soft signals.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 17.1 | `im here with 3 friends, what can we do` | `guided_recommendation` | medium | Must register `companions = friends`, `group_size ≈ 4`. Must offer a few directions: dining, entertainment, shopping together. Must NOT give a solo-visitor response. |
| 17.2 | `we want something quick, not crowded` | `guided_recommendation` | high | Must apply `pace = quick`, `ambiance = not_crowded`. Must avoid recommending peak-time food courts or popular spots known to be busy. May note timing if relevant. |
| 17.3 | `maybe food, something we can share` | `guided_recommendation` | high | Must interpret "share" as group-style dining or shared plates. Must filter toward restaurants that work for 4 people sharing. Must NOT suggest solo-format dining. |
| 17.4 | `anything with a chill vibe` | `guided_recommendation` | high | Must stack `ambiance = relaxed/casual`. Must NOT suggest loud bars or family-chaotic environments. Must hold group of 4 + quick + not crowded + shared dining. |

**Expected Behavior Summary:**
- After 17.1: `companions = friends`, `group_size = 4`
- After 17.2: `pace = quick`, `ambiance = not_crowded`
- After 17.3: `dining_style = sharing/group plates`
- After 17.4: `ambiance = chill/relaxed`; all four constraints stacked
- Must avoid: recommending solo-dining formats, noisy spots, losing group context

---

## Scenario 18 — Topic Switch: Movies → Something Cheaper → Dessert

> Visitor is moving fast through topics. Tests whether the bot tracks each switch cleanly.

**Primary Area:** Topic continuity  
**Why It Matters:** Topic switching is a dominant real-user pattern. Bot must close old topics cleanly and open new ones without bleeding them together.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 18.1 | `what movies do you have` | `direct_factual` | high | Must list currently showing films. Standard cinema query. |
| 18.2 | `actually forget that, i want to shop something` | `guided_recommendation` | medium | Clear topic switch signal. Must fully close cinema topic. Must NOT carry any cinema context forward. Must ask or offer a direction for shopping. |
| 18.3 | `something cheaper` | `guided_recommendation` | medium | Ambiguous without prior shopping context established. Must clarify: cheaper than what? Or ask what category of shopping. Must NOT guess randomly. |
| 18.4 | `i mean affordable brands, clothes` | `guided_recommendation` | high | Must now surface affordable clothing stores. Topic = fashion, budget = affordable. Must NOT re-open cinema topic. |
| 18.5 | `ok done, where can i get dessert` | `guided_recommendation` | high | Clear topic switch to dessert. Must close shopping topic. Must recommend dessert spots. Must NOT carry shopping or cinema context into the dessert response. |

**Expected Behavior Summary:**
- After 18.2: cinema topic fully closed; shopping opens
- After 18.4: `category = clothing`, `budget = affordable`
- After 18.5: dessert topic opens cleanly; all prior topics closed
- Must avoid: bleeding topics into each other, carrying cinema context after user said "forget that", asking "are you sure?" when user switches topics

---

## Scenario 19 — Elegant Dressy Shopping, Context-Only Turns

> User sets context first (event, style preference), then asks for shopping help. Context-only turns must be registered without acting on them yet.

**Primary Area:** Context-only turns  
**Why It Matters:** Some users set context before making a request. Bot must not treat context-only turns as requests or give premature recommendations.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 19.1 | `i have a dinner event tonight` | `context_acknowledgement` | high | Must acknowledge the event. Must NOT immediately list restaurants. Must ask or wait: is the user looking for dining, outfit, or both? |
| 19.2 | `yeah im looking for something to wear` | `guided_recommendation` | high | Must open shopping for occasion wear. `occasion = dinner_event`, `category = outfits`. Must NOT suggest casual wear. |
| 19.3 | `something elegant, i want to look put together` | `guided_recommendation` | high | Must narrow to elegant, polished options. `style = elegant`. Must NOT suggest casualwear or sportswear. |
| 19.4 | `not too over the top though` | `guided_recommendation` | high | Must interpret as smart-elegant, not OTT/gown level. Must narrow toward sophisticated but understated fashion. Must NOT suggest formal ball gowns. Must hold occasion + elegant + not-OTT simultaneously. |

**Expected Behavior Summary:**
- After 19.1: context captured; bot waits for the actual request
- After 19.2: shopping intent opens with occasion context
- After 19.3: `style = elegant`
- After 19.4: style refined to smart-elegant/understated
- Must avoid: treating 19.1 as a dining request, suggesting casual or over-formal options

---

## Scenario 20 — The Rambling User ("something nice not too much maybe for kid")

> Chaotic, run-on, multi-part query from a visitor juggling too many thoughts. Worst-case real input.

**Primary Area:** Vague / broken / progressive refinement  
**Why It Matters:** Some users dump everything in one message. Bot must extract what it can, confirm its interpretation, and avoid hallucinating the rest.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 20.1 | `something nice not too much maybe for kid` | `context_acknowledgement` | low | Multiple fragments: "something nice" = quality signal, "not too much" = budget signal, "for kid" = child recipient. Must confirm interpretation before recommending. Must NOT dump 10 stores. Must ask one clarifying question max: what kind of thing for the kid? |
| 20.2 | `like clothes or toy i dunno` | `guided_recommendation` | medium | Category is still ambiguous. Must offer a direction or list two tracks: kids' clothing stores vs. toy/gift options. Must NOT ask more clarifying questions — make a choice and offer it. |
| 20.3 | `clothes, my son, 5` | `guided_recommendation` | high | Now fully specified: `category = kids_clothing`, `child.gender = boy`, `child.age = 5`, `budget = affordable`. Must now give a clean recommendation. Must NOT re-ask any of the clarified information. |
| 20.4 | `is there like a sale or something` | `guided_recommendation` | medium | Must look for promotional context. If known, surface it. If unknown, say so honestly. Must NOT fabricate a sale. Must hold all prior context. |

**Expected Behavior Summary:**
- After 20.1: extract partial context, confirm with one question
- After 20.3: all context resolved; recommendation should be clean and confident
- After 20.4: factual promotion query; honest answer required
- Must avoid: asking more than one clarifying question at a time, hallucinating sales/offers, losing the kid + budget context, overwhelming the user after their confusion

---

## Quick Reference — Scenario Coverage Matrix

| Scenario | Category | Style | Key Constraints |
|----------|----------|-------|-----------------|
| 1 — Jacket refinement | Progressive shopping | Straightforward | child age, budget |
| 2 — Movie refinement | Cinema | Straightforward | kid filter, genre |
| 3 — Mall overview | Services | Straightforward | discovery mode |
| 4 — Bridesmaid | Shopping | Straightforward | occasion, style, budget |
| 5 — Family quick plan | Cross-intent | Straightforward | pace, proximity |
| 6 — Gift for girlfriend | Shopping | Vague → refined | recipient, budget, category |
| 7 — Food then movie | Cross-intent | Messy wording | dual intent, pace |
| 8 — Kid context drop | Cinema + family | Twisted | late context update |
| 9 — Affordable shoes | Shopping | Vague | minimal input, budget |
| 10 — Romantic dinner | Dining | Vague → upgraded | occasion mid-flow, ambiance |
| 11 — School wear | Family shopping | Multi-profile | two kids, budget |
| 12 — Near cinema food | Dining | Multi-constraint | proximity + pace + budget |
| 13 — ATM / prayer room | Services | Factual-only | no drift to recommendations |
| 14 — Typo / recovery | Unsupported | Broken | graceful repair |
| 15 — Taxi / delivery | Unsupported | Unsupported ask | honest refusal |
| 16 — Wedding shopping | Shopping | Twisted | smart-casual tension |
| 17 — Friends group | Group dining | Vague | group + ambiance + sharing |
| 18 — Topic switching | Continuity | Multi-switch | clean close/open |
| 19 — Context-only turns | Shopping | Context-first | delayed request |
| 20 — Rambling query | Vague / broken | Worst-case | multi-fragment parsing |
