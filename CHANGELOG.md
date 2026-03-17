# Changelog

All notable changes to the Cenomi Mall Concierge platform are recorded here.

Format: `## [vX.Y] — YYYY-MM-DD` with sections Added / Changed / Fixed.

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
