# Cenomi Mall Concierge — Implementation Guide

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite)   →   POST /api/chat                  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  FastAPI Application                                            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Concierge Service                                        │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  LangGraph StateGraph (11-node pipeline)            │  │  │
│  │  │                                                     │  │  │
│  │  │  load_session → interpret_turn → update_scene_memory│  │  │
│  │  │  → resolve_playbooks → choose_strategy              │  │  │
│  │  │  → compose_context → decide_retrieval               │  │  │
│  │  │  → fetch_exact_facts → generate_response            │  │  │
│  │  │  → update_memory → emit_debug_payload               │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────┐  ┌────────────────┐  ┌───────────────────┐  │
│  │ Mall Context   │  │ Session Store   │  │ Hallucination     │  │
│  │ Loader         │  │ (in-memory)    │  │ Guard             │  │
│  └───────┬───────┘  └────────────────┘  └───────────────────┘  │
│          │                                                      │
│  ┌───────▼────────────────────────────────────────────────┐     │
│  │  Mall Intelligence Layers                              │     │
│  │  ├── Canonical Entities (stores, dining, services)     │     │
│  │  ├── Semantic Intelligence (tags, audience fit, vibe)  │     │
│  │  └── Scenario Playbooks (pre-built response plans)     │     │
│  └────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
```

## Core Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Answer first** | Lead with concrete suggestions; only clarify when truly ambiguous |
| **Grounded in data** | Every store, restaurant, facility, and fact must come from canonical mall data |
| **Anti-hallucination** | Multi-layer defense: grounded prompts, canonical-only context, post-generation validation |
| **Concierge persona** | Warm, knowledgeable, like a friend walking you through the mall |
| **Stateful conversations** | Scene memory tracks companions, occasion, budget, active topic across turns |

---

## LangGraph Pipeline — Node-by-Node

### 1. `load_session`
- Loads or creates the visitor session from `SessionStore`
- Assigns a unique `turn_id`
- Normalizes the raw message (trim, collapse whitespace)
- Expands short queries (≤3 words) via `query_expander` for richer downstream matching

### 2. `interpret_turn`
- **Hybrid classifier**: rule-based fast path + LLM fallback
- Short/common queries resolved from static lookup tables (zero LLM cost)
- Keyword patterns match domain + sub_intent for mid-length queries
- LLM invoked only when rule confidence falls below threshold
- Outputs: `domain` (mall_info, dining, shopping, services, etc.), `sub_intent` (overview, prayer_room, gift_recommendation, etc.), `message_kind` (fresh_request, correction, refinement, followup, topic_switch)

### 3. `update_scene_memory`
- Extracts visitor context signals from the message
- Tracks: companions, occasion, budget, audience, current area
- Persists across turns for personalization

### 4. `resolve_playbooks`
- Matches the turn against pre-defined scenario playbooks
- Playbooks encode domain expertise (e.g., "gift for girlfriend" → jewelry + perfume + wrapping)
- Each playbook defines preferred tags, shortlist size, response shape, fallback rules

### 5. `choose_strategy`
- Maps intent + playbook → response strategy
- Strategies: `mall_overview`, `direct_fact`, `shortlist_recommendation`, `gift_formula`, `movie_plus_food`, `mini_itinerary`, `family_plan`, `budget_plan`, `exploration_overview`
- Each strategy has a shape hint (brief_answer, numbered_shortlist, curated_picks, etc.)
- Tenant config weights can modulate strategy selection

### 6. `compose_context`
- Selects relevant topic blocks (dining, gift, movie, family, services, mall_overview)
- Ranks and selects entities via playbook scoring or domain-based fallback
- Enriches entities with semantic profiles (audience fit, vibe, concierge notes)
- Builds semantic signal list from scene + entity tags

### 7. `decide_retrieval`
- Determines if exact fact retrieval is needed (hours, parking, showtimes, specific facilities)
- Most mall_info and general queries skip retrieval

### 8. `fetch_exact_facts`
- Executes targeted lookups when retrieval is flagged
- Returns structured facts (entity hours, movie listings, event details)

### 9. `generate_response`
Two response paths based on intent:

#### Mall Info Path (all `mall_info` domain queries)

```
mall_info/* → MallOverviewBlueprint (deterministic, from trusted data)
           → get_mall_overview_system_prompt (strict grounding rules)
           → LLM polish
           → Hallucination guard validation
```

- Covers: overview, opening_hours, facilities_summary, family_friendliness, what_is_available
- Sub-intent-specific focus hints steer the LLM to the relevant data section
- Fallback: deterministic `render_text()` if LLM fails

#### General Path (dining, shopping, services, entertainment, etc.)

```
canonical data → get_concierge_system_prompt (full tenant list + facilities)
              → user query + playbook + retrieval results
              → LLM generation
              → Hallucination guard validation
```

- System prompt includes the **complete tenant list** from canonical data
- Explicit instruction: "Do NOT reference any tenant not on this list"

### 10. `update_memory`
- Extracts mentioned entities from the response into the active shortlist
- Persists scene state for the next turn

### 11. `emit_debug_payload`
- Builds the debug trace (intent, playbook, strategy, retrieval, latency, node trace)
- Available in the frontend debug panel when debug mode is enabled

---

## Mall Intelligence Layers

### Layer 1: Canonical Entities

**Source:** `data/canonical/{mall_id}.json`

The single source of truth for all mall data.

| Section | Content |
|---------|---------|
| `mall_profile` | Name, city, address, floors, zones, landmarks, facilities, parking, operating hours, amenities |
| `stores` | Retail stores with location, category, price range, features, tags, audience |
| `dining` | Restaurants/cafés with cuisine, dining style, meal time, kids menu, private dining |
| `cinemas` | Cinema venues with screens, formats (IMAX, 4DX, Gold, Standard) |
| `movies` | Currently showing films with synopsis, ratings, showtimes, audience fit |
| `services` | Mall services (info desk, valet, currency exchange) with hours and pricing |
| `events` | Active events with dates, descriptions, locations |
| `offers` | Promotions with discount values, linked tenants, validity dates |

### Layer 2: Semantic Intelligence

**Source:** `data/semantic/{mall_id}.json`

Derived facts that enable concierge-level reasoning:

- **Semantic tags**: gift_experience, romantic, quick_bite, family_dining, dessert, cafe_hangout, luxury_shopping
- **Audience fit**: family_friendly, couple_friendly, solo_friendly, kid_friendly
- **Priority scores**: per-intent relevance (gift, dining, entertainment)
- **When to recommend**: contextual triggers for each entity
- **Concierge notes**: human-curated one-liners for natural conversation

### Layer 3: Scenario Playbooks

**Source:** `data/playbooks/{mall_id}.json`

Pre-built response strategies for common visitor intents:

| Playbook | Trigger | Strategy |
|----------|---------|----------|
| `pb-gift-girlfriend` | "gift for girlfriend" + couple companion | Jewelry → Perfume → Fashion, curated picks |
| `pb-family-visit` | "family" + kids companion | Kids activities → Family dining → Entertainment |
| `pb-romantic-dinner` | "date" + couple companion | Fine dining → Premium options → Dessert combo |
| `pb-quick-bite` | "hungry" + time pressure | Fast casual → Food court → Café |
| `pb-movie-night` | "movie" | Cinema options → Pre/post movie dining |
| `pb-budget-plan` | "budget" | Value-focused options across categories |

---

## Anti-Hallucination Architecture

### Defense Layer 1: Grounded System Prompt

The system prompt contains the **complete list** of every store, restaurant, service, and facility in the mall. The prompt explicitly states:

> "EVERY store, restaurant, service, facility, floor, zone, address, and hour listed below is REAL. Anything NOT listed here does NOT exist in this mall. Never invent or assume."

### Defense Layer 2: Mall Info Grounded Path

All `mall_info` domain queries (overview, hours, facilities, family-friendliness) bypass the general LLM path and use a **deterministic `MallOverviewBlueprint`** built exclusively from trusted canonical data. The LLM only provides natural-language polish over verified facts.

### Defense Layer 3: Hallucination Guard (Post-Generation)

After every LLM response, the `hallucination_guard` validates the text against the canonical data index:

| Check | What it catches |
|-------|----------------|
| Movie claims | Movie titles not in the current listings |
| Discount claims | Percentage discounts not backed by active offers |
| Promotion claims | Sale/deal references for stores without offers |
| Event claims | Event names not in the mall's event calendar |
| Showtime claims | Specific times not matching any listed movie |

Violations are automatically replaced with safe phrasing (e.g., "Check their listings for current showtimes").

### Defense Layer 4: Prompt-Level Constraints

- "NEVER invent store names, floor counts, addresses, or any other detail"
- "Do NOT draw on general knowledge about malls"
- "Every factual claim in your response must be traceable to the context provided"

---

## Context Injection Flow

```
Canonical JSON
    │
    ├── MallContextLoader.load()
    │       ├── MallNormalizer → CanonicalMallData
    │       ├── SemanticEnricher → SemanticProfiles
    │       └── PlaybookEngine → ScenarioPlaybooks
    │
    ├── build_context_pack() → GlobalContextPack
    │       ├── MallProfileBlock (structured overview)
    │       ├── TopicBlocks (dining, gift, movie, family, services)
    │       ├── OperationalContext (hours, parking)
    │       └── Events & Offers
    │
    ├── get_canonical_for_prompt() → flat dict for LLM
    │       ├── mall_profile (with zones, floors, facilities)
    │       ├── tenants (merged stores + dining + services + cinemas)
    │       ├── operational_context
    │       └── events_and_offers
    │
    └── Injected into system prompt via _format_mall_context()
```

---

## Session and State Management

### Session Data

| Field | Description |
|-------|-------------|
| `session_id` | Unique session identifier |
| `mall_id` | Active mall |
| `scene` | SceneMemory (companions, occasion, budget, current_area, audience, active_topic, shortlist, rejected_options) |
| `last_intent` | Previous turn's intent domain |
| `conversation_mode` | Current conversation mode |
| `turn_count` | Number of turns in this session |

### State Graph (`ConciergeState`)

Each turn flows through the LangGraph pipeline with a shared state object containing:

- **Session metadata**: session_id, tenant_id, mall_id, turn_id
- **Input**: raw_user_message, normalized_user_message, expanded_query
- **Classification**: InterpretedIntent (domain, sub_intent, message_kind, confidence)
- **Scene**: SceneMemory (companions, occasion, budget, etc.)
- **Playbook resolution**: selected_playbook, confidence, matched_playbooks
- **Context composition**: selected_topic_blocks, selected_entities, semantic_signals
- **Retrieval**: retrieval_needed, retrieval_results
- **Response plan**: chosen_strategy, response_shape_hint, constraints
- **Output**: final_response_text, messages, debug payload

---

## Intent Classification

### Domains and Sub-Intents

| Domain | Sub-Intents |
|--------|-------------|
| `mall_info` | overview, facilities_summary, opening_hours, family_friendliness, what_is_available |
| `exploration` | open_exploration, activity_suggestion, first_visit_guide |
| `dining` | general_dining, romantic_dining, quick_bite, family_dining, cafe_recommendation, dessert_recommendation |
| `shopping` | general_shopping, gift_recommendation, fashion_shopping |
| `entertainment` | general_entertainment, movie_showtime |
| `services` | store_hours, parking_info, service_info, prayer_room |
| `navigation` | location_query |
| `general` | general_inquiry |

### Classification Priority

1. **Smalltalk detection** (greetings, thanks, goodbye) → static responses, no LLM
2. **Rule-based keyword matching** → zero LLM cost, high confidence for common patterns
3. **LLM fallback** → only when rules fall below confidence threshold

---

## Response Strategy Matrix

| Strategy | Shape | When Used |
|----------|-------|-----------|
| `mall_overview` | Structured overview with bullets | Mall-level questions (about, hours, facilities) |
| `direct_fact` | 1-3 sentence answer | Specific factual questions (prayer room, parking, hours) |
| `shortlist_recommendation` | Numbered/grouped list | Category browsing (dining, shopping, entertainment) |
| `gift_formula` | Curated picks by category | Gift recommendations |
| `movie_plus_food` | Combo suggestion | Movie + dining plans |
| `mini_itinerary` | Step-by-step plan | Family visits, multi-stop plans |
| `family_plan` | Structured itinerary | Family-specific visit plans |
| `budget_plan` | Value-focused list | Budget-conscious visitors |
| `exploration_overview` | Casual mini-itinerary | Vague "what can I do" queries |

---

## API Contract

### `POST /api/chat`

**Request:**

```json
{
  "message": "Tell me about the mall",
  "session_id": "optional-session-id"
}
```

**Response:**

```json
{
  "session_id": "session-abc123",
  "message": "Cenomi Mall is a shopping destination in Riyadh...",
  "session_state": {
    "turn_count": 1,
    "active_topic": "mall_info",
    "companions": [],
    "occasion": "",
    "budget": "",
    "active_shortlist": ["Information Desk"]
  },
  "sources": [],
  "suggestions": [],
  "debug": {
    "intent": { "domain": "mall_info", "sub_intent": "overview" },
    "playbook": null,
    "strategy": "mall_overview",
    "retrieval": { "needed": false },
    "latency_ms": 4200,
    "node_trace": ["..."]
  }
}
```

### Other Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/session/{session_id}` | Get session state |
| `POST` | `/api/session/reset` | Reset session |
| `POST` | `/api/feedback` | Submit thumbs up/down feedback |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.11+, FastAPI, Uvicorn |
| Pipeline | LangGraph StateGraph |
| LLM | OpenAI GPT-4o (configurable model, temperature) |
| Models | Pydantic v2 |
| Sessions | In-memory (LRU, max 1000) |
| Frontend | React 19, Vite 7, TypeScript 5.9, Tailwind v4 |
| Data | JSON files (canonical, semantic, playbooks) |

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI entry point, lifespan initialization |
| `app/runtime.py` | Global singletons (mall context, session store) |
| `app/graph/builder.py` | LangGraph pipeline compilation |
| `app/nodes/generate_response.py` | LLM prompt assembly and response generation |
| `app/nodes/interpret_turn.py` | Hybrid intent classifier |
| `app/nodes/compose_context.py` | Entity selection and context building |
| `app/nodes/choose_strategy.py` | Strategy selection rules |
| `app/context/mall_context.py` | Mall data loader and entity lookup |
| `app/services/context_builder.py` | Context pack assembly from data layers |
| `app/services/concierge.py` | Chat turn orchestration |
| `app/prompts/builder.py` | Full prompt construction (identity, context, scene) |
| `llm/prompts/concierge_prompt.py` | Concierge system prompt with grounding rules |
| `guardrails/hallucination_guard.py` | Post-generation hallucination validation |
| `response/concierge_composer.py` | Deterministic response blueprints |
| `intent/query_classifier.py` | Rule-based intent classification |
