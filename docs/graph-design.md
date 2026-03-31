# LangGraph Concierge — State & Node Contract Design

Production-grade graph design for a single-mall AI Findr-style concierge.

---

## 1. Architecture Overview

```
                    ┌──────────────────────────────────────────────────────┐
                    │              ConciergeState (Pydantic)               │
                    │  session · intent · scene · playbook · context ·     │
                    │  retrieval · response_plan · response · debug ·      │
                    │  debug_enrichment · trace                            │
                    └──────────────────────────────────────────────────────┘
                                        │
                                    load_session
                                        │
                                  interpret_turn
                                   ╱           ╲
                          (smalltalk)           (normal)
                              │                    │
                           smalltalk        update_scene_memory
                              │                    │
                              │             resolve_playbooks
                              │                    │
                              │             choose_strategy
                              │                    │
                              │             compose_context
                              │                    │
                              │             rank_and_dedupe        ← NEW
                              │                    │
                              │             decide_retrieval
                              │              ╱           ╲
                              │       (needed)             (skip)
                              │           │                  │
                              │    fetch_exact_facts         │
                              │           │                  │
                              │           └────────┬─────────┘
                              │                    │
                              └──────► generate_response
                                             │
                                       update_memory
                                             │
                                     emit_debug_payload
                                             │
                                            END
```

### Design Principles

- **LLM-first classification (v1.6)** — `interpret_turn` is a pure LLM classifier (`gpt-4o-mini`). It returns `flow_type`, `response_mode`, `is_gibberish`, `secondary_intents`, `modifiers`, `scenario`, and `scene_corrections` directly. No keyword overrides run post-LLM. Route flow and scene extraction are thin policy layers.
- **Multi-mall** — five malls operational; `mall_id` is threaded through all models. `LRUMallContextRegistry` manages RAM → Redis → disk loading.
- **Answer-first** — the pipeline is biased toward generating a response immediately, not interrogating the user.
- **Concierge-first** — the system behaves like a smart human concierge: it infers context, selects a playbook, produces a compact guided plan, and acknowledges the visitor's situation naturally.
- **Smalltalk fast-path** — routing uses `SMALLTALK_KINDS` frozenset (not regex). The `smalltalk` node provides three-tier progressive greetings, LLM-generated farewells, context-aware thanks, and mood-plan emotional responses.
- **Scene engine** — `update_scene_memory` uses LLM-first delta extraction: a structured LLM call returns only changed fields. New fields tracked: `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task`. `scene_corrections` from `interpret_turn` are applied in a separate pass.
- **Semantic intelligence** — `semantic_signals` service maps structured `SceneMemory` and `InterpretedIntent` fields to semantic tags via pure lookup tables (no regex).
- **Playbook-driven** — scenario playbooks shape strategy selection, entity ranking boosts/penalties, and response shape; now includes occasion/wedding overrides, luxury guard, and shopping task scope checks.
- **rank_and_dedupe** — rebalanced scoring (audience weight raised to 0.30); hard audience mismatch penalty; child-relief anchor injection skipped when a specific shopping task is active.
- **Retrieval-optional** — exact retrieval only fires when the query demands factual precision (hours, showtimes, offers).
- **Constraint refinement** — messages like "something quicker" or "closer to cinema" are classified as `constraint_refinement` and refine the existing shortlist rather than resetting the conversation.
- **Observable** — every node emits a trace entry; the final node aggregates them into a rich debug payload including scene notes, ranking explanations, and playbook rejection reasons.

---

## 2. State Schema Reference

### Group 1 — Session Identity

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `session_id` | `str` | `""` | UUID-based session identifier |
| `tenant_id` | `str` | `"al_nakheel_plaza_28"` | Mall operator ID |
| `mall_id` | `str` | `"al_nakheel_plaza_28"` | Specific mall |
| `turn_id` | `str` | `""` | Unique per-turn ID |
| `last_successful_playbook` | `str` | `""` | Playbook from last successful turn |
| `last_response_shape` | `str` | `""` | Shape hint from last response |
| `preferred_categories` | `list[str]` | `[]` | Categories from successful retrievals |

### Group 2 — User Input

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `raw_user_message` | `str` | `""` | Original user text |
| `normalized_user_message` | `str` | `""` | After normalization |
| `expanded_query` | `str` | `""` | Query-expanded version for richer matching |

### Group 3 — Scope

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `mode` | `Literal["single_mall"]` | `"single_mall"` | Hardcoded for V1 |
| `active_mall_id` | `str` | `"al_nakheel_plaza_28"` | Runtime mall target |

### Group 4 — Conversation History

| Field | Type | Reducer | Purpose |
|-------|------|---------|---------|
| `messages` | `list[Message]` | `operator.add` | Append-only message history |

`Message` fields: `role` (user/assistant/system), `content`, `turn_id`, `metadata`.

### Group 5 — Interpreted Intent

| Field | Type | Purpose |
|-------|------|---------|
| `intent.domain` | `str` | dining, shopping, entertainment, services, navigation, exploration, mall_info, general, cross_mall |
| `intent.sub_intent` | `str` | Specific intent (general_dining, gift_recommendation, etc.) |
| `intent.message_kind` | `Literal` | fresh_request, refinement, **constraint_refinement**, correction, topic_switch, followup, greeting, smalltalk |
| `intent.confidence` | `float` | 0.0–1.0 classification confidence |
| `intent.implicit_goal` | `str` | Inferred higher-level goal (emitted by interpret_turn) |
| `intent.scene_candidates` | `list[str]` | Scene signal candidates from message |
| `intent.semantic_candidates` | `list[str]` | Semantic tag candidates from message |

**`message_kind` values** (now a `MessageKind` str enum — all values LLM-classified):

| Kind | Routed via `SMALLTALK_KINDS`? | When Used |
|------|------|-----------|
| `FRESH_REQUEST` | No | New standalone question |
| `FOLLOWUP` | No | Short continuation of active topic |
| `REFINEMENT` | No | Adding to current topic ("also", "what about") |
| `CONSTRAINT_REFINEMENT` | No | Tightening a prior suggestion ("something quicker", "not expensive") — does NOT reset the topic |
| `CORRECTION` | No | Correcting a previous answer ("no", "I meant") |
| `TOPIC_SWITCH` | No | Changing topic entirely ("forget that", "instead") |
| `CONTEXT_SETTING` | No | User declares scene context without asking a question — routes to concierge for acknowledgement |
| `CATEGORY_NEGATION` | No | Rejecting a category ("not that kind of restaurant") |
| `COMPANION_CORRECTION` | No | Correcting who they're with ("actually no kids, just adults") |
| `GREETING` | Yes | Hello, hi, welcome |
| `HOWRU` | Yes | "How are you?", "How's it going?" |
| `THANKS` | Yes | "Thanks", "That's helpful", "Great" |
| `FAREWELL` | Yes | "Bye", "See you", "Thanks, goodbye" |
| `CRISIS` | Yes | Emotional distress signals — handled with empathy redirect |
| `IDENTITY` | Yes | Questions about what the bot is |

### Group 6 — Scene Memory

| Field | Type | Purpose |
|-------|------|---------|
| `scene.visit_type` | `str` | family_visit, couple, solo, group |
| `scene.companions` | `list[str]` | child, girlfriend, boyfriend, wife, husband, family, friends, solo |
| `scene.companion_details` | `list[dict]` | Structured details e.g. `[{"type": "child", "age": 5}]` |
| `scene.occasion` | `str` | birthday, anniversary, date, before_movie, after_movie, casual, quick_visit |
| `scene.budget` | `str` | budget, mid_range, premium, luxury |
| `scene.pace` | `str` | fast, moderate, leisurely |
| `scene.audience` | `list[str]` | Semantic audience tags: family_friendly, kid_friendly, couple_friendly, parent_with_child, solo_friendly |
| `scene.current_area` | `str` | Physical location hint |
| `scene.current_need` | `str` | Current literal ask |
| `scene.previous_need` | `str` | Ask from previous turn |
| `scene.active_topic` | `str` | Current conversation domain |
| `scene.previous_topic` | `str` | Archived on topic switch |
| `scene.active_shortlist` | `list[str]` | Entity names from last response (top-5) |
| `scene.rejected_options` | `list[str]` | Entity IDs/names user rejected |
| `scene.current_preferences` | `dict` | Freeform preference signals |
| `scene.target_person` | `str` | Who the query is about (child, girlfriend, etc.) |
| `scene.goal` | `str` | Explicit goal (shopping, dining, entertainment) |
| `scene.implicit_goal` | `str` | Inferred higher-level goal e.g. "shopping while keeping child engaged" |
| `scene.topic_history` | `list[str]` | Last 10 domains visited |
| `scene.inferred_scene_notes` | `list[str]` | Human-readable notes about what was inferred this turn |
| `scene.visit_plan` | `list[str]` | Ordered planned activity sequence (v1.6 — LLM extracted) |
| `scene.completed_steps` | `list[str]` | Activities already discussed |
| `scene.visit_constraints` | `list[str]` | Inferred practical constraints: quick, kid_friendly_required, near_cinema_preferred, budget_sensitive, time_sensitive, quick_stop_preferred |
| `scene.multi_activity_mode` | `bool` | True when user stated a multi-step plan |
| `scene.current_plan_step` | `str` | Which visit_plan step is being addressed |
| `scene.topic_lock` | `bool` | Whether the active topic is locked (follow-up continuity guard) |
| `scene.topic_lock_confidence` | `float` | 0.0–1.0 confidence in the topic lock |
| `scene.last_context_setting_turn` | `int` | Turn index of the most recent `context_setting` message |
| `scene.last_selected_playbook` | `str` | Playbook ID from the previous turn |
| `scene.last_response_experience_mode` | `str` | Response experience mode from previous turn |
| `scene.scenario` | `str` | *(v1.6)* Visit scenario label (e.g. `"wedding_shopping"`, `"family_day_out"`) from LLM |
| `scene.user_role` | `str` | *(v1.6)* Visitor's role in the group (e.g. `"mother"`, `"bride"`, `"solo_shopper"`) |
| `scene.style_intent` | `str` | *(v1.6)* Aesthetic or style preference declared by the user |
| `scene.excluded_domains` | `list[str]` | *(v1.6)* Domains the user has explicitly ruled out this session |
| `scene.shopping_task` | `ShoppingTask \| None` | *(v1.6)* Active shopping task with `item`, `recipient`, `budget_hint`, `urgency` |
| `scene.greeting_streak` | `int` | *(v1.6)* Number of consecutive greeting turns — drives 3-tier greeting pool in `smalltalk` |
| `scene.recent_mood` | `str` | *(v1.6)* Emotional tone of the last user message (`"happy"`, `"stressed"`, `"neutral"`) |

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
| `context.selected_entities` | `list[dict]` | Matched entity summaries (post rank_and_dedupe) |
| `context.selected_semantic_signals` | `list[str]` | Active semantic tags |
| `context.ranking_notes` | `list[str]` | Ranking reasoning for generator |
| `context.candidate_count_before_dedupe` | `int` | Raw candidate count before rank_and_dedupe |

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

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `response_plan.chosen_strategy` | `str` | `""` | Strategy name |
| `response_plan.response_shape_hint` | `str` | `""` | Output format hint |
| `response_plan.response_constraints` | `list[str]` | `[]` | Hard constraints for generator |
| `response_plan.answer_mode` | `str` | `""` | concierge_guided, direct_answer, shortlist |
| `response_plan.tone_mode` | `str` | `""` | compact_human_concierge, structured |
| `response_plan.entity_cap` | `int` | `5` | Max entities to pass to LLM |
| `response_plan.must_acknowledge_scene` | `bool` | `False` | Whether LLM must open with scene acknowledgment |
| `response_plan.must_include_anchor_type` | `str` | `""` | Entity type that must appear (e.g. "entertainment") |

**Available strategies:**

| Strategy | Shape | When Used |
|----------|-------|-----------|
| `guided_plan` | concierge_guided_plan | Contextual visit with companions/occasion/child |
| `concise_shortlist` | compact_shortlist | Direct category ask, quick errand |
| `quick_answer` | brief_refined_answer | constraint_refinement turn |
| `route_plus_plan` | route_with_plan | Location + sequence important |
| `route_hint` | brief_answer | Pure navigation query |
| `shortlist_recommendation` | numbered_shortlist | General recommendations |
| `mini_itinerary` | step_by_step | Multi-step planned visit |
| `gift_formula` | curated_picks | Gift shopping |
| `movie_plus_food` | combo_suggestion | Cinema + dining |
| `mall_overview` | structured_overview | Mall information queries |
| `direct_fact` | brief_answer | Hours, parking, prayer room |
| `exploration_overview` | mini_itinerary | First visit / open exploration |
| `budget_plan` | value_focused_list | Budget-constrained shopping/dining |
| `fallback_guided_response` | conversational | No clear strategy match |

### Group 12 — Generated Response

| Field | Type | Purpose |
|-------|------|---------|
| `final_response_text` | `str` | The user-facing response |
| `response_debug_summary` | `str` | Human-readable debug string |

### Group 13 — Debug Enrichment

Rich observability data written by multiple nodes across the pipeline.

| Field | Type | Written By | Purpose |
|-------|------|-----------|---------|
| `debug_enrichment.inferred_scene_notes` | `list[str]` | update_scene_memory | Human-readable notes on scene inferences |
| `debug_enrichment.semantic_match_explanations` | `list[str]` | resolve_playbooks | Why each semantic signal was inferred |
| `debug_enrichment.ranking_explanations` | `list[str]` | rank_and_dedupe | Why each entity was ranked/included |
| `debug_enrichment.playbook_rejection_reasons` | `list[str]` | resolve_playbooks | Why competing playbooks were rejected |
| `debug_enrichment.candidate_count_before_dedupe` | `int` | rank_and_dedupe | Raw candidate count |
| `debug_enrichment.deduped_entity_count` | `int` | rank_and_dedupe | Count after deduplication |
| `debug_enrichment.final_entity_count` | `int` | rank_and_dedupe | Final count after capping |
| `debug_enrichment.response_strategy_reason` | `str` | choose_strategy | Why this strategy was selected |
| `debug_enrichment.last_refinement_applied` | `str` | update_scene_memory | What constraint was applied in refinement |
| `debug_enrichment.normalized_query` | `str` | interpret_turn | Canonical form of the user query after normalization (e.g., `"what movies are showing"`) |
| `debug_enrichment.canonical_query_pattern` | `str` | interpret_turn | Pattern label matched during normalization (e.g., `"movie_lookup"`, `"dining_lookup"`, `"offer_lookup"`) |
| `debug_enrichment.topic_lock` | `bool` | route_flow | Whether topic lock was active when routing was decided |
| `debug_enrichment.topic_lock_confidence` | `float` | route_flow | Confidence in the topic lock at routing time (0.0–1.0) |
| `debug_enrichment.retrieval_discipline_reason` | `str` | decide_retrieval / rank_and_dedupe | Why retrieval was or was not required for this turn |
| `debug_enrichment.canonical_name_normalization_notes` | `list[str]` | rank_and_dedupe | Which entity name variants were collapsed and why |
| `debug_enrichment.suppressed_playbooks` | `list[str]` | resolve_playbooks | Playbooks that were considered but explicitly suppressed |
| `debug_enrichment.selection_reason` | `str` | resolve_playbooks | Why the selected playbook was chosen over alternatives |
| `debug_enrichment.interpretation_contract` | `dict` | interpret_turn | Full per-turn interpretation contract (primary_intent, scenario, modifiers, message_kind, flow_type, active_topic, fact_scope, normalized_query, canonical_query_pattern, topic_lock, topic_lock_confidence) |

### Group 14 — Observability

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
| 2a | `smalltalk` *(fast-path)* | normalized_user_message, raw_user_message, turn_id | final_response_text, response_debug_summary, messages | Never — static responses only |
| 3 | `update_scene_memory` | intent, normalized_user_message, scene | scene, debug_enrichment (partial) | Parse error → preserve scene |
| 4 | `resolve_playbooks` | intent, scene, active_tenant_parameters, normalized_user_message | playbook, debug_enrichment (partial) | No match → empty |
| 5 | `choose_strategy` | intent, scene, playbook, active_tenant_parameters | response_plan | No fit → fallback |
| 6 | `compose_context` | intent, scene, playbook, response_plan, active_tenant_parameters | context | Missing data → degrade |
| **6a** | **`rank_and_dedupe`** | **context, intent, scene, playbook, response_plan** | **context (trimmed), debug_enrichment (partial)** | **Scoring error → preserve order** |
| 7 | `decide_retrieval` | intent, context, active_tenant_parameters | retrieval | Unknown → no retrieval |
| 8 | `fetch_exact_facts` | retrieval, active_mall_id | retrieval, context | Error → warning + proceed |
| 9 | `generate_response` | messages, intent, scene, playbook, context, response_plan, retrieval, active_tenant_parameters | final_response_text, response_debug_summary, messages | LLM error → fallback text |
| 10 | `update_memory` | scene, final_response_text, intent, context, playbook, response_plan | scene, last_successful_playbook, last_response_shape, preferred_categories | Error → warning, no change |
| 11 | `emit_debug_payload` | all fields | response_debug_summary, latency_by_node, evaluator_stub | Never fails |

### Routing Implications

| Node | Next Node | Condition |
|------|-----------|-----------|
| load_session | interpret_turn | Always |
| interpret_turn | **smalltalk** | `is_smalltalk(state) = True` |
| interpret_turn | **update_scene_memory** | `is_smalltalk(state) = False` |
| smalltalk | update_memory | Always (skips nodes 3–9) |
| update_scene_memory | resolve_playbooks | Always |
| resolve_playbooks | choose_strategy | Always |
| choose_strategy | compose_context | Always |
| compose_context | **rank_and_dedupe** | Always |
| rank_and_dedupe | decide_retrieval | Always |
| decide_retrieval | **fetch_exact_facts** | `retrieval.retrieval_needed = True` |
| decide_retrieval | **generate_response** | `retrieval.retrieval_needed = False` |
| fetch_exact_facts | generate_response | Always |
| generate_response | update_memory | Always |
| update_memory | emit_debug_payload | Always |
| emit_debug_payload | END | Always |

---

## 4. Routing Logic — Conditional Rules

### Rule 0: Smalltalk Fast-Path

```python
if is_smalltalk(state):
    → smalltalk → update_memory → emit_debug_payload → END
else:
    → update_scene_memory → ... (full pipeline)
```

Triggered by `interpret_turn` when `intent.message_kind` is in `SMALLTALK_KINDS` frozenset — evaluated without regex. The `smalltalk` node uses tiered response pools plus selective LLM calls (farewells, emotional responses) rather than pure static responses.

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

### Rule 3: Constraint Refinement

When `intent.message_kind == "constraint_refinement"` (e.g. "something quicker", "not expensive", "closer to cinema"):
- `update_scene_memory` → appends new constraints to `visit_constraints`; **does NOT reset** companions, goal, audience, or active_shortlist
- `choose_strategy` → selects `quick_answer` strategy
- `generate_response` → instructs LLM to refine the previous shortlist with the new constraint, not restart

**Detection:** The `_detect_message_kind()` function in `interpret_turn` checks for constraint phrases before hitting the LLM. Fires only when prior conversation context exists.

### Rule 4: Playbook Bias

When `playbook.selected_playbook` exists and `playbook_confidence > 0.3`:
- `choose_strategy` → overrides strategy with playbook's preferred strategy (guided_plan, gift_formula, etc.)
- `compose_context` → adds playbook ranking biases to `ranking_notes`
- `rank_and_dedupe` → uses `playbook.ranking_biases` as scoring weights

### Rule 5: Contextual Visit Override

When `scene.companions` or `scene.occasion` or child companion present:
- `choose_strategy` → overrides to `guided_plan` (unless category-level retrieval detected)
- `response_plan.must_acknowledge_scene = True`
- `response_plan.must_include_anchor_type = "entertainment"` when child present
- `generate_response` → adds GUIDED PLAN MODE instruction to LLM prompt

### Rule 6: Topic Switch

When `intent.message_kind == "topic_switch"`:
- `update_scene_memory` → archives `active_topic` → `previous_topic`, clears `active_shortlist`
- `compose_context` → includes cross-topic blocks from previous domain

### Rule 7: Followup Inheritance

When `intent.message_kind == "followup"`:
- `update_scene_memory` → preserves all scene fields, updates `current_need` only
- `choose_strategy` → adds `build_on_previous` constraint

### Rule 8: Family/Child Shopping Guard

When `child` in `scene.companions` AND `intent.domain == "shopping"`:
- `resolve_playbooks` → **force-selects** `pb-family-shopping` or `pb-family-visit`
- Luxury playbooks (`pb-luxury-shopping`) are blocked when child companions present

### Rule 9: Context-Setting Scene Declaration

When `intent.message_kind == "context_setting"` (user declaring scene context without asking a question):
- **Always routes to concierge** for scene acknowledgement
- Never routes to factual even if the message contains companion or family signals
- `update_scene_memory` extracts all scene fields; `update_memory` records `last_context_setting_turn`
- `response_plan.must_acknowledge_scene = True` is set

### Topic Lock Mechanism

`topic_lock` and `topic_lock_confidence` in `SceneMemory` maintain conversation stability across turns:

| Event | Effect |
|-------|--------|
| Fresh factual turn completes | `topic_lock = True`, `topic_lock_confidence = 0.7` |
| Follow-up on locked topic | `topic_lock_confidence` increases by 0.1 (max 1.0) |
| Refinement on locked topic | `topic_lock_confidence` unchanged |
| New intent with different domain | `topic_lock_confidence` decreases by 0.2 |
| Explicit topic_switch | `topic_lock = False`, `topic_lock_confidence = 0.0` |

In `route_flow`, Rule 0 (domain lock) uses `scene.active_primary_intent` — if it is in `_FACTUAL_PRIMARY_INTENTS` and no explicit switch is detected, routing stays factual regardless of new context signals.

### Interpretation Contract

Every `interpret_turn` execution emits a mandatory structured contract stored in `debug_enrichment.interpretation_contract`:

```json
{
  "primary_intent": "movie_lookup",
  "scenario": "before_movie",
  "modifiers": ["kid_friendly"],
  "message_kind": "FRESH_REQUEST",
  "flow_type": "factual",
  "response_mode": "direct_factual",
  "is_gibberish": false,
  "secondary_intents": [],
  "scene_corrections": {},
  "active_topic": "entertainment",
  "fact_scope": "current_mall",
  "normalized_query": "what movies are showing",
  "canonical_query_pattern": "movie_lookup",
  "topic_lock": false,
  "topic_lock_confidence": 0.0
}
```

The `scene_corrections` field allows `interpret_turn` to emit corrective patches (e.g. `{"companions": ["solo"]}`) that `update_scene_memory` applies atomically before its own LLM delta extraction.

This contract is the authoritative record of what `interpret_turn` decided and why. All downstream nodes (route_flow, resolve_playbooks, compose_context) should be traceable to its fields.

---

## 5. Semantic Signal Layer

The `app/services/semantic_signals.py` module extracts semantic tags from user messages and scene context. These signals flow into playbook matching, entity ranking, and response shaping.

### Example Signal Extraction

| Input | Inferred Signals |
|-------|-----------------|
| `"with my 5 yr old"` | kid_friendly, family_friendly, parent_friendly, child_present |
| `"want to do shopping"` | shopping_mission, practical_shopping |
| `"quick gift"` | gift_friendly, quick_stop |
| `"before movie"` | before_movie, time_sensitive, near_cinema |
| `"not expensive"` | budget_sensitive, value_shopping |
| `"closer to cinema"` | near_cinema |
| `"by myself"` | solo_friendly |
| `"with my girlfriend"` | couple_friendly, romantic |

### Signal Sources (v1.6 — lookup tables only, no regex)

1. **Scene companions** — each companion value maps to audience tags via lookup table
2. **Scene occasion** — each occasion value maps to context tags via lookup table
3. **Visit constraints** — each constraint maps to directional tags via lookup table
4. **Budget** — maps to price band tags
5. **Intent domain/sub-intent** — each intent value maps to task tags via lookup table
6. **Scenario** — new `scene.scenario` field maps to scenario-specific semantic tags
7. **Excluded domains** — mapped to anti-tags for ranking penalty

Phrase/text matching (`_PHRASE_TAG_MAP`) was removed in v1.6. All signals now originate from structured state fields set by the LLM classifier.

---

## 6. Rank & Dedupe Node

`app/nodes/rank_and_dedupe.py` — inserted between `compose_context` and `decide_retrieval`.

### Algorithm

```
1. Take context.selected_entities from compose_context
2. Record candidate_count_before_dedupe
3. Deduplicate by entity_id (or normalized name if no ID)
4. Score each entity using weighted formula (v1.6 rebalanced weights):
   final_score =
     intent_fit       * 0.20   (entity type matches domain)
     + semantic_score * 0.15   (tag overlap with semantic_signals) ← lowered
     + audience_fit   * 0.30   (audience_fit matches scene.audience) ← raised
     + playbook_score * 0.15   (ranking_biases from active playbook) ← lowered
     + constraint_fit * 0.15   (tags satisfy visit_constraints)
     + diversity_pen  * 0.05   (penalize 4th+ of same entity_type)
   + hard penalty: audience mismatch (entity tagged adult-only when children present) ← new
5. Sort descending by final_score
6. Apply diversity penalty: 3rd+ entity of same type loses 0.15/step
7. Enforce entity_cap (from response_plan.entity_cap, default 5)
   Exception: category-level retrieval bypasses capping
8. Enforce must_include_anchor_type (child-relief anchor injection):
   - If child companion + no entertainment entity → pull best entertainment entity
   - Replace lowest-scoring non-dining entity if at cap
   - SKIP anchor injection when scene.shopping_task is active (specific product purchase takes priority) ← new
9. Emit ranking_explanations for top-5
10. Update context.selected_entities and context.candidate_count_before_dedupe
```

### Deduplication Key

Entities are deduplicated by a normalized key derived from their canonical name:
1. Lowercase
2. Unicode-normalize (NFD → ASCII — strips accents)
3. Strip leading articles (`the `, `a `, `an `)
4. Replace non-word/non-space characters with spaces (strips `&`, `-`, `'`, `.`)
5. Collapse whitespace

This means `"H&M"` and `"H & M"` collapse to the same key (`"h m"`), and `"Häagen-Dazs"` matches `"Haagen Dazs"`. If `entity_id` is present it takes priority over the name-based key.

### Output Fields Written to debug_enrichment

- `candidate_count_before_dedupe` — count before dedup
- `deduped_entity_count` — count after dedup, before cap
- `final_entity_count` — count after cap + anchor injection
- `ranking_explanations` — list of per-entity reason strings
- `canonical_name_normalization_notes` — list of strings explaining which name variants were collapsed (e.g., `"'Zara (Shop)' collapsed into 'Zara (Store)' — same normalized key: zara"`)
- `retrieval_discipline_reason` — explanation of whether factual retrieval was applied to the entity set for this turn

---

## 7. Example Turn Traces

### AC1: "i am here with my 5 yr old, want to do shopping"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-001 |
| 2 | interpret_turn | domain=shopping, sub=general_shopping, kind=fresh_request |
| 3 | update_scene_memory | companions=[child], companion_details=[{type:child, age:5}], visit_type=family_visit, audience=[family_friendly, kid_friendly, parent_with_child], implicit_goal="shopping while keeping child engaged", pace=moderate |
| 4 | resolve_playbooks | **pb-family-shopping** selected (child+shopping guard fires), luxury playbooks blocked |
| 5 | choose_strategy | **guided_plan**, entity_cap=5, must_acknowledge_scene=True, must_include_anchor_type=entertainment |
| 6 | compose_context | entities: family-friendly stores + entertainment (Fun Time) |
| 6a | rank_and_dedupe | 14 candidates → 12 deduped → 5 final; Fun Time injected as child-relief anchor |
| 7 | decide_retrieval | NO |
| 8 | generate_response | Scene acknowledgment + 3-4 store plan + child-relief anchor + next step offer |
| 9 | update_memory | active_shortlist=[Zara, MINISO, Red Tag, Fun Time], last_successful_playbook=pb-family-shopping |
| 10 | emit_debug_payload | inferred_scene_notes, ranking_explanations, playbook_rejection_reasons all populated |

**Path:** Full pipeline. Family guard fires. Child-relief anchor injected. guided_plan mode.

---

### AC2: "tell me about the mall"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-002 |
| 2 | interpret_turn | domain=mall_info, sub=overview, kind=fresh_request |
| 3 | update_scene_memory | active_topic=mall_info |
| 4 | resolve_playbooks | no playbook match |
| 5 | choose_strategy | **mall_overview**, shape=structured_overview |
| 6 | compose_context | topic=[mall_overview], entities=[] |
| 6a | rank_and_dedupe | pass-through (no entity cap on overview) |
| 7 | decide_retrieval | NO |
| 8 | generate_response | Clean structured mall overview via dedicated overview prompt |
| 9 | update_memory | active_topic=mall_info |
| 10 | emit_debug_payload | strategy=mall_overview |

**Path:** mall_info fast-path in generate_response uses `get_mall_overview_system_prompt`.

---

### AC3: "i want a gift for my girlfriend"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-003 |
| 2 | interpret_turn | domain=shopping, sub=gift_recommendation, kind=fresh_request |
| 3 | update_scene_memory | companions=[girlfriend], visit_type=couple, audience=[couple_friendly], implicit_goal="finding a gift for partner" |
| 4 | resolve_playbooks | **pb-gift-girlfriend** (conf=0.7), semantic_signals=[gift_friendly, romantic, couple_friendly] |
| 5 | choose_strategy | **gift_formula**, shape=curated_picks |
| 6 | compose_context | signals=[romantic, gift_friendly, couple_friendly], entities boosted for premium + gift_friendly |
| 6a | rank_and_dedupe | entities ranked by playbook biases (romantic, gift_friendly, premium) |
| 7 | decide_retrieval | NO |
| 8 | generate_response | Curated gift picks with romantic tone |
| 9 | update_memory | active_shortlist updated |
| 10 | emit_debug_payload | playbook=pb-gift-girlfriend |

**Path:** Gift playbook selected correctly. Luxury/fashion guard does NOT override (explicit gift intent present).

---

### AC4: "we want something quick before the movie"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-004 |
| 2 | interpret_turn | domain=dining, sub=quick_bite, kind=fresh_request |
| 3 | update_scene_memory | occasion=before_movie, visit_constraints=[time_sensitive, quick_stop_preferred] |
| 4 | resolve_playbooks | **pb-before-movie** or **pb-quick-bite** (time_sensitive + near_cinema signals) |
| 5 | choose_strategy | **concise_shortlist**, entity_cap=3 |
| 6 | compose_context | signals=[before_movie, time_sensitive, near_cinema, quick_stop] |
| 6a | rank_and_dedupe | cap=3, near_cinema boosted entities float to top |
| 7 | decide_retrieval | NO |
| 8 | generate_response | 3 quick near-cinema options, time acknowledged |
| 9 | update_memory | active_shortlist updated |
| 10 | emit_debug_payload | playbook=pb-before-movie |

---

### AC6: "something quicker" (after prior shopping/dining recommendation)

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-005 |
| 2 | interpret_turn | message_kind=**constraint_refinement** (detected by rule-based pattern) |
| 3 | update_scene_memory | visit_constraints += [quick_stop_preferred]; companions/goal/shortlist **preserved** |
| 4 | resolve_playbooks | previous playbook re-evaluated with new constraint |
| 5 | choose_strategy | **quick_answer**, shape=brief_refined_answer |
| 6 | compose_context | same entities, signals include quick_stop |
| 6a | rank_and_dedupe | quick_stop_preferred entities float to top; cap=3 |
| 7 | decide_retrieval | NO |
| 8 | generate_response | "For something quicker, [X] is a fast stop near you..." |
| 9 | update_memory | last_refinement_applied updated |
| 10 | emit_debug_payload | last_refinement_applied="quick_stop_preferred" |

**Path:** Constraint refinement — topic NOT reset, shortlist refined.

---

### Turn: "No, I mean inside the cinema"

| Step | Node | Key Output |
|------|------|------------|
| 1 | load_session | turn_id=msg-006 |
| 2 | interpret_turn | domain=entertainment, sub=general_entertainment, kind=**correction** |
| 3 | update_scene_memory | **correction override**: current_area=cinema, topic→entertainment |
| 4 | resolve_playbooks | **pb-movie-night** (conf=0.5) |
| 5 | choose_strategy | movie_plus_food, shape=combo_suggestion, constraint=acknowledge_correction |
| 6 | compose_context | topics=[cinema, entertainment], signals=[before_movie, couple_friendly] |
| 6a | rank_and_dedupe | cinema-type entities ranked highest |
| 7 | decide_retrieval | YES — needs cinema concessions info |
| 8 | **fetch_exact_facts** | retrieves cinema snack bar / VIP lounge data |
| 9 | generate_response | Cinema snack options + VIP lounge mention |
| 10 | update_memory | moved previous shortlist → rejected_options |
| 11 | emit_debug_payload | retrieval=yes |

**Path:** Correction triggers retrieval. Previous shortlist marked as rejected.

---

## 8. Production Design

### Implemented File Structure

```
backend/app/
├── main.py                     # FastAPI entrypoint + lifespan
├── runtime.py                  # Global singletons (mall context, sessions, feedback)
├── graph/
│   └── builder.py              # StateGraph construction + conditional routing
├── models/
│   ├── state.py                # ConciergeState + all sub-models (incl. DebugEnrichment)
│   ├── tenant.py               # TenantConfig + entity models
│   ├── semantic.py             # SemanticProfile + enrichment
│   ├── playbook.py             # ScenarioPlaybook
│   ├── context_pack.py         # GlobalContextPack + TopicBlock
│   ├── mall.py                 # MallProfile + zones
│   ├── api.py                  # ChatRequest / ChatResponse
│   └── feedback.py             # FeedbackRecord
├── nodes/
│   ├── __init__.py             # Re-exports all node functions + is_smalltalk
│   ├── _tracing.py             # @traced_node decorator
│   ├── load_session.py         # 1. Session initialization
│   ├── interpret_turn.py       # 2. Intent classification + constraint_refinement detection
│   ├── smalltalk.py            # 2a. Smalltalk fast-path (static, zero LLM cost)
│   ├── update_scene_memory.py  # 3. Scene engine (age extraction, implicit goals, pace, constraints)
│   ├── resolve_playbooks.py    # 4. Playbook matching + family guard + luxury guard
│   ├── choose_strategy.py      # 5. Strategy selection (guided_plan, quick_answer, etc.)
│   ├── compose_context.py      # 6. Context assembly (scene/playbook-dominant)
│   ├── rank_and_dedupe.py      # 6a. Scoring, dedup, entity cap, child-relief anchor ← NEW
│   ├── decide_retrieval.py     # 7. Retrieval gate
│   ├── fetch_exact_facts.py    # 8. Exact data lookup
│   ├── generate_response.py    # 9. LLM generation (guided_plan mode, constraint_refinement path)
│   ├── update_memory.py        # 10. Memory persistence (shortlist, preferred_categories, etc.)
│   └── emit_debug_payload.py   # 11. Rich observability with DebugEnrichment fields
├── services/
│   ├── concierge.py            # High-level orchestration
│   ├── semantic_signals.py     # Phrase→semantic-tag extraction layer ← NEW
│   ├── clean_context.py        # Contamination-free per-turn context assembly
│   ├── context_builder.py      # Context pack assembly from data layers
│   ├── enricher.py             # Semantic enrichment of entities
│   ├── normalizer.py           # Canonical data normalization
│   ├── playbook_engine.py      # Playbook loading + matching
│   ├── session_store.py        # In-memory LRU session store
│   ├── tenant_params.py        # Parameter resolution
│   ├── tenant_runtime.py       # TenantConfig loader
│   ├── feedback_service.py     # Feedback lifecycle management
│   ├── feedback_normalizer.py  # Feedback normalization
│   ├── implicit_feedback_detector.py  # Implicit signal detection
│   ├── session_tuning_engine.py       # Per-session feedback tuning
│   ├── tenant_parameter_tuner.py      # Tenant-level feedback aggregation
│   └── knowledge_gap_analyzer.py      # Playbook/knowledge gap analysis
├── context/
│   ├── mall_context.py         # Mall data loader + entity lookup
│   └── semantic_mall_model.py  # Semantic mall intelligence layer
├── retrieval/
│   └── retriever.py            # Canonical/semantic/playbook lookup
├── prompts/
│   └── builder.py              # LLM prompt assembly (identity + context + scene)
├── api/
│   ├── chat.py                 # POST /api/chat
│   ├── health.py               # GET /api/health
│   ├── feedback.py             # POST/GET /api/feedback
│   └── session.py              # GET /api/session, POST /api/session/reset
├── config/
│   ├── settings.py             # Pydantic Settings (BACKEND_ prefix)
│   └── constants.py            # App constants
├── observability/
│   └── logger.py               # Structured logging
├── feedback/
│   └── service.py              # Feedback service wrapper
└── utils/
    └── ids.py                  # ID generation (session, message, feedback)

backend/data/playbooks/
├── al_nakheel_plaza_28.json    # 18 playbooks (incl. pb-family-shopping, pb-before-movie, etc.)
└── al_nakheel_plaza_13.json    # 18 playbooks (same new entries)
```

### State Typing Strategy

- **Pydantic BaseModel** for the top-level `ConciergeState` and all sub-models.
- **`Annotated[list, operator.add]`** for accumulating fields (`messages`, `node_trace`, `warnings`) — nodes append without reading the full list.
- **`model_copy(deep=True)`** when a node needs to mutate a nested Pydantic object (e.g., SceneMemory).
- All fields have defaults — the graph can be invoked with just `raw_user_message` and `mall_id`.
- `DebugEnrichment` is written incrementally across multiple nodes; `emit_debug_payload` reads the final merged state.

### FastAPI Integration

```python
from app.graph.builder import build_concierge_graph

# Build once at startup
graph = build_concierge_graph()

@router.post("/api/chat")
async def chat(request: ChatRequest):
    result = await graph.ainvoke({
        "session_id": request.session_id or generate_session_id(),
        "mall_id": request.mall_id or "al_nakheel_plaza_28",
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

The `emit_debug_payload` node now produces an enriched payload:

1. **`response_debug_summary`** — human-readable text including scene notes, semantic signals, playbook, ranking explanations.
2. **`evaluator_stub`** — structured dict with all DebugEnrichment fields for quality scoring pipelines.
3. **`latency_by_node`** — per-node latency map for performance monitoring.
4. **`node_trace`** — full execution trace for replay and debugging.

Example evaluator_stub output:

```json
{
  "intent_domain": "shopping",
  "message_kind": "fresh_request",
  "playbook_used": "pb-family-shopping",
  "strategy_used": "guided_plan",
  "must_acknowledge_scene": true,
  "entity_cap": 5,
  "scene_summary": {
    "companions": ["child"],
    "companion_details": [{"type": "child", "age": 5}],
    "visit_type": "family_visit",
    "audience": ["family_friendly", "kid_friendly", "parent_with_child"],
    "implicit_goal": "shopping while keeping child engaged",
    "visit_constraints": ["kid_friendly_required"],
    "pace": "moderate"
  },
  "inferred_scene_notes": [
    "Detected parent_with_child from age reference '5 yr old' (age 5)",
    "Inferred family_visit from child age mention",
    "Inferred implicit goal: shopping while keeping child engaged"
  ],
  "semantic_match_explanations": [
    "kid_friendly: child companion detected (age 5)",
    "shopping_mission: visitor has explicit shopping intent"
  ],
  "ranking_explanations": [
    "MINISO (store): matches signals: kid_friendly, family_friendly; playbook boost: kid_friendly",
    "Fun Time (entertainment): child-relief anchor; matches signals: child_activity"
  ],
  "playbook_rejection_reasons": [
    "pb-luxury-shop rejected: luxury playbook inappropriate when child companions are present"
  ],
  "candidate_count_before_dedupe": 16,
  "deduped_entity_count": 14,
  "final_entity_count": 5
}
```

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

The `load_session` node reads from the store; `update_memory` writes back. The `update_memory` node now also persists `last_successful_playbook`, `last_response_shape`, and `preferred_categories` to support warm-start continuity.

### Implementation Status

| Step | Status |
|------|--------|
| LLM wired in `generate_response` (OpenAI GPT-4.1) | Done |
| `fetch_exact_facts` with canonical data lookups | Done |
| `compose_context` pulls real entities from context packs | Done |
| `normalizer.py` wired into `load_session` | Done |
| `ScenarioPlaybook` objects loaded in `resolve_playbooks` | Done |
| In-memory session store (LRU, max 1 000) | Done |
| Clean context builder (no LLM history contamination) | Done |
| Semantic mall model (`semantic_mall_model.py`) | Done |
| Feedback system (explicit + implicit + tuning) | Done |
| **Scene engine upgrade** (age, companion_details, implicit_goal, pace, constraints) | **Done** |
| **Semantic signal extraction layer** (`semantic_signals.py`) | **Done** |
| **`rank_and_dedupe` node** (scoring, dedup, entity cap, child-relief anchor) | **Done** |
| **`constraint_refinement` message_kind** (detect + handle without resetting scene) | **Done** |
| **`guided_plan` strategy** (scene acknowledgment, concierge plan mode) | **Done** |
| **`pb-family-shopping`, `pb-before-movie`, `pb-after-movie`, `pb-quick-errand`** playbooks | **Done** |
| **Family/luxury playbook guards** in `resolve_playbooks` | **Done** |
| **`DebugEnrichment`** model with scene notes, ranking, rejection reasons | **Done** |
| **Acceptance tests** (47 tests, 7 AC queries) | **Done** |
| **Query normalization table** (`_NORMALIZATION_TABLE`) — movie, dining, mall, offer equivalences | **Done** |
| **Unsupported input detection** (`is_likely_unsupported`) — gibberish, keyboard mashing, high-consonant strings; character-diversity check scoped to ≤ 4-token inputs only (natural English sentences have inherently low unique-char ratios and must not be flagged) | **Done** |
| **Brand misspelling correction** (`maybe_correct_brand`) — fuzzy matching with trailing filler word stripping | **Done** |
| **`context_setting` message kind** — scene declarations route to concierge for acknowledgement | **Done** |
| **Dual-flow routing discipline** (`route_flow.py`) — priority-ordered factual vs. concierge rules | **Done** |
| **Factual sub-intent registry** — `offer_details`, `brand_availability` added to `_FACTUAL_SUB_INTENTS` | **Done** |
| **`_is_pure_lookup` expansion** — `"any movies"`, `"now showing"`, `"movies with"` recognized as filtered factual lookups | **Done** |
| **Topic lock persistence** (`topic_lock`, `topic_lock_confidence`, `last_context_setting_turn` in SceneMemory) | **Done** |
| **Offer honesty guard** in `generate_response` — no hallucinated promotions when offer data absent | **Done** |
| **Unsupported recovery path** in `generate_response` — deterministic fallback, no LLM call | **Done** |
| **Extended `DebugEnrichment`** — `normalized_query`, `canonical_query_pattern`, `topic_lock`, `topic_lock_confidence`, `retrieval_discipline_reason`, `canonical_name_normalization_notes`, `suppressed_playbooks`, `selection_reason`, `interpretation_contract` | **Done** |
| **Normalized canonical deduplication** — unicode-normalized, article-stripped, punctuation-stripped dedupe key | **Done** |
| **Comprehensive stability test suite** — `tests/test_stability.py`, 80 tests across 14 AC categories | **Done** |
| **Near-cinema dining pre-check** in `route_flow.py` — dining intent + proximity phrase routes to concierge (proximity is a location modifier, not a cinema intent override) | **Done** |
| **`_FACTUAL_FLOW_RECOMMENDATION_OVERRIDES`** in `response_mode_resolver.py` — curated recommendation phrases ("any high-end options", "which stores give the best value") force `guided_recommendation` even when `flow_type = "factual"` | **Done** |
| **Confidence calibration fixes** — broad category openers always return MEDIUM; budget declarations (`"budget around X SAR"`) always return MEDIUM; vague short constraints (`"not too heavy"`, ≤ 6 tokens) return MEDIUM; open-ended superlatives (`"most fun thing"`) return MEDIUM | **Done** |
| **`_infer_goal` word-boundary fix** in `update_scene_memory.py` — `_GOAL_SIGNALS` matched with `\bsignal\b` regex to prevent false positives (e.g. `"eat"` inside `"weather"`) | **Done** |
| **ATM substring false-positive fix** — `"atm"` replaced with explicit phrases (`"find an atm"`, `"where is the atm"`, etc.) in `_FACTUAL_SERVICE_PATTERNS` to avoid matching `"treatment"` | **Done** |
| **Vector store + Chroma ingestion** — `VectorStoreService` (`services/vector_store.py`) + `ingest_vectors.py`; two per-mall Chroma collections live (`al_nakheel_plaza_28`: 104 vectors, `al_nakheel_plaza_13`: 54 vectors); three-tier caching (Redis → Chroma → OpenAI embed) | **Done** |
| **Contextual query augmentation** — `build_scene_prefix(scene)` prepends occasion, companions, visit_type, budget to the vector query before embedding; applied in `compose_context` vector fallback and via `MallRetriever.search_semantic(scene=None)` for direct call sites | **Done** |
| **Redis-based session persistence** — `RedisSessionStore` in `services/session_store.py`; activates when `BACKEND_REDIS_URL` is set, falls back to in-memory LRU | **Done** |
| **LangGraph checkpointer for durable state** — `runtime.py` wires `MemorySaver` (dev) or `AsyncRedisSaver` (when Redis URL set); controlled by `BACKEND_ENABLE_CHECKPOINTER`; `build_concierge_graph(checkpointer=...)` accepts it | **Done** |
| **Quality evaluator from `evaluator_stub` data** — `services/quality_evaluator.py` LLM-as-judge scorer; fires fire-and-forget after every turn in both `concierge.py` and `stream.py`; persists to `data/evaluations/`; controlled by `BACKEND_ENABLE_EVALUATOR` | **Done** |
| **Streaming responses (SSE)** — `app/api/stream.py` implements `POST /api/chat/stream` with token-by-token SSE; blocking `POST /api/chat` endpoint unaffected | **Done** |
| **LLM-first `interpret_turn`** — pure `gpt-4o-mini` classifier; returns `flow_type`, `response_mode`, `is_gibberish`, `secondary_intents`, `modifiers`, `scenario`, `scene_corrections` | **Done (v1.6)** |
| **Thin `route_flow`** — six business-policy rules, `_resolve_response_strategy()`, topic lock stability; keyword tables removed | **Done (v1.6)** |
| **LLM-first `update_scene_memory`** — structured delta extraction; `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task` fields | **Done (v1.6)** |
| **`MessageKind` str enum** — 13 values including `CRISIS`, `IDENTITY`, `HOWRU`, `THANKS`, `FAREWELL`, `CATEGORY_NEGATION`, `COMPANION_CORRECTION`; `SMALLTALK_KINDS` frozenset | **Done (v1.6)** |
| **`ShoppingTask` sub-model** — `item`, `recipient`, `budget_hint`, `urgency` in `SceneMemory` | **Done (v1.6)** |
| **Progressive greeting engine** — `scene.greeting_streak` drives 3-tier greeting pool in `smalltalk` | **Done (v1.6)** |
| **LLM-generated personalised farewell** — `gpt-4o-mini` call using scene context | **Done (v1.6)** |
| **Context-aware thanks & mood-plan responses** in `smalltalk` | **Done (v1.6)** |
| **Semantic signals lookup tables** — `semantic_signals.py` now purely lookup-based; regex/phrase matching removed | **Done (v1.6)** |
| **Thin `response_mode_resolver`** — trusts LLM hint; hard overrides for edge cases only | **Done (v1.6)** |
| **Rebalanced `rank_and_dedupe` weights** — audience 0.30, hard mismatch penalty, shopping task guard | **Done (v1.6)** |
| **Playbook occasion/wedding override + luxury guard + shopping task scope** in `resolve_playbooks` | **Done (v1.6)** |
| **`load_session` query expansion** — `expand_short_query()` produces `expanded_query` from scene context | **Done (v1.6)** |
| **`stream.py` suggestions** — `done` event includes `suggestions` CTA chips; `conversation_mode` preserved across smalltalk | **Done (v1.6)** |
| **Three new malls** — Al Ahsa Mall (1), The View Mall (10), Al Nakheel Mall (27); five malls total | **Done (v1.6)** |
| **`classifier_model` setting** — `gpt-4o-mini` for classification calls; reduces cost ~30× vs GPT-4o | **Done (v1.6)** |
