# Component Map

This document maps every implemented component to its location in the codebase and describes its purpose.

---

## Backend — `backend/app/`

### API Layer (`app/api/`)

| File | Purpose |
|------|---------|
| `api/chat.py` | `POST /api/chat` — receives messages, invokes the LangGraph pipeline, returns response + debug payload |
| `api/stream.py` | `POST /api/chat/stream` — SSE streaming endpoint; emits `event: token` per chunk, `event: done` with suggestions; preserves `conversation_mode` across smalltalk turns; non-LLM paths emit `final_response_text` as a single token |
| `api/feedback.py` | `POST /api/feedback` / `GET /api/feedback` — submit and retrieve feedback |
| `api/health.py` | `GET /api/health` — server health check; returns `mall_ids` array |
| `api/session.py` | `GET /api/session`, `POST /api/session/reset` — session state and reset |

### Configuration (`app/config/`)

| File | Purpose |
|------|---------|
| `config/settings.py` | Pydantic-settings configuration; all env vars prefixed `BACKEND_`; `classifier_model` (default `gpt-4o-mini`) for cost-efficient classification calls; `mall_ids` defaults to all five malls |
| `config/constants.py` | Application-wide constants |

### Mall Context (`app/context/`)

| File | Purpose |
|------|---------|
| `context/mall_context.py` | Loads canonical JSON, normalises entities, builds context packs and lookup indices |
| `context/semantic_mall_model.py` | Converts raw canonical data into semantically-tagged tenant entities grouped by experience cluster and category; enables concierge-level reasoning (gift shopping, family outing, romantic evening, etc.) |

### Graph Pipeline (`app/graph/`)

| File | Purpose |
|------|---------|
| `graph/builder.py` | Assembles and compiles the 12-node `ConciergeState` graph with conditional smalltalk and retrieval routing |

### Models (`app/models/`)

| File | Purpose |
|------|---------|
| `models/state.py` | `ConciergeState` — the Pydantic state object that flows through the LangGraph pipeline. Also defines `MessageKind` (str enum, 13 values incl. `CRISIS`, `IDENTITY`, `HOWRU`, `THANKS`, `FAREWELL`, `CATEGORY_NEGATION`, `COMPANION_CORRECTION`), `SMALLTALK_KINDS` frozenset, and `ShoppingTask` sub-model. `SceneMemory` extended with `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task`, `greeting_streak`, `topic_lock`, `topic_lock_confidence`, `recent_mood`, `topic_history` |
| `models/api.py` | `ChatRequest` / `ChatResponse` API contracts |
| `models/tenant.py` | Canonical tenant entities and `TenantConfig` with tunable weights |
| `models/mall.py` | `MallProfile`, zones, facilities |
| `models/context_pack.py` | `GlobalContextPack`, `TopicBlock`, `MallProfileBlock` |
| `models/semantic.py` | `SemanticProfile`, audience fit, priority scores |
| `models/playbook.py` | `ScenarioPlaybook` definition |
| `models/feedback.py` | `FeedbackRecord`, implicit signal models |

### Nodes (`app/nodes/`)

| File | Node | Purpose |
|------|------|---------|
| `nodes/load_session.py` | `load_session` | Loads or creates session; assigns `turn_id`; normalises message; calls `expand_short_query()` to produce a richer `expanded_query` from scene context while preserving `normalized_user_message` |
| `nodes/interpret_turn.py` | `interpret_turn` | Pure LLM classifier (`gpt-4o-mini`). Returns `flow_type`, `response_mode`, `is_gibberish`, `secondary_intents`, `modifiers`, `scenario`, `scene_corrections` — no keyword overrides post-LLM. All `MessageKind` values (`CRISIS`, `IDENTITY`, `HOWRU`, `THANKS`, `FAREWELL`, `CATEGORY_NEGATION`, `COMPANION_CORRECTION`, etc.) are LLM-classified. Emits structured `interpretation_contract` to `debug_enrichment`. |
| `nodes/route_flow.py` | `route_flow` | Thin policy layer — applies six business rules on top of `intent.flow_type_candidate`. Includes `_resolve_response_strategy()` helper and topic lock stability tracking. Keyword/regex tables removed. |
| `nodes/smalltalk.py` | `smalltalk` | Rich fast-path handler. Three-tier progressive greeting pool keyed to `scene.greeting_streak`; LLM-generated personalised farewells (`gpt-4o-mini`); context-aware thanks responses suggesting unexplored domains; mood-plan emotional responses. Routes via `SMALLTALK_KINDS` — no regex. |
| `nodes/update_scene_memory.py` | `update_scene_memory` | LLM-first delta extraction. Extracts `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task`; applies `scene_corrections` from `interpret_turn`; handles abbreviation normalisation and wedding/event role disambiguation |
| `nodes/resolve_playbooks.py` | `resolve_playbooks` | Matches the turn against scenario playbooks. Added occasion/wedding override, luxury playbook guard (against children), and shopping task scope checks |
| `nodes/choose_strategy.py` | `choose_strategy` | Maps intent + playbook → response strategy and shape hint |
| `nodes/compose_context.py` | `compose_context` | Selects topic blocks, ranks entities via playbook scoring, builds semantic signals. Includes vector search fallback for fuzzy queries with no category rule match. Before calling the fallback, augments the query with `build_scene_prefix(scene)` so the embedding reflects the visitor's actual context (occasion, companions, visit_type, budget). |
| `nodes/decide_retrieval.py` | `decide_retrieval` | Gates exact-fact retrieval (hours, showtimes, offers) — most turns skip this |
| `nodes/fetch_exact_facts.py` | `fetch_exact_facts` | Executes targeted lookups when retrieval is flagged |
| `nodes/generate_response.py` | `generate_response` | Assembles prompt, calls LLM, runs hallucination guard, returns `final_response_text` |
| `nodes/update_memory.py` | `update_memory` | Extracts mentioned entities into shortlist; persists scene for next turn |
| `nodes/emit_debug_payload.py` | `emit_debug_payload` | Builds the debug trace (intent, playbook, strategy, retrieval, latency, node trace) |
| `nodes/_tracing.py` | — | `@traced_node` decorator — wraps each node with latency tracking and trace emission |

### Prompts (`app/prompts/`)

| File | Purpose |
|------|---------|
| `prompts/builder.py` | Assembles the full LLM prompt: identity, mall context, scene memory, grounding constraints, strategy hints |

### Retrieval (`app/retrieval/`)

| File | Purpose |
|------|---------|
| `retrieval/retriever.py` | Multi-layer lookup over canonical entities, semantic profiles, and playbooks. `build_scene_prefix(scene)` constructs a keyword prefix from `SceneMemory` (`occasion`, `companions`, `visit_type`, `budget`) that is prepended to vector queries before embedding, so contextually different sessions produce different embedding vectors. `MallRetriever.search_semantic(query, scene=None)` accepts an optional `scene` — when provided, the prefix is applied internally before embedding, making direct call sites from tests, scripts, or future nodes automatically context-aware. |

### Services (`app/services/`)

| File | Purpose |
|------|---------|
| `services/concierge.py` | Orchestrates a single chat turn by invoking the compiled LangGraph pipeline; calls `ensure_mall_loaded()` at turn start |
| `services/semantic_signals.py` | Pure lookup-table signal extraction — maps `SceneMemory` and `InterpretedIntent` fields to semantic tags; no regex or raw text matching |
| `services/vector_store.py` | `VectorStoreService` — Chroma vector search with Redis result cache (`cenomi:vcache:{mall_id}:{hash}`). Embeds queries with `text-embedding-3-small`, caches results at `BACKEND_VECTOR_CACHE_TTL`. |
| `services/clean_context.py` | Builds a contamination-free per-turn context (no raw LLM history — only structured scene + mall data) |
| `services/context_builder.py` | Assembles `GlobalContextPack` from canonical, semantic, and playbook data layers |
| `services/enricher.py` | Enriches entities with semantic profiles and concierge notes |
| `services/normalizer.py` | Normalises raw canonical JSON into typed entity models |
| `services/playbook_engine.py` | Loads, scores, and ranks `ScenarioPlaybook` objects against the current turn |
| `services/session_store.py` | In-memory LRU session store (max 1 000 sessions); Redis-backed via `RedisSessionStore` when `BACKEND_REDIS_URL` is set |
| `services/tenant_params.py` | Loads and merges `TenantConfig` from defaults + per-mall overrides |
| `services/tenant_runtime.py` | Runtime tenant config management |
| `services/feedback_service.py` | Full feedback lifecycle: receive → validate → persist → aggregate |
| `services/feedback_normalizer.py` | Normalises explicit and implicit feedback into actionable signals |
| `services/implicit_feedback_detector.py` | Detects implicit negative signals (e.g., correction, rejection, repetition) |
| `services/session_tuning_engine.py` | Applies feedback-based parameter tuning per session |
| `services/tenant_parameter_tuner.py` | Aggregates feedback across sessions to tune tenant-level weights |
| `services/knowledge_gap_analyzer.py` | Identifies gaps in playbook coverage and canonical data |
| `services/response_mode_resolver.py` | Thin policy layer — trusts `intent.response_mode_hint` from the LLM; applies hard overrides only for out-of-scope requests, unsupported/gibberish input, or short factual follow-ups on a locked topic. Previous keyword pattern matching removed. |
| `services/semantic_signals.py` | Simplified lookup-table based signal extraction. All regex and raw text matching removed; maps structured `SceneMemory` and `InterpretedIntent` fields directly to semantic tags via pure lookup tables |

### Observability (`app/observability/`)

| File | Purpose |
|------|---------|
| `observability/logger.py` | Structured logger with request context |

### Utilities (`app/utils/`)

| File | Purpose |
|------|---------|
| `utils/ids.py` | ID generation: `session-<hex16>`, `msg-<hex12>`, `fb-<hex12>` |

---

## Backend — `backend/scripts/`

| File | Purpose |
|------|---------|
| `scripts/ingest_vectors.py` | Offline vector ingestion — builds rich text descriptions for each entity, embeds with `text-embedding-3-small`, upserts into per-mall Chroma collection. Run once per mall or after data updates: `python scripts/ingest_vectors.py --mall-id al_nakheel_plaza_28` or `--all` |
| `scripts/convert_to_canonical.py` | Deterministic ETL — converts `output_mall_N.json` → `data/canonical/{mall_id}.json`; no LLM required |
| `scripts/synthesize_mall_data.py` | LLM synthesis — takes a canonical file and produces semantic, playbooks, tenant_config, and context_pack layers |
| `scripts/generate_mall_data.py` | Full pipeline — `output_mall_N.json` → all five intelligence layers; supports `--dry-run`, `--canonical-only`, `--steps` |
| `scripts/run_all_tests.py` | Comprehensive test runner — executes all three test suites (standard, broken, complex multi-turn) against the live backend, generates Markdown + JSON reports in `test-results/`. Supports `--suite all|standard|broken|complex` and `--no-wait` flags. |

---

## Backend — `backend/data/`

| Directory / File | Purpose |
|------------------|---------|
| `canonical/al_nakheel_plaza_{1,10,13,27,28}.json` | Single source of truth — all stores, dining, cinemas, movies, services, events, offers per mall |
| `semantic/al_nakheel_plaza_{1,10,13,27,28}.json` | Derived semantic intelligence — tags, audience fit, priority scores, concierge notes per mall |
| `playbooks/al_nakheel_plaza_{1,10,13,27,28}.json` | Pre-built scenario playbooks per mall |
| `context_packs/al_nakheel_plaza_{1,10,13,27,28}_context.json` | Pre-assembled context bundles per mall |
| `tenant_config/tenant_defaults.json` | Default tenant behavioral weights |
| `tenant_config/al_nakheel_plaza_{1,10,13,27,28}.json` | Mall-specific tenant config overrides |
| `chroma/` | Chroma vector DB persistence — per-mall HNSW collections populated by `ingest_vectors.py`. Cosine distance index. Mounted as a Docker volume so embeddings survive image rebuilds. |
| `feedback/implicit/` | Auto-generated implicit feedback signal files |
| `feedback/normalized/` | Normalized and aggregated feedback records |
| `examples/example_turn_state.json` | Reference `ConciergeState` for development and testing |
| `examples/mall_profile.json` | Sample mall profile structure |
| `examples/playbooks_sample.json` | Sample playbook definitions |
| `examples/semantic_entries_sample.json` | Sample semantic entries |
| `examples/tenant_params_sample.json` | Sample tenant parameter schema |
| `examples/tenants_sample.json` | Sample tenant records |

---

## Frontend — `frontend/src/`

### Pages (`src/pages/`)

| File | Purpose |
|------|---------|
| `pages/ChatPage.tsx` | Main page — renders message list, welcome screen, typing indicator, and chat input |

### Components (`src/components/`)

| File | Purpose |
|------|---------|
| `components/TopBar.tsx` | Header — five-mall selector, session ID, debug toggle, export, reset |
| `components/ChatInput.tsx` | Message textarea with send button (4 000-character limit) |
| `components/ChatMessage.tsx` | Individual message bubble — AI response, cited sources, feedback controls |
| `components/SuggestedChips.tsx` | Clickable suggestion chips for quick prompts |
| `components/FeedbackControls.tsx` | Thumbs up/down with optional negative reasons and comment field |
| `components/DebugPanel.tsx` | Collapsible debug sidebar — displays `TurnInspector` and `RawDrawer` per turn |
| `components/TurnInspector.tsx` | Per-turn pipeline inspector — intent, strategy, context blocks, retrieval, scene snapshot |
| `components/StrategyCard.tsx` | Displays selected playbook, confidence, strategy, and response shape |
| `components/RetrievalCard.tsx` | Shows retrieval status, reason, targets, and result count |
| `components/ContextBlocksCard.tsx` | Displays topic blocks, entities, semantic signals, and ranking notes (updated for v1.6 signal fields) |
| `components/SceneSnapshotCard.tsx` | Key-value display of the current scene snapshot — updated for all v1.6 fields: `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task`, `greeting_streak`, `recent_mood` |
| `components/RawDrawer.tsx` | Expandable raw JSON viewer for trace, state, and prompt data with copy functionality |

### Hooks (`src/hooks/`)

| File | Purpose |
|------|---------|
| `hooks/useChat.ts` | Core chat hook — send message, feedback submission, session reset, export, mock mode |

### API Client (`src/api/`)

| File | Purpose |
|------|---------|
| `api/client.ts` | HTTP client for all backend API calls with typed request/response shapes |

### Types (`src/types/`)

| File | Purpose |
|------|---------|
| `types/api.ts` | TypeScript types for API request and response payloads |
| `types/chat.ts` | TypeScript types for chat state, messages, and `DebugPayload` |

### Supporting Files

| File | Purpose |
|------|---------|
| `lib/constants.ts` | Application-wide frontend constants — `MALLS` array now includes all five malls with their IDs, labels, and cities |
| `mock/responses.ts` | Mock API responses for offline development |
| `styles/index.css` | Tailwind imports and custom CSS variables |
| `assets/hero.png` | Hero image asset |

---

## Entry Points

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI entrypoint — lifespan initialises mall context, session store, feedback services |
| `backend/app/runtime.py` | Global singletons — `LRUMallContextRegistry` (three-tier RAM → Redis → disk, capacity = `BACKEND_MALL_CACHE_SIZE` default 5), session store, feedback service, `VectorStoreService`. Async helpers `search_brand_across_configured_malls` and `build_merged_guard_canonical_for_configured_malls` handle LRU-safe multi-mall queries. |
| `frontend/src/main.tsx` | React entry point — `createRoot`, `StrictMode` |
| `frontend/src/App.tsx` | Root component with routing |
