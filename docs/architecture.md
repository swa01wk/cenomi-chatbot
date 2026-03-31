# Architecture Overview

## System Diagram

```
User → Frontend (React) → /api/chat (or /api/chat/stream) → FastAPI → LangGraph Pipeline (12 nodes) → Response
                                          │
                         ┌────────────────┼──────────────────────┐
                         │                │                      │
                    Mall Context    Clean Context Builder    Prompt Builder
                    (canonical +    (per-turn, no history   (persona + context
                     semantic +      contamination)          + grounding rules)
                     semantic_mall_model +
                     playbooks)
```

## Backend Architecture

### Request Flow

1. **API Layer** (`app/api/`) — HTTP endpoints; blocking `POST /api/chat` and streaming `POST /api/chat/stream`
2. **Concierge Service** (`app/services/`) — orchestrates a single chat turn
3. **Mall Context** (`app/context/`) — loads and assembles mall intelligence, semantic model
4. **LangGraph Pipeline** (`app/graph/`) — stateful 12-node reasoning pipeline
5. **Nodes** (`app/nodes/`) — individual graph steps including smalltalk fast-path, retrieval gate, generator, and debug emitter
6. **Retrieval** (`app/retrieval/`) — multi-layer search over mall data (canonical + semantic + vector)
7. **Prompts** (`app/prompts/`) — prompt assembly from templates + context
8. **Feedback** (`app/feedback/`) — feedback collection and storage

### Data Architecture

```
data/
├── raw/              Unprocessed source data (CSVs, JSONs from mall ops)
├── canonical/        Normalized tenant entities  (al_nakheel_plaza_{1,10,13,27,28}.json)
├── semantic/         Derived semantic intelligence entries  (al_nakheel_plaza_{1,10,13,27,28}.json)
├── playbooks/        Scenario playbooks  (al_nakheel_plaza_{1,10,13,27,28}.json)
├── context_packs/    Pre-assembled context bundles  (al_nakheel_plaza_{1,10,13,27,28}_context.json)
├── tenant_config/    Tenant behavioral parameters (defaults + per-mall overrides)
├── chroma/           Chroma vector DB — per-mall embedding collections
├── feedback/         Implicit and normalized feedback records
└── examples/         Sample data for development (state, tenants, playbooks, semantic)
```

Five malls are currently operational: Al Nakheel Plaza (Buraidah, mall 28), Mall of Arabia (Jeddah, mall 13), Al Ahsa Mall (Al Ahsa, mall 1), The View Mall (Riyadh, mall 10), and Al Nakheel Mall (Riyadh, mall 27).

### Model Layers

| File                    | Purpose                                    |
|-------------------------|--------------------------------------------|
| `models/state.py`       | LangGraph `ConciergeState` (flows through pipeline); `MessageKind` enum; `SceneMemory` with `ShoppingTask`, `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `topic_lock`, `greeting_streak` |
| `models/api.py`         | API request/response contracts             |
| `models/tenant.py`      | Canonical tenant entities + tunable params |
| `models/mall.py`        | Mall profile, zones, facilities            |
| `models/context_pack.py`| Context pack and topic block models        |
| `models/semantic.py`    | Semantic intelligence models               |
| `models/playbook.py`    | Scenario playbook models                   |
| `models/feedback.py`    | Feedback records                           |

### Configuration

- **Environment** — all secrets and runtime config via `.env` + `pydantic-settings`
- **Mall config** — data files in `data/` loaded at startup; five malls by default
- **Tenant params** — per-tenant tunable weights in `data/tenant_config/`
- **Constants** — shared values in `config/constants.py`

## Frontend Architecture

Internal testing console with three concerns:

1. **Chat interface** — send messages, display streaming responses with blinking cursor
2. **Debug panel** — inspect pipeline state, traces, retrieval results, scene snapshot with all new fields
3. **Feedback widget** — rate responses, capture quality signals

Five malls selectable from the TopBar; active mall persisted to `localStorage`.

## Key Design Decisions

### Why LLM-first classification (v1.6)?
The previous rule-based fast path (threshold 0.75) was context-blind — a query like `"coffee?"` would be classified as `cafe_recommendation` without ever seeing the `companions=["child"]` scene state, producing a generic response instead of a family-aware one. The LLM classifier (`gpt-4o-mini`, ~30× cheaper than GPT-4o) now runs on every turn and returns `flow_type`, `response_mode`, `secondary_intents`, `modifiers`, `scenario`, `scene_corrections`, and `is_gibberish` directly. No keyword overrides run after the LLM output. Route flow and scene extraction became thin policy layers rather than large keyword tables. See `docs/LLM-FIRST-FLOW.md` for the full gap analysis.

### Why LangGraph (not simple chain)?
- Conditional routing (smalltalk fast-path, dual-flow factual/concierge) requires a graph, not a linear chain
- Observable state at each node step for the debug inspector
- Extensible for multi-step playbook execution and human-in-the-loop
- SSE streaming via `graph.astream_events()` is built in

### Why a `smalltalk` fast-path node?
Greetings and casual messages bypass the full 12-node pipeline. The `smalltalk` node handles them with a rich static pool plus two LLM-assisted variants:
- **Farewell**: personalised `gpt-4o-mini` call using scene context (companions, occasion, active shortlist)
- **Thanks**: context-aware pool that proactively suggests unexplored domains
- **Greeting**: three-tier progressive pool keyed to `scene.greeting_streak`
- Routing predicate `is_smalltalk()` reads `state.intent.message_kind` against `SMALLTALK_KINDS` — no regex

### Why clean context (no conversation history in LLM)?
- Previous LLM responses can contain hallucinations that would be re-injected each turn
- `clean_context.py` ensures each turn receives only: current message + structured mall data + scene memory
- Session continuity is provided via structured `SceneMemory`, not raw history

### Why three intelligence layers?
- **Canonical**: structured lookup, exact matching, filtering
- **Semantic** (`semantic_mall_model.py`): tag-based enrichment, audience fit, experience clusters
- **Playbooks**: pre-built strategies that reduce LLM improvisation

### Why augment the vector query with scene context?
Vector search embeds the raw user query and finds semantically similar entities. Without augmentation, "something for dinner" produces the same vector regardless of whether the user is celebrating an anniversary or visiting with young children — returning the same entity ranking for both.

`build_scene_prefix(scene)` in `retriever.py` prepends available `SceneMemory` signals (`occasion`, `companions`, `visit_type`, `budget`) to the query before embedding:

```
Session A (anniversary):  "anniversary partner date something for dinner"
Session B (family):       "kids family outing something for dinner"
Session C (no context):   "something for dinner"   ← unchanged
```

This moves the query to the correct neighbourhood in the embedding space before search — higher-quality entity matches with zero latency cost. An `if scene_prefix` guard preserves existing behaviour for fresh sessions.

### Why tenant parameters?
- Different tenants may need different treatment (boost, suppress, tone)
- Feedback can tune these parameters over time via `tenant_parameter_tuner`
- Keeps core pipeline generic; tenant behavior is configurable

### Why `utils/ids.py`?
- Centralises all ID generation (session, message, feedback) in one place
- Consistent format: `session-<hex16>`, `msg-<hex12>`, `fb-<hex12>`

## Extensibility Notes

The codebase is structured for:
- **Multi-mall**: five malls operational; `mall_id` is threaded through all models and state; LRU cache handles N malls
- **Streaming**: `POST /api/chat/stream` delivers token-by-token responses via SSE
- **Mall comparison**: cross-mall retrieval already implemented via `search_brand_across_configured_malls`
- **Tenant tuning**: feedback → parameter adjustment pipeline via `session_tuning_engine` and `tenant_parameter_tuner`
- **LangGraph checkpointing**: optional turn snapshots for replay/debugging (`BACKEND_ENABLE_CHECKPOINTER`)
