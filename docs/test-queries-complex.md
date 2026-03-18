# Complex Multi-Turn Conversation Scenarios

> More demanding than `test-queries.md`. Each scenario runs 10–15 turns and tests:
> - Deep context persistence across many turns
> - Constraint stacking (multiple active conditions simultaneously)
> - Topic interleaving (visitor jumps between categories mid-conversation)
> - Edge-case intent disambiguation
> - Graceful refinement without context loss
>
> **How to read this file**
> - Each turn has a **Query**, the expected `response_mode` + `confidence_level`, **Must / Must NOT** validation rules, and a **Sample AI Response** showing how the bot should sound.
> - Sample responses are illustrative — exact wording will differ. Validate against the Must/Must NOT rules, not the exact text.
> - Turns marked *(after X.Y)* depend on the conversation state built in that turn.

---

## Scenario 1 — Anniversary Evening: Dietary Restrictions + Romantic Plan

> A couple celebrating their anniversary. One partner is vegetarian.  
> The conversation starts romantic, picks up a dietary constraint mid-flow, and ends with dessert planning.  
> The bot must hold the romantic framing AND the vegetarian filter simultaneously across all turns.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 1.1 | `it's our wedding anniversary tonight` | `context_acknowledgement` | high | Must acknowledge the anniversary warmly. Must offer 3 next-step directions (dinner, experiences, gifts). Must NOT jump directly into a restaurant list. `scene.occasion = anniversary`, `companions = partner`. |
| 1.2 | `we want something really special for dinner` | `guided_recommendation` | high | Must suggest romantic, upscale dining. Should mention atmosphere. Must NOT suggest food courts or quick-service. |
| 1.3 | `my wife is vegetarian — does that change your suggestions?` | `guided_recommendation` | high | Must acknowledge the constraint and re-filter suggestions. `dietary_restrictions = vegetarian` must be set. Must NOT drop previously suggested restaurants without explaining why. |
| 1.4 | *(after 1.3)* `which of those have good vegetarian menus specifically?` | `guided_recommendation` | high | Must surface only the venues with confirmed vegetarian options. Must NOT guess or say "they probably have options." Must NOT re-list meat-heavy restaurants. |
| 1.5 | `we'd also love some flowers or a small gift — is there anywhere here?` | `guided_recommendation` | high | Must surface gift/flower shops. Must NOT abandon the dinner context — should position this as "before or after dinner." |
| 1.6 | *(after 1.2)* `can we do dinner and then a movie as a full night?` | `hybrid_plan` | high | Must produce a structured 2-step plan: dinner (vegetarian-friendly, romantic) → movie. Must NOT give two disconnected lists. The vegetarian constraint must carry into the dinner portion of the plan. |
| 1.7 | *(after 1.6)* `what's a good movie for couples?` | `guided_recommendation` | high | Must filter or highlight romantic/drama films from the current schedule. Must NOT suggest action or horror as the primary recommendation for a couple. |
| 1.8 | `what time should we aim for dinner to make the 9pm show?` | `hybrid_plan` | high | Must give timing guidance — e.g. "Aim for 7pm dinner to be comfortable for a 9pm show." Must NOT refuse to advise on timing. |
| 1.9 | *(after 1.8)* `is 7pm reservation likely to be available on a weekend?` | `guided_recommendation` | medium | Must advise on peak-time likelihood or direct to reservations. Must NOT hallucinate a booking system it doesn't have. Should recommend the visitor call ahead. |
| 1.10 | `and something sweet after the movie — any dessert places?` | `guided_recommendation` | high | Must suggest dessert options. Should note which are vegetarian-safe. Must NOT re-suggest the dinner restaurant as a dessert option. |
| 1.11 | *(after 1.10)* `something not too heavy` | `guided_recommendation` | medium | Must refine toward lighter desserts (gelato, fruit-based, etc). Must NOT re-suggest heavy cake/pastry-focused options as the first choice. |
| 1.12 | `this has been really helpful — can you give me a full itinerary for tonight?` | `hybrid_plan` | high | Must produce a clean, time-sequenced itinerary: gift shop → dinner (vegetarian, romantic) → movie → dessert. All constraints must be reflected. Must NOT produce a generic itinerary that ignores the dietary filter. |

**Sample AI Responses:**

> **1.1** — "Happy anniversary! What a wonderful occasion to celebrate here. Tonight could be a really special evening — shall I help you plan a romantic dinner, find something memorable to gift your partner, or put together a full evening experience? Let me know where you'd like to start."

> **1.3** — "Of course — let me refine those suggestions. With a vegetarian requirement, I'd highlight [Restaurant A], which has a dedicated vegetarian menu, and [Restaurant B], known for its plant-based options. I'd set aside [Restaurant C] I mentioned earlier as it's primarily a steakhouse. Would you like to narrow further by cuisine style?"

> **1.6** — "Great idea — here's how your anniversary evening could look:  
> 🍽 **Dinner (7:00 PM):** [Restaurant A] — romantic setting, strong vegetarian menu, around 90 minutes.  
> 🎬 **Movie (9:00 PM):** [Film title] at the cinema — a great pick for couples.  
> Would you like me to suggest a dessert spot after the show as well?"

> **1.12** — "Here's your anniversary evening plan:  
> **6:30 PM** — Pick up flowers/gift at [Shop] (Level 1)  
> **7:00 PM** — Dinner at [Restaurant] (Level 2) — vegetarian-friendly, romantic atmosphere  
> **9:00 PM** — [Film] at the cinema (Level 3)  
> **11:00 PM** — Dessert at [Dessert Place] (Level 1) — light options available  
> Enjoy your evening — have a wonderful anniversary! 🎉"

---

## Scenario 2 — Corporate Team Outing (10 Colleagues, Mixed Preferences)

> A manager is planning an afternoon/evening outing for a team of 10.  
> Needs group-appropriate dining, a shared activity, and logistics for a large group.  
> Bot must hold the group size context and prioritise group-capable venues.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 2.1 | `i'm planning a team outing for about 10 people` | `context_acknowledgement` | high | Must acknowledge the group outing context. Must set `group_size ≈ 10`, `occasion = team_outing`. Must offer 3 directions: group dining, activities, logistics. Must NOT give a solo-visitor response. |
| 2.2 | `we need a place to eat together — ideally a private or semi-private space` | `guided_recommendation` | high | Must suggest restaurants with group/private dining capability. Must NOT suggest small café tables or food-court spots. Must NOT hallucinate availability without flagging it should be booked. |
| 2.3 | `what cuisines does the team get to choose from — what's available?` | `guided_recommendation` | high | Must list available cuisine types at the mall. Response must be formatted as a clear menu of choices, not a wall of text. |
| 2.4 | `the team has one person who only eats halal and one vegetarian — is that a problem?` | `guided_recommendation` | high | Must confirm that halal is standard in the region and vegetarian options are available at specific venues. Must NOT gloss over the vegetarian constraint. Must flag which group-dining venues satisfy both. |
| 2.5 | *(after 2.4)* `great — which restaurant would you actually recommend for us?` | `guided_recommendation` | high | Must give ONE clear primary recommendation with reasoning (group size, private space, halal+veg coverage). Must NOT give a 5-option list that avoids the decision. |
| 2.6 | `after lunch, we want something fun we can all do together — any group activities?` | `guided_recommendation` | high | Must suggest group-compatible activities. Must surface entertainment that works for 10 adults. Must NOT suggest toddler play areas. |
| 2.7 | *(after 2.6)* `how competitive are these? we want something with a bit of rivalry` | `guided_recommendation` | medium | Must refine toward competitive/game-based activities (e.g. bowling, arcade competitions, escape rooms). Must NOT re-suggest passive entertainment like cinema. |
| 2.8 | `are there any meeting or briefing spaces in the mall if we need 10 mins to debrief?` | `direct_factual` | medium | Must answer based on known facilities. If there are no formal meeting rooms, must say so honestly and suggest alternatives (quiet café area, etc.). Must NOT invent a "business centre." |
| 2.9 | `what's the best way to get 10 people to the mall — is there parking for multiple cars?` | `direct_factual` | high | Must give parking guidance. Should mention group arrival logistics. Must NOT ignore the group-size context. |
| 2.10 | *(after 2.5)* `how far in advance should we book the restaurant?` | `guided_recommendation` | medium | Must give practical booking advice. Must NOT say "I can book it for you" if no booking system exists. Should recommend direct contact. |
| 2.11 | `can you put together a rough schedule for a 3-hour team outing?` | `hybrid_plan` | high | Must produce a time-blocked 3-hour itinerary: arrival → lunch → activity → optional wrap-up. Must reflect group size, halal+veg constraints, and competitive activity preference. Must NOT produce a generic tourist plan. |
| 2.12 | *(after 2.11)* `what if we want to extend it to 4 hours?` | `hybrid_plan` | medium | Must suggest what to add or extend: more activity time, a dessert stop, shopping browse. Must NOT re-produce the same 3-hour plan with "4 hours" swapped in. |

**Sample AI Responses:**

> **2.1** — "Sounds like a great outing! For a group of around 10, I can help you find a restaurant with group seating or a private dining area, suggest team-friendly activities, and share practical tips on getting here. Where would you like to start — food first, or shall we plan the activity?"

> **2.5** — "For a group of 10 with both halal and vegetarian requirements, I'd recommend **[Restaurant Name]** on Level 2. They offer semi-private group seating, a halal-certified kitchen, and a strong vegetarian section on their menu. It's a comfortable 90-minute lunch at that group size. I'd call ahead to reserve — weekday lunch slots fill up for groups."

> **2.11** — "Here's a rough 3-hour plan for your team:  
> **12:00 PM** — Arrive, settle in at [Restaurant] for group lunch (~75 mins)  
> **1:15 PM** — Head to [Activity Venue] for bowling/arcade competition (~75 mins)  
> **2:30 PM** — Optional: grab a coffee/dessert at [Café] to wind down (~30 mins)  
> **3:00 PM** — Head out  
> Want me to swap anything out?"

---

## Scenario 3 — International Tourist: Culture, Local Food & Souvenirs

> A first-time tourist to Saudi Arabia — no local knowledge.  
> Wants cultural orientation, local food experiences, prayer times, and souvenir shopping.  
> Bot must act as a cultural guide, not just a store directory.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 3.1 | `i'm a tourist visiting Saudi Arabia for the first time — what should I know about this mall?` | `context_acknowledgement` | high | Must give a welcoming cultural orientation: mall structure, prayer times/rooms, dress code expectations if any, key highlights. Must NOT dump a store list. |
| 3.2 | `where can I try real Saudi or Arabic food?` | `guided_recommendation` | high | Must surface restaurants serving Saudi/Arabic/Middle Eastern cuisine. Must NOT suggest fast-food chains as the primary answer. Should describe the experience (atmosphere, dishes to try). |
| 3.3 | *(after 3.2)* `what dishes would you recommend for someone who's never had Arabic food?` | `guided_recommendation` | high | Must give 3–5 beginner-friendly dish recommendations (e.g. kabsa, mandi, hummus, kunafa). Must NOT just say "try everything." Should be specific and welcoming. |
| 3.4 | `i need to pray — where are the prayer rooms and when are the prayer times?` | `direct_factual` | high | Must give prayer room location (floor/zone). Must give next prayer time or direct to where to check. Must NOT guess prayer times — should acknowledge they are live data or direct to a prayer app. |
| 3.5 | *(after 3.4)* `does the mall pause business during prayer time?` | `direct_factual` | high | Must give accurate information about mall operations during prayer. Must NOT guess or give conflicting information. |
| 3.6 | `i want to buy souvenirs — traditional Saudi items, not generic tourist stuff` | `guided_recommendation` | high | Must surface shops carrying authentic local goods (oud, dates, traditional clothing, crafts). Must NOT suggest electronics or international fashion as souvenirs. |
| 3.7 | *(after 3.6)* `what's a good price range for oud (the perfume)?` | `guided_recommendation` | medium | Must give a realistic price range or direct to in-store staff. Must NOT hallucinate specific prices. Should mention what drives price variation (wood quality, brand). |
| 3.8 | `is Arabic coffee sold anywhere here? i want to take some home` | `direct_factual` | high | Must answer yes/no and give location if yes. Must NOT say "probably" or hedge. |
| 3.9 | `where should I go to take a nice photo or two — any Instagram-worthy spots in the mall?` | `guided_recommendation` | medium | Must suggest visually distinctive areas (architecture, fountains, decorated zones). Should be fun and engaging. Must NOT say "I don't know" — can describe known design features. |
| 3.10 | `my phone battery is dying — is there anywhere to charge it?` | `direct_factual` | high | Must answer with charging station location or alternatives (café with USB ports). Must NOT suggest purchasing a new charger as the first response. |
| 3.11 | `i only have about 2 hours left here — what should I prioritize?` | `hybrid_plan` | high | Must build a prioritised 2-hour plan: prayer (if needed) → food → souvenirs → photo spot. Must respect tourist context. Must NOT suggest fashion shopping as a priority over cultural experiences. |
| 3.12 | `can you recommend any good halal restaurants nearby the mall for tonight?` | `graceful_recovery` | low | Must clarify it can only advise on what's inside the mall. Must NOT make up external restaurant recommendations. Should redirect to helpful internal alternatives. |
| 3.13 | `shukran! one last thing — do you have any Cenomi loyalty card offers I can use as a tourist?` | `direct_factual` | medium | Must give honest information about loyalty schemes. If tourists are eligible, say so. If not, redirect to alternatives. Must NOT fabricate a loyalty offer. |

**Sample AI Responses:**

> **3.1** — "Welcome to Saudi Arabia — great choice to start here! A few things worth knowing: the mall has prayer rooms on Levels 1 and 3, and shops briefly pause during prayer times. It's a family-friendly space. You'll find international brands, but also some lovely local spots for Arabic food and traditional goods. Shall I point you toward local cuisine, souvenirs, or practical facilities first?"

> **3.3** — "Great question for a first timer! Here are a few dishes to start with:  
> - **Kabsa** — Saudi-style spiced rice with meat, the national dish  
> - **Hummus & Meze** — light starters, perfect to share  
> - **Shawarma** — a street-food classic, widely loved  
> - **Kunafa** — a warm, sweet cheese pastry for dessert  
> Most Arabic restaurants are generous with portions — sharing dishes is very common!"

> **3.11** — "With 2 hours, here's what I'd prioritise:  
> **Now–30 min** — Head to [Arabic Restaurant] for a quick authentic meal  
> **30–60 min** — Pick up souvenirs at [Souvenir Shop] (Level 1) — great for oud and dates  
> **60–90 min** — Explore [Scenic Area] for photos  
> **90–120 min** — Grab Arabic coffee to go from [Café] before you leave  
> Enjoy every moment — it's a beautiful culture to discover!"

---

## Scenario 4 — Child's Birthday Party Planning

> Parent planning a child's (7-year-old) birthday at the mall.  
> Needs activities, food that works for kids, a cake, and possibly some party items.  
> Bot must treat this as a planning scenario across multiple categories.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 4.1 | `i'm planning my daughter's 7th birthday at the mall` | `context_acknowledgement` | high | Must acknowledge the birthday planning context. Must set `occasion = birthday`, `child_age = 7`, `audience = kids`. Must offer 3 directions: activities, food, gifts/party items. Must NOT suggest adult entertainment. |
| 4.2 | `what activities would a 7-year-old girl enjoy here?` | `guided_recommendation` | high | Must surface age-appropriate kids activities. Must NOT suggest cinema as the only option or suggest adult activities. |
| 4.3 | *(after 4.2)* `she loves dancing and arts and crafts — anything like that?` | `guided_recommendation` | high | Must refine to creative/performing arts activities. Must NOT suggest video gaming arcades as the primary match. If no exact match, must say so honestly and offer the closest alternatives. |
| 4.4 | `where can we do the birthday lunch — she wants pizza` | `guided_recommendation` | high | Must suggest pizza restaurants, family-friendly with good group seating. Must NOT suggest romantic or fine-dining spots. |
| 4.5 | *(after 4.4)* `we'll have about 12 kids and 6 parents — is that size okay?` | `guided_recommendation` | high | Must address group size (18 people). Must recommend venues that can accommodate. Must flag need to book in advance. Must NOT suggest a small café. |
| 4.6 | `can i get a birthday cake from somewhere here?` | `direct_factual` | high | Must answer yes/no and give location(s). If bakeries are available, must name them. Must NOT say "there might be" — requires a definitive answer or honest uncertainty. |
| 4.7 | *(after 4.6)* `do any of them do custom cakes with 24 hours notice?` | `direct_factual` | medium | Must give honest answer. If unknown, must direct to the relevant store and not speculate. Must NOT confirm custom cake availability without data. |
| 4.8 | `i need to get her a birthday present too — she loves unicorns and art supplies` | `guided_recommendation` | high | Must suggest toy/gift stores and stationery/art supply shops. Should be enthusiastic and age-appropriate. Must NOT suggest fashion or electronics as primary options. |
| 4.9 | *(after 4.8)* `budget around 150 SAR for the gift` | `guided_recommendation` | medium | Must acknowledge the budget and refine suggestions. Must NOT suggest premium boutiques. Should confirm that budget is workable. |
| 4.10 | `is there face painting or any party entertainment here?` | `direct_factual` | medium | Must answer based on known data. If not available, must say so and not speculate. Should suggest alternatives if entertainment is limited. |
| 4.11 | `what's the best order to do everything — activities first or lunch first?` | `hybrid_plan` | high | Must produce a logical birthday party flow: arrival → activities → lunch → cake → gift. Must consider child energy and logistics. Must NOT produce an adult event timeline. |
| 4.12 | `my daughter has a nut allergy — i should check the pizza place, right?` | `guided_recommendation` | high | Must strongly affirm the need to check with the restaurant directly. Must NOT say "pizza is usually fine." Must NOT downplay the allergy. Should flag that the bot cannot confirm allergen information and staff should be consulted. |
| 4.13 | `thank you — can you give me a birthday checklist?` | `hybrid_plan` | high | Must produce a structured checklist: book restaurant, order cake, buy gift, confirm activity bookings, check allergen info. Must be practical and specific to the conversation context. |

**Sample AI Responses:**

> **4.1** — "How exciting — a 7th birthday at the mall is a great idea! Let's make it special. I can help you find fun activities for her and her friends, sort out birthday lunch, find a cake, and pick the perfect gift. Where shall we start?"

> **4.11** — "Here's a fun birthday flow:  
> **10:30 AM** — Arrive & head straight to [Kids Activity] — energy is highest early  
> **12:30 PM** — Birthday lunch at [Pizza Restaurant] (book ahead for 18 people!)  
> **1:30 PM** — Cake time 🎂  
> **2:00 PM** — Gift opening + browse the toy shop  
> **2:30 PM** — Wind down with ice cream or a treat  
> Kids that age hit a wall around 2:30–3pm, so front-loading the activity works well!"

> **4.12** — "Really important that you do — please speak directly with the restaurant staff about the nut allergy before ordering. I can't confirm allergen information on their behalf, and it's always safest to verify in person. Most family restaurants are well-prepared for allergy queries, but always ask before ordering."

---

## Scenario 5 — Multi-Generational Family: Grandparents + Parents + Toddlers

> A large family of 8: two grandparents (one with limited mobility), two parents, and four children aged 2–10.  
> Every category needs to be accessible, paced for different ages, and logistically feasible.  
> The bot must balance multiple conflicting constraints simultaneously.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 5.1 | `we're a big family — grandparents, parents, and four kids between 2 and 10` | `context_acknowledgement` | high | Must acknowledge the multi-generational group. Must set `group_size = 8`, `ages_present = [elderly, adult, child, toddler]`. Must offer 3 directions: accessibility info, family dining, children's activities. |
| 5.2 | `one of the grandparents uses a walking frame — is the mall accessible?` | `direct_factual` | high | Must give a clear answer on accessibility: lifts, ramp access, seating areas, wheelchair/walking-aid routes. Must NOT give a vague "it's generally accessible" answer. |
| 5.3 | `is there a stroller we can borrow for the 2-year-old?` | `direct_factual` | high | Must answer yes/no definitively. If available: give pickup location. Must NOT speculate. |
| 5.4 | `where can the grandparents sit and relax while we take the kids somewhere?` | `guided_recommendation` | high | Must suggest comfortable seating areas, cafés with relaxed atmosphere, or rest zones. Must NOT suggest active entertainment for this group. |
| 5.5 | `what activities work for kids ranging from 2 to 10?` | `guided_recommendation` | high | Must surface activities with a wide age range. Must NOT suggest activities that exclude the 2-year-old. Should note age suitability for each option. |
| 5.6 | *(after 5.5)* `can we all do something together — grandparents included?` | `guided_recommendation` | medium | Must suggest low-intensity, inclusive experiences (e.g. cinema, food experience, walking through a decorated area). Must NOT suggest escape rooms or physically demanding activities. |
| 5.7 | `where should we eat with 8 people including young kids and elderly?` | `guided_recommendation` | high | Must prioritise: accessible seating, child-friendly menu, quiet enough for elderly, high chairs available, group table size. Must NOT suggest a trendy loud bar-restaurant. |
| 5.8 | *(after 5.7)* `we want somewhere not too loud — the grandparents find it hard to hear in noisy places` | `guided_recommendation` | high | Must refine toward quieter dining environments. Must NOT re-suggest loud or open-plan food courts. Must acknowledge the acoustic constraint. |
| 5.9 | `the kids are starting to get tired — how do we wind down the visit?` | `hybrid_plan` | high | Must suggest a calming end-of-visit routine: dessert, quiet activity, resting in a lounge area. Must NOT suggest energising activities. |
| 5.10 | `is there a family toilet or a changing room for the toddler?` | `direct_factual` | high | Must give floor/zone for baby changing facilities and family restrooms. Must be definitive — no hedging. |
| 5.11 | `the grandparents want to buy the kids a small gift each before we leave` | `guided_recommendation` | high | Must suggest accessible gift/toy shops. Must NOT suggest a store that requires climbing stairs or is physically challenging to navigate. Should consider ease of access. |
| 5.12 | `can you give us a family-friendly plan for a 4-hour visit?` | `hybrid_plan` | high | Must produce a time-blocked plan that accounts for grandparent mobility, toddler nap-time risk, kids' activities, group dining, and a rest period. Must NOT produce an activities-marathon itinerary. |
| 5.13 | `one of the kids has a peanut allergy and is lactose intolerant — what should we watch for at the restaurant?` | `guided_recommendation` | high | Must strongly recommend checking with restaurant staff. Must NOT confirm that specific menu items are safe. Should give general advice on what to ask. Must NOT minimise the allergy. |

**Sample AI Responses:**

> **5.1** — "What a lovely family outing! With grandparents, parents, and four little ones from 2 to 10, we want to make sure everyone is comfortable and has a great time. The mall has good accessibility features, family dining, and plenty to keep the kids entertained. Shall I start with accessibility info for your grandparents, or jump into activities for the kids?"

> **5.2** — "Yes, the mall is walking-frame accessible. All levels are connected by lifts located near [location]. The main entrances have ramp access, and there are rest seating areas throughout. I'd recommend using the [entrance] for the smoothest access — it's closest to the lifts and has the widest corridors."

> **5.12** — "Here's a family plan that works for everyone:  
> **Arrival (30 min)** — Collect stroller, orient grandparents to rest area near [Café], kids explore nearby kids zone  
> **Hour 1** — [Kids Activity] for ages 4–10; grandparents relax at [Quiet Café] nearby  
> **Hour 2** — Group lunch at [Restaurant] — quiet, accessible, group seating  
> **Hour 3** — Grandparents browse gifts with parents; toddler nap in stroller  
> **Hour 4** — Ice cream/dessert together, then gradual exit via [Accessible Exit]  
> A gentle pace makes a much better day than trying to fit everything in!"

---

## Scenario 6 — Back-to-School Power Shopper (Budget + Time Pressured)

> Parent with three kids (ages 6, 11, 15) who has a detailed shopping list, a 500 SAR budget, and 2.5 hours.  
> Bot must help prioritise, route efficiently, and stay within budget.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 6.1 | `school is starting next week and i need to shop for three kids — ages 6, 11, and 15` | `context_acknowledgement` | high | Must acknowledge the back-to-school context. Must set `shopping_task.occasion = back_to_school`, `children_ages = [6, 11, 15]`. Must offer a structured approach (by category or by child). |
| 6.2 | `i need school bags, stationery, and shoes for all three — different sizes obviously` | `guided_recommendation` | high | Must surface stores covering all three categories. Should note which stores cover multiple categories (efficiency). Must NOT suggest one store for each category without considering overlap. |
| 6.3 | `what's the most efficient order to visit the stores — i want to minimize walking` | `guided_recommendation` | high | Must give a routing suggestion based on floor/zone proximity. Must NOT give an alphabetically sorted list of stores. |
| 6.4 | `my budget is 500 SAR for everything — is that realistic?` | `guided_recommendation` | medium | Must give honest guidance. Should break down rough per-category expectations. Must NOT say "that's plenty" without context. Should flag if the budget is tight for 3 kids across 3 categories. |
| 6.5 | *(after 6.4)* `which stores give the best value for money?` | `guided_recommendation` | medium | Must surface value-for-money stores specifically. Must NOT recommend luxury brands. `budget_preference = value`. |
| 6.6 | `the 15-year-old is very particular about style — any trendy options for teens?` | `guided_recommendation` | high | Must surface teen-oriented fashion/footwear with a style-conscious framing. Must maintain the budget constraint. Must NOT suggest kids brands for a 15-year-old. |
| 6.7 | *(after 6.6)* `she specifically wants Nike or Adidas shoes` | `direct_factual` | high | Must confirm yes/no whether those brands are present and give their location. Must NOT guess. |
| 6.8 | `for the 6-year-old I need something durable — he destroys shoes in a month` | `guided_recommendation` | high | Must suggest durable kids footwear brands. Should note which brands are known for durability. Must NOT suggest fashion-over-function options. |
| 6.9 | `is there a stationery store with a good back-to-school section?` | `direct_factual` | high | Must answer yes/no and give location. Must NOT redirect to a general office supplies chain without addressing the back-to-school angle. |
| 6.10 | *(after 6.9)* `do they do pre-packed school supply bundles or only individual items?` | `direct_factual` | medium | Must answer honestly. If unknown, must direct to the store rather than guessing. |
| 6.11 | `i also need to grab a quick lunch mid-shop — something i can eat on the go` | `guided_recommendation` | high | Must suggest grab-and-go options. Must NOT suggest sit-down restaurants. Should position it efficiently within the shopping route. |
| 6.12 | `i have 2.5 hours — can you map out a shopping plan?` | `hybrid_plan` | high | Must produce a time-efficient, floor-by-floor shopping plan. Must include: which store to start at, routing logic, a 15-minute lunch break, and a buffer. Must reflect all three kids' needs and the 500 SAR budget context. |
| 6.13 | *(after 6.12)* `what if i can't find everything — what's the priority order?` | `guided_recommendation` | high | Must advise on priority (e.g. shoes first as hardest to find right size, then bags, then stationery). Must give a reasoned answer, not just a list. |

**Sample AI Responses:**

> **6.3** — "To save your legs, here's an efficient route:  
> **Level 1** — Start at [Shoe Store A] (kids and teen sizes) + [Value Fashion] next door  
> **Level 2** — [Sports Brand] for the Nike/Adidas check + [School Bags Store]  
> **Level 1 again** — [Stationery Store] near the entrance on your way out  
> This way you're not doubling back. Want me to factor in a lunch stop?"

> **6.4** — "500 SAR for 3 kids across shoes, bags, and stationery is achievable but will be tight. A rough breakdown: shoes (3 pairs) could run 150–250 SAR if you stick to mid-range brands; bags 80–150 SAR; stationery 60–100 SAR. Value stores like [Brand] will help stretch it. The teen's Nike/Adidas request may be the biggest squeeze — worth checking if there are sale items."

> **6.12** — "Here's a 2.5-hour shopping plan:  
> **0:00–0:30** — Level 2: [Sports Brand] — teen shoes (Nike/Adidas check) + 11-year-old bag  
> **0:30–1:00** — Level 1: [Kids Shoe Store] — durable shoes for the 6-year-old  
> **1:00–1:15** — Quick lunch at [Grab-and-Go spot] (Level 1, near escalator)  
> **1:15–1:45** — Level 2: [Back-to-School Store] — stationery for all three  
> **1:45–2:15** — Level 1: [Value Fashion] — bags and remaining items  
> **2:15–2:30** — Buffer / checkout  
> Stay flexible — shoe sizing always takes longer than you think!"

---

## Scenario 7 — Luxury / VIP Experience Seeker

> A high-end shopper looking for the most premium experience the mall offers.  
> Budget is not a constraint. Wants curation, not a list.  
> Bot must adopt a concierge tone without sounding like a catalogue.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 7.1 | `i'm looking for a premium experience today — money is not a concern` | `context_acknowledgement` | high | Must acknowledge the luxury/VIP framing. Must set `budget_preference = premium`. Must adopt a warm, concierge tone. Must NOT immediately list luxury brand names — lead with experience. |
| 7.2 | `start with fashion — i want to refresh my wardrobe, both casual and formal` | `guided_recommendation` | high | Must suggest premium fashion brands for both categories. Must NOT suggest high-street or budget brands. Should offer a curated "edit" feel, not a full brand dump. |
| 7.3 | *(after 7.2)* `any of those offer personal shopping assistance or styling?` | `direct_factual` | medium | Must answer honestly. If personal shopping is available at specific stores, must say which. Must NOT fabricate a service. |
| 7.4 | `i also want to find something truly unique — a gift that's not off the shelf` | `guided_recommendation` | high | Must suggest bespoke, artisanal, or limited-availability gift options. Must NOT suggest generic gift shops. |
| 7.5 | *(after 7.4)* `ideally something locally crafted or Saudi-inspired` | `guided_recommendation` | high | Must narrow to locally sourced or culturally authentic items. Must NOT suggest imported luxury goods as a "local" option. |
| 7.6 | `where should I have lunch — i want exceptional food, not just fine dining for its own sake` | `guided_recommendation` | high | Must recommend based on quality and experience. Should mention what makes the recommendation stand out. Must NOT give a generic "upscale restaurant" answer. |
| 7.7 | *(after 7.6)* `can you tell me more about the menu or signature dishes there?` | `guided_recommendation` | medium | Must give honest, specific information if available. Must NOT fabricate menu items. If detailed menu info is unavailable, must say so and suggest the visitor check directly. |
| 7.8 | `after lunch i want a beauty or grooming treatment — any high-end options?` | `guided_recommendation` | high | Must surface premium beauty/grooming/spa options. Must NOT suggest a basic hair salon as a luxury option without context. |
| 7.9 | `i'd like to end the day with a tasting or something experiential — not just shopping` | `guided_recommendation` | medium | Must suggest experiential options: gourmet coffee tasting, oud experience, chocolate tasting, cooking demo if available. Must NOT suggest a cinema as the primary experiential option. |
| 7.10 | *(after 7.9)* `is there anywhere that does a proper Arabic coffee or oud ceremony?` | `direct_factual` | medium | Must answer honestly. If available, give location. If not, say so and suggest the nearest alternative. |
| 7.11 | `what's the most exclusive item or experience I probably couldn't find elsewhere in the city?` | `best_effort_shortlist` | medium | Must attempt to curate a differentiated answer. Must NOT give a generic luxury brand recommendation. Should highlight whatever is unique to this specific mall. |
| 7.12 | `put together a luxury day plan — i'll arrive at noon and leave around 8pm` | `hybrid_plan` | high | Must produce an 8-hour premium itinerary: shopping → personal styling → lunch → beauty → experiential → dinner. Must be specific, curated, and reflect luxury context throughout. Must NOT include budget options. |

**Sample AI Responses:**

> **7.1** — "A premium day — let's make it exceptional. Rather than a list of brand names, let me help you build an experience: world-class fashion, a memorable lunch, and something truly distinctive to take home. Where shall we begin — wardrobe first, or shall I map out a full day?"

> **7.6** — "For an exceptional lunch rather than just 'fine dining,' I'd point you to [Restaurant Name]. What sets it apart is [specific quality — e.g. the open-kitchen concept, a chef with regional recognition, a menu that changes seasonally]. It's the kind of place where the food has a story. Worth reserving a table — they fill up at lunch."

> **7.12** — "Here's your luxury day from noon to 8pm:  
> **12:00–1:30 PM** — Fashion: [Luxury Store A] for casual, [Luxury Store B] for formal. Ask for the in-store stylist at B.  
> **1:30–3:00 PM** — Lunch at [Restaurant] — book the chef's table if possible  
> **3:00–4:30 PM** — [Premium Grooming/Beauty] — full treatment  
> **4:30–5:30 PM** — Artisan gifts: [Local Craft Shop] for something Saudi-made  
> **5:30–6:30 PM** — Arabic coffee tasting at [Café]  
> **6:30–8:00 PM** — Dinner at [Evening Restaurant] — signature tasting menu  
> Shall I refine any part of this?"

---

## Scenario 8 — Health & Wellness Enthusiast

> A visitor who eats clean, works out, and is shopping for activewear and supplements.  
> All food suggestions must be healthy. Shopping must be fitness-oriented.  
> Bot must maintain the wellness lens across all turns.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 8.1 | `i'm really into fitness and healthy eating — what's here for me?` | `context_acknowledgement` | high | Must acknowledge the wellness/fitness profile. Must offer 3 directions: healthy dining, sportswear, supplements/health products. Must NOT suggest standard restaurants without a healthy qualifier. |
| 8.2 | `what healthy food options do you have — i mean actually healthy, not just a salad` | `guided_recommendation` | high | Must surface genuinely health-conscious restaurants or menu options. Must NOT suggest fast food with a "they have salads" caveat. Should describe why each option qualifies as healthy. |
| 8.3 | *(after 8.2)* `any options with high protein focus?` | `guided_recommendation` | high | Must refine to protein-focused options (grill, lean meat, legumes, etc.). Must maintain the "genuinely healthy" filter. Must NOT suggest a burger as high-protein. |
| 8.4 | `i'm also vegan — does that narrow it down much?` | `guided_recommendation` | high | Must immediately re-filter. `dietary_restrictions = vegan` must be applied. Must NOT suggest any venue serving animal products as the primary recommendation. |
| 8.5 | *(after 8.4)* `so what's left that's both vegan and high-protein?` | `guided_recommendation` | high | Must surface options meeting BOTH constraints simultaneously. If options are very limited, must say so honestly. Must NOT hallucinate a vegan protein restaurant. |
| 8.6 | `where can i find good activewear — not just one brand, I want a few choices` | `guided_recommendation` | high | Must list multiple activewear brands/stores. Must NOT suggest a single store. `product_category = activewear`. |
| 8.7 | *(after 8.6)* `i specifically want women's running gear and yoga pants` | `guided_recommendation` | high | Must refine to women's performance categories. Must NOT suggest men's or general fashion stores. |
| 8.8 | `do you have any supplement or nutrition stores?` | `direct_factual` | high | Must answer yes/no and give location. Must NOT redirect to a pharmacy unless they specifically stock sports nutrition. |
| 8.9 | `i want to grab a post-workout smoothie — any good spots?` | `guided_recommendation` | high | Must suggest smoothie/juice bars. Must be vegan-compatible. Must NOT suggest a milkshake bar or dairy-heavy option without flagging the vegan constraint. |
| 8.10 | *(after 8.9)* `something with no added sugar` | `guided_recommendation` | medium | Must refine toward no-added-sugar options. Must acknowledge the constraint. Must NOT present the same smoothie bar recommendations without addressing the refinement. |
| 8.11 | `what's the best approach if i want to do a "clean" meal before a workout session?` | `guided_recommendation` | medium | Must give practical pre-workout meal guidance (light, digestible, vegan-compatible). Must NOT prescribe a specific medical diet. Should be helpful and informative. |
| 8.12 | `is there a gym or fitness studio in or near the mall?` | `direct_factual` | medium | Must answer based on known data. If there's a gym in the mall, give location. If not, must say so — must NOT invent one. |
| 8.13 | `give me a health-focused plan for my 2 hours here` | `hybrid_plan` | high | Must build a wellness-optimised 2-hour plan: healthy meal → supplement/nutrition shop → activewear → smoothie. Must be vegan, high-protein focused throughout. Must NOT include any dining or shopping options that contradict the established profile. |

**Sample AI Responses:**

> **8.4** — "Good to know — that does narrow things down, but there are still solid options. Let me filter out anything non-vegan. From what I mentioned: [Restaurant A] has a dedicated vegan menu, [Restaurant B] has strong plant-based options. I'd set aside [Restaurant C] as their protein options are mostly animal-based. Want me to go deeper on either of the remaining two?"

> **8.13** — "Here's your 2-hour wellness plan:  
> **0:00–0:30** — Pre-workout meal at [Vegan Restaurant] — ask for their high-protein plant bowl  
> **0:30–1:00** — [Activewear Store A] + [Activewear Store B] for running gear and yoga pants  
> **1:00–1:20** — [Nutrition Store] for supplements  
> **1:20–1:40** — [Juice Bar] — no-added-sugar smoothie  
> **1:40–2:00** — Browse or wind down  
> Clean, efficient, and fully plant-based. Enjoy your session!"

---

## Scenario 9 — Teenage Group Hangout (Budget-Conscious, Social)

> A group of 5 teenagers, 14–17, with a combined budget of ~300 SAR.  
> They want fun, social experiences — not shopping. They'll push back on boring suggestions.  
> Bot must stay engaging, not condescending, and work within a tight budget.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 9.1 | `we're a group of 5 teens just hanging out — what's fun to do here?` | `best_effort_shortlist` | high | Must acknowledge the teen group context. Must surface entertainment/social activities. Must NOT suggest toddler play areas or family-centric activities. Tone must be casual and engaging. |
| 9.2 | `we have like 300 SAR between us — so what can we actually afford?` | `guided_recommendation` | medium | Must frame suggestions within the 300 SAR total budget (60 SAR per person). Must NOT recommend expensive experiences without flagging cost. |
| 9.3 | `what's the most fun thing for that budget?` | `guided_recommendation` | medium | Must give ONE confident recommendation rather than hedging. Must explain why it's the best value-for-fun ratio. Must NOT give a non-committal "it depends" answer. |
| 9.4 | `does the cinema have any good movies right now?` | `direct_factual` | high | Must return current movie schedule. Should highlight which films are popular or well-rated among teens. Must NOT editorialize about movie quality without flagging it's a suggestion. |
| 9.5 | *(after 9.4)* `how much is a ticket for 5 people?` | `direct_factual` | medium | Must give honest ticket pricing if known. Must NOT hallucinate a price. If pricing is unavailable, must say so and direct to the cinema desk. |
| 9.6 | `we want something to eat but nothing too expensive — decent food for around 50–60 SAR each` | `guided_recommendation` | high | Must surface casual, teen-friendly food at the stated per-person price range. Must NOT suggest fine dining. Tone should be upbeat. |
| 9.7 | *(after 9.6)* `somewhere we can sit together as a group of 5 and it won't be awkward` | `guided_recommendation` | high | Must prioritise group seating and casual/social atmosphere. Must NOT suggest small tables or formal environments. |
| 9.8 | `any challenges or competitions we can do together?` | `guided_recommendation` | high | Must suggest competitive activities (bowling, arcade, mini-golf if available). Must surface activities that work for 5 people simultaneously. |
| 9.9 | *(after 9.8)* `what's the cheapest of those?` | `guided_recommendation` | medium | Must give cost comparison across the suggested activities. Must NOT hedge — should give a clear "cheapest option is X" answer. |
| 9.10 | `is there anywhere to just sit and chill if we're tired?` | `guided_recommendation` | medium | Must suggest low-key areas: food court seating, café with casual vibe, open lounge areas. Must NOT suggest the cinema (already covered). |
| 9.11 | `what if we want to take photos together — any cool backdrops here?` | `guided_recommendation` | medium | Must suggest photogenic spots in the mall. Tone should match teen energy. Must NOT give a generic "the mall is nice" answer. |
| 9.12 | `can we fit in movies, food, and one activity in 3 hours?` | `hybrid_plan` | high | Must give an honest assessment. If 3 activities in 3 hours is tight, must say so. Must produce a realistic plan or suggest prioritisation. |
| 9.13 | `suggest the best possible 3-hour hangout plan for 5 teens on 300 SAR total` | `hybrid_plan` | high | Must produce a realistic, fun, budget-conscious 3-hour plan. Must include food, at least one activity, and some free/low-cost social time. Must NOT exceed the 300 SAR total. Must feel like a recommendation from a friend, not a schedule. |

**Sample AI Responses:**

> **9.3** — "Honestly? For that budget and a group of 5, the arcade/gaming area gives you the most fun per riyal. You can stretch your time there, it's competitive, you'll have a proper laugh, and you won't blow through the budget in 20 minutes like you would at bowling. Go there first while the energy's high."

> **9.13** — "Okay, here's your ideal 3-hour hangout:  
> **Hour 1** — Head straight to [Arcade/Activity] — split 150 SAR between you (30 SAR each), play for an hour while energy is high  
> **Hour 2** — Food at [Casual Spot] — proper group seating, around 50 SAR each, budget intact  
> **After food** — [Movie/Chill] if you've got the SAR left, or grab drinks and find a chill spot near [Lounge Area] and post your photos  
> You'll have enough left for drinks and snacks. Make it count 🙌"

> **9.12** — "Tight but doable IF you stay in one area and don't mess around between spots. Movies alone eat 1.5 hours, food takes 45 min, so you'd need a quick activity. Realistically — pick 2 and enjoy them properly, or do the activity fast (arcade rounds, not full bowling). My suggestion: skip the movie today and do it justice another time."

---

## Scenario 10 — Pre-Wedding Bridal Party Shopping Spree

> A bride-to-be with 4 bridesmaids on a full pre-wedding prep day.  
> Needs bridal accessories, bridesmaids' outfits, beauty treatments, celebration dinner.  
> Bot must maintain the bridal context across 14 turns and escalating complexity.

| # | Query | response_mode | confidence_level | Must / Must NOT |
|---|-------|--------------|-----------------|-----------------|
| 10.1 | `we're a bridal party — the wedding is in 3 days and we're doing our final shopping day` | `context_acknowledgement` | high | Must acknowledge the bridal occasion with warmth and excitement. Must set `scenario = wedding_related`, `occasion = bridal_party_shopping`, `group_size = 5`. Must offer structured next steps: bridal wear, bridesmaids, beauty, celebration. |
| 10.2 | `the bride needs something to wear to the rehearsal dinner — elegant but not the wedding dress` | `guided_recommendation` | high | Must suggest elegant, occasion-appropriate women's fashion. Must NOT suggest casual or bridal/wedding dress stores. `style_intent = elegant`, `occasion = rehearsal_dinner`. |
| 10.3 | *(after 10.2)* `she has a specific colour in mind — dusty rose` | `guided_recommendation` | high | Must acknowledge the colour preference and refine. Must NOT suggest stores without mentioning the colour guidance caveat (can't confirm exact stock). Must direct to stores most likely to carry that palette. |
| 10.4 | `the 4 bridesmaids need matching accessories — not too matchy-matchy, more coordinated` | `guided_recommendation` | high | Must suggest accessories stores with variety to allow coordination without uniformity. Must NOT suggest an accessories kiosk with no curation. Should understand the "coordinated not identical" distinction. |
| 10.5 | *(after 10.4)* `budget for accessories is about 200 SAR per bridesmaid` | `guided_recommendation` | medium | Must apply the per-person budget. Must surface options within that range. Must NOT recommend high-end jewellery boutiques as the primary option. |
| 10.6 | `we all want to get a blow-dry and makeup done — where can we all go?` | `guided_recommendation` | high | Must surface beauty salons capable of handling a group of 5. Must flag that appointment booking is essential. Must NOT suggest a single-chair salon. |
| 10.7 | *(after 10.6)* `we need to be done by 2pm — is there a salon that can take all 5 of us at once?` | `direct_factual` | high | Must answer honestly. If unknown, must direct to the salon to confirm group booking availability. Must NOT confirm availability without data. |
| 10.8 | `the bride also wants a gift from all of us — something she'll remember` | `guided_recommendation` | high | Must suggest memorable, personal gift options: jewellery, a keepsake, personalised item, or premium beauty set. Must NOT suggest generic mall gift vouchers as the primary. |
| 10.9 | *(after 10.8)* `she loves oud perfume — anything premium here?` | `guided_recommendation` | high | Must surface premium oud/fragrance options. Must NOT suggest a generic perfume counter. Should describe what makes the suggestion premium. |
| 10.10 | `after the shopping we want to celebrate — a nice dinner for 5, feels special` | `guided_recommendation` | high | Must suggest celebratory, group-capable, elegant dining. Must maintain the bridal occasion framing. Must NOT suggest a casual food court option. |
| 10.11 | *(after 10.10)* `can we get a private space or at least be seated together without being split up?` | `guided_recommendation` | high | Must prioritise venues with confirmed group seating. Must flag that advance booking is essential for groups. Must NOT recommend a walk-in-only venue without flagging it. |
| 10.12 | `we also want to find something fun to do after dinner — the bride wants to keep the celebration going` | `guided_recommendation` | medium | Must suggest celebratory post-dinner options: dessert experience, live entertainment, lounge area. Must be appropriate for a bridal celebration. Must NOT suggest going home or passive options. |
| 10.13 | `can you map out a full bridal party day plan?` | `hybrid_plan` | high | Must produce a well-sequenced full-day plan: shopping (bridal + bridesmaids) → beauty appointments → gift → celebration dinner → post-dinner activity. Must reflect all constraints: colour preference, budget for accessories, group beauty, premium dinner. Must be warm and celebratory in tone. |
| 10.14 | `one of the bridesmaids can't wear any fragrance — she's allergic — will that affect anything?` | `guided_recommendation` | high | Must acknowledge the allergy and advise caution in fragrance stores. Must NOT dismiss it. Should advise the bridesmaid to stay back during oud/perfume shopping or alert salon staff. Must NOT say "fragrances are usually fine." |

**Sample AI Responses:**

> **10.1** — "Congratulations! Three days to go — how exciting! Let's make today absolutely perfect. Between fashion for the rehearsal dinner, bridesmaids' accessories, beauty appointments, and a celebration dinner, you've got a full and fabulous day ahead. Shall we start with the bride's rehearsal outfit, or sort the bridesmaids first while you're all together?"

> **10.13** — "Here's your bridal party day:  
> **10:00 AM** — Bride: rehearsal dinner outfit shopping at [Fashion Store] (dusty rose options)  
> **10:00–11:00 AM** — Bridesmaids: coordinated accessories at [Accessories Store] (~200 SAR each)  
> **11:00 AM** — Group gift: premium oud at [Fragrance Boutique] (note: bridesmaid with allergy to wait outside)  
> **11:30 AM–1:30 PM** — [Beauty Salon] — blow-dry and makeup for all 5 (pre-booked, 2pm deadline met)  
> **2:00–2:30 PM** — Light lunch or coffee break while looking gorgeous  
> **3:00–5:00 PM** — Final browse, any last-minute additions  
> **7:00 PM** — Celebration dinner at [Elegant Restaurant] — private table for 5, pre-booked  
> **9:00 PM** — Post-dinner: [Dessert Experience / Lounge] to keep the evening going  
> You're going to have the best day. Congratulations to the bride! 💐"

> **10.14** — "Worth flagging — yes, this matters. When visiting the fragrance boutique for the oud gift, it's worth having [bridesmaid's name] wait outside or at a nearby café while the rest of you browse. For the beauty salon, make absolutely sure to inform the stylist in advance about the allergy — both for any finishing sprays and hairspray. Better to mention it when booking rather than on the day."

---

## Debug Fields Reference for Complex Scenarios

The following additional debug fields become important in multi-turn complex scenarios:

| Debug field | What to verify in complex scenarios |
|-------------|--------------------------------------|
| `constraint_stack` | All active constraints should accumulate across turns (e.g. `[vegan, high_protein, no_added_sugar]` by scenario 8.10) |
| `context_turn_depth` | Should increment with each turn; must NOT reset mid-conversation |
| `active_filters` | Must reflect all refinements applied so far |
| `plan_components` | For `hybrid_plan` responses — must list all distinct plan elements |
| `topic_lock` | Must persist even when visitor interleaves tangential questions |
| `persona_mode` | Should shift to `concierge` for scenarios 7 and 10; `guide` for scenario 3 |
| `group_context.size` | Must be set and maintained for all group scenarios (2, 5, 9, 10) |
| `occasion_context` | Must persist: `anniversary` (S1), `team_outing` (S2), `birthday` (S4), `wedding_related` (S10) |
| `budget_ceiling` | Must be tracked: 500 SAR (S6), 300 SAR total / 60 SAR pp (S9), 200 SAR pp accessories (S10) |
| `dietary_stack` | In complex scenarios: multiple dietary constraints may stack (vegan + no-added-sugar in S8) |

---

## Conversation Integrity Checklist

Run these checks after completing any complex scenario end-to-end:

- [ ] **Constraint persistence** — Every constraint introduced in turn N is still respected in turn N+8
- [ ] **No context reset** — The bot never forgets who the user is mid-conversation (companion, occasion, group size)
- [ ] **No hallucination under pressure** — When asked for specifics (prices, menu items, hours), the bot gives data or admits uncertainty — never invents
- [ ] **Tone consistency** — Luxury scenarios maintain concierge tone; teen scenario maintains casual tone; bridal scenario stays celebratory throughout
- [ ] **Budget guard** — Any suggestion after a budget is declared must respect that budget or explicitly flag the exception
- [ ] **Allergy/dietary hard stops** — Any suggestion that contradicts a stated dietary restriction or allergy is an immediate failure
- [ ] **Honest "I don't know"** — The bot must say it doesn't have specific information rather than speculate (custom cake lead times, salon group capacity, specific menu items)
- [ ] **Plan coherence** — In `hybrid_plan` responses across long conversations, all elements must be consistent with each other and with the full conversation history
