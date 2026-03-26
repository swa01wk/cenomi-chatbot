# Architecture Overview

## System Diagram

```
User → Frontend (React) → /api/chat → FastAPI → LangGraph Pipeline (12 nodes) → Response
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

1. **API Layer** (`app/api/`) — HTTP endpoints, request validation
2. **Concierge Service** (`app/services/`) — orchestrates a single chat turn
3. **Mall Context** (`app/context/`) — loads and assembles mall intelligence, semantic model
4. **LangGraph Pipeline** (`app/graph/`) — stateful 12-node reasoning pipeline
5. **Nodes** (`app/nodes/`) — individual graph steps including smalltalk fast-path, retrieval gate, generator, and debug emitter
6. **Retrieval** (`app/retrieval/`) — multi-layer search over mall data
7. **Prompts** (`app/prompts/`) — prompt assembly from templates + context
8. **Feedback** (`app/feedback/`) — feedback collection and storage

### Data Architecture

```
data/
├── raw/              Unprocessed source data (CSVs, JSONs from mall ops)
├── canonical/        Normalized tenant entities  (al_nakheel_plaza_28.json)
├── semantic/         Derived semantic intelligence entries  (al_nakheel_plaza_28.json)
├── playbooks/        Scenario playbooks  (al_nakheel_plaza_28.json)
├── context_packs/    Pre-assembled context bundles  (al_nakheel_plaza_28_context.json)
├── tenant_config/    Tenant behavioral parameters (defaults + per-mall overrides)
├── feedback/         Implicit and normalized feedback records
└── examples/         Sample data for development (state, tenants, playbooks, semantic)
```

### Model Layers

| File                    | Purpose                                    |
|-------------------------|--------------------------------------------|
| `models/state.py`       | LangGraph `ConciergeState` (flows through pipeline) |
| `models/api.py`         | API request/response contracts             |
| `models/tenant.py`      | Canonical tenant entities + tunable params |
| `models/mall.py`        | Mall profile, semantic entries, playbooks  |
| `models/context_pack.py`| Context pack and topic block models        |
| `models/semantic.py`    | Semantic intelligence models               |
| `models/playbook.py`    | Scenario playbook models                   |
| `models/feedback.py`    | Feedback records                           |

### Configuration

- **Environment** — all secrets and runtime config via `.env` + `pydantic-settings`
- **Mall config** — data files in `data/` loaded at startup
- **Tenant params** — per-tenant tunable weights in `data/canonical/`
- **Constants** — shared values in `config/constants.py`

## Frontend Architecture

Internal testing console with three concerns:

1. **Chat interface** — send messages, display responses
2. **Debug panel** — inspect pipeline state, traces, retrieval results
3. **Feedback widget** — rate responses, capture quality signals

## Key Design Decisions

### Why LangGraph (not simple chain)?
- Conditional routing (smalltalk fast-path, retrieval gate) requires a graph, not a linear chain
- Observable state at each node step for the debug inspector
- Extensible for multi-step playbook execution and human-in-the-loop
- Future: streaming, multi-agent, cross-mall routing

### Why a `smalltalk` fast-path node?
- Greetings and casual messages ("hi", "how are you", "thanks") don't need the full 10-node pipeline
- The `smalltalk` node uses static pattern matching — zero LLM cost, sub-100ms response
- Responses always steer back to mall services, maintaining concierge persona

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

This moves the query to the correct neighbourhood in the embedding space before search — higher-quality entity matches with zero latency cost (prefix construction is pure Python, no extra API calls). An `if scene_prefix` guard preserves existing behaviour for fresh sessions.

The augmentation is applied in two places:

1. **`compose_context.py` vector fallback** (lines 738–740) — the primary pipeline path; the augmented string is passed to `_vector_search_fallback → VectorStoreService.search()`.
2. **`MallRetriever.search_semantic(scene=None)`** — the method now accepts an optional `scene` parameter and applies the prefix internally, so tests, scripts, and future pipeline nodes that call this method directly also benefit without any extra wiring at the call site.

### Why tenant parameters?
- Different tenants may need different treatment (boost, suppress, tone)
- Feedback can tune these parameters over time via `tenant_parameter_tuner`
- Keeps core pipeline generic; tenant behavior is configurable

### Why `utils/ids.py`?
- Centralises all ID generation (session, message, feedback) in one place
- Consistent format: `session-<hex16>`, `msg-<hex12>`, `fb-<hex12>`

## Extensibility Notes

The codebase is structured for eventual:
- **Multi-mall**: `mall_id` is threaded through all models and state
- **Mall comparison**: retrieval can be extended to cross-mall search
- **Tenant tuning**: feedback → parameter adjustment pipeline via `session_tuning_engine` and `tenant_parameter_tuner`
- **Streaming**: FastAPI supports SSE; LangGraph supports streaming
