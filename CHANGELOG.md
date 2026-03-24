# Changelog

All notable changes to the Cenomi Mall Concierge platform are recorded here.

Format: `## [vX.Y] — YYYY-MM-DD` with sections Added / Changed / Fixed.

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
