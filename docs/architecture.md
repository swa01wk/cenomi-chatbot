# Architecture Overview

## System Diagram

```
User → Frontend (React) → /api/chat → FastAPI → LangGraph Pipeline → Response
                                          │
                         ┌────────────────┼────────────────┐
                         │                │                │
                    Mall Context    Retrieval Service    Prompt Builder
                    (canonical +    (vector + struct)    (persona + context)
                     semantic +
                     playbooks)
```

## Backend Architecture

### Request Flow

1. **API Layer** (`app/api/`) — HTTP endpoints, request validation
2. **Concierge Service** (`app/services/`) — orchestrates a single chat turn
3. **Mall Context** (`app/context/`) — loads and assembles mall intelligence
4. **LangGraph Pipeline** (`app/graph/`) — stateful multi-step reasoning
5. **Nodes** (`app/nodes/`) — individual graph steps (router, retriever, generator, guardrail)
6. **Retrieval** (`app/retrieval/`) — multi-layer search over mall data
7. **Prompts** (`app/prompts/`) — prompt assembly from templates + context
8. **Feedback** (`app/feedback/`) — feedback collection and storage

### Data Architecture

```
data/
├── raw/              Unprocessed source data (CSVs, JSONs from mall ops)
├── canonical/        Normalized tenant entities
├── semantic/         Derived semantic intelligence entries
├── playbooks/        Scenario playbooks
├── context_packs/    Pre-assembled context bundles
└── examples/         Sample data for development
```

### Model Layers

| File                | Purpose                                    |
|---------------------|--------------------------------------------|
| `models/state.py`   | LangGraph state (flows through pipeline)   |
| `models/api.py`     | API request/response contracts             |
| `models/tenant.py`  | Canonical tenant entities + tunable params |
| `models/mall.py`    | Mall profile, semantic entries, playbooks  |
| `models/feedback.py`| Feedback records                           |

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
- Need conditional routing (different intents → different retrieval strategies)
- Need observable state at each step for debugging
- Need extensibility for multi-step playbook execution
- Future: human-in-the-loop, streaming, multi-agent

### Why three intelligence layers?
- **Canonical**: structured lookup, exact matching, filtering
- **Semantic**: natural language similarity, derived knowledge
- **Playbooks**: pre-built strategies that reduce LLM improvisation

### Why tenant parameters?
- Different tenants may need different treatment (boost, suppress, tone)
- Feedback can tune these parameters over time
- Keeps core pipeline generic; tenant behavior is configurable

## Extensibility Notes

The codebase is structured for eventual:
- **Multi-mall**: `mall_id` is threaded through all models and state
- **Mall comparison**: retrieval can be extended to cross-mall search
- **Tenant tuning**: feedback → parameter adjustment pipeline
- **Streaming**: FastAPI supports SSE; LangGraph supports streaming
