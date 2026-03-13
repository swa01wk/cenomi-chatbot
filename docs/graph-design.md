# LangGraph Concierge — State & Node Contract Design

Production-grade graph design for a single-mall AI Findr-style concierge.

---

## 1. Architecture Overview

```
                    ┌──────────────────────────────────────────────┐
                    │           ConciergeState (Pydantic)          │
                    │  session · intent · scene · playbook ·       │
                    │  context · retrieval · response_plan ·       │
                    │  response · trace                            │
                    └──────────────────────────────────────────────┘
                                        │
    ┌───────────┬───────────┬───────────┼───────────┬──────────────┐
    │           │           │           │           │              │
  load     interpret   update_scene  resolve   choose         compose
  session    turn       memory      playbooks  strategy       context
    │           │           │           │           │              │
    └───────────┴───────────┴───────────┴───────────┴──────────────┘
                                        │
                                  decide_retrieval
                                   ╱           ╲
                          (needed)               (skip)
                              │                    │
                       fetch_exact_facts           │
                              │                    │
                              └──────┬─────────────┘
                                     │
                              generate_response
                                     │
                               update_memory
                                     │
                            emit_debug_payload
                                     │
                                    END
```

### Design Principles

- **Single-mall scoped** — `mode = "single_mall"` always; multi-mall is a future concern.
- **Answer-first** — the pipeline is biased toward generating a response immediately, not interrogating the user.
- **Scene memory** — visitor context (companions, budget, occasion, etc.) persists across turns, enabling natural conversation flow.
- **Playbook-driven** — scenario playbooks shape strategy selection and entity ranking.
- **Retrieval-optional** — exact retrieval only fires when the query demands factual precision (hours, showtimes, offers).
- **Observable** — every node emits a trace entry; the final node aggregates them into a debug payload.

---

## 2. State Schema Reference

### Group 1 — Session Identity

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `session_id` | `str` | `""` | UUID-based session identifier |
| `tenant_id` | `str` | `"cenomi_mall_01"` | Mall operator ID |
| `mall_id` | `str` | `"cenomi_mall_01"` | Specific mall |
| `turn_id` | `str` | `""` | Unique per-turn ID |

### Group 2 — User Input

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `raw_user_message` | `str` | `""` | Original user text |
| `normalized_user_message` | `str` | `""` | After normalization |

### Group 3 — Scope

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `mode` | `Literal["single_mall"]` | `"single_mall"` | Hardcoded for V1 |
| `active_mall_id` | `str` | `"cenomi_mall_01"` | Runtime mall target |

### Group 4 — Conversation History

| Field | Type | Reducer | Purpose |
|-------|------|---------|---------|
| `messages` | `list[Message]` | `operator.add` | Append-only message history |

`Message` fields: `role` (user/assistant/system), `content`, `turn_id`, `metadata`.

### Group 5 — Interpreted Intent

| Field | Type | Purpose |
|-------|------|---------|
| `intent.domain` | `str` | dining, shopping, entertainment, services, navigation, general |
| `intent.sub_intent` | `str` | Specific intent (general_dining, gift_recommendation, etc.) |
| `intent.message_kind` | `Literal` | fresh_request, refinement, correction, topic_switch, followup |
| `intent.confidence` | `float` | 0.0–1.0 classification confidence |

### Group 6 — Scene Memory

| Field | Type | Purpose |
|-------|------|---------|
| `scene.visit_type` | `str` | family_outing, solo_browse, date, quick_errand |
| `scene.companions` | `list[str]` | girlfriend, kids, family, solo |
| `scene.occasion` | `str` | birthday, anniversary, date, casual |
| `scene.budget` | `str` | budget, mid_range, premium, luxury |
| `scene.audience` | `list[str]` | Semantic audience tags (couple_friendly, etc.) |
| `scene.current_area` | `str` | Physical location hint |
| `scene.current_need` | `str` | Freeform need description |
| `scene.active_topic` | `str` | Current conversation domain |
| `scene.previous_topic` | `str` | Archived on topic switch |
| `scene.active_shortlist` | `list[str]` | Entity IDs from last response |
| `scene.rejected_options` | `list[str]` | Entity IDs user rejected |
| `scene.current_preferences` | `dict` | Freeform preference signals |

### Group 7 — Playbook Resolution

| Field | Type | Purpose |
|-------|------|---------|
| `playbook.matched_playbooks` | `list[str]` | All playbooks above threshold |
| `playbook.selected_playbook` | `str` | Best match playbook ID |
| `playbook.playbook_confidence` | `float` | Score of selected playbook |

### Group 8 — Context Composition

| Field | Type | Purpose |
|-------|------|---------|
| `context.selected_topic_blocks` | `list[str]` | Topic block names for prompt |
| `context.selected_entities` | `list[dict]` | Matched entity summaries |
| `context.selected_semantic_signals` | `list[str]` | Active semantic tags |
| `context.ranking_notes` | `list[str]` | Ranking reasoning for generator |

### Group 9 — Retrieval

| Field | Type | Purpose |
|-------|------|---------|
| `retrieval.retrieval_needed` | `bool` | Whether to execute retrieval |
| `retrieval.retrieval_reason` | `str` | Why/why not |
| `retrieval.retrieval_targets` | `list[str]` | What to retrieve |
| `retrieval.retrieval_results` | `list[dict]` | Retrieved data |

### Group 10 — Tenant Config / Session Tuning

| Field | Type | Purpose |
|-------|------|---------|
| `active_tenant_parameters` | `TenantConfig` | Full tenant behavioral config |
| `active_session_tuning` | `dict` | Per-session parameter overrides |

### Group 11 — Response Planning

| Field | Type | Purpose |
|-------|------|---------|
| `response_plan.chosen_strategy` | `str` | Strategy name |
| `response_plan.response_shape_hint` | `str` | Output format hint |
| `response_plan.response_constraints` | `list[str]` | Hard constraints for generator |

### Group 12 — Generated Response

| Field | Type | Purpose |
|-------|------|---------|
| `final_response_text` | `str` | The user-facing response |
| `response_debug_summary` | `str` | Human-readable debug string |

### Group 13 — Observability

| Field | Type | Reducer | Purpose |
|-------|------|---------|---------|
| `node_trace` | `list[NodeTraceEntry]` | `operator.add` | Per-node execution traces |
| `latency_by_node` | `dict[str, float]` | — | Aggregated latency map |
| `evaluator_stub` | `dict` | — | Quality scoring seed data |
| `warnings` | `list[str]` | `operator.add` | Non-fatal issues |

---

## 3. Node Contracts

### Summary Table

| # | Node | Reads | Writes | Can Fail? |
|---|------|-------|--------|-----------|
| 1 | `load_session` | session_id, mall_id, raw_user_message | turn_id, tenant_id, active_mall_id, active_tenant_parameters, normalized_user_message, messages | Config miss → defaults |
| 2 | `interpret_turn` | normalized_user_message, messages, scene | intent | Classification fail → general |
| 3 | `update_scene_memory` | intent, normalized_user_message, scene | scene | Parse error → preserve scene |
| 4 | `resolve_playbooks` | intent, scene, active_tenant_parameters | playbook | No match → empty |
| 5 | `choose_strategy` | intent, scene, playbook, active_tenant_parameters | response_plan | No fit → fallback |
| 6 | `compose_context` | intent, scene, playbook, response_plan, active_tenant_parameters | context | Missing data → degrade |
| 7 | `decide_retrieval` | intent, context, active_tenant_parameters | retrieval | Unknown → no retrieval |
| 8 | `fetch_exact_facts` | retrieval, active_mall_id | retrieval, context | Error → warning + proceed |
| 9 | `generate_response` | messages, intent, scene, playbook, context, response_plan, retrieval, active_tenant_parameters | final_response_text, response_debug_summary, messages | LLM error → fallback text |
| 10 | `update_memory` | scene, final_response_text, intent | scene | Error → warning, no change |
| 11 | `emit_debug_payload` | all fields | response_debug_summary, latency_by_node, evaluator_stub | Never fails |

### Routing Implications

| Node | Next Node | Condition |
|------|-----------|-----------|
| load_session | interpret_turn | Always |
| interpret_turn | update_scene_memory | Always |
| update_scene_memory | resolve_playbooks | Always |
| resolve_playbooks | choose_strategy | Always |
| choose_strategy | compose_context | Always |
| compose_context | decide_retrieval | Always |
| decide_retrieval | **fetch_exact_facts** | `retrieval.retrieval_needed = True` |
| decide_retrieval | **generate_response** | `retrieval.retrieval_needed = False` |
| fetch_exact_facts | generate_response | Always |
| generate_response | update_memory | Always |
| update_memory | emit_debug_payload | Always |
| emit_debug_payload | END | Always |

---

## 4. Routing Logic — Conditional Rules

### Rule 1: Retrieval Gate

```python
if state.retrieval.retrieval_needed:
    → fetch_exact_facts → generate_response
else:
    → generate_response  (skip retrieval)
```

### Rule 2: Correction Handling

When `intent.message_kind == "correction"`:
- `update_scene_memory` → **strongly overrides** relevant scene fields (companions, budget, topic)
- `update_memory` → moves current `active_shortlist` → `rejected_options`, clears shortlist
- `choose_strategy` → adds `acknowledge_correction` constraint

### Rule 3: Playbook Bias

When `playbook.selected_playbook` exists and `playbook_confidence > 0.5`:
- `choose_strategy` → overrides strategy with playbook's preferred strategy
- `compose_context` → adds playbook ranking biases to `ranking_notes`

### Rule 4: Topic Switch

When `intent.message_kind == "topic_switch"`:
- `update_scene_memory` → archives `active_topic` → `previous_topic`, clears shortlist
- `compose_context` → includes cross-topic blocks from previous domain

### Rule 5: Followup Inheritance

When `intent.message_kind == "followup"`:
- `update_scene_memory` → preserves all scene fields, updates `current_need` only
- `choose_strategy` → adds `build_on_previous` constraint

---

## 5. Example Turn Traces

### Turn 1: "Where should I eat?"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-001, mall=cenomi_mall_01 |
| 2 | interpret_turn | domain=dining, sub=general_dining, kind=fresh_request |
| 3 | update_scene_memory | active_topic=dining |
| 4 | resolve_playbooks | no match (no scene signals yet) |
| 5 | choose_strategy | shortlist_recommendation, shape=numbered_shortlist |
| 6 | compose_context | topics=[dining, food_court, restaurants, cafes, events_and_offers] |
| 7 | decide_retrieval | NO — general recommendation |
| 8 | generate_response | 3-item shortlist of dining options |
| 9 | update_memory | active_topic confirmed: dining |
| 10 | emit_debug_payload | total ~860ms |

**Path:** load → interpret → scene → playbooks → strategy → context → decide → **SKIP** → generate → memory → debug → END

---

### Turn 2: "Romantic place" (followup)

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-002 |
| 2 | interpret_turn | domain=dining, sub=romantic_dining, kind=**followup** |
| 3 | update_scene_memory | +audience:couple_friendly, occasion=date |
| 4 | resolve_playbooks | **pb-romantic-dinner** (conf=0.7) |
| 5 | choose_strategy | shortlist_recommendation (playbook bias) |
| 6 | compose_context | topics=[dining, ...], signals=[couple_friendly, date] |
| 7 | decide_retrieval | NO |
| 8 | generate_response | Romantic dining shortlist with ambiance notes |
| 9 | update_memory | shortlist updated |
| 10 | emit_debug_payload | playbook=pb-romantic-dinner |

**Path:** Same linear path, retrieval skipped. Playbook now active.

---

### Turn 3: "Gift for my girlfriend"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-003 |
| 2 | interpret_turn | domain=shopping, sub=gift_recommendation, kind=**topic_switch** |
| 3 | update_scene_memory | topic_switch: dining→shopping, +companion:girlfriend |
| 4 | resolve_playbooks | **pb-gift-recommendation** (conf=0.7) |
| 5 | choose_strategy | **gift_formula**, shape=curated_picks |
| 6 | compose_context | topics=[fashion, electronics, lifestyle, luxury, dining(cross)], signals=[couple_friendly, girlfriend] |
| 7 | decide_retrieval | NO — general recommendation |
| 8 | generate_response | Curated gift picks across categories |
| 9 | update_memory | active_topic=shopping, previous=dining |
| 10 | emit_debug_payload | strategy=gift_formula |

**Path:** Topic switch detected. Cross-topic dining blocks included. Gift playbook active.

---

### Turn 4: "Anything quick before movie?"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-004 |
| 2 | interpret_turn | domain=dining, sub=quick_bite, kind=**fresh_request** |
| 3 | update_scene_memory | topic→dining, occasion=before_movie |
| 4 | resolve_playbooks | **pb-quick-bite** (conf=0.7), also pb-movie-night |
| 5 | choose_strategy | shortlist_recommendation, shape=numbered_shortlist |
| 6 | compose_context | topics=[dining, food_court, ...], signals=[before_movie, couple_friendly] |
| 7 | decide_retrieval | NO |
| 8 | generate_response | Quick-bite options near cinema |
| 9 | update_memory | active_topic=dining |
| 10 | emit_debug_payload | playbook=pb-quick-bite |

**Path:** Quick-bite playbook. Scene retains girlfriend companion from turn 3.

---

### Turn 5: "No, I mean inside the cinema"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-005 |
| 2 | interpret_turn | domain=entertainment, sub=general_entertainment, kind=**correction** |
| 3 | update_scene_memory | **correction override**: current_area=cinema, topic→entertainment |
| 4 | resolve_playbooks | **pb-movie-night** (conf=0.5) |
| 5 | choose_strategy | movie_plus_food, shape=combo_suggestion, constraint=acknowledge_correction |
| 6 | compose_context | topics=[cinema, entertainment, ...], signals=[before_movie, couple_friendly] |
| 7 | decide_retrieval | YES — needs cinema concessions info |
| 8 | **fetch_exact_facts** | retrieves cinema snack bar / VIP lounge data |
| 9 | generate_response | Cinema snack options + VIP lounge mention |
| 10 | update_memory | moved previous shortlist → rejected_options |
| 11 | emit_debug_payload | retrieval=yes |

**Path:** Correction triggers retrieval. Previous shortlist marked as rejected. Cinema-specific data fetched.

---

## 6. Production Design

### Recommended File Structure

```
backend/app/
├── graph/
│   └── builder.py              # StateGraph construction + routing
├── models/
│   ├── state.py                # ConciergeState + all sub-models
│   ├── tenant.py               # TenantConfig + entity models
│   ├── semantic.py             # SemanticProfile + enrichment
│   ├── playbook.py             # ScenarioPlaybook
│   ├── context_pack.py         # GlobalContextPack + TopicBlock
│   ├── mall.py                 # MallProfile + zones
│   ├── api.py                  # ChatRequest / ChatResponse
│   └── feedback.py             # FeedbackRecord
├── nodes/
│   ├── __init__.py             # Re-exports all node functions
│   ├── _tracing.py             # @traced_node decorator
│   ├── load_session.py         # 1. Session initialization
│   ├── interpret_turn.py       # 2. Intent classification
│   ├── update_scene_memory.py  # 3. Scene enrichment
│   ├── resolve_playbooks.py    # 4. Playbook matching
│   ├── choose_strategy.py      # 5. Strategy selection
│   ├── compose_context.py      # 6. Context assembly
│   ├── decide_retrieval.py     # 7. Retrieval gate
│   ├── fetch_exact_facts.py    # 8. Exact data lookup
│   ├── generate_response.py    # 9. LLM generation
│   ├── update_memory.py        # 10. Memory persistence
│   └── emit_debug_payload.py   # 11. Observability
├── services/
│   ├── concierge.py            # High-level orchestration
│   ├── context_builder.py      # Context pack assembly
│   ├── enricher.py             # Semantic enrichment
│   ├── normalizer.py           # Message normalization
│   ├── playbook_engine.py      # Playbook loading + matching
│   ├── tenant_params.py        # Parameter resolution
│   └── tenant_runtime.py       # TenantConfig loader
├── retrieval/
│   └── retriever.py            # Vector store / canonical lookup
├── prompts/
│   └── builder.py              # LLM prompt templates
├── api/
│   ├── chat.py                 # POST /api/chat
│   ├── health.py               # GET /api/health
│   └── feedback.py             # POST /api/feedback
├── config/
│   ├── settings.py             # Pydantic Settings
│   └── constants.py            # App constants
├── observability/
│   └── logger.py               # Structured logging
└── main.py                     # FastAPI entrypoint
```

### State Typing Strategy

- **Pydantic BaseModel** for the top-level `ConciergeState` and all sub-models.
- **`Annotated[list, operator.add]`** for accumulating fields (`messages`, `node_trace`, `warnings`) — nodes append without reading the full list.
- **`model_copy(deep=True)`** when a node needs to mutate a nested Pydantic object (e.g., SceneMemory).
- All fields have defaults — the graph can be invoked with just `raw_user_message` and `mall_id`.

### FastAPI Integration

```python
from app.graph.builder import build_concierge_graph

# Build once at startup
graph = build_concierge_graph()

@router.post("/api/chat")
async def chat(request: ChatRequest):
    result = await graph.ainvoke({
        "session_id": request.session_id or generate_session_id(),
        "mall_id": request.mall_id,
        "raw_user_message": request.message,
        # Pass previous scene for multi-turn
        "scene": load_scene_from_session_store(request.session_id),
    })

    return ChatResponse(
        session_id=result["session_id"],
        message=result["final_response_text"],
        sources=result.get("context", {}).get("selected_entities", []),
        suggestions=[],
        debug=result["response_debug_summary"] if settings.debug else None,
    )
```

### Debug Mode Payloads

The `emit_debug_payload` node produces:

1. **`response_debug_summary`** — human-readable text for developer console.
2. **`evaluator_stub`** — structured dict for quality scoring pipelines.
3. **`latency_by_node`** — per-node latency map for performance monitoring.
4. **`node_trace`** — full execution trace for replay and debugging.

For the internal debug UI, return the full `evaluator_stub` and `node_trace` when `settings.debug = True`:

```python
if settings.debug:
    response.debug = {
        "evaluator": result["evaluator_stub"],
        "trace": [entry.model_dump() for entry in result["node_trace"]],
        "latency": result["latency_by_node"],
        "warnings": result["warnings"],
    }
```

### Session Persistence

Scene memory must persist across turns. Options:

1. **In-memory dict** (development) — keyed by `session_id`, TTL-based eviction.
2. **Redis** (production) — serialize `SceneMemory` as JSON, 30-minute TTL.
3. **LangGraph checkpointer** — use `MemorySaver` or `SqliteSaver` for automatic state persistence.

The `load_session` node reads from the store; `update_memory` writes back.

### Next Implementation Steps

1. **Wire LLM** — Replace placeholder in `generate_response` with actual OpenAI/Anthropic call.
2. **Wire retrieval** — Implement `fetch_exact_facts` with canonical data lookups.
3. **Wire context builder** — Have `compose_context` pull real entities from `GlobalContextPack`.
4. **Wire normalizer** — Plug `normalizer.py` into `load_session`.
5. **Wire playbook loader** — Load `ScenarioPlaybook` objects in `resolve_playbooks`.
6. **Add LLM-based classifier** — Replace heuristic intent detection in `interpret_turn`.
7. **Add session store** — Implement Redis/memory-based scene persistence.
8. **Add evaluator** — Build quality scoring from `evaluator_stub` data.
