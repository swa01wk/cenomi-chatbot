# Data Pipeline & Context Building

This document covers the raw source data files in `./data/`, the `transform_mall_data.py` ETL script that converts them into a structured canonical JSON, and the full context-building pipeline that turns that canonical JSON into grounded LLM prompts at runtime.

---

## 1. Source Data: `./data/` Folder

The root-level `data/` folder holds five JSON files. Four are raw API responses fetched from the Cenomi client application; one is a manually curated output contract.

```
data/
├── sample.json          ← output schema contract (target shape)
├── mall_and_movie.json  ← API: mall metadata + movie listings
├── services.json        ← API: mall services (pre-filtered for mall 28)
├── engagements.json     ← API: promotions and offers
└── brands.json          ← API: brand/tenant records
```

### `sample.json` — Output Schema Contract

`sample.json` is **not** a data source — it is the **target shape** that `transform_mall_data.py` must produce. It defines exactly which top-level keys, nested keys, and array structures the output file must contain. The script uses it purely as a validation reference.

Top-level structure:

```json
{
  "property_group_id": 28,
  "marketing_name": "Nakheel Plaza",
  "city": "Buraidah",
  "country": "Saudi Arabia",
  "mall_information": {
    "Social":              { "X", "Threads", "Youtube", "Facebook", "LinkedIn", "Instagram" },
    "region":              "...",
    "MallMapEn":           "...",
    "mallmapid":           "...",
    "MallLogoEn":          "...",
    "MallNameEn":          "...",
    "MallTiming":          [ { "dayen", "start_time", "end_time" } ],
    "MallContact":         { "Email", "Phone", "Address1En", "Address2En" },
    "TimingMsgEn":         "...",
    "sr_mallname":         "...",
    "GoogleMapURL":        "...",
    "MallImagesEn":        { "Image1", "Image2", "Image3" },
    "MallDescriptionEn":   "...",
    "GravtyMappingAttribute": "..."
  },
  "image":           "...",
  "gps_coordinates": "...",
  "muvilist": {
    "ar": [ { movie fields... } ],
    "en": [ { movie fields... } ]
  },
  "services": [ { "id", "details", "service_details" } ],
  "brands":   [ { "brand_id", "brand_name_en", ..., "available_in_malls", "engagements" } ]
}
```

---

### `mall_and_movie.json` — Mall Metadata + Movie Listings

**API origin:** fetched from the Cenomi property/mall API, returns data for all malls.

**Envelope shape:**
```json
{
  "success": true,
  "data": {
    "list": [ { ...mall records... } ]
  }
}
```

Each mall record contains:
- `property_group_id` — numeric mall identifier (target: `28`)
- `unique_property_id` — internal property ID
- `marketing_name` / `marketing_name_ar` — display name in English and Arabic
- `city`, `country` — location
- `image` — hero image URL
- `gps_coordinates` — lat/lng string
- `mall_information` — nested object with:
  - `Social` — social media links (X, Instagram, Facebook, YouTube, LinkedIn, Threads)
  - `MallNameEn` / `MallNameAr` — localized name
  - `MallLogoEn` / `MallLogoAr` — logo image URLs
  - `MallMapEn` / `MallMapAr` — Mappedin interactive map URLs
  - `mallmapid` — Mappedin map identifier
  - `MallTiming` — opening hours per day, each entry has `dayen` (English day), `dayar` (Arabic day), `start_time`, `end_time`
  - `MallContact` — `Email`, `Phone`, `Address1En`, `Address1Ar`, `Address2En`, `Address2Ar`
  - `MallImagesEn` / `MallImagesAr` — gallery images
  - `MallDescriptionEn` / `MallDescriptionAr` — marketing description
  - `TimingMsgEn` / `TimingMsgAr` — special timing message
  - `GoogleMapURL`, `sr_mallname`, `GravtyMappingAttribute` — additional metadata
- `muvilist` — cinema listings object:
  - `ar` — list of movies for the Arabic audience
  - `en` — list of movies for the English audience
  - Each movie record: `id`, `type`, `title`, `image`, `coverUrl`, `synopsis`, `genres` (with `fontColor`/`backgroundColor` that are stripped in transform), `runtime`, `movieLang`, `releaseDate`, `bookingUrl`, `trailerUrl`, `shareLink`, `titleTag`, `descriptionTag`, `isAdvancedBooking`, `subtitle`

**What the transform keeps:** All top-level mall fields; `mall_information` filtered to English keys only; `muvilist` with genre color fields stripped.

---

### `services.json` — Mall Services

**API origin:** fetched from the Cenomi services API, already scoped to `property_group_id = 28`.

**Envelope shape:**
```json
{
  "success": true,
  "data": {
    "property_group_id": 28,
    "services": [ { ...service records... } ]
  }
}
```

Unlike the other API files, this payload does **not** wrap items in a `list` key and is already filtered to one mall, so no filtering step is needed.

Each service record contains:
- `id` — numeric service identifier
- `details` — array of content blocks for the service description page:
  - Each item: `type` (`"heading"`, `"bullet"`, `"button"`), `content` (English), `content_ar` (Arabic, stripped in transform)
  - Button items additionally carry `button_icon_type`
- `service_details` — operational metadata:
  - `content` — array of items typed as `"title"`, `"time"`, `"location"`, `"phone"` etc.; each has `content` (English) and `content_ar` (Arabic). The `"time"` type is the only case where `content_ar` is preserved in the output.
  - `floor` — floor identifier (e.g., `"groundFloor"`)
  - `icon` — SVG icon URL
  - `image` — card image URL
  - `mappedin_location` — Mappedin location label
  - `mappedin_location_ar` — Arabic Mappedin label (stripped in transform)
  - `navigation_enable` — boolean for in-map navigation support

**What the transform keeps:** `id`, `details` (English content only), `service_details` (all fields except `mappedin_location_ar`; `content_ar` kept only for `"time"` items).

---

### `engagements.json` — Promotions and Offers

**API origin:** fetched from the Cenomi engagements/promotions API, returns promotions across all malls.

**Envelope shape:**
```json
{
  "success": true,
  "data": {
    "list": [ { ...engagement records... } ]
  }
}
```

Each engagement record contains:
- `engagement_id` — unique identifier
- `brand_id` — links the promotion to a brand (used for reverse-mapping into brands)
- `tenant_profile_id` — tenant profile identifier
- `title_en` / `title_ar` — promotion title
- `type` — engagement type (e.g., `"promotions"`)
- `description_en` / `description_ar` — promotional copy
- `terms_conditions_en` / `terms_conditions_ar` — terms text
- `start_date`, `end_date`, `publish_date` — ISO 8601 timestamps
- `is_exclusive` — flag (0/1)
- `images_en` / `images_ar` — arrays of `{ "url", "is_primary" }` objects
- `tags_en` / `tags_ar` — arrays of `{ "tag_id", "tag_text" }` objects
- `ext_url` — external link
- `brand_logo` — brand logo URL
- `brand_name` — brand display name
- `group_name` — store group type
- `property_group_ids` — **list of mall IDs** this promotion applies to (used for filtering)

**Filtering:** The transform script keeps only engagements where `property_group_ids` contains `28`. Engagements are then grouped by `brand_id` and embedded inside their parent brand record.

**What the transform keeps:** All English-facing fields; `images_ar` and `tags_ar` are dropped.

---

### `brands.json` — Brand / Tenant Records

**API origin:** fetched from the Cenomi brands API, returns all brands across all malls.

**Envelope shape:**
```json
{
  "success": true,
  "data": {
    "list": [ { ...brand records... } ]
  }
}
```

Each brand record contains:
- `brand_id` — unique brand identifier (used to merge engagements)
- `anchor_brand` — flag (0/1) marking flagship/anchor tenants
- `tenant_profile_id` — tenant profile identifier
- `brand_name_en` / `brand_name_ar` — brand name
- `brand_logo` — logo image URL
- `company_name_en` / `company_name_ar` — legal company name
- `category_name` / `category_name_ar` — retail category (e.g., `"Cosmetics"`, `"Fashion"`)
- `group_name` / `group_name_ar` — store group type (e.g., `"Shop"`, `"Dining"`)
- `brand_profile_id` — profile page identifier
- `store_phone_code`, `store_phone_number`, `store_email`, `store_website` — contact details
- `publish_date`, `is_published` — visibility metadata
- `social_*` — social handles (tiktok, instagram, facebook, threads, twitter, snapchat, youtube)
- `description_en` / `description_ar` — brand description (HTML)
- `banner_en` / `banner_ar` — banner image URLs
- `images_en` / `images_ar` — gallery images `{ "url", "is_primary" }`
- `tags_en` / `tags_ar` — searchable tags `{ "tag_id", "tag_text" }`
- `google_rating`, `reviews` — ratings data
- `available_in_malls` — **list of mall membership objects**, each with `mall_id`, `property_group_id`, `brand_id`, `pms_unit_code` (unit location codes)
- `pms_unit_codes` — flat list of PMS unit codes across all malls

**Filtering:** Only brands whose `available_in_malls` list contains an entry with `property_group_id = 28` are included. The `available_in_malls` array is further trimmed to only that one entry.

**What the transform keeps:** All English-facing fields; Arabic name/description/banner/images/tags are dropped. `available_in_malls` is filtered to mall 28's entry only. Engagements are embedded under the key `"engagements"`.

---

## 2. Transform Script: `transform_mall_data.py`

### Purpose

`transform_mall_data.py` is a standalone ETL script at the project root. It reads the five `./data/` JSON files and produces `output_mall_28.json` — a single structured document shaped to the `sample.json` contract, scoped entirely to mall `property_group_id = 28`.

**Run:**
```bash
python transform_mall_data.py
```

**Output:** `output_mall_28.json` in the project root.

### Pipeline Overview

```
data/sample.json          ──► validation reference
data/mall_and_movie.json  ──┐
data/services.json        ──┤
data/engagements.json     ──┼──► build_output_mall_28() ──► output_mall_28.json
data/brands.json          ──┘
```

### Key Configuration

| Constant | Value | Purpose |
|---|---|---|
| `TARGET_PROPERTY_GROUP_ID` | `28` | Mall to extract data for |
| `DATA_DIR` | `"./data"` | Source file directory |
| `OUTPUT_FILE` | `"output_mall_28.json"` | Output path |

### Function Reference

#### I/O and Utilities

| Function | Description |
|---|---|
| `load_json(filepath)` | Safe JSON loader — returns `None` on `FileNotFoundError` or `JSONDecodeError` |
| `normalize_id(value)` | Coerces any ID (int, str, float) to a stripped string for safe cross-type comparison (e.g., `28 == "28"`) |
| `index_brands_by_id(brands_list)` | Builds an O(1) `brand_id → brand_dict` lookup for fast engagement merging |

#### Filtering

| Function | Description |
|---|---|
| `filter_engagements_for_mall(list, pgid)` | Returns engagements whose `property_group_ids` list contains the target mall ID |
| `filter_brands_for_mall(list, pgid)` | Returns brands whose `available_in_malls` has an entry matching the target mall ID |
| `get_mall_record(list, pgid)` | Finds the single mall record matching the target `property_group_id` |
| `get_services_for_mall(services_data)` | Extracts the services list from the pre-filtered `services.json` payload |

#### Transformation

| Function | Description |
|---|---|
| `transform_movie(movie)` | Maps a raw movie to the `muvilist` schema — strips `fontColor`/`backgroundColor` from genres |
| `transform_muvilist(raw)` | Applies `transform_movie` to both `ar` and `en` lists |
| `transform_mall_timing(list)` | Extracts `dayen`, `start_time`, `end_time` — drops Arabic day name |
| `transform_mall_contact(raw)` | Keeps `Email`, `Phone`, `Address1En`, `Address2En` — drops Arabic variants |
| `transform_mall_information(raw)` | Maps the full `mall_information` block to English-only keys per `sample.json` |
| `transform_service_detail_item(item)` | Maps one service detail item — keeps `content`, `type`, `button_icon_type`; drops `content_ar` |
| `transform_service_details_content_item(item)` | Maps one `service_details.content` item — keeps `content_ar` only for `"time"` type items |
| `transform_service(service)` | Maps a full service record to the `sample.json` service schema |
| `transform_engagement(eng)` | Maps an engagement to the schema — English fields only, drops `images_ar`/`tags_ar` |
| `transform_brand(brand, engagements)` | Maps a brand record, trims `available_in_malls` to mall 28 only, embeds pre-filtered engagements |

#### Orchestration and Validation

| Function | Description |
|---|---|
| `validate_output(output, sample)` | Checks that top-level keys exactly match `sample.json`; prints warnings for missing or extra keys |
| `build_output_mall_28()` | Main orchestrator: load → filter → transform → assemble → validate → return output dict |
| `main()` | Entry point: calls `build_output_mall_28()` and writes the result to `output_mall_28.json` |

### Orchestration Steps (`build_output_mall_28`)

1. **Load** all five source files via `load_json`.
2. **Unwrap** API envelopes — extract `data.list` from `mall_and_movie.json`, `engagements.json`, and `brands.json`; extract `data.services` from `services.json`.
3. **Locate mall record** — find the single entry in `mall_and_movie.json` where `property_group_id == 28`.
4. **Filter engagements** — keep only those targeting mall 28; group by `brand_id` into a dict for O(1) lookup.
5. **Filter brands** — keep only brands available in mall 28.
6. **Transform each section** using the dedicated transform functions.
7. **Assemble output** — build the final dict with keys matching `sample.json` exactly.
8. **Validate** top-level key parity against `sample.json`.
9. **Print summary** — counts for services, brands, and engagements included.

### Output Shape

The output mirrors `sample.json` at top level, with one addition: each brand object carries an `"engagements"` key (absent from `sample.json` because engagements are brand-scoped data embedded at the brand level):

```json
{
  "property_group_id": 28,
  "marketing_name": "...",
  "city": "...",
  "country": "...",
  "mall_information": { ... },
  "image": "...",
  "gps_coordinates": "...",
  "muvilist": { "ar": [...], "en": [...] },
  "services": [...],
  "brands": [
    {
      "brand_id": ...,
      "brand_name_en": "...",
      "available_in_malls": [ { "mall_id", "property_group_id", "brand_id", "pms_unit_code" } ],
      "engagements": [ { ...transformed engagement... } ],
      ...
    }
  ]
}
```

---

## 3. Vector Ingestion: `ingest_vectors.py`

**File:** `backend/scripts/ingest_vectors.py`

After the canonical file exists, this offline script embeds every entity description and loads the vectors into Chroma so the semantic search fallback in `compose_context` can operate at runtime.

```
data/canonical/{mall_id}.json
    │
    ▼
_extract_entities()          ── builds a dense text blob per entity
    │                             (name, category, tags, description, entity_type_label)
    ▼
VectorStoreService.upsert()  ── embeds each blob with text-embedding-3-small
    │                             then upserts into Chroma collection cenomi_mall_{mall_id}
    ▼
data/chroma/                 ── persistent HNSW index (cosine distance)
```

### Entity text format

`_build_entity_text()` concatenates these fields (deduplicated, comma-separated):

```
name, category, subcategory, dining_style, cuisine_type, tags (up to 12), description (first 200 chars), entity_type_label
```

Example output for a dining entity:
```
Kudu, dining, fast_food, Saudi, burgers, fast food, grilled, quick, halal, dining
```

### Running ingestion

```bash
cd backend

# Single mall
python scripts/ingest_vectors.py --mall-id al_nakheel_plaza_28

# All malls found in data/canonical/
python scripts/ingest_vectors.py --all

# Dry-run: shows what would be embedded without calling OpenAI
python scripts/ingest_vectors.py --all --dry-run
```

**Requires:** `BACKEND_OPENAI_API_KEY` in `.env`, `chromadb` installed.

### Current collection state

| Collection | Stores | Dining | Services | Cinemas | Total |
|---|---|---|---|---|---|
| `cenomi_mall_al_nakheel_plaza_28` | 83 | 10 | 10 | 1 | **104** |
| `cenomi_mall_al_nakheel_plaza_13` | 48 | 5 | 0 | 1 | **54** |

Re-run after any canonical data refresh to keep the vector index in sync.

---

## 4. Context Building Pipeline

Context building happens in two stages: an **offline build** that processes canonical mall data into a pre-assembled context pack, and a **runtime assembly** that uses that pack plus live query state to build the exact LLM prompt for each turn.

### Stage 1 — Offline: `ContextBuilder` → Context Packs (and Vector Ingestion)

**File:** `backend/app/services/context_builder.py`

`ContextBuilder` reads the canonical data in `backend/data/` and produces a `GlobalContextPack` stored in `backend/data/context_packs/`. This runs once (or when data changes) — not on every request.

**Input sources:**

| File | Purpose |
|---|---|
| `backend/data/canonical/al_nakheel_plaza_28.json` | Master mall data: stores, dining, cinemas, movies, services, events, offers |
| `backend/data/semantic/al_nakheel_plaza_28.json` | Enrichment rules and tag taxonomy |
| `backend/data/playbooks/al_nakheel_plaza_28.json` | Scenario playbooks for intent-driven reasoning |

**Pipeline:**

```
load_canonical()
load_semantic()          ──► build_context_pack() ──► GlobalContextPack ──► context_packs/*.json
load_playbooks()
```

**`build_context_pack` produces:**

| Field | Content |
|---|---|
| `mall_profile_summary` | Name, city, tagline, floors, key zones, highlights |
| `operational_context` | Hours, parking, facilities |
| `topic_blocks` | Pre-built blocks per topic: `dining`, `gift`, `family`, `movie`, `services`, `mall_overview`, `quick_visit` |
| `events_and_offers` | Current events and active promotions |
| `playbooks` | Summarized playbook records (trigger conditions, response shape hints, reasoning notes) |
| `contextual_reasoning_hints` | Pre-written concierge reasoning tips |
| `concierge_guidelines` | Behavioral guidelines baked into the context pack |

**Intent-to-topic mapping** (`get_blocks_for_intent`):

At runtime, the pipeline maps detected intent keywords to relevant topic blocks to inject only what is needed:

| Intent keyword | Topic blocks injected |
|---|---|
| `dining`, `food` | `dining` |
| `gift` | `gift` |
| `family` | `family`, `dining`, `mall_overview` |
| `movie` | `movie`, `dining` |
| `quick` | `quick_visit`, `dining` |
| `date` | `dining`, `movie`, `gift` |
| `mall` | `mall_overview`, `services` |

---

### Stage 2 — Runtime: `PromptBuilder` → LLM Messages

**File:** `backend/app/prompts/builder.py`

`PromptBuilder` assembles the full multi-message payload sent to the LLM on every chat turn. It reads from `ConciergeState` — the live pipeline state that carries the detected intent, selected entities, scene memory, and query context.

#### Message structure (in order)

```
Message 1 — SYSTEM:  Identity + behavioral rules + hallucination constraints + tone + strategy
Message 2 — SYSTEM:  Mall intelligence context (operational info, topic blocks, entities, retrieval facts, events)
Message 3 — SYSTEM:  Visitor context + playbook reasoning + reasoning hints
Message 4 — USER:    Current user message (with inline follow-up context when applicable)
```

Previous LLM responses are **never injected**. Conversational continuity is maintained through structured scene memory (`scene.previous_need`, `scene.active_shortlist`, `scene.companions`, etc.) embedded in message 3.

---

#### Message 1 — System Prompt (`build_system_prompt`)

Built from four sub-blocks:

**`_identity_block`**
Sets the concierge persona. Reads `mall_profile_summary.name` from the context pack.
```
You are the Al Nakheel Plaza Concierge — a warm, confident, and knowledgeable
in-mall assistant who helps visitors find exactly what they need.
```

**`_behavioral_rules`**
16 strict rules covering: answer-first bias, low clarification, concierge tone, contextual continuity, floor/zone directions, real-entities-only, concise formatting, structured recommendations, no brochure language, strict category boundaries, no padding, complete lists, experience-based framing, and focused picks.

**`_hallucination_constraints`**
4 hard rules: never invent information, only reference provided entities, respond generically about unconfirmed promotions, only state facts present in context.

**`_tone_block`**
Derived from `TenantRuntime` using `state.active_tenant_parameters` (loaded from `tenant_config/al_nakheel_plaza_28.json`). Generates a natural-language tone instruction from numeric tone axes (warmth, directness, formality, emoji level, etc.).

**`_strategy_block`**
Injects the chosen response strategy and shape hint from `state.response_plan`. Strategies include: `brief_answer`, `numbered_shortlist`, `curated_picks`, `combo_suggestion`, `mini_itinerary`, `structured_itinerary`, `exploration_overview`, `casual_shortlist`, `value_focused_list`, `conversational`.

---

#### Message 2 — Mall Intelligence Context (`build_context_block`)

Assembled from five sub-sections, all prefixed with `MALL INTELLIGENCE (use this to ground your answers):`:

**`_mall_operational_context`**
Reads `operational_context` from the context pack. Formats opening hours per period and parking details.
```
[Mall Operations]
  Weekdays: 9:00 AM – 11:00 PM
  Weekends: 9:00 AM – 11:00 PM
  Friday: 1:00 PM – 11:00 PM
  Parking: 1200 spaces, rate Free, valet not available
```

**`_topic_block_context`**
Injects only the topic blocks selected by the pipeline (from `state.context.selected_topic_blocks`). Each block contributes its summary, concierge tips (up to 4), and semantic highlights (up to 3).
```
[Dining]
  Food court with 10+ fast food and casual dining options...
  • Best for quick lunch: [names]
  ★ Family-friendly atmosphere in the food court
```

**`_entity_context`**
Lists the specific entities retrieved for this query (from `state.context.selected_entities`). The header adapts based on retrieval type:
- **Category lookup:** `[Category-Matched Entities — list ALL of them]`
- **Broad discovery:** `[Stores Across Categories — group by category]`
- **Recommendation:** `[Curated Options — present TOP 5-6 with reasoning]`

Each entity line includes: name, type, category, floor/zone, semantic tags (up to 4), and description or concierge notes.
```
[Curated Options — present your TOP 5-6 with reasoning...]
  • Sephora (store | Cosmetics) — Ground Floor, Main Gallery [beauty, gift_friendly, trendy]
    International beauty retailer with skincare, fragrance, and makeup.
```

**`_retrieval_facts`**
Injects structured key-value facts from `state.retrieval.retrieval_results` (e.g., exact hours, contact details, specific movie showtimes) under `[Exact Facts Retrieved — use these for factual accuracy]`.

**`_events_offers_context`**
Lists up to 6 current events and offers from the context pack under `[Current Events & Offers — mention when relevant]`.

---

#### Message 3 — Scene and Instructions (`build_scene_and_instructions`)

Built from three sub-blocks:

**`_scene_block`**
Injects visitor context under `VISITOR CONTEXT (use to personalize your response):`:
- Companions (family, partner, kids, etc.)
- Occasion (birthday, anniversary, etc.)
- Budget
- Current location in the mall
- Audience signals
- Active topic
- Previous suggestions shown
- Items the visitor rejected
- Previous intent
- Message kind flags: `correction` (visitor correcting an answer), `refinement` (narrowing a previous request), `topic_switch` (fresh topic), `followup` (interpret in prior context)

**`_playbook_block`**
If a playbook is active (`state.playbook.selected_playbook`), injects its scenario name, response shape hint, concierge reasoning notes, fallback rules, and next-step suggestion under `ACTIVE PLAYBOOK:`.

**`_reasoning_hints`**
Up to 5 pre-written concierge reasoning tips from the context pack, under `REASONING HINTS:`.

---

#### Message 4 — Current User Turn (`_build_current_turn`)

For `followup` and `refinement` message kinds, the turn is prefixed with the previous question and audience/companion context so the LLM can interpret the current message correctly:
```
[Previous question: where can I find gifts for kids?]
[With: family, kids]

Current follow-up: what about food?
```

For all other message kinds, the normalized user message is sent directly.

---

### Full Context Flow (End to End)

```
Raw API JSON files (./data/)
        │
        ▼
transform_mall_data.py
        │
        ▼
output_mall_28.json  ──► manually reviewed and curated ──►  backend/data/canonical/*.json
                                                                        │
                                                          ContextBuilder (offline)
                                                                        │
                                                          backend/data/context_packs/*.json
                                                                        │
                                                            loaded at server startup into
                                                                MallContext singleton
                                                                        │
                                              ┌─────────────────────────┴────────────────────────┐
                                              │                                                    │
                                  Per-turn pipeline state                               PromptBuilder
                                  (intent, entities, scene,                      reads context pack +
                                   retrieval, response plan)                     pipeline state to build
                                              │                                  4-message LLM payload
                                              └──────────────────────────────────────────┘
                                                                        │
                                                                   LLM call
                                                                        │
                                                              grounded response
```
