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
│  │  │  LangGraph StateGraph (12-node pipeline)            │  │  │
│  │  │                                                     │  │  │
│  │  │  load_session → interpret_turn → update_scene_memory│  │  │
│  │  │  → resolve_playbooks → choose_strategy              │  │  │
│  │  │  → compose_context → rank_and_dedupe                │  │  │
│  │  │  → decide_retrieval → fetch_exact_facts             │  │  │
│  │  │  → generate_response → update_memory                │  │  │
│  │  │  → emit_debug_payload                               │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────┐  ┌────────────────┐  ┌───────────────────┐  │
│  │ Mall Context   │  │ Session Store   │  │ Hallucination     │  │
│  │ Loader         │  │ (in-memory)    │  │ Guard             │  │
│  └───────┬───────┘  └────────────────┘  └───────────────────┘  │
│          │                                                      │
│  ┌───────▼──────────────────────────────────────────────────┐   │
│  │  Mall Intelligence Layers (5 layers per mall)            │   │
│  │  ├── Canonical      (authoritative facts, entities)      │   │
│  │  ├── Semantic       (tags, audience fit, enrichment)     │   │
│  │  ├── Playbooks      (scenario-aware response strategies) │   │
│  │  ├── Context Packs  (LLM-ready topic blocks)             │   │
│  │  └── Tenant Config  (behavior tuning, retrieval rules)   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Core Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Answer first** | Lead with concrete suggestions; only clarify when truly ambiguous |
| **Grounded in data** | Every store, restaurant, facility, and fact must come from canonical mall data |
| **Scenario-first data** | For every important user scenario, explicit supporting data must exist in the store |
| **Anti-hallucination** | Multi-layer defense: grounded prompts, canonical-only context, post-generation validation |
| **Concierge persona** | Warm, knowledgeable, like a friend walking you through the mall |
| **Stateful conversations** | Scene memory tracks companions, occasion, budget, active topic across turns |
| **Cross-mall consistency** | Canonical entity IDs and brand identity are stable and reusable across malls |

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
- **Query normalization** (`normalize_query_with_pattern`): semantically equivalent variants ("now showing", "what's playing", "what can i watch") are mapped to a canonical form before classification; also emits a `canonical_query_pattern` label (e.g., `"movie_lookup"`) for debug tracing
- **Unsupported input detection** (`is_likely_unsupported`): gibberish, keyboard mashing (e.g., `"asdf"`), and high-consonant/low-vowel strings are detected early; the node sets `is_unsupported=True` and bypasses the LLM path entirely. **Important:** The character-diversity check (`_MIN_UNIQUE_CHAR_RATIO`) only fires on inputs with ≤ 4 tokens — natural English sentences always have low unique-char ratios (~0.25–0.35) due to repeated common letters, so applying the check to longer text causes false positives on valid multi-word queries.
- **Brand misspelling correction** (`maybe_correct_brand`): fuzzy-matches common brand misspellings (e.g., `"nkie"` → `"Nike"`) and injects a correction hint into the debug payload
- Outputs: `domain`, `sub_intent`, `message_kind` (fresh_request, correction, refinement, followup, topic_switch, **context_setting**)
- **Interpretation contract** emitted on every turn (stored in `debug_enrichment`):
  ```json
  {
    "primary_intent": "",
    "scenario": "",
    "modifiers": [],
    "message_kind": "",
    "flow_type": "",
    "active_topic": "",
    "fact_scope": "",
    "normalized_query": "",
    "canonical_query_pattern": "",
    "topic_lock": false,
    "topic_lock_confidence": 0.0
  }
  ```

### 3. `update_scene_memory`
- Extracts visitor context signals from the message
- Tracks: companions, occasion, budget, audience, current area, active topic
- Persists across turns for personalization
- Scene signals feed directly into playbook resolution and entity ranking
- **`_infer_goal` uses word-boundary matching** (`\bsignal\b` regex) when checking `_GOAL_SIGNALS`. Bare substring matching was causing false positives (e.g. `"eat"` matching inside `"weather"`, setting `scene.goal = "dining"` on an off-topic first turn). Word boundaries prevent these contamination cases.

### 4. `resolve_playbooks`
- Matches the turn against pre-defined scenario playbooks using `trigger_domains`, `trigger_sub_intents`, and `required_scene_signals`
- Each playbook defines preferred tags, ranking boosts/penalties, must-include entity types, and response shape
- Movie/cinema intent playbooks carry override priority to prevent shopping drift
- Before/after movie playbooks bias toward `near_cinema` and `quick_stop` entities

### 5. `choose_strategy`
- Maps intent + playbook → response strategy
- Strategies: `mall_overview`, `direct_fact`, `shortlist_recommendation`, `gift_formula`, `movie_plus_food`, `mini_itinerary`, `family_plan`, `budget_plan`, `exploration_overview`, `guided_plan`, `exact_retrieval`, `proximity_guided_shortlist`
- Tenant config `strategy_weights` and `behavior_rules` can override strategy selection
- `intent_override_priority` in tenant config ensures movie and service queries win over shopping

### 6. `compose_context`
- Selects relevant topic blocks from context packs (dining, gift, movie, family, services, mall_overview, cinema_and_movies, before_movie, after_movie, kids_visit, date_night, quick_visit)
- Applies playbook `ranking_boost_tags` and `ranking_penalty_tags` to entity selection
- Enriches entities with semantic profiles (audience fit, vibe, concierge notes, routing_tags)
- Builds semantic signal list from scene + entity tags for downstream scoring

### 7. `rank_and_dedupe`
- Scores entities using audience fit overlap, semantic tag overlap, usefulness scores, and diversity
- Applies playbook `entity_cap` to prevent overlong shortlists
- Deduplicates by **normalized canonical name** (case-insensitive, punctuation-stripped, article-stripped, unicode-normalized); also deduplicates by `entity_id` when available
- `must_include_entity_types` forces inclusion of required anchors (e.g., `kids_entertainment` for family scenarios)
- Filters out entities matching `excluded_tags` or `ranking_penalty_tags`
- Emits `canonical_name_normalization_notes` to `debug_enrichment` listing which entity name variants were collapsed and why
- Emits `retrieval_discipline_reason` explaining whether factual retrieval was applied to this entity set

### 8. `decide_retrieval`
- Determines if exact fact retrieval is needed (showtimes, specific facilities, service locations, offer details, brand presence checks)
- Tenant config `retrieval` rules specify strategy per intent type (exact_retrieval, tag_filtered, proximity_filtered)
- Movie showtime queries always trigger exact retrieval from `canonical.movies`
- Populates `retrieval_discipline_reason` in `debug_enrichment` explaining the retrieval decision (e.g., `"factual sub-intent 'offer_details' requires exact data"` vs. `"concierge flow — retrieval not required"`)
- Factual sub-intents that always trigger retrieval: `movie_showtime`, `opening_hours`, `store_hours`, `location_query`, `service_info`, `prayer_room`, `parking_info`, `cross_mall_search`, `offer_details`, `brand_availability`

### 9. `fetch_exact_facts`
- Executes targeted lookups when retrieval is flagged
- Returns structured facts (movie listings with status, service entity details)
- Filtered by status=SHOWING_NOW for movie queries

### 10. `generate_response`
Three response paths based on intent:

#### Unsupported Recovery Path (`is_unsupported = True`)

```
is_unsupported flag → _build_unsupported_recovery_response()
                    → deterministic fallback message (no LLM call)
                    → optional brand correction hint if maybe_correct_brand matched
```

- Fires before any LLM call when `interpret_turn` detected gibberish or a random input
- If `maybe_correct_brand` found a likely correction, the response surfaces it: *"Did you mean Nike?"*
- If no correction is available, the response offers common query suggestions
- Prevents hallucination on malformed inputs entirely

#### Mall Info Path (all `mall_info` domain queries)

```
mall_info/* → MallOverviewBlueprint (deterministic, from trusted data)
           → get_mall_overview_system_prompt (strict grounding rules)
           → LLM polish
           → Hallucination guard validation
```

- Covers: overview, opening_hours, facilities_summary, family_friendliness, what_is_available
- `overview_summary` and `facilities_summary` in canonical `mall_profile` provide pre-built concise descriptions
- Sub-intent-specific focus hints steer the LLM to the relevant data section

#### General Path (dining, shopping, services, entertainment, etc.)

```
canonical data → get_concierge_system_prompt (full tenant list + facilities)
              → user query + playbook + retrieval results + ranked entities
              → LLM generation
              → Hallucination guard validation
```

- System prompt includes the **complete tenant list** from canonical data
- Explicit instruction: "Do NOT reference any tenant not on this list"
- **Offer/deal honesty guard**: when `sub_intent == "offer_details"`, the LLM prompt explicitly instructs: *"If no offer data is present in the context, say so honestly — do NOT invent promotions or discount values"*

### 11. `update_memory`
- Extracts mentioned entities from the response into the active shortlist
- Persists scene state for the next turn
- Carries movie context and family context across turns when `session_adaptation` flags are set
- **Topic lock persistence**: establishes, reinforces, or clears `topic_lock` and `topic_lock_confidence` in `SceneMemory` based on message kind and intent continuity — follow-up turns increase confidence; explicit topic switches clear it
- **Context-setting tracking**: records `last_context_setting_turn` (the turn index at which the user last provided scene context such as companions, occasion, or visit type)
- Persists `last_selected_playbook` and `last_response_experience_mode` for warm-start continuity on the next turn

### 12. `emit_debug_payload`
- Builds the debug trace (intent, playbook, strategy, retrieval, latency, node trace)
- Available in the frontend debug panel when debug mode is enabled

---

## Mall Intelligence Layers

All layers live under `backend/data/` and are keyed by `mall_id`.

### Layer 1: Canonical (`data/canonical/{mall_id}.json`)

The single authoritative source of truth for all mall facts.

| Section | Content |
|---------|---------|
| `mall_profile` | Name, city, address, floors, zones, landmarks, facilities, operating hours, amenities, `overview_summary`, `facilities_summary` |
| `stores` | Retail stores — entity_id, canonical_name, category, subcategory, floor, zone, unit_number, directions_hint, price_range, brand_id |
| `dining` | Restaurants/cafés — cuisine_type, dining_style, halal_certified, has_kids_menu, average_meal_time_minutes |
| `cinemas` | Cinema venues — operator, formats_available, booking_url |
| `movies` | Currently showing films — title, genre, duration, language, synopsis, status (SHOWING_NOW / COMING_SOON), cinema_entity_id, booking_url |
| `services` | Mall services — service_category (prayer_room, atm, family, accessibility, information), floor, zone |
| `events` | Active events with dates, descriptions, locations |
| `offers` | Brand promotions — discount values, linked tenants, validity dates |

**Key requirements:**
- `entity_id` must be stable and consistent (used for cross-layer linking)
- `overview_summary` and `facilities_summary` must be concise and LLM-ready
- Movie data must be rich enough to support showtime lookup without hallucination
- Cross-mall brand identity is preserved via `brand_id`

### Layer 2: Semantic (`data/semantic/{mall_id}.json`)

Derived enrichment that enables concierge-level reasoning.

**Structure:**
```json
{
  "mall_id": "...",
  "tag_taxonomy_version": "1.0",
  "enrichment_rules": [ ... ],
  "enrichment_rules_v2": [ ... ],
  "profiles": [ ... ]
}
```

**`enrichment_rules`** — Rule-based bulk enrichment by category/zone/dining_style. Applied at load time to seed initial tags.

**`enrichment_rules_v2`** — Scenario-specific override rules. Key rule sets:
- `er-cinema-zone` — Cinema entities → `entertainment_anchor`, `showtime_lookup`, `movie_night`, scenario priority boosts
- `er-before-movie-foodcourt` — Food Court dining → `near_cinema`, `before_movie`, `quick_stop`
- `er-before-movie-kiosk` — Kiosk Row snacks → `near_cinema`, `before_movie`, `grab_and_go`
- `er-after-movie-reward` — Dessert/cafe entities → `after_movie`, `reward_stop`
- `er-kids-entertainment-plan` — Kids Entertainment → `child_relief_anchor`, `entertainment_anchor`, `stroller_friendly`
- `er-movie-kids-animation` *(al_nakheel_plaza_13 only)* — Animation films → `kid_friendly`, `family_movie`

**`profiles`** — Per-entity semantic entries. Each profile includes:
- `semantic_tags`, `audience_fit`, `vibe`, `price_band`
- `outing_fit` — scenario-level fit (movie_night_plan, family_shopping_with_child, etc.)
- `gift_fit`, `meal_fit`, `routing_tags`
- `now_showing_summary` *(cinema entities)* — current lineup snapshot
- `when_to_recommend`, `avoid_if` — contextual guidance
- `priority_scores` — per-scenario numerical weights
- `concierge_notes` — specific, entity-level one-liners for natural conversation

**Core tag taxonomy:**

| Tag Group | Key Tags |
|-----------|---------|
| Audience | `family_friendly`, `kid_friendly`, `couple_friendly`, `solo_friendly`, `group_friendly`, `teen_friendly`, `parent_friendly` |
| Movie/Entertainment | `entertainment_anchor`, `cinema_now`, `showtime_lookup`, `movie_night`, `family_movie`, `animation`, `adult_movie`, `date_night` |
| Routing/Timing | `near_cinema`, `before_movie`, `after_movie`, `quick_stop`, `grab_and_go`, `low_commitment`, `reward_stop` |
| Family/Kids | `child_relief_anchor`, `kids_entertainment`, `stroller_friendly`, `supervised_activity` |
| Shopping | `gift_friendly`, `premium`, `budget`, `practical_shopping`, `occasion_wear`, `fashion_forward` |
| Date/Gift | `romantic`, `special_occasion`, `experience_gift` |

### Layer 3: Playbooks (`data/playbooks/{mall_id}.json`)

Scenario-aware response strategy plans. Each mall has 28 playbooks.

**Playbook schema (v2):**
```json
{
  "playbook_id": "pb-movie-showtime-lookup",
  "name": "Movie Showtime Lookup",
  "description": "...",
  "trigger_domains": ["entertainment"],
  "trigger_sub_intents": ["movie_showtime"],
  "required_scene_signals": [],
  "optional_scene_signals": ["companions", "date_night"],
  "required_semantic_signals": ["cinema_now"],
  "ranking_boost_tags": ["cinema_now", "showtime_lookup", "entertainment_anchor"],
  "ranking_penalty_tags": ["shopping", "quick_stop"],
  "excluded_tags": [],
  "preferred_response_shape": "direct_fact",
  "preferred_strategy": "exact_retrieval",
  "entity_cap": 1,
  "must_include_entity_types": ["cinema"],
  "fallback_entity_types": [],
  "itinerary_template": null,
  "retrieval_triggers": ["canonical.movies", "canonical.cinemas"],
  "priority": 1
}
```

**28 playbooks per mall:**

| Playbook ID | Scenario | Strategy |
|-------------|---------|----------|
| `pb-mall-overview` | Tell me about the mall | `mall_overview` |
| `pb-brand-lookup` | Where is Zara / do you have X | `direct_lookup` |
| `pb-category-shopping` | I want shoes / fashion stores | `shortlist_recommendation` |
| `pb-family-shopping` | Family with kids, want to shop | `guided_plan` |
| `pb-solo-browse` | Solo visitor browsing | `shortlist_recommendation` |
| `pb-gift-girlfriend` | Gift for girlfriend | `gift_formula` |
| `pb-gift-family` | Family gift | `gift_formula` |
| `pb-family-food` | Family dining | `shortlist_recommendation` |
| `pb-quick-bite` | Quick snack/lunch | `shortlist_recommendation` |
| `pb-movie-showtime-lookup` | What movies are playing | `exact_retrieval` |
| `pb-movie-night` | Movie night plan | `movie_plus_food` |
| `pb-family-movie` | Family movie plan | `guided_plan` |
| `pb-kids-entertainment` | Something for kids | `shortlist_recommendation` |
| `pb-movie-food` | Movie + dining combo | `movie_plus_food` |
| `pb-kid-movie-food` | Kid movie + family dining | `guided_plan` |
| `pb-date-night` | Date night | `mini_itinerary` |
| `pb-shopping-dessert` | Shopping + dessert | `mini_itinerary` |
| `pb-budget-family` | Budget family outing | `budget_plan` |
| `pb-luxury-shopping` | Premium / luxury shopping | `shortlist_recommendation` |
| `pb-anniversary` | Anniversary plan | `mini_itinerary` |
| `pb-last-minute-gift` | Last-minute gift | `gift_formula` |
| `pb-child-activity` | Child activity while parents shop | `guided_plan` |
| `pb-service-lookup` | ATM, stroller, prayer room | `direct_fact` |
| `pb-route-refinement` | Closer to cinema / nearby X | `proximity_guided_shortlist` |
| `pb-quick-snack` | Quick snack before movie | `proximity_guided_shortlist` |
| `pb-mall-overview` | Mall overview | `mall_overview` |
| `pb-teen-hangout` | Teen/group hangout | `shortlist_recommendation` |
| `pb-exploration` | First visit, open exploration | `exploration_overview` |

### Layer 4: Context Packs (`data/context_packs/{mall_id}_context.json`)

Compact, LLM-ready topic blocks for `compose_context`. These allow retrieval-free composition for common scenarios.

**Topic blocks per mall:**

| Block | Contents |
|-------|---------|
| `mall_overview` | Name, address, floors, zones summary, highlights, opening hours |
| `shopping_overview` | Key stores by category, anchor brands, fashion/beauty/jewelry highlights |
| `dining_overview` | Dining by zone, quick bites, cafes, dessert options |
| `services` | ATM, stroller, wheelchair, prayer rooms, info desk — locations |
| `entertainment` | Cinema details, kids entertainment anchors |
| `cinema_and_movies` | Current lineup, SHOWING_NOW vs COMING_SOON, booking URL, pairing suggestions |
| `family_visit` | Family-friendly stores, dining, entertainment anchors, itinerary template |
| `kids_visit` | Kids entertainment anchor, safe dining, proximity to parents' shopping |
| `gift_ideas` | Gift categories by recipient (girlfriend, family, last-minute) |
| `quick_visit` | Quick-stop stores, kiosk row, food court — low-commitment options |
| `date_night` | Romantic options, dining, cinema pairing, gift suggestions |
| `before_movie` | Quick food options near cinema, grab-and-go, Food Court and Kiosk Row |
| `after_movie` | Dessert and cafe options, reward stops near cinema |

### Layer 5: Tenant Config (`data/tenant_config/{mall_id}.json`)

Mall-specific behavioral tuning and retrieval rules.

**Config structure (v2):**

```json
{
  "mall_id": "...",
  "config_version": "2.0.0",
  "tone": { "warmth", "directness", "formality", "concierge_confidence", ... },
  "clarification": { "ambiguity_tolerance", "answer_first_bias", ... },
  "behavior_rules": {
    "movie_intent_override": { "enabled", "trigger_signals", "preferred_strategy", ... },
    "kids_movie_redirect": { "enabled", "redirect_entity_id", ... },
    "service_lookup": { "entity_cap": 2, "preferred_strategy": "direct_fact" },
    "before_movie_quick_stop": { "preferred_zones": ["Food Court", "Kiosk Row"], ... },
    "family_guided_plan": { "must_include_entity_types": ["kids_entertainment"], ... },
    "route_proximity_refinement": { "ranking_boost_tags": ["near_cinema", ...] },
    "brand_direct_lookup": { "preferred_strategy": "direct_lookup" },
    "cross_mall_fallback": { "enabled": true, "fallback_scope": "same_city_first" }
  },
  "strategy_weights": { "movie_showtime_lookup": 1.5, "kids_entertainment_plan": 1.4, ... },
  "response_shape": { "default_shortlist_size": 4, "movie_shortlist_size": 5, ... },
  "context_weights": { "prioritize_movie_signals": 0.90, ... },
  "retrieval": {
    "movie_showtime_query": { "strategy": "exact_retrieval", "source": "canonical.movies" },
    "service_query": { "strategy": "exact_retrieval", "source": "canonical.services" },
    "before_movie_query": { "strategy": "tag_filtered", "required_tags": ["near_cinema", "before_movie"] },
    ...
  },
  "ranking": { "favor_family_friendly", "favor_near_cinema_for_movie_context", ... },
  "session_adaptation": { "carry_movie_context_across_turns": true, ... },
  "intent_override_priority": { "movie_showtime_lookup": 1, "service_lookup": 2, ... }
}
```

**Key behavior rules:**
- `movie_intent_override` — Movie/cinema queries override shopping drift; prefer `exact_retrieval`
- `kids_movie_redirect` *(al_nakheel_plaza_28)* — No animation films → redirect to Fun Time
- `kids_movie_highlight` *(al_nakheel_plaza_13)* — Family + kids → proactively highlight animation films
- `service_lookup` — Service queries return `direct_fact`, capped at 2 entities
- `family_guided_plan` — Families with young children get itinerary with `child_relief_anchor`
- `route_proximity_refinement` — "Near cinema" queries filter by `near_cinema` tag

---

## Anti-Hallucination Architecture

### Defense Layer 1: Grounded System Prompt

The system prompt contains the **complete list** of every store, restaurant, service, and facility in the mall:

> "EVERY store, restaurant, service, facility, floor, zone, address, and hour listed below is REAL. Anything NOT listed here does NOT exist in this mall. Never invent or assume."

### Defense Layer 2: Canonical-Only Context Composition

`compose_context` and `rank_and_dedupe` only surface entities that exist in the canonical data with valid `entity_id` references. The `overview_summary` and `facilities_summary` fields provide pre-verified description text.

### Defense Layer 3: Mall Info Grounded Path

All `mall_info` domain queries bypass the general LLM path and use a **deterministic `MallOverviewBlueprint`** built exclusively from trusted canonical data.

### Defense Layer 4: Hallucination Guard (Post-Generation)

| Check | What it catches |
|-------|----------------|
| Movie claims | Movie titles not in current listings |
| Discount claims | Percentage discounts not backed by active offers |
| Promotion claims | Sale/deal references without active offers |
| Event claims | Event names not in the mall's event calendar |
| Showtime claims | Specific times not matching any listed movie |

### Defense Layer 5: Prompt-Level Constraints
- "NEVER invent store names, floor counts, addresses, or any other detail"
- "Do NOT draw on general knowledge about malls"
- "Every factual claim must be traceable to the context provided"

### Defense Layer 6: Unsupported / Gibberish Input Guard

When `is_likely_unsupported` detects random input (keyboard mashing, high consonant ratio, very short nonsense strings):
- The LLM is **never called** — no hallucination risk on degenerate input
- A deterministic fallback response is returned with suggested valid query patterns
- If `maybe_correct_brand` finds a likely match, the correction is surfaced directly

**Guard calibration:** The character-diversity check is restricted to queries with **≤ 4 tokens**. Multi-turn queries containing 5+ words are common natural English and must not be classified as unsupported regardless of their unique-character ratio. Only short inputs — single-word mashing, two/three-character nonsense, etc. — are evaluated by the diversity heuristic.

### Defense Layer 7: Offer Honesty Guard

When `sub_intent == "offer_details"`, the LLM prompt contains an explicit constraint:
> "If no offer data is present in the retrieved context, say so honestly. Do NOT invent promotions, discount percentages, or validity dates."

---

## Data Pipeline

All mall intelligence data is generated from a standard source file (`output_mall_XX.json`) through a two-step pipeline.

### Source Format (`output_mall_XX.json`)

A structured JSON file output by the Cenomi data platform. Top-level shape:

```
{
  "property_group_id": 28,          ← numeric mall ID (used to build mall_id)
  "marketing_name": "Nakheel Plaza",
  "city": "Buraidah",
  "country": "Saudi Arabia",
  "gps_coordinates": "21.55, 39.18",
  "image": "/assets/...",
  "mall_information": { ... },       ← timing, contact, social, description
  "muvilist": { "ar": [...], "en": [...] },  ← movie listings
  "services": [ ... ],               ← mall service records
  "brands": [ ... ]                  ← all tenant/store records
}
```

**`mall_information`** key fields:
- `MallTiming` — array of `{ dayen, start_time, end_time }` per day
- `MallContact` — `{ Email, Phone, Address1En, Address2En }`
- `MallNameEn`, `MallDescriptionEn`, `MallLogoEn`, `MallImagesEn`
- `GoogleMapURL`, `MallMapEn` (Mappedin URL), `mallmapid`
- `Social` — `{ X, Instagram, Facebook, Youtube, LinkedIn, Threads }`

**`muvilist.en[]`** movie record fields:
- `id`, `type` (`SHOWING_NOW` or `COMING_SOON`)
- `title`, `synopsis`, `runtime` (minutes), `movieLang`
- `genres[]` — `{ id, name }` objects
- `releaseDate` (ISO string), `bookingUrl`, `trailerUrl`
- `coverUrl`, `image`, `subtitle` (subtitle language), `isAdvancedBooking`

**`services[]`** record fields:
- `id`, `details[]` — content blocks (`{ type, content }`) with headings, bullets, compass directions
- `service_details` — `{ content[], floor, icon, image, mappedin_location, navigation_enable }`
  - `content[]` types: `title`, `time`, `location`, `phone`
  - `floor` values: `groundFloor`, `firstFloor`, `cinemaLevel`

**`brands[]`** tenant record fields:
- `brand_id`, `brand_name_en`, `company_name_en`
- `category_name`, `group_name` (`Shop`, `Dine`, `Service`, etc.)
- `anchor_brand` (0 or 1), `is_published`, `publish_date`
- `store_phone_code`, `store_phone_number`, `store_email`, `store_website`
- `description_en` (HTML), `banner_en`, `images_en[]`, `brand_logo`
- `tags_en[]` — `{ tag_id, tag_text }` — often contain location hints ("Near Gate 2", "Next to Centrepoint")
- `pms_unit_codes[]` — primary unit codes used to infer floor/zone
- `available_in_malls[]` — `{ mall_id, property_group_id, brand_id, pms_unit_code[] }` for cross-mall identity
- `social_instagram`, `social_tiktok`, `social_facebook`, `social_twitter`, `social_snapchat`, `social_youtube`
- `engagements[]` — active promotions/offers linked to this brand

### Step 1: Deterministic Conversion → Canonical

`scripts/convert_to_canonical.py` converts the raw source into the canonical schema with zero LLM dependency:

```
output_mall_XX.json  →  data/canonical/{mall_id}.json
```

Key transformations:
- `pms_unit_codes` prefixes inferred to floor/zone (CNL→Cinema Level, FC→Food Court, GFENT→Entertainment Wing, etc.)
- `brands` categorized into `stores`, `dining`, or `cinemas` based on `category_name`
- `muvilist.en` converted to structured `movies` array with status, genre, booking_url
- Mall profile enriched with zones, landmarks, facilities, operating hours
- `overview_summary` and `facilities_summary` added for LLM-ready consumption

### Step 2: LLM Synthesis → All Derived Layers

`scripts/generate_mall_data.py` (or `synthesize_mall_data.py`) takes the canonical file and synthesizes all remaining layers using GPT-4.1 with few-shot examples from the reference mall (`al_nakheel_plaza_28`):

```
data/canonical/{mall_id}.json
  → data/semantic/{mall_id}.json           (enrichment_rules + profiles)
  → data/playbooks/{mall_id}.json          (28 scenario playbooks)
  → data/tenant_config/{mall_id}.json      (behavior rules + weights)
  → data/context_packs/{mall_id}_context.json  (topic blocks)
```

### Running the Pipeline

```bash
# From the backend/ directory:
cd backend

# Preview what would be generated — no files written, no API key needed
python scripts/generate_mall_data.py ../output_mall_28.json --dry-run
python scripts/generate_mall_data.py ../output_mall_13.json --dry-run

# Step 1 only — deterministic ETL, no API key needed
python scripts/generate_mall_data.py ../output_mall_28.json --canonical-only
# or equivalently:
python scripts/convert_to_canonical.py ../output_mall_28.json

# Full pipeline (all 5 layers) — requires OPENAI_API_KEY in .env
python scripts/generate_mall_data.py ../output_mall_28.json
python scripts/generate_mall_data.py ../output_mall_13.json

# Selective steps only
python scripts/generate_mall_data.py ../output_mall_28.json --steps semantic playbooks
python scripts/generate_mall_data.py ../output_mall_28.json --steps tenant_config context_pack

# Step 2+ only (canonical already exists) — requires OPENAI_API_KEY
python scripts/synthesize_mall_data.py data/canonical/al_nakheel_plaza_28.json

# Use a specific model
python scripts/generate_mall_data.py ../output_mall_28.json --model gpt-4.1
```

**Output files written per mall:**
```
data/canonical/{mall_id}.json
data/semantic/{mall_id}.json
data/playbooks/{mall_id}.json
data/tenant_config/{mall_id}.json
data/context_packs/{mall_id}_context.json
```

**PMS unit code → floor/zone mapping** (used in Step 1):

| Code prefix | Floor | Zone |
|-------------|-------|------|
| `CNL` | Cinema Level | Cinema Zone |
| `FC` | Ground | Food Court |
| `GFENT` | Ground | Entertainment Wing |
| `GFE` | Ground | Entertainment Wing |
| `GFK` | Ground | Kiosk Row |
| `GFHYPM` | Ground | Hypermarket Area |
| `GFA` | Ground | Main Gallery |
| `GF` | Ground | Main Gallery |
| `MEZ` | Mezzanine | Main Gallery |
| `DM` | Ground | Digital Media Area |
| `PRTB` | Ground | Service Area |

---

## Context Injection Flow

```
Canonical JSON
    │
    ├── MallContextLoader.load()
    │       ├── MallNormalizer → CanonicalMallData
    │       ├── SemanticEnricher → SemanticProfiles (applies enrichment_rules + enrichment_rules_v2)
    │       └── PlaybookEngine → ScenarioPlaybooks (resolves trigger_domains, required_signals)
    │
    ├── build_context_pack() → GlobalContextPack
    │       ├── MallProfileBlock (overview_summary, operational_context)
    │       ├── TopicBlocks (dining, gift, movie, family, services, cinema_and_movies,
    │       │               before_movie, after_movie, kids_visit, date_night, quick_visit)
    │       └── Events & Offers
    │
    ├── get_canonical_for_prompt() → flat dict for LLM
    │       ├── mall_profile (with zones, floors, facilities, overview_summary)
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

Each turn flows through the LangGraph pipeline with a shared state object:

- **Session metadata**: session_id, tenant_id, mall_id, turn_id
- **Input**: raw_user_message, normalized_user_message, expanded_query
- **Classification**: InterpretedIntent (domain, sub_intent, message_kind, confidence)
- **Scene**: SceneMemory (companions, occasion, budget, active_topic, topic_lock, topic_lock_confidence, last_context_setting_turn, last_selected_playbook, last_response_experience_mode, etc.)
- **Playbook resolution**: selected_playbook, confidence, matched_playbooks
- **Context composition**: selected_topic_blocks, selected_entities, semantic_signals
- **Ranked entities**: ranked_entity_list (output of rank_and_dedupe)
- **Retrieval**: retrieval_needed, retrieval_results
- **Response plan**: chosen_strategy, response_shape_hint, constraints
- **Output**: final_response_text, messages, debug payload
- **Debug enrichment**: normalized_query, canonical_query_pattern, topic_lock, topic_lock_confidence, retrieval_discipline_reason, canonical_name_normalization_notes, suppressed_playbooks, selection_reason

---

## Intent Classification

### Domains and Sub-Intents

| Domain | Sub-Intents |
|--------|-------------|
| `mall_info` | overview, facilities_summary, opening_hours, family_friendliness, what_is_available |
| `exploration` | open_exploration, activity_suggestion, first_visit_guide |
| `dining` | general_dining, romantic_dining, quick_bite, family_dining, cafe_recommendation, dessert_recommendation, before_movie_dining, after_movie_dining |
| `shopping` | general_shopping, gift_recommendation, fashion_shopping, category_lookup, brand_lookup |
| `entertainment` | general_entertainment, movie_showtime, kids_entertainment, group_hangout |
| `services` | store_hours, service_info, prayer_room, atm, stroller, wheelchair, navigation |
| `navigation` | location_query, proximity_refinement, near_cinema |
| `general` | general_inquiry |

### Query Normalization

Before classification, semantically equivalent phrasings are mapped to a canonical form via `_NORMALIZATION_TABLE` in `query_classifier.py`:

| Variant | Canonical Form |
|---------|---------------|
| "now showing", "what's playing", "what can i watch", "what films are on" | "what movies are showing" |
| "what's in this mall", "what does this mall have" | "what is available in this mall" |
| "i want to eat", "looking for food", "hungry" | "where can i eat" |
| "any deals", "any promotions", "current offers" | "what offers are available" |

`normalize_query_with_pattern` returns both the normalized query string and a `canonical_query_pattern` label (e.g., `"movie_lookup"`) for debug tracing.

### Classification Priority

1. **Unsupported input detection** (`is_likely_unsupported`) → early exit with graceful recovery, no LLM
2. **Smalltalk detection** (greetings, thanks, goodbye) → static responses, no LLM
3. **Query normalization** → map variants to canonical form
4. **Rule-based keyword matching** → zero LLM cost, high confidence for common patterns
5. **LLM fallback** → only when rules fall below confidence threshold

### Message Kinds

| Kind | When Used |
|------|-----------|
| `fresh_request` | New standalone question |
| `followup` | Short continuation of active topic |
| `refinement` | Adding to current topic ("also", "what about") |
| `constraint_refinement` | Tightening a prior suggestion ("something quicker", "not expensive") |
| `correction` | Correcting a previous answer |
| `topic_switch` | Changing topic explicitly |
| `context_setting` | User declares scene context without asking a question — *"I'm here with my kid"*, *"it's our anniversary"*, *"solo visit"*. Routes to concierge for scene acknowledgement. Never routes to factual even if companion signals are present |
| `greeting` / `smalltalk` | Casual chat, greetings |

### Intent Override Priority (from tenant config)

When multiple domains could match, tenant config `intent_override_priority` resolves conflicts:

```
1. movie_showtime_lookup  (strongest — explicit movie query always wins)
2. service_lookup         (ATM, stroller, prayer room — direct fact)
3. kids_entertainment     (strong when companion=child)
4. before_after_movie     (stronger than general dining)
5. family_guided_plan     (stronger than solo shopping)
6. gift_plan
7. dining
8. shopping               (weakest — does not override entertainment/service)
```

---

## Dual-Flow Architecture

**File:** `backend/app/nodes/route_flow.py`

Every query is routed to one of two execution flows before `compose_context` runs:

| Flow | When Used | Behaviour |
|------|-----------|-----------|
| `factual` | Exact data lookup needed — showtimes, hours, brand presence, offer details | Retrieval-first; entities sourced from canonical lookup; LLM only polishes the structured result |
| `concierge` | Planning, recommendations, exploration, scenario-rich queries | Full playbook + entity ranking pipeline; LLM drives the narrative |

### Priority-Ordered Routing Rules

| Rule | Trigger | Result |
|------|---------|--------|
| 0 — Domain lock | Previous turn was factual + no explicit topic switch | Stay factual (continuity) |
| 0b — Near-cinema dining | Dining intent + proximity phrase ("near cinema", "near the food court") | Concierge — proximity acts as a location modifier, not a cinema intent override |
| 1 — Cross-mall | `cross_mall` domain or `cross_mall_search` sub-intent | Always factual |
| 2 — Factual sub-intent | `sub_intent` ∈ `_FACTUAL_SUB_INTENTS` | Factual — unless clear planning overlay |
| 3 — Factual domain | `domain` ∈ `navigation`, `cross_mall` | Factual |
| 4 — Hard factual signals | Keyword match: "now showing", "where is the", "is X here", etc. | Factual |
| 5 — Strong concierge sub-intent | `sub_intent` ∈ `_CONCIERGE_SUB_INTENTS` | Concierge |
| 6 — Scene context | Companions/occasion/visit_type present AND primary intent not factual | Concierge |
| 7 — Concierge hard signals | Planning keywords detected | Concierge (with factual primary intent override) |
| 7b — Context-setting | `message_kind == "context_setting"` | Concierge (scene acknowledgement) |
| 8 — Follow-up continuity | Prior turn was factual + `message_kind` is followup/refinement | Stay factual |

### Factual Sub-Intents (`_FACTUAL_SUB_INTENTS`)

These sub-intents always route to factual flow regardless of scene context:

| Sub-Intent | Query Pattern |
|-----------|---------------|
| `movie_showtime` | What movies are showing |
| `opening_hours` / `store_hours` | When does X open |
| `location_query` | Where is X |
| `service_info` / `prayer_room` / `parking_info` | ATM, stroller, prayer room |
| `cross_mall_search` | Cross-mall brand lookup |
| `offer_details` | What offers/deals are available |
| `brand_availability` | Do you have X / Is X here |

### Pure Lookup Recognition (`_is_pure_lookup`)

When a query contains companion or context signals alongside a factual question (e.g., *"any movies with my kid"*), `_is_pure_lookup` identifies it as a filtered factual lookup — the companion acts as a **filter**, not an intent replacement. Recognized patterns include: `"what movies"`, `"any movies"`, `"movies with"`, `"now showing"`, `"where is the"`, `"do you have "`, `"is there a "`, etc.

---

## Response Strategy Matrix

| Strategy | Shape | When Used |
|----------|-------|-----------|
| `mall_overview` | Structured overview with bullets | Mall-level questions |
| `direct_fact` | 1-3 sentence answer | Specific factual questions (prayer room, ATM, hours) |
| `direct_lookup` | Name + location | Explicit brand/store lookup |
| `exact_retrieval` | Structured list from canonical | Movie showtimes, service details |
| `shortlist_recommendation` | Numbered/grouped list | Category browsing |
| `gift_formula` | Curated picks by category | Gift recommendations |
| `movie_plus_food` | Combo suggestion | Movie + dining plans |
| `mini_itinerary` | Step-by-step plan | Date night, shopping+dessert |
| `family_plan` | Structured itinerary | Family visits |
| `guided_plan` | Itinerary with child-relief anchor | Family with young kids |
| `proximity_guided_shortlist` | Shortlist filtered by location | Near cinema, before movie |
| `budget_plan` | Value-focused list | Budget-conscious visitors |
| `exploration_overview` | Casual mini-itinerary | Vague "what can I do" |

---

## API Contract

### `POST /api/chat`

**Request:**

```json
{
  "message": "What movies can I watch?",
  "session_id": "optional-session-id"
}
```

**Response:**

```json
{
  "session_id": "session-abc123",
  "message": "Here's what's showing at Muvi Cinema right now...",
  "session_state": {
    "turn_count": 1,
    "active_topic": "entertainment",
    "companions": [],
    "occasion": "",
    "budget": "",
    "active_shortlist": ["Muvi Cinema", "SHELTER", "THE HOUSEMAID"]
  },
  "sources": [],
  "suggestions": [],
  "debug": {
    "intent": { "domain": "entertainment", "sub_intent": "movie_showtime" },
    "playbook": "pb-movie-showtime-lookup",
    "strategy": "exact_retrieval",
    "retrieval": { "needed": true, "source": "canonical.movies" },
    "latency_ms": 2100,
    "node_trace": ["load_session", "interpret_turn", "update_scene_memory",
                   "resolve_playbooks", "choose_strategy", "compose_context",
                   "rank_and_dedupe", "decide_retrieval", "fetch_exact_facts",
                   "generate_response", "update_memory", "emit_debug_payload"]
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
| LLM | OpenAI GPT-4.1 (configurable model, temperature) |
| Models | Pydantic v2 |
| Sessions | In-memory (LRU, max 1000) |
| Frontend | React 19, Vite 7, TypeScript 5.9, Tailwind v4 |
| Data | JSON files (canonical, semantic, playbooks, context_packs, tenant_config) |
| Data Pipeline | Deterministic ETL (convert_to_canonical.py) + LLM synthesis (generate_mall_data.py) |

---

## Key Files Reference

### Backend Runtime

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI entry point, lifespan initialization |
| `app/runtime.py` | Global singletons (mall context, session store) |
| `app/graph/builder.py` | LangGraph pipeline compilation |
| `app/nodes/generate_response.py` | LLM prompt assembly and response generation (incl. unsupported recovery path and offer honesty guard) |
| `app/nodes/interpret_turn.py` | Hybrid intent classifier — query normalization, unsupported detection, brand correction, context_setting kind |
| `app/nodes/route_flow.py` | Dual-flow routing — factual vs. concierge decision with priority-ordered rules |
| `app/nodes/compose_context.py` | Entity selection and context building |
| `app/nodes/rank_and_dedupe.py` | Semantic ranking, normalized canonical dedup, entity capping, normalization debug notes |
| `app/nodes/decide_retrieval.py` | Retrieval gate — populates `retrieval_discipline_reason` in debug |
| `app/nodes/update_memory.py` | Post-response state write-back — topic_lock, last_context_setting_turn, playbook persistence |
| `app/nodes/choose_strategy.py` | Strategy selection (uses tenant config intent_override_priority) |
| `app/nodes/resolve_playbooks.py` | Playbook matching (trigger_domains, required_scene_signals) |
| `app/context/mall_context.py` | Mall data loader and entity lookup |
| `app/services/context_builder.py` | Context pack assembly from data layers |
| `app/services/concierge.py` | Chat turn orchestration |
| `app/prompts/builder.py` | Full prompt construction (identity, context, scene) |
| `llm/prompts/concierge_prompt.py` | Concierge system prompt with grounding rules |
| `guardrails/hallucination_guard.py` | Post-generation hallucination validation |
| `response/concierge_composer.py` | Deterministic response blueprints |
| `intent/query_classifier.py` | Rule-based classifier — `_NORMALIZATION_TABLE`, `SHORT_QUERY_INTENTS`, `normalize_query_with_pattern`, `is_likely_unsupported`, `maybe_correct_brand` |
| `tests/test_stability.py` | 80-test stability suite covering all 14 acceptance criteria categories |

### Data Pipeline Scripts

| File | Purpose |
|------|---------|
| `scripts/convert_to_canonical.py` | Deterministic ETL: `output_mall_XX.json` → `canonical/{mall_id}.json` |
| `scripts/synthesize_mall_data.py` | LLM synthesis: `canonical/*.json` → semantic, playbooks, tenant_config, context_packs |
| `scripts/generate_mall_data.py` | **Full pipeline**: `output_mall_XX.json` → all 5 intelligence layers |

### Data Files (per mall)

| Path | Layer |
|------|-------|
| `data/canonical/{mall_id}.json` | Authoritative facts and entities |
| `data/semantic/{mall_id}.json` | Enrichment rules and per-entity profiles |
| `data/playbooks/{mall_id}.json` | 28 scenario playbooks |
| `data/context_packs/{mall_id}_context.json` | LLM-ready topic blocks |
| `data/tenant_config/{mall_id}.json` | Behavior tuning and retrieval rules |
| `data/tenant_config/tenant_defaults.json` | Default config values for new malls |
