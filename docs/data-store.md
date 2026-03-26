# Backend Data Store

`backend/data/` is the mall intelligence store — a set of layered JSON files that together give the concierge everything it needs to answer questions, build recommendations, and adapt its tone per mall. Each subdirectory is a distinct intelligence layer consumed at different points in the pipeline.

```
backend/data/
├── raw/                 Unprocessed source files (ETL input)
├── canonical/           Normalized entity records per mall
├── semantic/            Enrichment rules + semantic tag taxonomy
├── playbooks/           Scenario-driven response strategies
├── context_packs/       Pre-assembled LLM context bundles
├── tenant_config/       Per-mall tunable parameters + global defaults
├── chroma/              Chroma vector DB — per-mall embedding collections
└── examples/            Sample / reference files for development
```

---

## Layer Overview

| Layer | Consumed by | Purpose |
|---|---|---|
| `raw/` | ETL script | Source-of-truth API data, unmodified |
| `canonical/` | Retriever, Context loader | Full structured entity records |
| `semantic/` | Retriever, Context loader | Tag enrichment rules for stores/dining |
| `playbooks/` | Retriever, Generator | Scenario logic + ranking biases |
| `context_packs/` | Retriever, Generator | Fast-access LLM-ready context |
| `tenant_config/` | All nodes | Tone, strategy weights, session rules |
| `chroma/` | `VectorStoreService` | Persisted entity embedding vectors for semantic search |

---

## `raw/`

**Purpose:** Holds unprocessed source files from upstream APIs before the ETL transform.

Currently kept empty (`.gitkeep`) — raw API dumps are placed here before running the ETL transform script (`transform_mall_data.py` at the project root). Processed output flows into `canonical/`.

**Naming convention:** No fixed schema — these are raw API payloads.

---

## `canonical/`

**Purpose:** The authoritative, normalized record for every entity in a mall — stores, dining outlets, cinemas, kiosks, facilities, and the mall profile itself. This is the single source of truth for all entity data.

**Files:** `<mall_id>.json`

```
canonical/
├── al_nakheel_plaza_13.json
└── al_nakheel_plaza_28.json
```

### Top-level schema

```jsonc
{
  "mall_profile": { ... },   // Mall identity, zones, landmarks, facilities, hours
  "stores":       [ ... ],   // Retail stores
  "dining":       [ ... ],   // Restaurants and cafes
  "services":     [ ... ],   // Mall services (ATMs, information desks, etc.)
  "kiosks":       [ ... ],   // Inline kiosk tenants
  "cinemas":      [ ... ]    // Cinema entries (if applicable)
}
```

### `mall_profile`

| Field | Description |
|---|---|
| `mall_id` | Unique mall identifier (e.g. `al_nakheel_plaza_13`) |
| `name` / `marketing_name` | Display and marketing names |
| `city`, `country`, `address` | Location metadata |
| `coordinates` | `{ lat, lng }` |
| `floors` | Ordered list of floor labels |
| `zones[]` | Each zone has `zone_id`, `name`, `floor`, `description`, `category_focus[]` |
| `landmarks[]` | Named physical landmarks: entrances, info desks, etc. |
| `facilities[]` | Prayer rooms, wheelchair service, strollers, ATMs |
| `operating_hours` | Per-day open/close times |
| `parking` | Notes, pricing, valet, EV charging |
| `contact` | Phone, email, address, Google Maps link, social media URLs |
| `amenities[]` | Human-readable amenity list |
| `accessibility_features[]` | Accessibility-specific notes |
| `loyalty` | Loyalty program name, app availability, sign-up locations |
| `map_url` | Interactive map URL |

### Entity record (store / dining / kiosk)

Each entity across `stores`, `dining`, and `kiosks` shares a common shape:

```jsonc
{
  "entity_id":    "t-store-001",
  "entity_type":  "store",           // store | dining | cinema | kiosk | service
  "name":         "L'Occitane",
  "brand":        "L'Occitane Arabia Trading LTD CO",
  "brand_id":     111,
  "category":     "Beauty",
  "subcategory":  "Cosmetics & Skincare",
  "description":  "...",
  "location": {
    "floor":          "Ground",
    "zone":           "Main Gallery",
    "unit_number":    "GF044",
    "directions_hint": "Near Gate 6, next to Stradivarius"
  },
  "operating_hours": { "sunday": "09:00 AM–11:59 PM", ... },
  "contact": { "phone": "...", "email": "...", "website": "..." },
  "price_range":  "premium",         // budget | mid_range | premium | luxury
  "brand_logo":   "https://...",
  "banner":       "https://..."
}
```

---

## `semantic/`

**Purpose:** Defines enrichment rules that attach semantic tags to canonical entities at context-load time. Tags classify entities by audience suitability, vibe, outing fit, gift fitness, and priority boosts — enabling scenario-aware retrieval and ranking.

**Files:** `<mall_id>.json`

```
semantic/
├── al_nakheel_plaza_13.json
└── al_nakheel_plaza_28.json
```

### Top-level schema

```jsonc
{
  "mall_id":               "al_nakheel_plaza_13",
  "tag_taxonomy_version":  "1.0",
  "enrichment_rules":      [ ... ],   // Bulk category/zone rule-based enrichment
  "enrichment_rules_v2":   [ ... ],   // Scenario-specific override rules (cinema, before/after movie, kids)
  "profiles":              [ ... ]    // Per-entity semantic profiles
}
```

### `enrichment_rules` — bulk enrichment rule

Applied at load time to seed initial tags based on category/zone matching:

```jsonc
{
  "rule_id":     "er-fashion-main",
  "description": "Fashion stores in the Main Gallery are ideal for style-conscious shoppers, families, and gifting.",
  "match_conditions": {
    "category": ["Fashion"],
    "zone":     ["Main Gallery"]
  },
  "apply_tags":       ["fashion", "style_shopping", "main_gallery", "gift_friendly"],
  "apply_audience":   ["solo_friendly", "couple_friendly", "family_friendly", "teen_friendly"],
  "apply_vibe":       ["modern", "trendy"],
  "apply_outing_fit": ["shopping_spree", "quick_visit", "practical_shopping"],
  "apply_gift_fit":   ["girlfriend_gift", "wife_gift", "friend_gift"],
  "priority_boost": {
    "shopping_plus_dessert_combo": 0.7,
    "gift_for_girlfriend":         0.7,
    "last_minute_gift":            0.6,
    "family_visit_plan":           0.5
  }
}
```

### `enrichment_rules_v2` — scenario-specific overrides

Applied after `enrichment_rules` to attach scenario-critical tags that simple category/zone matching cannot infer. Key rule sets:

| Rule ID | Entities targeted | Tags applied |
|---|---|---|
| `er-cinema-zone` | Cinema entities | `entertainment_anchor`, `showtime_lookup`, `movie_night` + scenario priority boosts |
| `er-before-movie-foodcourt` | Food Court dining | `near_cinema`, `before_movie`, `quick_stop` |
| `er-before-movie-kiosk` | Kiosk Row snacks | `near_cinema`, `before_movie`, `grab_and_go` |
| `er-after-movie-reward` | Dessert / café entities | `after_movie`, `reward_stop` |
| `er-kids-entertainment-plan` | Kids Entertainment | `child_relief_anchor`, `entertainment_anchor`, `stroller_friendly` |
| `er-movie-kids-animation` *(al_nakheel_plaza_13 only)* | Animation films | `kid_friendly`, `family_movie` |

### `profiles` — per-entity semantic profile

Each profile includes:

```jsonc
{
  "entity_id":   "t-store-001",
  "semantic_tags":  ["fashion", "gift_friendly", "premium"],
  "audience_fit":   ["couple_friendly", "solo_friendly"],
  "vibe":           ["modern", "upscale"],
  "price_band":     "premium",
  "outing_fit":     ["date_night", "gift_for_girlfriend"],
  "gift_fit":       ["girlfriend_gift", "wife_gift"],
  "meal_fit":       [],
  "routing_tags":   ["main_gallery"],
  "priority_scores": { "gift_for_girlfriend": 0.85, "date_night": 0.75 },
  "when_to_recommend": "When visitor is shopping for a partner or special occasion.",
  "avoid_if":        "Budget-constrained visit",
  "concierge_notes": "Great for premium fragrance gifts — wide range, bilingual staff."
}
```

### Tag categories

| Field | Description |
|---|---|
| `match_conditions` | Filters by `category`, `zone`, `entity_type`, or other canonical fields |
| `apply_tags` / `semantic_tags` | General searchable tags attached to matched entities |
| `apply_audience` / `audience_fit` | Who the entity is suitable for (`solo_friendly`, `kid_friendly`, `couple_friendly`, …) |
| `apply_vibe` / `vibe` | Mood/atmosphere descriptors (`modern`, `casual`, `upscale`, `romantic`, …) |
| `apply_outing_fit` / `outing_fit` | Trip-type suitability (`family_visit_plan`, `shopping_spree`, `date_night`, …) |
| `apply_gift_fit` / `gift_fit` | Gift scenario suitability (`girlfriend_gift`, `last_minute_gift`, …) |
| `priority_boost` / `priority_scores` | Floating-point boosts (0–1) per named scenario used during retrieval ranking |

---

## `playbooks/`

**Purpose:** Scenario playbooks encode the concierge's response strategy for specific visit situations. Each playbook defines what triggers it, which entity types and tags to prioritize, how to rank candidates, what shape the response should take, and what to exclude.

**Files:** `<mall_id>.json` — each file is a JSON array of playbook objects.

```
playbooks/
├── al_nakheel_plaza_13.json
└── al_nakheel_plaza_28.json
```

### Playbook schema (v2)

```jsonc
{
  "playbook_id":   "pb-family-movie",
  "name":          "Family Movie Plan",
  "description":   "A full family outing covering cinema, dining, and kids entertainment.",

  "trigger_domains":      ["entertainment"],
  "trigger_sub_intents":  ["kids_entertainment", "general_entertainment"],
  "required_scene_signals":   ["companions"],
  "optional_scene_signals":   ["budget"],
  "required_semantic_signals": ["family_friendly"],

  "ranking_boost_tags":   ["family_friendly", "kid_friendly", "entertainment_anchor"],
  "ranking_penalty_tags": [],
  "excluded_tags":        ["adult_movie", "romantic"],

  "preferred_response_shape": "guided_plan",
  "preferred_strategy":       "guided_plan",
  "entity_cap":               6,
  "must_include_entity_types": ["kids_entertainment"],
  "fallback_entity_types":     ["dining"],

  "itinerary_template": "1) Kids entertainment → 2) Family dining → 3) Cinema → 4) Dessert",
  "retrieval_triggers": ["canonical.movies", "canonical.cinemas"],
  "priority": 2
}
```

### Standard playbook IDs (28 per mall)

| Playbook ID | Scenario | Strategy |
|---|---|---|
| `pb-mall-overview` | Tell me about the mall | `mall_overview` |
| `pb-brand-lookup` | Where is Zara / do you have X | `direct_lookup` |
| `pb-category-shopping` | I want shoes / fashion stores | `shortlist_recommendation` |
| `pb-family-shopping` | Family with kids, want to shop | `guided_plan` |
| `pb-gift-girlfriend` | Gift for girlfriend | `gift_formula` |
| `pb-date-night` | Date night | `mini_itinerary` |
| `pb-movie-showtime-lookup` | What movies are playing | `exact_retrieval` |
| `pb-movie-night` | Movie night plan | `movie_plus_food` |
| `pb-family-movie` | Family movie plan | `guided_plan` |
| `pb-kids-entertainment` | Something for kids | `shortlist_recommendation` |
| `pb-before-movie` / `pb-quick-snack` | Quick food before a movie | `proximity_guided_shortlist` |
| `pb-after-movie` / `pb-shopping-dessert` | Dessert / reward after movie | `mini_itinerary` |
| `pb-service-lookup` | ATM, stroller, prayer room | `direct_fact` |
| `pb-budget-family` | Budget family outing | `budget_plan` |
| `pb-luxury-shopping` | Premium / luxury shopping | `shortlist_recommendation` |
| `pb-exploration` | First visit, open exploration | `exploration_overview` |
| … (28 total) | | |

---

## `context_packs/`

**Purpose:** Pre-assembled, LLM-ready context bundles. Rather than building context from scratch on every turn, the pipeline loads a context pack that has already aggregated the most important mall information into a compact, structured format optimized for injection into prompts.

**Files:** `<mall_id>_context.json`

```
context_packs/
├── al_nakheel_plaza_13_context.json
└── al_nakheel_plaza_28_context.json
```

### Top-level schema

```jsonc
{
  "mall_id":             "al_nakheel_plaza_13",
  "version":             "1.0",
  "mall_profile_summary": { ... },   // Condensed mall identity for the prompt
  "operational_context":  { ... },   // Hours, parking, accessibility, services
  "topic_blocks":         { ... }    // Pre-grouped entity summaries by topic
}
```

### `mall_profile_summary`

A compact version of the canonical mall profile — name, city, tagline, key zones with a one-line description each, and highlights. Injected at the start of every system prompt.

### `operational_context`

Contains the most commonly queried operational facts:

| Field | Description |
|---|---|
| `hours` | Per-day open/close times |
| `parking` | Capacity, rate, valet, EV charging |
| `accessibility` | Ramps, accessible washrooms, wheelchair/stroller loans |
| `essential_services` | Information desk, prayer rooms, ATMs, lost & found |
| `current_season` | Current marketing season (e.g. `"Spring 2026"`) |
| `active_promotions` | Live promotional offers |
| `upcoming_events` | Scheduled events |

### `topic_blocks`

A dictionary keyed by topic (`dining`, `fashion`, `beauty`, `entertainment`, etc.). Each block contains:

```jsonc
"dining": {
  "topic":    "dining",
  "summary":  "One-paragraph overview of dining options in this mall.",
  "entities": [
    {
      "name":       "Kudu Restaurant",
      "type":       "fast_food",
      "cuisine":    "Chicken Cuisine",
      "price":      "budget",
      "location":   "Ground floor, Food Court (FC006)",
      "best_for":   "quick meals, families, kids",
      "meal_time":  "20 min",
      "kids_menu":  true,
      "key_note":   "Saudi fast food, halal, made-to-order meals"
    }
  ]
}
```

Topic blocks give the retriever a fast lookup path — instead of scanning all canonical entities, it can pull the pre-filtered, summarized block for the detected topic.

---

## `tenant_config/`

**Purpose:** Per-mall tunable parameters that control how the concierge behaves — tone, clarification strategy, retrieval rules, response shape, ranking biases, and session adaptation. A `tenant_defaults.json` provides global fallback values; mall-specific files override individual fields.

```
tenant_config/
├── tenant_defaults.json           Global defaults for all malls
├── al_nakheel_plaza_13.json       Mall of Arabia (Jeddah) overrides
└── al_nakheel_plaza_28.json       Al Nakheel Plaza (Buraidah) overrides
```

### Schema (v2)

The config version is `"2.0.0"`. All keys below are present in `tenant_defaults.json`; mall-specific files override only the keys they need to change.

#### `tone`

Controls how the concierge sounds:

| Parameter | Range | Description |
|---|---|---|
| `warmth` | 0–1 | How friendly and personal the response feels |
| `directness` | 0–1 | Answer-first vs. exploratory style |
| `formality` | 0–1 | Formal/professional vs. conversational |
| `emoji_level` | 0–1 | Frequency of emoji use (default: low) |
| `concierge_confidence` | 0–1 | How assertive recommendations are |
| `promotional_intensity` | 0–1 | Degree to which offers are surfaced |
| `mall_branding_prominence` | 0–1 | How often the mall brand name is used |
| `practicalness_vs_exploratory` | 0–1 | 1 = practical, 0 = exploratory suggestions |

#### `clarification`

Controls when and how the bot asks follow-up questions:

| Parameter | Range | Description |
|---|---|---|
| `ambiguity_tolerance` | 0–1 | Higher = guess more, ask less |
| `answer_first_bias` | 0–1 | Prefer answering before clarifying |
| `max_followups_before_answer` | int | Maximum clarifying questions per turn |
| `assumption_aggressiveness` | 0–1 | How boldly it assumes user intent |

#### `behavior_rules`

Named override rules that trigger specific pipeline behaviors. Key rules:

| Rule | What it does |
|---|---|
| `movie_intent_override` | Movie/cinema queries override shopping drift; force `exact_retrieval` |
| `kids_movie_redirect` *(al_nakheel_plaza_28)* | No animation films → redirect to Fun Time kids entertainment |
| `kids_movie_highlight` *(al_nakheel_plaza_13)* | Family + kids → proactively highlight animation films |
| `service_lookup` | Service queries return `direct_fact`, capped at 2 entities |
| `before_movie_quick_stop` | Before-movie queries prefer Food Court and Kiosk Row zones |
| `family_guided_plan` | Families with young children always get a `child_relief_anchor` in the itinerary |
| `route_proximity_refinement` | "Near cinema" queries filter by `near_cinema` tag |
| `brand_direct_lookup` | Explicit brand name queries use `direct_lookup` strategy |
| `cross_mall_fallback` | Enables cross-mall brand search when entity not found in home mall |

#### `strategy_weights`

Floating-point multipliers that influence which response strategy the generator selects:

```jsonc
{
  "movie_showtime_lookup":   1.5,
  "kids_entertainment_plan": 1.4,
  "direct_fact":             1.0,
  "shortlist_recommendation":1.2,
  "mini_itinerary":          0.8,
  "gift_formula":            0.9,
  "family_plan":             1.0,
  "movie_plus_food":         0.8,
  "budget_plan":             0.8
}
```

#### `response_shape`

Controls what generated responses look like:

| Parameter | Description |
|---|---|
| `default_shortlist_size` | Number of entities in a recommendation shortlist |
| `movie_shortlist_size` | Number of movies in a showtime response |
| `default_itinerary_steps` | Steps in a generated itinerary |
| `location_detail_level` | `floor_zone` \| `zone_only` \| `floor_only` |
| `suggest_next_step` | Whether to append a follow-up suggestion |
| `paragraph_vs_bullets` | `paragraph` \| `bullets` \| `mixed` |

#### `context_weights`

Controls what context the retriever prioritizes:

```jsonc
{
  "prioritize_movie_signals":   0.90,
  "prioritize_playbooks":       0.80,
  "prioritize_semantic_tags":   0.70,
  "prioritize_current_topic":   0.85,
  "prioritize_family_signals":  0.75,
  "prioritize_kid_signals":     0.70
}
```

#### `retrieval`

Per-intent retrieval strategy overrides:

```jsonc
{
  "movie_showtime_query":  { "strategy": "exact_retrieval",  "source": "canonical.movies" },
  "service_query":         { "strategy": "exact_retrieval",  "source": "canonical.services" },
  "before_movie_query":    { "strategy": "tag_filtered",     "required_tags": ["near_cinema", "before_movie"] },
  "after_movie_query":     { "strategy": "tag_filtered",     "required_tags": ["after_movie"] },
  "brand_lookup_query":    { "strategy": "exact_retrieval",  "source": "canonical.stores" }
}
```

#### `ranking`

Floating-point biases applied when scoring retrieved entities:

```jsonc
{
  "favor_family_friendly":           0.60,
  "favor_kid_friendly":              0.55,
  "favor_near_cinema_for_movie_context": 0.80,
  "favor_anchor_entities":           0.55,
  "favor_diversity_vs_confidence":   0.45
}
```

#### `session_adaptation`

Controls how strongly the pipeline preserves or re-uses context across turns:

```jsonc
{
  "carry_movie_context_across_turns": true,
  "carry_family_context_across_turns": true,
  "preserve_companions_strongly":      0.85,
  "preserve_budget_strongly":          0.80,
  "preserve_active_topic_strongly":    0.75,
  "correction_overrides_previous_scope": 0.90
}
```

#### `intent_override_priority`

When multiple intent domains match, this map resolves conflicts (lower number = higher priority):

```jsonc
{
  "movie_showtime_lookup": 1,
  "service_lookup":        2,
  "kids_entertainment":    3,
  "before_after_movie":    4,
  "family_guided_plan":    5,
  "gift_plan":             6,
  "dining":                7,
  "shopping":              8
}
```

### Resolution order

When the pipeline loads config for a mall, it applies defaults first, then overlays the mall-specific file — any key present in the mall file overrides its default counterpart. Missing keys fall back to `tenant_defaults.json`.

---

## `chroma/`

**Purpose:** Chroma persistent vector database storing `text-embedding-3-small` embeddings of every canonical entity description, used by `VectorStoreService` for fuzzy semantic search.

**Files:** Chroma stores its internal HNSW index files per-segment plus a central SQLite catalogue.

```
chroma/
├── chroma.sqlite3                          Central Chroma catalogue (collections, segments, metadata)
└── <segment-uuid>/                         Per-collection HNSW segment directory
    ├── data_level0.bin                     HNSW graph data
    ├── header.bin
    ├── length.bin
    └── link_lists.bin
```

### Collections

One Chroma collection per mall, named `cenomi_mall_{mall_id}`:

| Collection | Stores | Dining | Services | Cinemas | Total |
|---|---|---|---|---|---|
| `cenomi_mall_al_nakheel_plaza_28` | 83 | 10 | 10 | 1 | **104** |
| `cenomi_mall_al_nakheel_plaza_13` | 48 | 5 | 0 | 1 | **54** |

Each vector was created by `scripts/ingest_vectors.py` which builds a semantically-dense text blob per entity (name + category + tags + description) and embeds it with `text-embedding-3-small`. The collection uses cosine distance (`hnsw:space: cosine`).

### Ingestion

Run once per mall (or after data updates):

```bash
cd backend
python scripts/ingest_vectors.py --mall-id al_nakheel_plaza_28
python scripts/ingest_vectors.py --all          # all malls in data/canonical/
python scripts/ingest_vectors.py --all --dry-run  # preview without embedding
```

### When it is queried

`VectorStoreService.search()` is called from the `_vector_search_fallback` helper in `compose_context.py` when both category-rule retrieval and playbook-based ranking return no entities. Before the search, `build_scene_prefix(scene)` prepends scene signals (occasion, companions, visit_type, budget) to the raw query so the embedded vector reflects the visitor's context rather than bare query text.

`MallRetriever.search_semantic(scene=None)` exposes the same vector path as a standalone method; passing `scene` applies the same prefix augmentation internally.

### Three-tier caching

| Tier | Storage | Latency |
|---|---|---|
| 1 | Redis (`cenomi:vcache:{mall_id}:{sha256(query)}`) | ~1 ms |
| 2 | Chroma HNSW index (disk, `data/chroma/`) | ~50–200 ms |
| 3 | OpenAI embedding API (on full cache miss) | ~200–500 ms |

Redis is optional (disabled by default — set `BACKEND_REDIS_URL` to enable). Chroma files are mounted as a Docker volume so embeddings survive image rebuilds.

---

## Adding a New Mall

The recommended workflow uses the two-step data pipeline scripts:

### Step 1 — Generate the raw mall JSON (ETL)

```bash
# From the workspace root — produces output_mall_<N>.json
python transform_mall_data.py --mall-id <N>
```

### Step 2 — Convert to canonical (deterministic, no API key)

```bash
cd backend
python scripts/convert_to_canonical.py ../output_mall_<N>.json
# Creates data/canonical/<mall_id>.json
```

### Step 3 — Synthesize all derived layers (requires OpenAI API key)

```bash
cd backend
python scripts/synthesize_mall_data.py data/canonical/<mall_id>.json
# Creates:
#   data/semantic/<mall_id>.json
#   data/playbooks/<mall_id>.json
#   data/tenant_config/<mall_id>.json
#   data/context_packs/<mall_id>_context.json
```

Or use the combined pipeline script for all steps at once:

```bash
cd backend
python scripts/generate_mall_data.py ../output_mall_<N>.json
```

### Step 4 — Register the mall

Add the new `mall_id` to `BACKEND_MALL_IDS` in `backend/.env`, and add the mall display entry in `frontend/src/lib/constants.ts`. Restart the backend.

See [`docs/data-pipeline.md`](data-pipeline.md) for full documentation on source formats, transform logic, and pipeline options.
