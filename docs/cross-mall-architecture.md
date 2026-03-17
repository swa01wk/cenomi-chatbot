# Cross-Mall Architecture — Implementation, Architecture & Graph Design

This document captures the **as-is** state of the cross-mall implementation, documents its architecture, and defines the graph design that governs how multi-mall queries are detected and answered today and in the near future.

---

## 1. What Is Cross-Mall?

Cross-mall is the ability of a visitor chatting in the context of **one home mall** to ask questions that span **multiple Cenomi properties**:

| Query Type | Example | Home-mall relevant? |
|-----------|---------|---------------------|
| Brand presence check | "Does Mall of Arabia also have Starbucks?" | Yes — check both |
| Brand exclusive check | "Is Charles & Keith at any other Cenomi mall?" | Yes — home first |
| Cross-property comparison | "Which of your malls has H&M?" | Both malls equally |
| Availability fallback | "Does Al Nakheel have Popeyes?" (from Mall of Arabia) | Other mall |

Single-mall questions (`"Do you have Zara?"`) are **not** affected and flow through the standard single-mall pipeline.

---

## 2. Current Implementation Status

### What Is Built (as of now)

| Capability | Status | Location |
|-----------|--------|---------|
| Multi-mall data loading at startup | **Done** | `app/runtime.py` → `initialize()` |
| Runtime registry for all mall contexts | **Done** | `_mall_contexts: dict[str, MallContextLoader]` |
| `search_brand_across_malls()` utility | **Done** | `app/runtime.py` |
| `get_loaded_mall_ids()` | **Done** | `app/runtime.py` |
| `get_all_mall_canonical_for_guard()` | **Done** | `app/runtime.py` |
| Regex-based cross-mall intent detection | **Done** | `intent/query_classifier.py` |
| `domain = cross_mall`, `sub_intent = cross_mall_search` | **Done** | `app/nodes/interpret_turn.py` |
| LLM classifier instructions for cross-mall | **Done** | `CLASSIFICATION_PROMPT` |
| Cross-mall context injection into generate_response | **Done** | `app/nodes/generate_response.py` |
| Hallucination guard that covers all-mall entities | **Done** | `get_all_mall_canonical_for_guard()` |
| Multi-mall health endpoint (`mall_ids` array) | **Done** | `app/api/health.py` |
| Frontend mall selector for home mall | **Done** | `TopBar.tsx` |

### What Is Not Yet Built

| Capability | Status | Notes |
|-----------|--------|-------|
| Dedicated `cross_mall_search` graph node | Not done | Currently inline in `generate_response` |
| Cross-mall playbook resolution | Not done | Playbooks are per-mall only |
| Cross-mall scene memory | Not done | `SceneMemory` is home-mall anchored |
| Cross-mall semantic signal ranking | Not done | Signals are single-mall only |
| Cross-mall concierge plans ("visit both malls") | Not done | No multi-mall itinerary |
| Mall-switching recommendations | Not done | No "that brand is only at Mall X" redirect |
| Travel/distance awareness between malls | Not done | Not in data model |
| Cross-mall entity deduplication | Not done | `rank_and_dedupe` is single-mall |
| Cross-mall `DebugEnrichment` fields | Not done | Debug only shows home mall data |

---

## 3. System Architecture

### Runtime Data Model

At startup, `app/runtime.py` initializes one `MallContextLoader` per mall listed in `BACKEND_MALL_IDS`:

```
startup
  │
  ├── runtime.initialize(["al_nakheel_plaza_28", "al_nakheel_plaza_13"])
  │         │
  │         ├── MallContextLoader("al_nakheel_plaza_28").load()   → _mall_contexts["al_nakheel_plaza_28"]
  │         └── MallContextLoader("al_nakheel_plaza_13").load()   → _mall_contexts["al_nakheel_plaza_13"]
  │
  └── Shared services: SessionStore, FeedbackService, ImplicitFeedbackDetector
```

Each `MallContextLoader` holds:

```
MallContextLoader
├── _builder: ContextPackBuilder         ← canonical + semantic + playbooks
│     ├── _canonical: CanonicalMallData  ← stores, dining, cinemas, movies, services, events, offers, mall_profile
│     ├── _semantic: list[SemanticEntry]
│     └── _playbooks: list[ScenarioPlaybook]
├── get_context_pack()                   ← GlobalContextPack (topic blocks + entity lists)
├── get_canonical_for_prompt()           ← flat dict for LLM system prompt
├── get_canonical_for_guard()            ← sections for hallucination guard
└── match_playbook(signals)              ← playbook matcher
```

### Cross-Mall Data Access Utilities (runtime.py)

```python
# All loaded mall IDs
get_loaded_mall_ids() → ["al_nakheel_plaza_28", "al_nakheel_plaza_13"]

# Brand search across all malls, home mall first
search_brand_across_malls(brand_name, home_mall_id) → [
    {"mall_id": "al_nakheel_plaza_28", "name": "Starbucks", "is_home_mall": True, ...},
    {"mall_id": "al_nakheel_plaza_13", "name": "Starbucks", "is_home_mall": False, ...},
]

# Merged canonical dict for hallucination guard
get_all_mall_canonical_for_guard() → {
    "stores": [...all_malls_stores...],
    "dining": [...all_malls_dining...],
    ...
}
```

### Configuration

```bash
# backend/.env
BACKEND_MALL_IDS=al_nakheel_plaza_28,al_nakheel_plaza_13
```

Each mall needs its data files in the standard per-mall paths:

```
backend/data/
├── canonical/al_nakheel_plaza_28.json
├── canonical/al_nakheel_plaza_13.json
├── semantic/al_nakheel_plaza_28.json
├── semantic/al_nakheel_plaza_13.json
├── playbooks/al_nakheel_plaza_28.json
├── playbooks/al_nakheel_plaza_13.json
├── context_packs/al_nakheel_plaza_28_context.json
└── context_packs/al_nakheel_plaza_13_context.json
```

---

## 4. Intent Detection — Cross-Mall Classification

### Detection Priority

Cross-mall intent is detected before LLM classification fires. It uses a **high-priority regex rule** in `intent/query_classifier.py`:

```
User message
    │
    ├── 1. Exact/static table lookup   (< 3 words — no cross-mall queries here)
    │
    ├── 2. Cross-mall regex match (HIGH PRIORITY — before general keyword rules)
    │        Patterns: "mall of arabia", "nakheel", "other mall", "both malls",
    │                  "any cenomi mall", "across malls", "which.*mall.*has",
    │                  "does.*also have", "available at.*other"
    │        → domain="cross_mall", sub_intent="cross_mall_search", conf=0.97
    │
    ├── 3. General keyword rules
    │
    └── 4. LLM fallback
```

### LLM Classifier Instructions (when rule confidence is insufficient)

`CLASSIFICATION_PROMPT` in `interpret_turn.py` instructs the LLM:

> Use `cross_mall` ONLY when the question explicitly references other malls, "both malls", "any of your malls", "Mall of Arabia", "across malls", or asks "does [other mall] also have X?"
>
> DO NOT use `cross_mall` for single-mall questions like "Do you have Nike?" or "Is H&M here?"

### Valid Cross-Mall Values

```python
VALID_DOMAINS = { ..., "cross_mall" }
VALID_SUB_INTENTS = { ..., "cross_mall_search" }
```

---

## 5. Graph Design — Current Single Pipeline Handling

The current graph is a **single unified pipeline** for both single-mall and cross-mall queries. Cross-mall is not a separate branch — it is handled by injecting cross-mall results into the existing `generate_response` node when `intent.domain == "cross_mall"`.

```
START
  │
  ▼
load_session
  │   Reads: session_id, mall_id (= home_mall_id), raw_user_message
  ▼
interpret_turn
  │   Detects cross_mall via regex → domain="cross_mall", sub_intent="cross_mall_search"
  │
  ├─── (smalltalk) ───► smalltalk ──────────────────────────────────┐
  │                                                                  │
  ▼ (normal path — including cross_mall)                            │
update_scene_memory                                                  │
  │   cross_mall: no special branch — scene updated normally        │
  ▼
resolve_playbooks
  │   cross_mall: no home-mall playbook matches; returns empty      │
  ▼
choose_strategy
  │   cross_mall domain → strategy="direct_fact" or "shortlist"    │
  ▼
compose_context
  │   cross_mall: home-mall context only (current limitation)       │
  ▼
rank_and_dedupe
  │   cross_mall: entities from home mall only (current limitation) │
  ▼
decide_retrieval
  │   cross_mall: retrieval_needed=True (brand lookup needed)       │
  ▼
fetch_exact_facts
  │   Triggers search_brand_across_malls() via retriever            │
  │   Returns: [{mall_id, name, floor, category, is_home_mall}, …]  │
  ▼
generate_response
  │   Detects intent.domain == "cross_mall"                         │
  │   Injects cross-mall results into prompt                        │
  │   Hallucination guard uses get_all_mall_canonical_for_guard()   │
  │   (all malls' entities are valid, not just home mall)           │
  ▼
update_memory
  ▼
emit_debug_payload
  ▼
END
```

### How generate_response Handles Cross-Mall

When `intent.domain == "cross_mall"`, `generate_response` uses a dedicated path:

1. Calls `search_brand_across_malls(brand_name, home_mall_id)` to get per-mall results
2. Formats results grouped by `is_home_mall` (home first)
3. Injects into the LLM prompt:
   - Cross-mall search results (home mall prominently)
   - Instruction: "Home mall results first; clearly label which mall each entity belongs to"
4. Hallucination guard validates against `get_all_mall_canonical_for_guard()` (all malls merged)

---

## 6. State Schema — Cross-Mall Relevant Fields

The current `ConciergeState` is home-mall scoped. These fields are relevant to cross-mall:

| Field | Type | Cross-Mall Use |
|-------|------|---------------|
| `mall_id` | `str` | Home mall ID — the visitor's current/primary mall |
| `active_mall_id` | `str` | Same as `mall_id` for V1; may diverge in future |
| `intent.domain` | `str` | Set to `"cross_mall"` when cross-mall detected |
| `intent.sub_intent` | `str` | Set to `"cross_mall_search"` |
| `retrieval.retrieval_results` | `list[dict]` | Cross-mall search results injected here |
| `context.selected_entities` | `list[dict]` | Contains `mall_id` field per entity for cross-mall |

### Entity Shape in Cross-Mall Results

```python
{
    "mall_id": "al_nakheel_plaza_28",
    "mall_name": "Al Nakheel Plaza",
    "entity_type": "stores",       # stores | dining | cinemas
    "name": "Starbucks",
    "floor": "Ground Floor",
    "category": "Café",
    "is_home_mall": True,
    "source": "cross_mall_search",
}
```

---

## 7. Loaded Mall Reference (Current)

| Mall ID | Mall Name | City |
|---------|-----------|------|
| `al_nakheel_plaza_28` | Al Nakheel Plaza | Buraidah |
| `al_nakheel_plaza_13` | Mall of Arabia | Jeddah |

### Known Cross-Mall Brand Matrix

| Brand | Al Nakheel (28) | Mall of Arabia (13) |
|-------|----------------|---------------------|
| Starbucks | yes | yes |
| Zara | yes | yes |
| Bershka | yes | yes |
| The Body Shop | yes | yes |
| MINISO | yes | yes |
| Bath & Body Works | yes | yes |
| Baskin Robbins | yes | yes |
| Ajmal Perfumes | yes | yes |
| McDonald's | yes | **no** |
| Charles & Keith | yes | **no** |
| Red Tag | yes | **no** |
| Kudu Restaurant | **no** | yes |
| Popeyes | **no** | yes |

---

## 8. Response Behavior

### Home-Mall-First Ordering

`search_brand_across_malls()` sorts results:

```python
results.sort(key=lambda r: (0 if r["is_home_mall"] else 1, r["mall_name"]))
```

The LLM is instructed to follow this ordering: home mall first, then other properties.

### Response Patterns by Query Type

| Query | Response Shape |
|-------|---------------|
| Brand in both malls | Confirms at home mall first, then names other malls |
| Brand only at home | Confirms at home, states not listed at other malls |
| Brand only at other mall | States not at home mall, points to the other property |
| Which malls carry X | Lists all carrying malls, home first |
| General "any Cenomi mall" | Exhaustive per-mall breakdown |

### Hallucination Guard — All-Mall Mode

For cross-mall responses, the guard switches from home-mall-only validation to all-mall validation:

```python
# Single-mall path
guard_data = get_mall_context(home_mall_id).get_canonical_for_guard()

# Cross-mall path
guard_data = get_all_mall_canonical_for_guard()   # merged across all loaded malls
```

This prevents the guard from incorrectly stripping valid entity names from other malls.

---

## 9. Debug Output — Cross-Mall Fields

When `debug=True`, the cross-mall path surfaces:

```json
{
  "intent": {
    "domain": "cross_mall",
    "sub_intent": "cross_mall_search",
    "classifier": "rule_based",
    "confidence": 0.97
  },
  "selected_entities": [
    { "name": "Starbucks", "mall_id": "al_nakheel_plaza_28", "is_home_mall": true },
    { "name": "Starbucks", "mall_id": "al_nakheel_plaza_13", "is_home_mall": false }
  ]
}
```

---

## 10. Current Gaps & Limitations

The present cross-mall implementation handles brand presence queries well. The following gaps exist for more complex cross-mall scenarios:

### Gap 1 — No Dedicated Graph Node

Cross-mall context assembly is embedded in `generate_response` rather than being a first-class pipeline stage. This means:
- `compose_context` and `rank_and_dedupe` use home-mall entities only
- Cross-mall results bypass the scene/playbook/ranking pipeline
- No `debug_enrichment` fields for cross-mall ranking

### Gap 2 — Brand Search Only

`search_brand_across_malls()` is a name-match search (`query in entity_name.lower()`). It does not support:
- Category-level cross-mall queries ("Which mall has more dining options?")
- Semantic cross-mall matching ("Which mall is better for a family visit?")
- Cross-mall experience comparison

### Gap 3 — No Scene Memory Cross-Mall Propagation

If a visitor has established a family-visit scene (child companion, implicit goal) and asks a cross-mall question, the cross-mall response does not respect scene context. It treats the query as a raw brand lookup.

### Gap 4 — No Cross-Mall Playbook or Strategy

There are no playbooks for cross-mall scenarios. The `choose_strategy` node defaults to `direct_fact` or `shortlist_recommendation` for all cross-mall queries, regardless of visit context.

### Gap 5 — No Mall-Switch Recommendation

The system cannot yet recommend that a visitor **go to a different mall** because a brand or experience they are seeking is exclusively there. This is a valuable "I should tell you — Popeyes is at Mall of Arabia, not here" concierge behavior.

---

## 11. Near-Term Design Upgrade Path

The following upgrades are scoped and well-contained. They do not require a graph rewrite.

### Upgrade A — Cross-Mall Context Node

Insert a `compose_cross_mall_context` node that runs when `intent.domain == "cross_mall"`. This node:
- Calls `search_brand_across_malls()`
- Optionally searches semantically across malls (by category or tag)
- Produces a structured `cross_mall_results` block in `context`
- Allows `rank_and_dedupe` to apply home-mall preference weighting

**Graph change:**

```
decide_retrieval
    │
    ├── (cross_mall + retrieval_needed)  → compose_cross_mall_context → generate_response
    ├── (retrieval_needed)               → fetch_exact_facts          → generate_response
    └── (not needed)                                                   → generate_response
```

### Upgrade B — Cross-Mall Strategy and Playbook

Add a `cross_mall_brand_check` strategy in `choose_strategy.py` with:
- Shape: `cross_mall_comparison`
- Entity cap: all matching results (no cap — user expects exhaustive answer)
- `must_acknowledge_scene = False` (typically a factual lookup)

Add a `pb-cross-mall-check` playbook with:
- Trigger: `domain == "cross_mall"`
- Response shape: home-mall first, then per-mall breakdown
- No ranking boosts (show all results)

### Upgrade C — Scene-Aware Cross-Mall

If the scene has a `visit_type` or `companions`, the cross-mall response should respect it:

```
"Does Mall of Arabia also have a fun place for kids?"
→ cross_mall brand search filtered by kid_friendly tag
→ child companion acknowledged in response
→ "Yes, Mall of Arabia has [X], which is great for kids too"
```

This requires `compose_cross_mall_context` to accept scene signals and apply them as filters.

### Upgrade D — Mall-Switch Recommendation

When `intent.domain != "cross_mall"` but the home-mall context has no match for a user query, and a loaded other mall does have a strong match:
- New flag: `retrieval.cross_mall_fallback = True`
- `generate_response` appends a non-intrusive mention: "That said, [Brand X] is available at Mall of Arabia if you're able to visit."

---

## 12. Full Future Architecture — Multi-Mall Concierge

This section documents the target architecture for a full multi-mall concierge capability beyond brand lookup.

### Graph Topology (Future)

```
START
  │
  ▼
load_session
  │
  ▼
interpret_turn
  │
  ├── (smalltalk)   → smalltalk ──────────────────────────────────────────────┐
  │                                                                            │
  ├── (cross_mall)  → update_scene_memory                                     │
  │                   → resolve_cross_mall_intent          ← NEW              │
  │                   → compose_cross_mall_context         ← NEW              │
  │                   → rank_cross_mall_results            ← NEW              │
  │                   → generate_response                                     │
  │                   → update_memory → emit_debug_payload → END              │
  │                                                                            │
  └── (normal)      → update_scene_memory                                     │
                      → resolve_playbooks                                     │
                      → choose_strategy                                       │
                      → compose_context                                       │
                      → rank_and_dedupe                                       │
                      → [optional: cross_mall_fallback_check]  ← NEW         │
                      → decide_retrieval                                      │
                      → fetch_exact_facts?                                    │
                      → generate_response                                     │
                      │                                                       │
                      ├───────────────────────────────────────────────────────┘
                      ▼
                    update_memory
                      ▼
                    emit_debug_payload
                      ▼
                     END
```

### New Nodes (Future)

| Node | Purpose |
|------|---------|
| `resolve_cross_mall_intent` | Classifies cross-mall query into: brand_check, category_compare, mall_recommendation, availability_fallback |
| `compose_cross_mall_context` | Searches all mall contexts using brand name, category, or semantic tags; groups results by mall |
| `rank_cross_mall_results` | Scores results by: home-mall preference, semantic fit to scene, brand relevance; caps and orders |
| `cross_mall_fallback_check` | On single-mall no-match, checks other malls silently; sets `retrieval.cross_mall_fallback` if relevant |

### State Schema Extensions (Future)

```python
class CrossMallResult(BaseModel):
    mall_id: str
    mall_name: str
    entity_type: str
    name: str
    floor: str = ""
    category: str = ""
    semantic_tags: list[str] = []
    is_home_mall: bool = False
    audience_fit: list[str] = []

class CrossMallContext(BaseModel):
    query_brand: str = ""
    query_category: str = ""
    query_semantic_signals: list[str] = []
    results_by_mall: dict[str, list[CrossMallResult]] = {}
    home_mall_has_match: bool = False
    other_malls_with_match: list[str] = []
    cross_mall_fallback_triggered: bool = False

# Added to ConciergeState:
cross_mall_context: CrossMallContext = Field(default_factory=CrossMallContext)
```

### Cross-Mall Strategies (Future)

| Strategy | Shape | When |
|----------|-------|------|
| `cross_mall_brand_check` | per_mall_factual | Brand presence query across malls |
| `cross_mall_category_compare` | structured_comparison | "Which mall has more dining?" |
| `cross_mall_recommendation` | guided_suggestion | "Which mall should I visit for X?" |
| `cross_mall_fallback_mention` | brief_addendum | Inline mention when home mall lacks match |

### Cross-Mall Playbooks (Future)

| Playbook ID | Trigger | Response Shape |
|-------------|---------|---------------|
| `pb-cross-mall-brand-check` | `domain=cross_mall`, brand name in query | Exhaustive per-mall list, home first |
| `pb-cross-mall-family` | `domain=cross_mall` + child companion | Kid-friendly filter applied, guided |
| `pb-cross-mall-gift` | `domain=cross_mall` + gift sub-intent | Premium/gift stores across malls |
| `pb-mall-recommendation` | "which mall is better for X" | Comparative guided recommendation |

---

## 13. API Contract — Cross-Mall

### Current Request (no change needed)

```json
{
  "message": "Does Mall of Arabia also have Starbucks?",
  "mall_id": "al_nakheel_plaza_28",
  "tenant_id": "al_nakheel_plaza_28",
  "session_id": "optional",
  "debug": false
}
```

The `mall_id` field designates the visitor's **home mall**. All cross-mall results are computed relative to this.

### Current Response Shape

```json
{
  "session_id": "session-abc123",
  "message": "Yes! Starbucks is available here at Al Nakheel Plaza (Ground Floor, Zone A). It's also at Mall of Arabia in Jeddah. Would you like details on either location?",
  "sources": [
    { "name": "Starbucks", "mall_id": "al_nakheel_plaza_28", "is_home_mall": true, "floor": "Ground Floor" },
    { "name": "Starbucks", "mall_id": "al_nakheel_plaza_13", "is_home_mall": false, "floor": "Level 1" }
  ],
  "debug": {
    "intent": { "domain": "cross_mall", "sub_intent": "cross_mall_search", "classifier": "rule_based", "confidence": 0.97 }
  }
}
```

### Future Response Shape (with CrossMallContext)

```json
{
  "session_id": "session-abc123",
  "message": "...",
  "sources": [...],
  "cross_mall_summary": {
    "query_brand": "Starbucks",
    "home_mall_has_match": true,
    "other_malls_with_match": ["al_nakheel_plaza_13"],
    "results_by_mall": {
      "al_nakheel_plaza_28": [{ "name": "Starbucks", "floor": "Ground Floor" }],
      "al_nakheel_plaza_13": [{ "name": "Starbucks", "floor": "Level 1" }]
    }
  }
}
```

---

## 14. Running Cross-Mall Locally

```bash
# 1. Set both malls in .env
echo "BACKEND_MALL_IDS=al_nakheel_plaza_28,al_nakheel_plaza_13" >> backend/.env

# 2. Start the server
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# 3. Confirm both malls loaded
curl -s http://localhost:8000/api/health | python3 -m json.tool
# Expected: "mall_ids": ["al_nakheel_plaza_28", "al_nakheel_plaza_13"]

# 4. Cross-mall query
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Does Mall of Arabia also have Starbucks?",
    "mall_id": "al_nakheel_plaza_28",
    "debug": true
  }' | python3 -m json.tool
```

For full test scripts and edge cases, see [`docs/cross-mall-testing.md`](cross-mall-testing.md).

---

## 15. Key Files Reference

| File | Cross-Mall Role |
|------|----------------|
| `backend/.env` | `BACKEND_MALL_IDS` — comma-separated list of mall IDs to load |
| `app/runtime.py` | Multi-mall context registry; `search_brand_across_malls()`; `get_all_mall_canonical_for_guard()` |
| `app/main.py` | Lifespan calls `runtime.initialize(settings.mall_ids)` |
| `app/config/settings.py` | `mall_ids: list[str]` setting (parsed from `BACKEND_MALL_IDS`) |
| `app/nodes/interpret_turn.py` | `cross_mall` domain + regex detection |
| `intent/query_classifier.py` | High-priority cross-mall regex rules |
| `app/nodes/generate_response.py` | Cross-mall path: injects brand results, uses all-mall guard |
| `app/api/health.py` | Returns `mall_ids` list for observability |
| `app/context/mall_context.py` | Per-mall intelligence loader |
| `guardrails/hallucination_guard.py` | All-mall guard mode for cross-mall responses |
| `frontend/src/components/TopBar.tsx` | Mall selector UI (home mall selector) |
