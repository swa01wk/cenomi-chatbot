# Cenomi Chatbot — Capabilities & Roadmap
**Product:** Cenomi AI Mall Concierge  
**Version:** v1.6  
**Date:** March 2026  
**Status:** Production-Ready

---

## Overview

The Cenomi AI Mall Concierge is a conversational AI assistant embedded at the point of visit — on digital kiosks, web portals, and mobile touchpoints — that helps mall visitors find what they need, plan their visit, and discover experiences they might otherwise miss. It speaks the language of the visitor, understands context, and responds like a knowledgeable human concierge would.

Powered by a Large Language Model (LLM) pipeline built on a directed graph architecture, the concierge adapts its behaviour to each query: it looks up exact facts when precision matters, offers curated recommendations when context is richer, and builds itineraries when a visitor needs to plan their whole visit.

---

## What the Chatbot Can Do Today

### 1. Factual Lookups — Instant, Precise Answers

The chatbot has complete factual knowledge of each configured mall and answers direct questions immediately.

| Capability | Example Queries |
|------------|----------------|
| Store locations | "Where is Zara?", "How do I get to Swarovski?" |
| Operating hours | "What time does the mall close?", "Is it open on Fridays?" |
| Cinema listings | "What movies are showing?", "Any kids' movies today?", "What time is the first show?" |
| Services & facilities | "Where is the prayer room?", "Is there an ATM?", "Do you have wheelchairs?" |
| Parking | "Is parking free?", "Where can I park near Gate 3?" |
| Brand availability | "Is Nike here?", "Do you have H&M?", "Is there a Starbucks?" |
| Cross-mall brand search | "Which Cenomi malls have Zara?", "Where can I find a cinema nearby?" |
| Mall overview | "Tell me about this mall", "What's here?", "Is this mall family-friendly?" |

**Accuracy:** Responses draw directly from the mall's structured data store — store units, floor numbers, zone names, and directions are precise.

---

### 2. Guided Recommendations — Personalised Suggestions

When a visitor has an intent but needs help narrowing it down, the chatbot acts as a curator — filtering, ranking, and presenting the best-fit options for their context.

| Capability | Example Queries |
|------------|----------------|
| Dining recommendations | "Where should we eat?", "Something nice for a date", "Quick food before the movie" |
| Shopping by category | "Where can I find shoes?", "I need a jacket", "Looking for perfume as a gift" |
| Budget filtering | "Something affordable", "My budget is 300 SAR", "Not too expensive" |
| Audience-aware suggestions | "For my 5-year-old son", "She likes bags", "Trendy options for teens" |
| Occasion-driven picks | "I'm a bridesmaid", "It's our anniversary", "Shopping for back to school" |
| Constraint refinement | "Actually, she's vegan", "Something that isn't too loud", "More casual" |
| Correction handling | "Actually I'm alone, no kids", "I meant for a girl, not a boy" |

The chatbot **remembers prior turns** in a session — so "something more affordable" after a shopping recommendation correctly applies the budget filter to the prior suggestion, without the visitor re-explaining.

---

### 3. Multi-Domain Planning — Full Visit Itineraries

For visitors planning a complete outing, the chatbot composes structured plans that span multiple activities, zones, and timings.

| Capability | Example Queries |
|------------|----------------|
| Movie + meal plans | "Food and movies", "We want to catch a movie and then eat" |
| Timed visit plans | "I have 2 hours — what should I prioritise?", "Map out a 4-hour family visit" |
| Pre / post movie routing | "Something quick before the movie starts", "Dessert after the film" |
| Occasion itineraries | "Can you give me a full itinerary for tonight?", "Plan our anniversary evening" |
| Group outings | "Team outing for 10 people — food and activities", "Fun for 5 teens on a budget" |
| Shopping day plans | "I need a 2.5-hour back-to-school shopping route" |
| Bridal party planning | "Map out a full bridal party day" |

Plans are structured, sequenced, and anchored to real zones and stores — not generic advice.

---

### 4. Context Awareness — Understands Who You Are

The chatbot continuously builds a scene model of the visitor's situation and uses it to tailor every subsequent response.

| Context Signal | How It's Used |
|----------------|---------------|
| Companions (family, kids, group, couple) | Adjusts recommendations for appropriate age groups, dining style, activity type |
| Occasion (anniversary, birthday, wedding, team outing) | Selects the right tone, venue suggestions, and experience anchors |
| Budget declared ("around 300 SAR") | Filters all subsequent recommendations to budget-appropriate options |
| Target person ("it's for my 7-year-old daughter") | Scopes suggestions to the intended recipient |
| Excluded domains ("no food", "skip dining") | Removes that domain from all further suggestions |
| Prior refinements | Preserved across turns so the visitor never has to repeat themselves |

---

### 5. Smalltalk & Conversational Handling

The chatbot handles the full range of conversational turns a real concierge would encounter.

| Category | Examples |
|----------|---------|
| Greetings | "Hi", "Hello", "Hey" — receives a warm, context-setting welcome |
| Gratitude | "Thank you", "Shukran" — acknowledged naturally with a soft next-step offer |
| Farewells | "Goodbye", "See you" — warm close with invitation to return |
| Identity questions | "Who are you?", "Are you a real person?", "Are you a bot?" — honest, reassuring response |
| Emotional turns | "I'm so bored", "I feel overwhelmed" — empathetic acknowledgement, gentle redirection |
| Crisis language | Detected and handled with care — no clinical flags, no panic, calm supportive response |
| Off-topic queries | "What's the weather?", "Can you book a taxi?" — gracefully declined with a redirect to what the chatbot can help with |
| Garbage input | "asdf", "a" — recovers gracefully and offers to help |

---

### 6. Multi-Mall Support & Cross-Mall Search

The chatbot is configured for **5 Cenomi mall properties** and supports both single-mall conversations and cross-mall brand queries.

| Mall | City | Stores | Dining | Cinema |
|------|------|--------|--------|--------|
| Al Nakheel Plaza (Buraidah) | Buraidah | 83 | 10 | ✅ Muvi |
| Al Nakheel Plaza (Riyadh) | Riyadh | 83 | 10 | ✅ Muvi |
| Al Ahsa Mall | Al Ahsa | 85 | 12 | ✅ Muvi |
| The View Mall | Riyadh | 125 | 28 | ✅ Muvi |
| Al Nakheel Mall | Riyadh | 219 | 37 | ✅ Muvi |
| Mall of Arabia | Riyadh | 242 | 44 | ✅ Muvi |

**Cross-mall queries** ("Which Cenomi malls have Starbucks?") are answered with a ranked list spanning all configured properties.

---

### 7. Observability & Feedback

Each conversation generates rich observability data used to improve the system over time.

- **Debug payload** on every response: response mode, confidence level, active playbook, flow type, retrieval status, latency per node
- **Implicit feedback detection:** Detects signals like topic switches, refinements, and repeated requests to identify satisfaction signals
- **Feedback normalisation:** Collected feedback is normalised for quality analysis
- **Session memory:** Full conversation history preserved within a session; the concierge never loses track of what was said

---

## How It Works (Technical Summary for Stakeholders)

The chatbot uses a **12-node LLM-first pipeline** built on LangGraph:

1. **Interpret Turn** — LLM classifies the visitor's intent, extracts scene context (companions, occasion, budget), and selects a response strategy
2. **Route Flow** — Decides between the factual path (exact data lookup) and the concierge path (recommendation/planning)
3. **Resolve Playbooks** — Matches the intent to pre-defined experience playbooks (28 per mall) that guide the response shape
4. **Compose Context** — Assembles the relevant stores, dining options, services, and facts into a prioritised context window
5. **Rank & Dedupe** — Scores and orders entities by relevance to the visitor's stated intent, companions, and constraints
6. **Generate Response** — LLM generates a fluent, concierge-quality reply grounded in the composed context
7. **Update Scene Memory** — Captures new context signals (new companions, excluded domains) for use in subsequent turns

**LLM used:** GPT-4o-mini (OpenAI) — balances response quality with cost and speed  
**Average response time:** 7.5 seconds end-to-end  
**Data freshness:** Mall data updated through a structured ingest pipeline; movies, offers, and events reflect the canonical data snapshot

---

## Playbook Library (28 Scenarios per Mall)

Each mall is configured with 28 response playbooks — pre-defined scenario templates that govern the recommendation strategy, entity selection, and response shape for common visitor journeys:

| Category | Playbooks |
|----------|-----------|
| Family visits | Family visit plan, family shopping with child, child activity while parents shop, kids entertainment plan, family movie plan |
| Dining | Quick lunch, quick snack, activity before/after movie |
| Shopping | Category shopping, brand store lookup, luxury shopping plan, last-minute gift, quick errand shopping |
| Entertainment | Movie showtime lookup, movie night plan, movie + food plan, kid-friendly movie + food |
| Planning | Date plan, anniversary plan, mall overview, route proximity refinement |
| Gifts | Gift for girlfriend, gift for family |
| Budget-conscious | Budget family outing |
| Solo | Solo visit plan |
| Combinations | Shopping + dessert combo |

---

## Current Limitations

The following are known boundaries of the current version:

| Area | Current Behaviour |
|------|-------------------|
| Language | English only; Arabic is not yet supported |
| Pricing data | The chatbot cannot confirm live prices — it guides visitors to the right stores |
| Real-time inventory | Stock availability is not integrated; based on tenant listing only |
| Promotions | Active promotions are shown when present in the data; not live-synced |
| Booking / transactions | The chatbot is informational only — it cannot make reservations or process payments |
| Third-party services | Cannot call taxis, order food delivery, or perform external actions |
| Image / video | Text-only interface in current form |

---

## In Progress (Q2 2026)

These features are actively in development or scoped for the next sprint:

| Feature | Description |
|---------|-------------|
| **Arabic language support** | Full RTL Arabic-language responses, with cultural tone adaptation for Saudi visitors |
| **Live promotions sync** | Real-time pull from the tenant promotion feed — offers reflected within hours of going live |
| **Enhanced personalisation** | Returning visitor recognition — remembers stated preferences across sessions |
| **Richer movie data** | Showtimes, ratings, trailers linked — not just title and genre |
| **Feedback loop** | Explicit thumbs-up/down feedback surfaced to the visitor; fed back into ranking |
| **Admin dashboard** | Real-time view of conversation volumes, top queries, and satisfaction signals per mall |
| **Additional malls** | Onboarding pipeline validated for 3+ new Cenomi properties |

---

## Future Enhancements (v2.0 Roadmap)

These capabilities are planned but not yet scoped for development:

| Feature | Description |
|---------|-------------|
| **Voice interface** | Speak your query at a kiosk — response read aloud in Arabic or English |
| **Personalised loyalty integration** | Cenomi loyalty card data to personalise recommendations ("You usually shop at Zara — they have a new collection") |
| **Turn-by-turn navigation** | In-mall mapping integrated with chatbot responses — "Turn left at Gate 2, then 30m ahead" |
| **Wish list & session save** | Visitor can save a list of stores to visit; sent to their phone via QR code |
| **Event-driven promotions** | The chatbot proactively surfaces relevant events — "There's a Flash Sale at Centrepoint today" |
| **Multi-modal input** | Photo recognition — "What is this store?" from a photo |
| **B2B concierge mode** | Corporate and group booking management — team outings, event space inquiries |
| **Sentiment analytics** | Real-time dashboard showing sentiment trends across malls — mood-of-the-mall heatmap |
| **White-label SDK** | Package the concierge pipeline as a configurable SDK deployable for any retail/hospitality brand |

---

## Quality Standards

The system is designed to the following principles:

- **LLM-first, rule-free:** All intent classification, context extraction, and response generation is driven by the LLM — no regex, no keyword matching, no hardcoded rules
- **Graceful degradation:** Every error case has a safe fallback — the chatbot never crashes or returns an empty response
- **Privacy-first:** No personally identifiable information is stored; session state is ephemeral and keyed to an anonymous session ID
- **Auditability:** Every response includes a debug envelope capturing the full decision chain — what flow was taken, which playbook matched, what confidence was assigned

---

*Cenomi AI Mall Concierge — v1.6 — March 2026*  
*For technical queries, contact the Engineering Team.*
