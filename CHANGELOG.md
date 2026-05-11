# Changelog

All notable changes to the Cenomi Mall Concierge platform are recorded here.

Format: `## [vX.Y] — YYYY-MM-DD` with sections Added / Changed / Fixed.

---

## [v1.7] — 2026-04-24

### Added

**Bilingual Concierge — English / Arabic**

End-to-end language support for Assistant output and UX chrome.

- **`ChatRequest.language` (`backend/app/models/api.py`)** — Optional client override `"en"` | `"ar"`. When omitted, `load_session` auto-detects from the normalized user message via `app/utils/language.py` (`detect_language`).
- **`GraphState.detected_language` (`backend/app/models/state.py`)** — Carried through the graph; concierge system prompts (`llm/prompts/concierge_prompt.py`), smalltalk pools, closure-question side prompts in `generate_response.py`, and CTA chips (`cta_generator.py` / `get_cta_suggestions`) receive Arabic instructions when `ar`.
- **Blocking + streaming APIs** (`concierge.py`, `stream.py`) — Persist `detected_language` on the reply path; SSE `done` suggestions are localized.

**Frontend — RTL + Language Toggle**

- **`TopBar`** — EN / عربي toggle; title tooltips for switch target language.
- **`App.tsx`** — Sync `document.documentElement.lang` / `dir`; root container `dir` for RTL when Arabic.
- **Copy** — `constants.ts`: `getSuggestedQueries`, `getFeedbackReasonLabels`, Arabic UI copy for welcome, typing indicator, placeholders (`ChatPage.tsx`, `ChatInput.tsx`, `FeedbackControls.tsx`).
- **`useChat.ts`** — Persists preference in `localStorage` key `cenomi_language`; sends `language` on every chat/stream request (`types/api.ts`, `client.ts`).

**CTA Pills + Tenant Cards + Mall Map Modal**

- **`ChatMessage.tsx`** — Extracts quick-reply **pills** from `**bold**` spans (English and Arabic separators: commas, `،`, `أو`, `و`); renders horizontal **tenant cards** (`TenantCard`) when structured card metadata is present.
- **`MapModal.tsx`** **(new)** — Full-screen Mappedin iframe: `#/profile?location=…` deep link plus optional `postMessage` fallback (`app-loaded` / `set-state`).

**QA / Evaluation Tooling**

- **`backend/scripts/run_arabic_tests.py`** — Runs test suites with `language: "ar"` on each request.
- **`docs/evaluation-methodology.md`**, **`docs/pipeline-stability-and-query-capability.md`** — Methodology and stability references for operators.
- **`backend/scripts/run_pipeline_evaluation.py`**, **`run_comparison_queries.py`**, **`generate_evaluation_report.py`** — Pipeline and comparison helpers; **`generate_client_report.py`** (repo root) — client-facing report generation.
- **`backend/scripts/audit_session.py`** — Expanded session audit utilities.
- **Tests:** `backend/tests/test_client_feedback.py`, `backend/tests/test_concierge_scenarios.py`; refinements elsewhere (e.g. `test_e2e_scenarios.py`).

**Data**

- **`backend/scripts/arabic_translations_cache.json`** — Auxiliary cache for Arabic-related scripting.
- **`backend/data/semantic/al_nakheel_plaza_28.json`**, **`backend/data/playbooks/al_nakheel_plaza_28.json`** — Incremental refreshes for mall 28.

### Changed

**`backend/llm/prompts/query_expander.py`**, **`concierge_prompt.py`**, **`generate_response.py`** — Prompt and expansion adjustments for bilingual grounding and Arabic output quality.

**`frontend/src/styles/index.css`** — Styling tweaks for RTL / new components.

**Files changed:** `backend/app/models/api.py`, `backend/app/models/state.py`, `backend/app/utils/language.py`, `backend/app/nodes/load_session.py`, `backend/app/nodes/generate_response.py`, `backend/app/nodes/smalltalk.py`, `backend/app/services/concierge.py`, `backend/app/services/clean_context.py`, `backend/app/services/cta_generator.py`, `backend/app/api/stream.py`, `backend/llm/prompts/concierge_prompt.py`, `backend/llm/prompts/query_expander.py`, `backend/scripts/run_arabic_tests.py`, `backend/scripts/audit_session.py`, `frontend/src/App.tsx`, `frontend/src/pages/ChatPage.tsx`, `frontend/src/components/TopBar.tsx`, `frontend/src/components/ChatMessage.tsx`, `frontend/src/components/ChatInput.tsx`, `frontend/src/components/FeedbackControls.tsx`, `frontend/src/components/MapModal.tsx` *(new)*, `frontend/src/hooks/useChat.ts`, `frontend/src/api/client.ts`, `frontend/src/lib/constants.ts`, `frontend/src/types/api.ts`, `frontend/src/types/chat.ts`, mall 28 semantic/playbook JSON, docs under `docs/`, assorted `backend/scripts/` and test artefacts as committed.

---

## [v1.6] — 2026-03-31

### Added

**Three New Malls — Data Pipeline**

The platform now serves five malls simultaneously. Data pipeline run for three new properties:

- **Al Ahsa Mall** (Al Ahsa, mall 1) — canonical, semantic, playbook, tenant config, and context pack files generated.
- **The View Mall** (Riyadh, mall 10) — canonical, semantic, playbook, tenant config, and context pack files generated.
- **Al Nakheel Mall** (Riyadh, mall 27) — canonical, semantic, playbook, tenant config, and context pack files generated.

`BACKEND_MALL_IDS` default updated to include all five malls: `al_nakheel_plaza_28,al_nakheel_plaza_13,al_nakheel_plaza_1,al_nakheel_plaza_10,al_nakheel_plaza_27`.

**Progressive Greeting Engine (`smalltalk.py`)**

The greeting response now adapts across repeat greetings within the same session using a three-tier pool keyed to `scene.greeting_streak`:
- `streak == 0` — warm welcome with capability hint.
- `streak == 1` — second greeting: orient the visitor with a brief spatial cue.
- `streak >= 2` — persistent greeting: gently ask what the visitor is actually looking for.

**LLM-Generated Farewell Personalisation (`smalltalk.py`)**

Farewell responses are now generated by `gpt-4o-mini` using scene context (companions, occasion, active topic, active shortlist). Falls back to the static pool automatically on any LLM error so the node never crashes.

**Context-Aware Thanks Responses (`smalltalk.py`)**

`_pick_thanks_response()` inspects completed steps and active topic to proactively suggest a domain the visitor hasn't explored yet — turning a potential conversation-ender into a live handoff. Falls back to a generic thanks pool when no context is available.

**Mood-Plan Emotional Responses (`smalltalk.py`)**

`_pick_emotional_response()` detects plan/uplift signals in the message (`"make me happy"`, `"cheer me up"`, `"give me a plan"`) and routes to a dedicated `emotional_mood_plan` pool that offers a complete uplifting itinerary, rather than the standard empathetic holding response.

**`ShoppingTask` Sub-Model (`state.py`)**

New Pydantic sub-model on `SceneMemory` for fine-grained shopping task tracking: `product_type`, `product_category`, `target_person`, `target_gender`, `target_age`, `budget_preference`, `use_case`, `shopping_stage`. Used by `update_scene_memory`, `rank_and_dedupe`, and `resolve_playbooks` to scope recommendations to the active product task without being overridden by family/companion signals.

**`MessageKind` Enum (`state.py`)**

`MessageKind` is now a proper `str` enum (backward-compatible — existing string comparisons continue to work). `SMALLTALK_KINDS` frozenset centralises the routing gate used by `is_smalltalk()` and `smalltalk.py`. New kinds added: `CRISIS`, `IDENTITY`, `HOWRU`, `THANKS`, `FAREWELL`, `CATEGORY_NEGATION`, `COMPANION_CORRECTION`.

**Expanded `SceneMemory` Fields (`state.py`)**

New fields added to `SceneMemory`:
- `scenario` — real-world context token (`wedding_related`, `family_outing`, `date`, `gift_shopping`, `before_movie`, `quick_visit`, `birthday`, `first_visit`, `group_outing`, `solo_visit`).
- `user_role` — declared visitor role (`bridesmaid`, `tourist`, `first_time_visitor`, etc.).
- `style_intent` — list of style signals (`elegant`, `luxury`, `casual`, `budget`, `fun`, etc.).
- `excluded_domains` — list of explicitly negated domains (`dining`, `cafe`, `shopping`, `entertainment`); accumulated and never removed.
- `visit_plan` — ordered list of activities (`["shopping", "coffee", "movie"]`).
- `shopping_task` — `ShoppingTask` sub-model (see above).
- `greeting_streak` — count of consecutive greetings answered in this session (drives progressive greeting tier).
- `topic_lock`, `topic_lock_confidence` — probabilistic factual topic lock (replaces the binary `active_primary_intent` lock).
- `recent_mood` — last detected emotional state for context passing to the next turn.
- `topic_history` — rolling list of the last 10 domains visited in this session.

### Changed

**`interpret_turn.py` — Pure LLM Classifier (no keyword overrides)**

The entire node is now LLM-first with no post-LLM rule overrides. The LLM (`gpt-4o-mini`) returns a rich JSON payload that includes all routing and scene signals directly:

- **Removed:** `_CONCIERGE_KEYWORD_SIGNALS`, `_detect_flow_type_candidate()`, `_extract_hybrid_intent_bundle()`. No rule-based signal extraction runs after the LLM.
- **LLM now returns:** `flow_type` (routing decision), `response_mode` (how to respond), `is_gibberish` (true/false), `secondary_intents` (list), `modifiers` (list), `scenario` (real-world context), `scene_corrections` (list of correction directives for companion-correction turns).
- **New message_kinds:** `category_negation` (explicit domain exclusion: "no food"), `companion_correction` (denying assumed companions: "I don't have kids"), and the five smalltalk kinds now fully LLM-classified: `crisis`, `identity`, `howru`, `thanks`, `farewell`.
- **Response mode guidance embedded in prompt:** The LLM selects from 7 canonical response modes (`direct_factual`, `guided_recommendation`, `hybrid_plan`, `best_effort_shortlist`, `context_acknowledgement`, `graceful_recovery`, `clarification_request`) using explicit rules in the classification prompt.
- **Gibberish detection:** Moved entirely to the LLM (`is_gibberish` field). Pre-flight short-circuit via `is_likely_unsupported()` retained only for truly empty / 1-2 char inputs.
- **Deterministic post-processing** retained for: `_PRIMARY_INTENT_MAP` lookup, `_DOMAIN_CONTEXT_TYPE` lookup, `_SUB_INTENT_TO_FACT_SCOPE` lookup, and topic lock confidence adjustment — none of these involve text matching.

**`route_flow.py` — Policy-Only Routing**

All keyword/regex-based signal lists removed. The node now applies six clean, ordered business-policy rules on top of the LLM's `flow_type_candidate`:

| Rule | Trigger | Decision |
|---|---|---|
| 0. Domain lock | `active_primary_intent` is factual, no explicit switch | Force factual, set `domain_locked=True` |
| 1. Cross-mall | `domain == "cross_mall"` or `sub_intent == "cross_mall_search"` | Always factual |
| 2. Context kinds | `message_kind` in `{context_setting, companion_correction, acknowledgement}` | Always concierge |
| 3. Constraint refinement | `message_kind == "constraint_refinement"` | Inherit prior flow |
| 4. Factual follow-up | `last_flow_type == "factual"` and followup/refinement | Stay factual (unless strong scene pivot) |
| 5. LLM trust | `intent.flow_type_candidate` is set | Trust the LLM directly |
| 6. Default | None of the above | Concierge |

`_resolve_response_strategy()` added: maps `primary_intent + secondary_intents/modifiers → canonical response strategy name` for downstream nodes. Topic lock stability tracking added (confidence-weighted: `+0.05` on followup, `−0.40` on topic_switch).

**`update_scene_memory.py` — LLM-First Scene Extraction**

Keyword scanner replaced with a structured LLM delta call (`gpt-4o-mini`). The LLM receives the current message, current scene, message kind, and last 4 turns of conversation — returning only the fields that changed as a JSON delta.

- **New fields extracted:** `scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task`, `clear_shopping_task`.
- **Abbreviation normalization:** `gf` → `girlfriend`, `bf` → `boyfriend`, `hubby` → `husband`, `wifey` → `wife`. Never stores abbreviations in scene.
- **Wedding / event role disambiguation:** `"for the bride"`, `"for a bridesmaid"`, etc. set `target_person` to the exact role rather than collapsing to `"someone"`.
- **`_apply_scene_corrections()`:** Processes LLM-generated `scene_corrections` directives (`all_family_context`, `companion:<name>`, `visit_type:<value>`, `target_person`, `scenario`) — always applied before any additive extraction so user denials override keyword-based additions.
- **Fast-exit paths** for `acknowledgement`, `companion_correction`, `constraint_refinement`, `category_negation`, `disengagement`, and factual flow — each path skips unnecessary extraction and returns appropriate debug enrichment.
- Keyword scanner retained as a fallback for `_SKIP_LLM_EXTRACTION_KINDS` message kinds.

**`semantic_signals.py` — Lookup-Table Only**

All raw text / regex matching removed. The function now maps structured `SceneMemory` fields to semantic tags via five lookup tables (`_SCENE_COMPANION_TAG_MAP`, `_SCENE_OCCASION_TAG_MAP`, `_SCENE_CONSTRAINT_TAG_MAP`, `_SCENE_BUDGET_TAG_MAP`, `_INTENT_SUBINTENT_TAG_MAP`). No phrase scanning against the user's message text.

**`response_mode_resolver.py` — Thin Policy Layer**

The resolver is now a thin policy layer over the LLM's `response_mode_hint`. It trusts the LLM hint for all normal turns and applies only four hard overrides: out-of-scope external venue requests, unsupported capability requests (taxi booking, food ordering), gibberish/unintelligible input, and short factual follow-ups in a locked topic (forces `direct_factual` so the LLM doesn't downgrade to `guided_recommendation` on a short follow-up). The ~200-line business-logic scoring fallback is retained only for the rare case where the LLM left `response_mode_hint` empty.

**`rank_and_dedupe.py` — Rebalanced Scoring Weights**

Audience fit weight raised from 0.15 to 0.30 (semantic 0.20→0.15, playbook 0.20→0.10) so that target person / companion context dominates ranking. Hard audience mismatch penalty added: when an active audience requirement is in force and an entity's raw audience fit score is below 0.20, the entity's score is multiplied by 0.2 — preventing premium/adult-only stores from outranking family-friendly alternatives on tag strength alone. Child-relief anchor injection skipped when a specific product shopping task is active (avoids injecting cinema entities into a jacket-search shortlist).

**`resolve_playbooks.py` — Occasion and Shopping Task Guards**

- **Occasion/wedding override** (new, runs before family override): when `scene.user_role` is a wedding role (`bridesmaid`, `bride`, `groom`, `maid_of_honor`, etc.) or `scene.scenario == "wedding_related"` and the domain is shopping/exploration, routes to `pb-luxury-shop` instead of family playbooks.
- **Luxury playbook guard**: `pb-luxury-shopping`, `pb-luxury-dining`, `pb-fine-dining` are rejected when child companions are present; falls back to family playbook when no specific product task is active.
- **Shopping task scope**: when `has_specific_shopping_task` is true, family override is suppressed and family signals are injected as ranking biases only (preventing `pb-family-shopping` from hijacking a "buy jackets for my 5-year-old" task).
- `scenario` and `user_role` are now included in `_collect_scene_signals()` for richer playbook matching.

**`resolve_fact_scope.py` — LLM Candidate Trusted First**

`intent.fact_scope_candidate` (set by the LLM classifier) is now used directly when present; keyword rules are fallback only (firing when the LLM left the field empty). `_SCOPE_DEFAULT_RESPONSE_MODE` mapping added to avoid re-running keyword rules when the scope is already known.

**`query_classifier.py` — Minimal Pre-Flight Only**

The entire rule-based classifier, `RULE_CONFIDENCE_THRESHOLD`, `_KEYWORD_RULES`, `_CANONICAL_QUERY_MAP`, and all helper functions have been removed. The module now provides only `is_likely_unsupported(query) → bool` — a fast pre-flight that returns `True` only for truly empty or 1–2 character inputs that are not recognisable keywords. All intent classification is delegated to the LLM in `interpret_turn.py`.

**`load_session.py` — Short Query Expansion**

The node now calls `expand_short_query(normalized, scene_context=scene_ctx)` from `llm.prompts.query_expander` on every turn. When the query is short and scene context is available (companions, occasion, topic, etc.), the expansion provides a richer query string (`expanded_query`) for downstream embedding and retrieval. The original `normalized_user_message` is preserved unchanged for classification and display.

**`stream.py` — CTA Suggestions + Non-LLM Token Emission**

- `done` event now includes a `suggestions` field: quick-reply chip text derived from `get_cta_suggestions(cta_type)` so the frontend can render contextual prompt chips before any closing question.
- Smalltalk paths (greeting, thanks, farewell, crisis, etc.) bypass `generate_response` and produce no `on_chat_model_stream` events. The final `final_response_text` is now emitted as a single `event: token` so the frontend renders the response correctly on these paths.
- `conversation_mode` is preserved from the prior session for transient smalltalk turns (prevents "Conversation mode: greeting" from leaking into the classifier context on the next real turn).

**Frontend — Five-Mall Selector**

`frontend/src/lib/constants.ts`: `MALLS` array expanded to five entries with city labels:
- Al Nakheel Plaza (Buraidah, mall 28)
- Mall of Arabia (Jeddah, mall 13)
- Al Nakheel Mall (Riyadh, mall 27)
- The View Mall (Riyadh, mall 10)
- Al Ahsa Mall (Al Ahsa, mall 1)

`ContextBlocksCard.tsx` and `SceneSnapshotCard.tsx` updated to render new scene fields (`scenario`, `user_role`, `style_intent`, `excluded_domains`, `visit_plan`, `shopping_task`). `TopBar.tsx` updated for five-mall layout.

**Files changed:** `backend/app/nodes/interpret_turn.py`, `backend/app/nodes/route_flow.py`, `backend/app/nodes/smalltalk.py`, `backend/app/nodes/update_scene_memory.py`, `backend/app/nodes/load_session.py`, `backend/app/nodes/rank_and_dedupe.py`, `backend/app/nodes/resolve_fact_scope.py`, `backend/app/nodes/resolve_playbooks.py`, `backend/app/nodes/compose_context.py`, `backend/app/nodes/generate_response.py`, `backend/app/models/state.py`, `backend/app/services/semantic_signals.py`, `backend/app/services/response_mode_resolver.py`, `backend/app/api/stream.py`, `backend/app/runtime.py`, `backend/app/config/settings.py`, `backend/intent/query_classifier.py`, `backend/llm/prompts/concierge_prompt.py`, `frontend/src/App.tsx`, `frontend/src/components/ContextBlocksCard.tsx`, `frontend/src/components/SceneSnapshotCard.tsx`, `frontend/src/components/TopBar.tsx`, `frontend/src/hooks/useChat.ts`, `frontend/src/lib/constants.ts`, data files for malls 1, 10, 27

---

## [v1.5] — 2026-03-30

### Added

**Cross-Mall Factual Path — Dedicated Pipeline Routing for Brand Availability**

Cross-mall brand queries now flow through the structured factual pipeline (`resolve_fact_scope → compose_fact_response_context → generate_response`) instead of being assembled inline inside `generate_response`. This gives cross-mall queries a proper scope, dedicated context composition, and a consistent response shape.

- `backend/app/services/cross_mall_brand.py` **(new)** — `resolve_cross_mall_brand_query(state)` resolves the search string for cross-mall lookups via a fallback chain: stripped message → `fact_query_entity` → `scene.last_resolved_entity` → `scene.active_shortlist[0]`. `extract_brand_query_from_message()` strips 15+ cross-mall boilerplate patterns before extracting the brand fragment. Enables follow-up queries like `"where else can I find it?"` to correctly resolve the brand from prior scene context.
- `backend/tests/test_cross_mall_v1.py` **(new)** — Unit tests with no LLM calls covering: brand resolution via all four fallback paths, `_format_fact_context` section ordering (AT YOUR CURRENT MALL before AT OTHER CENOMI MALLS), and flow-hint routing (`cross_mall_search` → `factual` / `cross_mall_availability` / `brand`, single-mall `brand_availability` not misclassified as cross-mall).

**New async runtime helpers (runtime.py)**

- `search_brand_across_configured_malls(brand_name, home_mall_id)` — async variant of `search_brand_across_malls`. Iterates every mall in `BACKEND_MALL_IDS` via `ensure_mall_loaded` per mall so LRU eviction between steps cannot drop results. Logs a warning when `mall_cache_size` is smaller than the configured mall count to surface potential churn under parallel traffic.
- `build_merged_guard_canonical_for_configured_malls()` — async version of `get_all_mall_canonical_for_guard`. Loads each configured mall through the Tier 1 → Tier 2 (Redis) → Tier 3 (disk) chain before merging, ensuring the hallucination guard covers all configured malls even when some are cold-evicted from RAM.
- `ensure_configured_malls_loaded()` — pre-warms every mall in `BACKEND_MALL_IDS` (convenience helper for startup or batch operations).

### Changed

**`compose_fact_response_context.py` — cross-mall early exit**

`_compose_cross_mall_fact_context()` added: when `scope == "cross_mall_availability"`, calls `resolve_cross_mall_brand_query` and `search_brand_across_configured_malls`, then splits results into `cross_mall_at_home` and `cross_mall_other` lists (each capped), attaches `cross_mall_brand_query`, and returns a structured fact context dict that `generate_response` formats directly. Empty brand query produces a graceful no-results payload instead of an error.

**`generate_response.py` — factual cross-mall formatting**

- `_format_fact_context` now recognises `scope == "cross_mall_availability"` and renders a two-section block: `AT YOUR CURRENT MALL` (home results) followed by `AT OTHER CENOMI MALLS` (other-mall results).
- Hallucination guard for the `cross_mall_availability` scope now uses `build_merged_guard_canonical_for_configured_malls()` (async, all configured malls) instead of the RAM-only `get_all_mall_canonical_for_guard()`.

**`interpret_turn.py` — cross-mall flow hint**

`_detect_flow_type_candidate` maps `domain="cross_mall"` / `sub_intent="cross_mall_search"` to `flow_type="factual"`, `scope="cross_mall_availability"`, `entity_type="brand"`. This steers the turn into the factual branch before `resolve_fact_scope` runs.

**`resolve_fact_scope.py` — cross-mall scope keywords**

Cross-mall trigger phrases (`"which.*mall"`, `"other mall"`, `"both malls"`, `"across malls"`, etc.) now map deterministically to `scope="cross_mall_availability"` with an empty `[]` retrieval targets list (targets are resolved downstream by `compose_fact_response_context`).

**`compose_context.py` — configured-malls search integration**

Cross-mall context assembly now calls `search_brand_across_configured_malls` and `resolve_cross_mall_brand_query` (instead of the RAM-only `search_brand_across_malls`) so all configured malls are searched regardless of LRU state.

**`session_store.py` — mall_id update on session reuse**

When a request arrives with an existing `session_id` but a different `mall_id` (visitor switches mall mid-session or the same session token is reused from a new mall context), the session's `mall_id` is now updated in both the in-memory store and Redis. Previously the stale `mall_id` was silently retained.

**`frontend/src/App.tsx`** — Responsive width classes adjusted for the chat vs debug panel split layout.

**`frontend/src/hooks/useChat.ts`** — Streaming edge-case hardening: `done` event finalises the bubble even if no tokens arrived; empty stream or mid-stream error no longer leaves the message in a permanent `isStreaming: true` state.

**Files changed:** `backend/app/services/cross_mall_brand.py` *(new)*, `backend/tests/test_cross_mall_v1.py` *(new)*, `backend/app/runtime.py`, `backend/app/nodes/compose_fact_response_context.py`, `backend/app/nodes/generate_response.py`, `backend/app/nodes/interpret_turn.py`, `backend/app/nodes/resolve_fact_scope.py`, `backend/app/nodes/compose_context.py`, `backend/app/services/session_store.py`, `backend/app/config/settings.py`, `frontend/src/App.tsx`, `frontend/src/hooks/useChat.ts`

---

## [v1.4.1] — 2026-03-24

### Changed

**Contextual Query Augmentation — Vector Search (Gap 2)**

The vector search fallback now embeds scene-aware queries instead of the bare user message. `SceneMemory` signals (`occasion`, `companions`, `visit_type`, `budget`) are prepended to the query before embedding so contextually different sessions produce different vectors — returning entities that match the visitor's actual situation rather than the generic query alone.

- `backend/app/retrieval/retriever.py` — new `build_scene_prefix(scene) -> str` function. Reads `occasion`, `companions` (up to 3), `visit_type`, and `budget` from `SceneMemory`; normalises underscores to spaces; deduplicates; returns a compact keyword prefix.
- `backend/app/nodes/compose_context.py` — `vector_query` is now augmented with `build_scene_prefix(scene)` before the `_vector_search_fallback` call. An `if scene_prefix` guard ensures bare sessions (no context accumulated yet) produce an unchanged query — no regression risk for fresh sessions and no unnecessary cache disruption.

**Example:**
```
# Same bare query, two different sessions:
# Session A (anniversary couple):  embed "anniversary partner date something for dinner"
# Session B (family with kids):    embed "kids family outing something for dinner"
# Session C (no context set yet):  embed "something for dinner"  ← unchanged
```

**Impact:** Zero regressions across all 204 test cases (test-queries.md, test-queries-broken.md, test-queries-complex.md — 100% pass). The change affects only the last-resort vector fallback path, which fires after all routing decisions are already made.

**Files changed:** `backend/app/retrieval/retriever.py`, `backend/app/nodes/compose_context.py`

**Test results:** `test-results/EVALUATION_REPORT_2026-03-24_post_gap2.md` — 204/204 (100%)

---

## [v1.4] — 2026-03-24

### Added

**Semantic / Vector Retrieval**

Mall entity descriptions (stores, dining, events) are now embedded and stored in a per-mall Chroma collection, enabling vector similarity search alongside the existing rule-based retrieval.

- `backend/app/services/vector_store.py` (new) — `VectorStoreService` wraps Chroma with a Redis-backed result cache (`cenomi:vcache:{mall_id}:{query_hash}`, TTL configurable via `BACKEND_VECTOR_CACHE_TTL`). On cache miss, embeds the query with `text-embedding-3-small`, searches the mall's Chroma collection, and writes the result back to Redis.
- `backend/scripts/ingest_vectors.py` (new) — offline ingestion script. Builds rich text descriptions for each entity (store profile, dining description, event summary), embeds them, and upserts into per-mall Chroma collections under `data/chroma/`.
- `backend/app/retrieval/retriever.py` — `search_semantic()` now delegates to `VectorStoreService`; falls back to tag-overlap matching when Chroma is unavailable.
- `backend/app/nodes/compose_context.py` — vector search fallback branch activated when no category rule matches, no playbook is selected, and intent domain is not `exploration` or `mall_info`.
- `backend/pyproject.toml` — added `vector` (chromadb ≥ 0.5) and `all` optional dependency groups.
- `backend/Dockerfile` — install target changed from `.[redis]` to `.[all]`; added `g++` and `cmake` to the builder stage for Chroma's native `hnswlib` dependency.
- `docker-compose.yml` — added `BACKEND_VECTOR_STORE_TYPE`, `BACKEND_CHROMA_PERSIST_DIR`, `BACKEND_VECTOR_CACHE_TTL`, `BACKEND_MALL_CACHE_SIZE`, `BACKEND_MALL_CTX_REDIS_TTL` environment variables; ensured `backend/data` volume mount covers the Chroma persistence directory.

**Multi-Mall LRU Cache with Three-Tier Resource Design**

The platform now manages up to 20 mall contexts efficiently without loading all of them into RAM simultaneously.

- `runtime.py` — `_mall_contexts` dict replaced with `LRUMallContextRegistry` (capacity configurable via `BACKEND_MALL_CACHE_SIZE`, default 5). Implements three-tier loading:
  - **Tier 1 (Python RAM)** — LRU cache of active `MallContextLoader` objects; eviction frees RAM.
  - **Tier 2 (Redis)** — Serialized `MallContextLoader` JSON cached with TTL `BACKEND_MALL_CTX_REDIS_TTL` (default 3600 s). Restoring from Redis is ~50–100× faster than disk + normalization.
  - **Tier 3 (Disk)** — Cold load from canonical/semantic/playbook JSON files; writes back to Redis on load.
- `context/mall_context.py` — `serialize()` and `from_serialized()` methods added for Redis-compatible JSON round-trip. Skips disk I/O on deserialization by rebuilding the context pack in memory.
- `settings.py` — new fields: `chroma_persist_dir`, `mall_cache_size`, `mall_ctx_redis_ttl`, `vector_cache_ttl`.

**Comprehensive Test Runner**

- `backend/scripts/run_all_tests.py` (new) — runs all three test suites (standard, broken, complex) against the live Docker backend, handles multi-turn sessions with consistent session IDs, generates Markdown and JSON reports in `test-results/`.

### Fixed

**Route Flow — Pronoun Reference Handling (`route_flow.py`)**

Queries like `"anything she would like"` were being routed to factual flow and treating the pronoun `"she"` as an entity/brand name to look up. Three interacting bugs were identified and fixed:

1. **`_CONCIERGE_HARD_SIGNALS` missing pronoun patterns** — Added `"anything she"`, `"anything he"`, `"she would"`, `"he would"`, `"for her"`, `"for him"`, etc. to both `route_flow.py`'s `_CONCIERGE_HARD_SIGNALS` and `interpret_turn.py`'s `_CONCIERGE_KEYWORD_SIGNALS`.

2. **Rule 0 domain lock `else` branch missing** — When an explicit domain switch bypassed the lock, `primary_intent` was left unset, causing `update_memory` to keep the stale `movie_lookup` in `scene.active_primary_intent`. The new `else` branch explicitly sets `primary_intent = "concierge_recommendation"` on domain switches.

3. **Rule 3 (`mall_info` domain) had no concierge override** — The LLM occasionally mis-classifies pronoun references as `mall_info/what_is_available`. Rule 3 now checks `_CONCIERGE_HARD_SIGNALS` before forcing factual flow, routing to concierge when strong pronoun/planning language is present.

**Impact:** B8.3 (`"anything she would like"`) now returns `guided_recommendation/high` across 7/7 consecutive stability test runs.

**New environment variables**

| Variable | Default | Description |
|---|---|---|
| `BACKEND_VECTOR_STORE_TYPE` | `chroma` | Vector backend: `chroma` (local) or `none` (disabled) |
| `BACKEND_CHROMA_PERSIST_DIR` | `data/chroma` | Chroma persistence directory (relative to app root) |
| `BACKEND_VECTOR_CACHE_TTL` | `900` | Redis TTL for vector search result cache (seconds) |
| `BACKEND_MALL_CACHE_SIZE` | `5` | Max mall contexts held in Python RAM simultaneously |
| `BACKEND_MALL_CTX_REDIS_TTL` | `3600` | Redis TTL for serialized mall context cache (seconds) |

**Files changed:** `backend/app/config/settings.py`, `backend/app/context/mall_context.py`, `backend/app/runtime.py`, `backend/app/services/vector_store.py` *(new)*, `backend/app/services/concierge.py`, `backend/app/retrieval/retriever.py`, `backend/app/nodes/compose_context.py`, `backend/app/nodes/interpret_turn.py`, `backend/app/nodes/route_flow.py`, `backend/pyproject.toml`, `backend/Dockerfile`, `backend/scripts/ingest_vectors.py` *(new)*, `backend/scripts/run_all_tests.py` *(new)*, `docker-compose.yml`

**Test results:** `test-results/run_2026-03-24_11-33-00.md` — 204/204 (100%)

---

## [v1.3] — 2026-03-23

### Added

**Redis session persistence**

Sessions are now stored durably in Redis instead of an in-memory dict. This means sessions survive backend restarts and can be shared across multiple backend workers.

- `AbstractSessionStore` protocol introduced in `session_store.py` — both `SessionStore` (in-memory fallback) and `RedisSessionStore` (async Redis) implement the same `async def get / get_or_create / save_turn / reset / delete` interface.
- `runtime.py` selects the store at startup: if `BACKEND_REDIS_URL` is set a `RedisSessionStore` is created; otherwise the existing in-memory store is used. Zero code change required at call sites.
- `SessionData.from_dict` classmethod added for safe JSON deserialization.
- Session TTL configurable via `BACKEND_REDIS_SESSION_TTL` (default 1 800 s / 30 min).
- `redis[asyncio] >= 5.0` added as an optional dependency group in `pyproject.toml`.

**LangGraph turn checkpointing**

When enabled, every graph invocation is snapshotted to a persistent checkpointer so any turn can be replayed or inspected offline.

- `builder.py` `build_concierge_graph(checkpointer=None)` — the compiled graph now accepts an injected checkpointer.
- `runtime.py` initialises either `AsyncRedisSaver` (when Redis is available and `BACKEND_ENABLE_CHECKPOINTER=true`) or `MemorySaver` as the graph's checkpointer.
- `runtime.get_checkpointer()` exposes the active checkpointer to both the regular and streaming pipelines.
- Each turn is keyed by `{session_id}:{time.time_ns()}` so individual turns are independently replayable.

**Asynchronous quality evaluator (LLM-as-judge)**

After each turn the concierge silently scores its own response on four dimensions using a secondary GPT-4o call. The evaluation runs in a background `asyncio.Task` and never blocks the user-facing response.

- `app/services/quality_evaluator.py` (new) — evaluates `intent_alignment`, `constraint_adherence`, `honesty`, and `conciseness`; each score 0–10 with a brief rationale.
- Results are written to `backend/data/evaluations/{session_id}-{turn_id}.json`.
- Feature-flagged via `BACKEND_ENABLE_EVALUATOR=true` (off by default).
- Integrated into both the blocking `/api/chat` endpoint and the new streaming endpoint.

**SSE streaming endpoint**

A new `POST /api/chat/stream` endpoint streams the LLM response token-by-token using Server-Sent Events, making the concierge feel significantly more responsive on long answers.

- `app/api/stream.py` (new) — full pipeline mirror of the blocking endpoint. Hooks into `graph.astream_events(version="v2")` and forwards every `on_chat_model_stream` event from the `generate_response` node as an `event: token` SSE message.
- Three SSE event types:
  - `event: token` — `{"text": "<chunk>"}` for each arriving token
  - `event: done` — `{"session_id", "session_state", "debug"}` when the stream completes
  - `event: error` — `{"detail": "<message>"}` on failure
- Session persistence, quality evaluation, and implicit feedback detection run as post-stream side effects — they never delay the first token.
- Nginx buffering disabled via `X-Accel-Buffering: no` header.
- Existing `POST /api/chat` blocking endpoint unchanged; all existing clients continue to work without modification.
- Registered in `main.py` under the `/api` prefix.

**Frontend streaming UI**

The React chat interface now renders responses token-by-token in real time.

- `src/api/client.ts` — `streamMessage(req)` async generator parses the SSE stream and yields typed `token / done / error` events. Uses native `fetch` + `ReadableStream` (no `EventSource`) since the request is a POST with a JSON body.
- `src/hooks/useChat.ts` — `send()` drives the streaming loop. On the first token an assistant message bubble is added immediately with `isStreaming: true`; subsequent tokens are appended in-place so the component never re-mounts. The `done` event attaches the session ID, debug payload, and feedback controls. Errors mid-stream cleanly finalise the bubble rather than leaving it in a streaming state.
- `src/types/chat.ts` — `ChatMessage` gains an optional `isStreaming?: boolean` field.
- `src/components/ChatMessage.tsx` — renders a blinking 2 px cursor `|` at the end of the text while `isStreaming` is true.
- `src/pages/ChatPage.tsx` — the "Thinking…" typing indicator hides as soon as the first token arrives (i.e. when any message has `isStreaming: true`), replaced by the live text bubble.
- `src/styles/index.css` — `@keyframes cursor-blink` and `.streaming-cursor` added.

**Docker / Docker Compose deployment**

The backend can now be run in a fully self-contained Docker environment with a single command.

- `backend/Dockerfile` — multi-stage build. The `builder` stage installs all dependencies (including the `redis` optional group) into a virtual environment. The lean `runtime` stage copies only the venv and application code, keeping the final image under 300 MB.
- `docker-compose.yml` — two services:
  - `redis` — `redis:7-alpine` with AOF persistence, `allkeys-lru` eviction, healthcheck.
  - `backend` — built from `./backend/Dockerfile`, depends on Redis being healthy, mounts `./backend/data` for persisted evaluation/feedback files, injects `BACKEND_REDIS_URL` pointing at the `redis` service.
- `backend/.dockerignore` — excludes `__pycache__`, `.env`, test fixtures, and IDE files.

**New environment variables**

| Variable | Default | Description |
|---|---|---|
| `BACKEND_REDIS_URL` | `""` | Redis connection string. Empty = in-memory fallback. |
| `BACKEND_REDIS_SESSION_TTL` | `1800` | Session idle timeout in seconds. |
| `BACKEND_ENABLE_CHECKPOINTER` | `false` | Enable LangGraph turn checkpointing to Redis. |
| `BACKEND_ENABLE_EVALUATOR` | `false` | Enable async LLM-as-judge quality scoring. |

**Files changed:** `backend/app/config/settings.py`, `backend/app/services/session_store.py`, `backend/app/runtime.py`, `backend/app/graph/builder.py`, `backend/app/services/concierge.py`, `backend/app/api/session.py`, `backend/app/api/stream.py` *(new)*, `backend/app/services/quality_evaluator.py` *(new)*, `backend/app/main.py`, `backend/pyproject.toml`, `backend/Dockerfile` *(new)*, `backend/.dockerignore` *(new)*, `backend/.env`, `backend/.env.example`, `docker-compose.yml` *(new)*, `frontend/src/api/client.ts`, `frontend/src/hooks/useChat.ts`, `frontend/src/types/chat.ts`, `frontend/src/components/ChatMessage.tsx`, `frontend/src/pages/ChatPage.tsx`, `frontend/src/styles/index.css`

---

## [v1.2] — 2026-03-17

### Added

**Cross-mall brand search (inline awareness)**

The concierge can now answer questions about brand/store availability across all loaded Cenomi malls while keeping the user anchored to their home mall.

- New `cross_mall` intent domain detected by the rule-based classifier (confidence 0.97, fires before all shopping/dining rules). Triggered by phrases like:
  - "Does Mall of Arabia also have Nike?"
  - "Which of your malls has Starbucks?"
  - "Is H&M available in both malls?"
  - "Do any other Cenomi malls carry this brand?"
- `search_brand_across_malls(brand_name, home_mall_id)` in `runtime.py` — searches all loaded `MallContextLoader` instances, tags each result `is_home_mall: true/false`, sorts home mall first.
- `_build_cross_mall_response()` in `generate_response.py` — dedicated response builder with structured `AT YOUR CURRENT MALL` / `AT OTHER CENOMI MALLS` prompt blocks and LLM instructions for home-first phrasing.
- Hallucination guard extended: cross-mall responses validate against a **merged canonical** from all loaded malls so valid entity names from other malls are never stripped.
- All single-mall chat behavior unchanged — cross-mall path only activates on explicit cross-mall phrasing.

**Files changed:** `intent/query_classifier.py`, `app/nodes/interpret_turn.py`, `app/runtime.py`, `app/nodes/compose_context.py`, `app/nodes/generate_response.py`

---

## [v1.1] — 2026-03-17

### Added

**Multi-mall support**

The platform now serves multiple malls simultaneously. Each mall has its own isolated knowledge context (canonical data, semantic profiles, playbooks). The API was already designed with `mall_id` per request — the main change was making the runtime hold a registry instead of a singleton.

- `transform_mall_data.py` now accepts `--mall-id <N>` CLI argument (was hardcoded to mall 28). Supports any of the 20 malls in the dataset.
- Data pipeline run for **Mall of Arabia (Jeddah, mall 13)** — generated canonical, semantic, playbook, tenant config, and context pack files.
- `runtime.py`: `_mall_context` singleton → `_mall_contexts: dict[str, MallContextLoader]`. `initialize()` now accepts `list[str]` and loads all malls at startup.
- `settings.py`: `BACKEND_MALL_ID` → `BACKEND_MALL_IDS` (comma-separated). `get_mall_id_list()` helper method added.
- All pipeline nodes updated to call `get_mall_context(state.mall_id)` instead of `get_mall_context()`.
- Health endpoint now returns `mall_ids: list[str]` instead of a single `mall_id`.
- Frontend mall selector (`TopBar`) now shows both malls via `MALLS` / `TENANTS` constants.

**To add a third mall:** see `docs/multi-mall.md`.

**Files changed:** `transform_mall_data.py`, `app/config/settings.py`, `app/runtime.py`, `app/main.py`, `app/services/concierge.py`, `app/nodes/*` (8 files), `app/retrieval/retriever.py`, `app/prompts/builder.py`, `app/api/health.py`, `frontend/src/lib/constants.ts`, `.env`, `.env.example`

---

## [v1.0] — 2026-03-13

### Added

**Iteration 1 — Core pipeline (single mall)**

Initial production-grade implementation of the Cenomi Mall Concierge platform.

- **LangGraph pipeline** — 12-node directed graph: `load_session → interpret_turn → smalltalk (fast path) → update_scene_memory → resolve_playbooks → choose_strategy → compose_context → decide_retrieval → fetch_exact_facts → generate_response → update_memory → emit_debug_payload`
- **Mall intelligence layer** — canonical entities (stores, dining, cinema, movies, services, offers), semantic profiles with tag/vibe/audience enrichment, scenario playbooks (date night, family day, gift hunt, etc.)
- **Intent classification** — rule-based fast path (< 3 words: static lookup; longer: regex rules) with GPT-4o LLM fallback. Domains: dining, shopping, entertainment, services, navigation, exploration, mall_info, general.
- **Category-based retrieval** — deterministic category rules returning complete tenant lists for queries like "What cafes are here?"
- **Scene memory** — accumulates companions, occasion, budget, visit plan across turns; resolves terse follow-ups in context.
- **Tenant parameter system** — per-mall tuning weights for recommendation biases; feedback-driven session overrides.
- **Anti-hallucination architecture** — grounded system prompts + post-generation guard validating movies, offers, and discount claims against canonical data.
- **Feedback system** — explicit (thumbs up/down + reasons) and implicit (detected from message kind) feedback; stored locally; applied as session tuning signals.
- **React testing console** — chat UI with full debug inspector (intent, playbook, entities, retrieval, node trace latencies), feedback controls, session export.
- **Data pipeline scripts** — `convert_to_canonical.py` (raw mall JSON → canonical schema) and `synthesize_mall_data.py` (canonical → semantic profiles + playbooks via GPT-4, one-time per mall).
- **Mall:** Al Nakheel Plaza, Buraidah (mall 28) — 94 brands, 1 cinema, 6 movies, 10 services.
