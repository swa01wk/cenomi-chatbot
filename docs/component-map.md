# Component Map

This document maps every implemented component to its location in the codebase and describes its purpose.

---

## Backend — `backend/app/`

### API Layer (`app/api/`)

| File | Purpose |
|------|---------|
| `api/chat.py` | `POST /api/chat` — receives messages, invokes the LangGraph pipeline, returns response + debug payload |
| `api/feedback.py` | `POST /api/feedback` / `GET /api/feedback` — submit and retrieve feedback |
| `api/health.py` | `GET /api/health` — server health check |
| `api/session.py` | `GET /api/session`, `POST /api/session/reset` — session state and reset |

### Configuration (`app/config/`)

| File | Purpose |
|------|---------|
| `config/settings.py` | Pydantic-settings configuration; all env vars prefixed `BACKEND_` |
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
| `models/state.py` | `ConciergeState` — the Pydantic state object that flows through the LangGraph pipeline |
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
| `nodes/load_session.py` | `load_session` | Loads or creates session; assigns `turn_id`; normalises and optionally expands the query |
| `nodes/interpret_turn.py` | `interpret_turn` | Hybrid intent classifier (rule-based fast path + LLM fallback); detects smalltalk for conditional routing |
| `nodes/smalltalk.py` | `smalltalk` | Fast-path handler for greetings and casual messages — static responses, zero LLM cost, always steers to mall |
| `nodes/update_scene_memory.py` | `update_scene_memory` | Extracts companion, occasion, budget, and area signals; updates persistent `SceneMemory` |
| `nodes/resolve_playbooks.py` | `resolve_playbooks` | Matches the turn against scenario playbooks; outputs selected playbook and confidence |
| `nodes/choose_strategy.py` | `choose_strategy` | Maps intent + playbook → response strategy and shape hint |
| `nodes/compose_context.py` | `compose_context` | Selects topic blocks, ranks entities via playbook scoring, builds semantic signals |
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
| `retrieval/retriever.py` | Multi-layer lookup over canonical entities, semantic profiles, and playbooks |

### Services (`app/services/`)

| File | Purpose |
|------|---------|
| `services/concierge.py` | Orchestrates a single chat turn by invoking the compiled LangGraph pipeline |
| `services/clean_context.py` | Builds a contamination-free per-turn context (no raw LLM history — only structured scene + mall data) |
| `services/context_builder.py` | Assembles `GlobalContextPack` from canonical, semantic, and playbook data layers |
| `services/enricher.py` | Enriches entities with semantic profiles and concierge notes |
| `services/normalizer.py` | Normalises raw canonical JSON into typed entity models |
| `services/playbook_engine.py` | Loads, scores, and ranks `ScenarioPlaybook` objects against the current turn |
| `services/session_store.py` | In-memory LRU session store (max 1 000 sessions); Redis-ready for production |
| `services/tenant_params.py` | Loads and merges `TenantConfig` from defaults + per-mall overrides |
| `services/tenant_runtime.py` | Runtime tenant config management |
| `services/feedback_service.py` | Full feedback lifecycle: receive → validate → persist → aggregate |
| `services/feedback_normalizer.py` | Normalises explicit and implicit feedback into actionable signals |
| `services/implicit_feedback_detector.py` | Detects implicit negative signals (e.g., correction, rejection, repetition) |
| `services/session_tuning_engine.py` | Applies feedback-based parameter tuning per session |
| `services/tenant_parameter_tuner.py` | Aggregates feedback across sessions to tune tenant-level weights |
| `services/knowledge_gap_analyzer.py` | Identifies gaps in playbook coverage and canonical data |
| `services/response_mode_resolver.py` | Determines `response_mode` (`direct_factual`, `guided_recommendation`, `graceful_recovery`, `hybrid_plan`, `best_effort_shortlist`, `context_acknowledgement`) and `confidence_level` (`high`/`medium`/`low`) from intent, scene, and raw message signals. Applies priority-ordered pattern matching, factual-flow recommendation overrides, and calibrated confidence rules for budget declarations, broad category openers, and vague constraint refinements. |

### Observability (`app/observability/`)

| File | Purpose |
|------|---------|
| `observability/logger.py` | Structured logger with request context |

### Utilities (`app/utils/`)

| File | Purpose |
|------|---------|
| `utils/ids.py` | ID generation: `session-<hex16>`, `msg-<hex12>`, `fb-<hex12>` |

---

## Backend — `backend/data/`

| Directory / File | Purpose |
|------------------|---------|
| `canonical/al_nakheel_plaza_28.json` | Single source of truth — all stores, dining, cinemas, movies, services, events, offers |
| `semantic/al_nakheel_plaza_28.json` | Derived semantic intelligence — tags, audience fit, priority scores, concierge notes |
| `playbooks/al_nakheel_plaza_28.json` | Pre-built scenario playbooks (gift, family, romantic dinner, quick bite, movie night, budget) |
| `context_packs/al_nakheel_plaza_28_context.json` | Pre-assembled context bundle for the mall |
| `tenant_config/tenant_defaults.json` | Default tenant behavioral weights |
| `tenant_config/al_nakheel_plaza_28.json` | Mall-specific tenant config overrides |
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
| `components/TopBar.tsx` | Header — tenant/mall selectors, session ID, debug toggle, export, reset |
| `components/ChatInput.tsx` | Message textarea with send button (4 000-character limit) |
| `components/ChatMessage.tsx` | Individual message bubble — AI response, cited sources, feedback controls |
| `components/SuggestedChips.tsx` | Clickable suggestion chips for quick prompts |
| `components/FeedbackControls.tsx` | Thumbs up/down with optional negative reasons and comment field |
| `components/DebugPanel.tsx` | Collapsible debug sidebar — displays `TurnInspector` and `RawDrawer` per turn |
| `components/TurnInspector.tsx` | Per-turn pipeline inspector — intent, strategy, context blocks, retrieval, scene snapshot |
| `components/StrategyCard.tsx` | Displays selected playbook, confidence, strategy, and response shape |
| `components/RetrievalCard.tsx` | Shows retrieval status, reason, targets, and result count |
| `components/ContextBlocksCard.tsx` | Displays topic blocks, entities, semantic signals, and ranking notes |
| `components/SceneSnapshotCard.tsx` | Key-value display of the current scene snapshot (companions, budget, occasion, etc.) |
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
| `lib/constants.ts` | Application-wide frontend constants |
| `mock/responses.ts` | Mock API responses for offline development |
| `styles/index.css` | Tailwind imports and custom CSS variables |
| `assets/hero.png` | Hero image asset |

---

## Entry Points

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI entrypoint — lifespan initialises mall context, session store, feedback services |
| `backend/app/runtime.py` | Global singletons — mall context, session store, feedback service |
| `frontend/src/main.tsx` | React entry point — `createRoot`, `StrictMode` |
| `frontend/src/App.tsx` | Root component with routing |
