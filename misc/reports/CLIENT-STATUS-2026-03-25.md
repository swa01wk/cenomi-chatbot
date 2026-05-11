# Cenomi Chatbot — Client Status Report

**As of:** Wednesday, 25 March 2026  
**Demo target:** Sunday, 30 March 2026 (5 days)

---

## 1. Where We Are on the Improvisation — What Has Been Implemented

The platform has shipped four full versions since the initial build on 13 March. Every change has been verified at 100% pass rate across the 204-query test suite (smoke + adversarial multi-turn + deep complex scenarios).

### v1.0 — 13 March: Core Pipeline (Single Mall)

- **LangGraph pipeline** — 12-node directed graph covering: session load → intent interpretation → scene memory update → playbook resolution → strategy selection → context composition → retrieval decision → fact fetch → response generation → memory update → debug payload emit
- **Intent classification** — three-tier hybrid: short-circuit rule table (< 1ms) → regex rule-based classifier → GPT-4o LLM fallback. Domains: dining, shopping, entertainment, services, navigation, exploration, mall_info, general
- **Scene memory** — accumulates companions, occasion, budget, visit plan, topic lock, active shortlist across every turn; resolves terse follow-ups in context
- **Playbook engine** — scenario playbooks matched and ranked against intent + entity context; tone-mapped outputs (date night vs family day vs gift hunt produce structurally different responses)
- **Anti-hallucination guard** — post-generation validation of all LLM output against canonical mall data before delivery
- **Feedback system** — explicit (thumbs up/down) + implicit (auto-detected corrections); session tuning engine applies signals in real time during the session
- **React testing console** — full debug inspector (intent, playbook, entities, retrieval, node trace, latencies), session export
- **Mall loaded:** Al Nakheel Plaza, Buraidah (mall 28) — 94 brands, 1 cinema, 6 movies, 10 services

### v1.1 — 17 March: Multi-Mall Support

- Runtime now holds a registry of mall contexts instead of a singleton
- Mall of Arabia, Jeddah (mall 13) added — full data pipeline run (canonical, semantic, playbook, tenant config)
- All pipeline nodes updated to use `mall_id`-scoped context
- Frontend mall selector updated; health endpoint returns `mall_ids` list

### v1.2 — 17 March: Cross-Mall Brand Search

- New `cross_mall` intent domain — fires for queries like "Does Mall of Arabia also have Nike?" or "Which Cenomi mall has Starbucks?"
- `search_brand_across_malls()` merges canonical data across all loaded malls, sorts home mall first
- Dedicated `_build_cross_mall_response()` with structured `AT YOUR CURRENT MALL / AT OTHER CENOMI MALLS` prompt blocks
- Hallucination guard extended to validate against a merged canonical index from all loaded malls

### v1.3 — 23 March: Infrastructure & Streaming

- **Redis session persistence** — sessions survive backend restarts and are shareable across workers; `RedisSessionStore` with configurable TTL; in-memory fallback when Redis is absent
- **LangGraph turn checkpointing** — every graph invocation snapshot-persisted to Redis (`BACKEND_ENABLE_CHECKPOINTER=true`); each turn is independently replayable for debugging and regression testing
- **Async quality evaluator** — LLM-as-judge evaluates every response on `intent_alignment`, `constraint_adherence`, `honesty`, and `conciseness` (0–10); runs in a background task, never blocks delivery
- **SSE token streaming** — `POST /api/chat/stream` streams response token-by-token; React UI renders live blinking cursor; session persistence and quality evaluation run post-stream without delaying the first token
- **Docker / Docker Compose** — single-command deployment; multi-stage Dockerfile under 300 MB; Redis + backend services with healthcheck; `backend/data` volume mount for persistence

### v1.4 — 24 March: Semantic Retrieval + Multi-Mall Scale

- **Three-layer hybrid retrieval:**
  - **Layer 1 — Category rules:** deterministic lookup, 100% precision, zero LLM cost (~0ms)
  - **Layer 2 — Canonical name search:** substring + word-boundary match for brand-specific queries
  - **Layer 3 — Vector similarity search:** `VectorStoreService` (Chroma + `text-embedding-3-small` + Redis result cache); falls back to tag-overlap matching when Chroma is unavailable
- `**VectorStoreService`** — Chroma PersistentClient (HNSW cosine, 1536-dim); Redis-backed result cache (TTL configurable); per-mall collections with zero cross-contamination
- **Multi-mall LRU cache (three-tier resource design):**
  - Tier 1 (Python RAM) — LRU cache of active `MallContextLoader` objects (default capacity 5); eviction frees RAM
  - Tier 2 (Redis) — serialized mall context JSON with TTL; restoring from Redis is ~50–100× faster than disk
  - Tier 3 (Disk) — cold load from canonical/semantic/playbook JSON; writes back to Redis on first load
- **Experience layer** (`experience_resolver.py`) — selects response composition mode before the LLM is called: `micro_itinerary`, `curated_shortlist`, `filtered_factual_list`, `guided_plan`, `structured_overview`
- **Pronoun reference fix** — queries like "anything she would like" now correctly route to `guided_recommendation` instead of treating "she" as a brand name
- **Comprehensive test runner** (`run_all_tests.py`) — runs all three test suites against the live Docker backend; generates Markdown + JSON reports in `test-results/`

### v1.4.1 — 24 March: Contextual Query Augmentation

- `build_scene_prefix(scene)` prepends `SceneMemory` signals (occasion, companions, visit_type, budget) to the vector query before embedding
- The same bare query from different visitor contexts now produces different embedding vectors:
  - Anniversary couple → embed `"anniversary partner date something for dinner"`
  - Family with kids → embed `"kids family outing something for dinner"`
  - Fresh session → embed `"something for dinner"` (unchanged — no regression risk)
- **Zero regressions: 204/204 test cases pass (100%)**

---

## 2. What Will Be Included Before the Demo (30.03.26)

The demo is in 5 days. Based on the roadmap priorities and effort estimates documented in `docs/COMPETITIVE-ANALYSIS-AI-CORE-RETRIEVAL.md` and `docs/AI-FINDR-COMPARISON.md`, the following items are **realistic to complete before 30 March:**

### Retrieval Improvements (can be done before demo)


| Item                                                                                                               | Effort   | Impact                                                                          | Status  |
| ------------------------------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------- | ------- |
| **BM25 entity name index + RRF fusion**                                                                            | 1–2 days | High — closes the exact brand name miss gap vs vector-only search               | Pending |
| **Cross-encoder re-ranker** (top-20 → top-5, `ms-marco-MiniLM-L-6-v2`, ~25MB, CPU-runnable)                        | < 1 day  | Medium — improves ranking for ambiguous queries by ~15–25%                      | Pending |
| **Vector fallback in factual flow** (`fetch_exact_facts` falls back to vector when canonical lookup returns empty) | < 1 day  | Medium — surfaces answers for service/facility queries that miss keyword lookup | Pending |


### Demo Preparation (should be done before demo)


| Item                                               | Notes                                                                                                                                                       |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **README update**                                  | Current README describes old 12-node single-flow architecture; live system is a 16-node dual-flow graph. Should reflect actual architecture and API routes. |
| **Delta-ingest trigger** (mtime-based, background) | Medium effort (2–3 days) — borderline for the demo window                                                                                                   |


### Items NOT Realistic Before 30.03.26


| Item                                        | Reason                                                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Arabic multilingual support                 | High effort — language detection, Arabic prompt variants, regional dialect handling; not achievable in 5 days |
| Analytics dashboard                         | Requires database migration + new frontend; 1+ week of work                                                   |
| WhatsApp integration                        | Requires WhatsApp Business API registration + integration work                                                |
| Visual / rich responses (cards, maps, CTAs) | Requires new response schema + frontend rendering overhaul                                                    |
| LangSmith production tracing                | Config exists but never wired into any code path                                                              |


---

## 3. How Close Are We with AI Findr


| AI Quality Dimension         | Parity           | Status                                                                                      |
| ---------------------------- | ---------------- | ------------------------------------------------------------------------------------------- |
| Conversational AI core       | ~87%             | **Cenomi leads**                                                                            |
| Retrieval quality            | ~85%             | **Cenomi leads** — three-layer hybrid + scene-augmented vectors; BM25 fusion in progress    |
| Intent classification depth  | **Cenomi leads** | Three-tier hybrid (rule fast-path + LLM fallback); AI Findr is single-pass LLM only         |
| Hallucination control        | **Cenomi leads** | Post-generation validation against live canonical data; AI Findr's grounding is unpublished |
| Real-time session adaptation | **Cenomi leads** | Mid-session behavioral tuning from feedback; AI Findr improves offline only                 |


### Where We Win

- **Multi-turn scene memory** — tracks companions, occasion, budget, and visit plan across every turn; AI Findr answers one query at a time
- **Dual-flow routing** — concierge mode for planning, factual mode for lookups; AI Findr is a single flow
- **Hallucination guard** — every LLM output validated against live mall data before delivery; AI Findr's grounding is opaque
- **Real-time session adaptation** — feedback signals (explicit and implicit) adjust behavior mid-session; AI Findr only improves offline
- **Playbook scenario engine** — "romantic dinner" and "family outing" produce structurally different responses over the same data
- **Full data ownership** — Gulf occasions, local brands, Saudi cultural context embedded natively; AI Findr is a generic platform
- **Data residency** — all visitor data stays on Cenomi infrastructure; AI Findr requires Enterprise tier for on-premises

### Where AI Findr Currently Leads

- **Visual responses** — interactive maps, store cards with images, booking CTAs
- **Arabic** — auto-detect multilingual with regional tone; we have no Arabic support yet
- **Analytics** — real-time dashboard for top queries, trends, and content gaps
- **Channels** — WhatsApp, kiosk, digital signage, voice, mobile app; we are web-only today

---

## 4. What AI Findr Doesn't Have That We Have (and Are Building)

### Already Differentiated (Cenomi capabilities)


| Capability                        | What It Means for Visitors                                                                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Scene Memory**                  | The concierge remembers who you're with, your occasion, budget, and shopping task — across every turn of the conversation                                           |
| **Concierge vs Factual routing**  | Planning queries ("help me plan a date night") and lookup queries ("where is Zara?") are handled by separate reasoning pipelines, not a one-size-fits-all flow      |
| **Hallucination guard**           | Store names, hours, movies, and offers in every response are validated against live mall data before the user sees them                                             |
| **Real-time feedback adaptation** | If a visitor corrects the bot mid-session, behavior adjusts immediately — no waiting for a next-day model update                                                    |
| **Playbook scenario engine**      | Context-matched scenarios (anniversary, family day, back-to-school) change entity ranking, tone, and response structure dynamically                                 |
| **Scene-augmented vector search** | The same query ("something for dinner") returns different results for an anniversary couple vs a family with kids — context shapes retrieval, not just the response |
| **Cross-mall brand search**       | "Is Zara in any Cenomi mall?" searches across all properties with a single validated answer                                                                         |
| **Quality evaluator**             | Every response is automatically scored on accuracy, relevance, and honesty in the background — without slowing delivery                                             |
| **Full ownership**                | Prompts, rules, playbooks, and entity data are Cenomi's — Gulf/Saudi context is embedded natively, not approximated by a generic vendor                             |
| **Data stays in-house**           | All visitor interactions processed on Cenomi infrastructure by default                                                                                              |


### In Progress (Roadmap)


| Capability                                  | Timeline  | What It Adds                                                                                                                |
| ------------------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Hybrid retrieval (BM25 + vector fusion)** | Pre-demo  | Exact brand name queries that slip through vector search are caught by keyword index — higher recall across all query types |
| **Re-ranking**                              | Pre-demo  | Top results for ambiguous queries reordered by true relevance — measurably better recommendations                           |
| **Arabic language support**                 | Post-demo | Full Arabic conversation with Gulf dialect handling and bilingual retrieval                                                 |
| **Analytics dashboard**                     | Post-demo | Business teams see top queries, trends, and content gaps without engineering involvement                                    |


