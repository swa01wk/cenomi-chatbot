# Cenomi Chatbot vs AI Findr — Competitive Analysis

> **Last updated:** March 2026 (v1.4.1)  
> **Scope:** Full feature and architecture comparison between the Cenomi mall concierge chatbot (this repo) and [AI Findr](https://aifindr.ai), a commercial conversational AI search platform targeting shopping centers.

---

## Executive Summary

The Cenomi chatbot and AI Findr are solving the same core problem — helping mall visitors discover stores, dining, promotions, and events through natural language — but from very different angles.

**Proximity score: ~60–65% feature parity with AI Findr.**

The Cenomi chatbot's conversational intelligence layer (scene memory, dual-flow planning, hallucination guard, playbook engine, experience layer, scene-augmented vector retrieval) is architecturally more sophisticated than what AI Findr publishes. AI Findr is significantly ahead on delivery channels, visual responses, business intelligence tooling, multilingual support, and operational non-technical controls.

The gap is not in AI quality — it is in the **delivery and operationalization layers** that sit around the AI core.

```
Conversational AI core      ████████████████████░░░  ~87% parity (Cenomi leads on depth; experience layer added)
Retrieval quality           ██████████████░░░░░░░░░  ~65% parity (three-layer hybrid + scene augmentation; no BM25/re-rank)
Omnichannel delivery        █████░░░░░░░░░░░░░░░░░░  ~20% parity (SSE streaming + text API; no WhatsApp/kiosk/voice)
Visual / rich responses     ██░░░░░░░░░░░░░░░░░░░░░  ~10% parity (plain text only)
Analytics & insights        ████░░░░░░░░░░░░░░░░░░░  ~20% parity (JSON files vs dashboard)
Non-technical config        ██░░░░░░░░░░░░░░░░░░░░░  ~10% parity (code-only config)
Multilingual (Arabic)       ░░░░░░░░░░░░░░░░░░░░░░░   0% parity (not implemented)
```

---

## Cenomi Chatbot — Strengths

### 1. Conversational Depth and Scene Memory
The chatbot maintains a rich `SceneMemory` object across every turn of the conversation, tracking:
- Travel companions (solo, couple, family, group)
- Budget constraints
- Occasion (anniversary, birthday, casual, etc.)
- Topic lock (staying focused on a shopping task)
- Active shopping task and visit plan
- Factual follow-up context

No equivalent published capability exists in AI Findr. Its model is query-response, not a sustained multi-turn concierge planning session.

### 2. Dual-Flow LangGraph Pipeline
A 16-node LangGraph pipeline routes every query through either a **concierge flow** (planning, recommendations, experience curation) or a **factual flow** (hours, locations, movies, "do you have X?"). The routing decision is made after intent interpretation, not before. This means the bot adapts its reasoning mode per query rather than applying a one-size-fits-all response strategy.

### 3. Explicit Hallucination Guard
`guardrails/hallucination_guard.py` is wired directly into the `generate_response` node. Every LLM output is validated against the loaded mall canonical data before being returned to the user. Combined with the cross-mall merged canonical index, this provides strong grounding guarantees that AI Findr only claims at a marketing level.

### 4. Sophisticated Feedback Loop
- **Explicit feedback** (thumbs up/down) normalizes and tunes session behavior in real time via `session_tuning_engine`
- **Implicit feedback detection** automatically identifies user corrections and refinements (e.g., "no, I meant…") via pattern matching — no user action required
- **Tenant-level aggregation** rolls up signals across sessions to identify systemic gaps
- **Knowledge gap analysis** (`knowledge_gap_analyzer.py`) surfaces specific content areas the bot failed to answer, informing data updates

### 5. Playbook Engine
Scenario playbooks are matched and ranked against entities and intent context, driving tone-mapped, situation-aware responses. A "romantic dinner" playbook produces different entity ranking and response tone than a "family outing" playbook over the same set of restaurants. AI Findr's "experience builder" is a static configuration UI — it does not dynamically match scenarios to context.

### 6. Cross-Mall Brand Search with Guard
`runtime.search_brand_across_malls` merges canonical data across all loaded mall IDs, enabling cross-property brand queries ("Is Zara in any Cenomi mall?") while the hallucination guard ensures the answer is grounded in actual data.

### 7. Quality Evaluator
An async LLM-as-judge evaluator (`quality_evaluator.py`) runs on every response after it is delivered to the user, writing structured evaluation data to disk. This enables systematic quality tracking without impacting response latency.

### 8. Debug Observability API
Every turn can return a debug payload containing node trace, per-step latency, detected intent, retrieved entities, scene state snapshot, and retrieval decisions. This makes the pipeline fully introspectable for development and QA.

### 9. Full Ownership and Cenomi-Specific Tuning
The entire stack — model, prompts, retrieval rules, playbooks, entity data — is owned and controlled. Cenomi-specific context (Saudi/Gulf mall estate, local brands, local language nuance, regional occasions) can be deeply embedded. A generic SaaS platform like AI Findr cannot match this specificity by default.

### 10. Three-Layer Hybrid Retrieval with Scene-Signal Query Augmentation (v1.4 / v1.4.1)
The retrieval stack is a deterministic-first, semantic-last three-layer pipeline:
1. **Category rules** — deterministic lookup for category-specific queries ("what cafes are here?")
2. **Canonical name search** — exact/partial name matching for brand-specific queries
3. **Vector similarity search** — `VectorStoreService` (Chroma + `text-embedding-3-small` + Redis result cache) for open-ended queries

Critically, vector queries are **scene-augmented**: `build_scene_prefix(scene)` prepends `SceneMemory` signals (occasion, companions, visit_type, budget) to the query before embedding. The same bare query in different visitor contexts produces different vectors, returning entities that match the visitor's actual situation rather than the generic query alone. Fresh sessions (no context accumulated) produce an unchanged query with no regression risk.

This scene-aware embedding closes the contextual relevance gap that pure semantic search cannot address — it is architecturally unique compared to AI Findr's static indexing.

### 11. SSE Token Streaming + Redis Session Persistence (v1.3)
- **SSE streaming endpoint** (`POST /api/chat/stream`) streams LLM responses token-by-token, making the concierge feel immediately responsive on long answers. The React chat UI renders a live blinking cursor during streaming. Session persistence, quality evaluation, and implicit feedback detection run as post-stream side effects without blocking the first token.
- **Redis session persistence** (`RedisSessionStore`) stores sessions durably across backend restarts and multiple workers. Sessions survive process restarts — in-memory degradation under load is eliminated. TTL is configurable via `BACKEND_REDIS_SESSION_TTL`.

### 12. Multi-Mall LRU Cache with Three-Tier Resource Design (v1.4)
The platform manages up to 20 mall contexts efficiently without loading all into RAM simultaneously via an `LRUMallContextRegistry`:
- **Tier 1 (Python RAM)** — LRU cache of active `MallContextLoader` objects (capacity configurable, default 5)
- **Tier 2 (Redis)** — serialized mall context JSON with TTL; restoring from Redis is ~50–100× faster than disk + normalization
- **Tier 3 (Disk)** — cold load from canonical/semantic/playbook JSON files; writes back to Redis on first load

This means the platform can serve queries across an arbitrary number of malls with bounded RAM usage — a genuine production-scale differentiator vs. a system that loads all malls at startup.

### 13. Experience Layer and Advanced Response Composition (v1.4)
The `generate_response` node now has a fully separated **Experience Layer** (`experience_resolver.py`) that selects the appropriate response composition mode before the LLM is called. This produces structurally different outputs for the same domain depending on context:
- `micro_itinerary` — step-by-step visit plan with transitions
- `curated_shortlist` — focused 3–4 picks with reasoning
- `filtered_factual_list` — factual data with context-aware filter notes (e.g. family-safe movie annotations)
- `guided_plan` — concierge narrative weaving entities into a plan
- `structured_overview` — mall-level factual summary

The router also resolves a **response strategy** (`factual_list`, `filtered_factual_list`, `factual_presence`, `guided_plan`, `structured_overview`) from primary intent + secondary filters, so the LLM receives explicit composition guidance rather than having to infer it from raw intent labels. This is a level of structured prompt engineering that AI Findr's black-box pipeline cannot expose or tune.

### 14. LangGraph Turn Checkpointing (v1.3)
When enabled (`BACKEND_ENABLE_CHECKPOINTER=true`), every graph invocation is snapshot-persisted to Redis via `AsyncRedisSaver`. Each turn is keyed by `{session_id}:{timestamp_ns}` for independent replayability. This enables offline debugging, root-cause analysis, and prompt regression testing against historical turns — capabilities that AI Findr's opaque pipeline cannot offer.

---

## Cenomi Chatbot — Weaknesses

### 1. Retrieval Is Semantic But Not Yet Hybrid (Gap Further Reduced)
The three-layer retrieval stack (category rules → canonical name search → vector similarity search) is live with `VectorStoreService` (Chroma + `text-embedding-3-small` + Redis cache). Scene-signal query augmentation (`build_scene_prefix`) is now implemented — the same query from different visitor contexts produces different embedding vectors, closing the contextual relevance gap.

**Remaining gaps vs AI Findr and production-grade retrieval:**
- No BM25 / keyword index — exact rare-word brand queries can still miss vector recall
- No hybrid fusion (BM25 + vector via Reciprocal Rank Fusion)
- No cross-encoder re-ranker over vector candidates
- ~~Scene-signal query augmentation~~ — ✅ **Complete (v1.4.1)**: `build_scene_prefix()` in `retriever.py` prepends occasion, companions, visit_type, and budget as a keyword prefix before embedding; 204/204 test cases pass with zero regressions
- Data sync is manual (`ingest_vectors.py`) — no change-triggered delta ingest

See `docs/COMPETITIVE-ANALYSIS-AI-CORE-RETRIEVAL.md` for a detailed retrieval gap analysis and prioritised roadmap.

### 2. No Omnichannel Delivery
The chatbot is a text JSON/SSE API. AI Findr runs on:
- Website widget (embed via JS snippet)
- WhatsApp (critical for Gulf region — near-universal penetration)
- Digital signage kiosks
- Live interactive maps
- Voice search
- Mobile apps

None of these channels exist in this codebase.

### 3. No Visual or Rich Responses
Responses are plain text (with markdown at best). AI Findr delivers:
- Interactive walking maps ("Where is Zara?" → live map with route)
- Store cards with images and promotions
- Event banners
- Context-aware CTAs embedded as UI elements ("Book Now", "Get Directions")

Higher visual engagement directly correlates with conversion. AI Findr reports +20% conversion rate vs. static directories.

### 4. No Multilingual Support
Arabic is the primary language for a substantial portion of Cenomi's visitor base. There is no language detection, no Arabic prompt handling, and no regional tone adaptation anywhere in the codebase. AI Findr supports auto-detect multilingual with regional tone out of the box.

### 5. Analytics is Flat JSON Files, Not a Dashboard
Feedback events, implicit signals, evaluation results, and knowledge gaps are stored as individual JSON files under `backend/data/`. There is no aggregation UI, no trend visualization, no tenant performance dashboard.

The `GET /api/feedback/tenant-summary` and `GET /api/feedback/playbook-performance` endpoints exist and serve raw JSON, but no frontend consumes them. Business teams cannot act on this data without engineering support.

AI Findr's insights dashboard — top queries, emerging trends, content gaps, per-channel performance — is a core differentiator for its mall operator customers.

### 6. No Non-Technical Configuration Tooling
Any behavioral change (tone, business rules, playbook thresholds, prompt tuning) requires Python code edits and a deployment. AI Findr's "experience builder" lets mall marketing teams adjust tone, branding, and business rules without developer involvement. The "playground" lets them test responses before going live.

### 7. No Lead Capture
When AI Findr cannot fully answer a query, it collects the visitor's contact information so a sales team can follow up. The Cenomi chatbot returns its best-effort answer or acknowledges it does not know — the visitor intent is lost.

### 8. Production Observability Partially Addressed
LangSmith tracing settings (`enable_tracing`, `langsmith_api_key`, `langsmith_project`) exist in `settings.py` but are **never wired into any code path**. **Partial progress (v1.3):** LangGraph turn checkpointing is now available (`BACKEND_ENABLE_CHECKPOINTER=true`) — every graph invocation is snapshot-persisted to Redis via `AsyncRedisSaver` and can be replayed or inspected offline. This is pipeline-level turn replay (debugging, regression testing), not live production observability. LangSmith integration remains unimplemented — real-time tracing, latency breakdowns per node, and live error alerting are still absent.

### 9. Feedback Storage Does Not Scale
Storing individual feedback events as JSON files under `backend/data/feedback/` works for development but will degrade at Cenomi's visitor scale. There is no database, no write batching, and no analytics pipeline downstream. **Note (v1.3):** Session state itself now persists durably in Redis, so in-memory signal aggregation is no longer vulnerable to process restarts. However, raw feedback event files remain flat JSON — the storage model for feedback signals is unchanged.

### 10. Compliance and Data Residency Gaps
No GDPR/CCPA compliance documentation. No RBAC (role-based access control). No data residency options documented — relevant given Saudi data localisation requirements. AI Findr's Enterprise tier offers GDPR/CCPA compliance, RBAC, explainability logging, and flexible hosting (cloud, private cloud, on-premises).

### 11. README is Outdated
The README still describes the old 12-node single-flow graph without `route_flow`, the factual branch, `rank_and_dedupe`, or `compose_fact_response_context`. The live graph in `backend/app/graph/builder.py` is a 16-node dual-flow architecture. The API route table in the README also does not match the actual routes.

---

## AI Findr — Strengths

### 1. Omnichannel from Day One
Website widget, WhatsApp, digital signage, live interactive maps, voice search, and mobile apps — all connected to the same knowledge base and insights pipeline. For a shopping center, meeting visitors where they already are (WhatsApp, kiosk in the car park) is a significant engagement multiplier.

### 2. Visual Dynamic Answers
Results are delivered as the format best suited to the query: interactive walking maps for navigation, cards with images and pricing for store/product discovery, banners for events and promotions, CTAs for bookings. This is not just cosmetic — it directly drives conversion.

### 3. Real-Time Insights Dashboard
Every search query is captured as an intent signal. The dashboard surfaces:
- Top queries by volume and trend
- Emerging interests before they peak
- Content gaps (queries with low-quality answers)
- Per-channel performance metrics

Mall operators can make merchandising, tenancy, and event programming decisions based on what visitors are actually searching for — without engineering involvement.

### 4. No-Code Experience Builder and Playground
Non-technical staff (marketing, operations) can configure tone of voice, branding rules, and business rules. The playground lets them test the full visitor experience before any change goes live.

### 5. Multilingual with Regional Tone
Auto-detects visitor language and responds accordingly, with regional tone calibration. Supports the Arabic-speaking majority of Gulf mall visitors out of the box.

### 6. Lead Capture
When the system cannot fully answer, it collects visitor contact information for sales follow-up. Unanswered queries become warm leads rather than dead ends.

### 7. Tenant Monetization Layer
Shopping centers can offer tenants priority placement (promoted listings) with tracked impressions, clicks, and conversions. This creates a direct revenue stream from the AI layer, not just an operational cost.

### 8. Search Everywhere Optimization
AI Findr monitors how the mall and its tenants appear in Google AI Overviews, ChatGPT, Perplexity, and Claude — and structures content to maximize visibility across all AI search surfaces. This is unique and highly relevant as AI-native search becomes the primary discovery channel.

### 9. Fast Deployment and Flexible Terms
Working prototype in under one week. Widget/API integration without IT involvement. Free pilot tier. Flexible contract terms.

### 10. Proven Results at Scale
Jockey Plaza (Peru's largest mall) and Multiplaza Bogotá: 87.3% first-contact query resolution, +20% conversion rate, +39 NPS points, +800% monthly user growth.

### 11. GDPR/CCPA Compliance, RBAC, Explainability
Every interaction is logged and auditable. Data never used to train third parties. Flexible hosting. Role-based access for dashboard and content editing.

---

## AI Findr — Weaknesses

### 1. Generic, Not Cenomi-Tuned
AI Findr is a horizontal platform serving malls worldwide. It has no built-in understanding of Cenomi's specific estate, brand mix, tenant relationships, or Gulf cultural context (Ramadan promotions, Saudi National Day events, prayer time considerations). The Cenomi chatbot can embed all of this natively. Achieving equivalent depth on AI Findr would require extensive custom data ingestion and ongoing maintenance with the vendor.

### 2. No Scene Memory or Concierge Planning
AI Findr operates on a query-response model. It answers individual questions well but does not maintain a conversational planning context across turns. It cannot:
- Track that the user is planning a family outing and surface only family-appropriate options
- Lock onto a shopping task ("I need to find a birthday outfit") and curate results across multiple follow-up questions
- Adapt recommendations based on earlier-stated budget constraints

This is the Cenomi chatbot's strongest differentiator.

### 3. Black Box Pipeline
The retrieval logic, hallucination controls, ranking algorithms, and response generation are entirely opaque. Any errors or quality issues require a vendor support ticket. There is no ability to inspect why a specific response was generated or to fix a specific failure without vendor involvement.

### 4. Subscription Cost at Scale
AI Findr pricing scales with query volume. At the scale of a major mall estate like Cenomi — potentially millions of monthly interactions across properties and channels — the ongoing cost could be substantial and unpredictable. The Cenomi chatbot's only variable cost is the OpenAI API.

### 5. Data Leaves Your Infrastructure (Standard Tiers)
On standard tiers, visitor query data and intent signals are processed on AI Findr's infrastructure. For a Saudi entity, this raises data residency and regulatory questions. The on-premises / private cloud option is Enterprise-tier only.

### 6. No Real-Time Per-Session Behavioral Adaptation
AI Findr mentions "self-improving via pattern recognition and feedback" but this operates at the model/platform level over time, not in real time within an individual session. The Cenomi chatbot's `session_tuning_engine` applies implicit feedback signals mid-session to adjust behavior for that specific visitor immediately.

---

## Side-by-Side Comparison

| Dimension | Cenomi Chatbot | AI Findr |
|---|---|---|
| **Conversational depth / scene memory** | Full scene memory, multi-turn planning | Basic query-response |
| **Intent routing** | Dual-flow (concierge vs factual) | Single flow |
| **Hallucination control** | Explicit guard, wired into pipeline | Claimed, details opaque |
| **Feedback loop** | Explicit + implicit, real-time session tuning | Platform-level self-improvement |
| **Knowledge gap analysis** | Built-in service | Not published |
| **Playbook / scenario engine** | Scenario matching with tone mapping | Static experience builder |
| **Cross-property search** | Cross-mall canonical search | Per-mall configuration |
| **Retrieval quality** | Three-layer hybrid (category rules → canonical name search → scene-augmented vector search) | Semantic indexing |
| **Response delivery** | SSE token streaming + blocking JSON API | Web widget, WhatsApp, kiosk, map, voice, app |
| **Omnichannel reach** | Web frontend only | Web, WhatsApp, kiosk, map, voice, app |
| **Visual responses** | Plain text | Maps, cards, images, banners |
| **Multilingual / Arabic** | Not implemented | Auto-detect, regional tone |
| **Analytics dashboard** | Raw JSON files | Real-time insights dashboard |
| **Tenant monetization** | Not implemented | Promoted listings with tracking |
| **Lead capture** | Not implemented | Built-in |
| **Non-technical config** | Code only | No-code experience builder |
| **Testing / playground** | Developer debug UI | Non-technical playground |
| **Production observability** | Turn checkpointing (opt-in); LangSmith not wired | Dashboard + full logging |
| **Search engine optimization** | Not applicable | Google AI, ChatGPT, Perplexity monitoring |
| **Compliance (GDPR/CCPA)** | Not documented | Certified |
| **Data residency control** | Self-hosted (full control) | Enterprise tier only |
| **RBAC** | Not implemented | Built-in |
| **Vendor independence** | Full ownership | Subscription dependency |
| **Domain specificity** | Deep Cenomi/Gulf tuning possible | Generic, all-mall |
| **Deployment speed** | Requires setup and data loading | Working prototype < 1 week |
| **Proven production results** | Internal testing only | 87.3% resolution, +20% conversion |

---

## Prioritized Gap Roadmap

The following gaps are ranked by business impact for the Cenomi deployment context.

### Priority 1 — Semantic / Vector Retrieval ✅ Substantially Complete
**Status:** The three-layer retrieval stack (category rules → canonical name search → scene-augmented vector search) is live. `VectorStoreService` (Chroma + `text-embedding-3-small` + Redis cache) is wired into `MallRetriever.search_semantic()`. Scene-signal query augmentation (`build_scene_prefix`) was completed in v1.4.1 — contextually different sessions now produce different embedding vectors.

**Completed (v1.4 / v1.4.1):**
- ✅ Vector similarity search via Chroma with Redis result cache
- ✅ Scene-signal query augmentation — `build_scene_prefix()` prepends `SceneMemory` context (occasion, companions, visit_type, budget) before embedding; 204/204 test cases pass with zero regressions

**Remaining work (see `docs/COMPETITIVE-ANALYSIS-AI-CORE-RETRIEVAL.md` for detail):**
- BM25 entity name index + RRF fusion with vector results — closes the hybrid gap vs Yext
- Cross-encoder re-ranker on the semantic path — improves ranking quality for ambiguous queries
- Delta-ingest trigger on canonical JSON change — eliminates manual sync requirement

### Priority 2 — Arabic Multilingual Support
**Why second:** A substantial portion of Cenomi's visitor base uses Arabic as their primary language. Without this, the chatbot is inaccessible to a major segment, regardless of how good the English pipeline is.

**What to build:**
- Language detection in `interpret_turn` (or a pre-processing step before it)
- Arabic system prompt variants in the prompts layer
- Test with Arabic queries across all node paths
- Consider regional dialect handling for Saudi vs Gulf variants

### Priority 3 — Analytics Dashboard
**Why third:** Business teams need to act on visitor intent data. The feedback and evaluation infrastructure already exists in the backend — it just needs a frontend.

**What to build:**
- Replace flat JSON file storage with a lightweight database (SQLite for single-node, PostgreSQL for production)
- Build a read API over the aggregated metrics (top queries, trend data, knowledge gaps, evaluator scores)
- Add a simple analytics frontend (could be a standalone React page or a Grafana/Metabase integration)
- Connect the existing `tenant-summary` and `playbook-performance` endpoints to a UI

### Priority 4 — WhatsApp Integration
**Why fourth:** WhatsApp penetration in the Gulf is near-universal and is the most impactful single channel addition before full omnichannel. A single WhatsApp Business API integration would significantly expand reach.

**What to build:**
- WhatsApp Business API webhook endpoint (wraps the existing `/api/chat` endpoint)
- Session mapping from WhatsApp sender ID to `session_id`
- Rich message formatting for WhatsApp (list messages, quick reply buttons)

### Priority 5 — Visual / Rich Responses
**Why fifth:** Plain text responses limit conversion. Adding structured response schemas (store cards, map links, CTA buttons) would align the delivery layer with the conversational quality the backend already produces.

**What to build:**
- Define a structured response schema (`response_cards`, `map_link`, `cta_buttons`) alongside the current `response_text`
- Populate these in `generate_response` based on response type
- Update the frontend to render cards and map links
- For kiosk/digital signage: a fullscreen display mode consuming the same structured response

---

## Summary

The Cenomi chatbot has a strong and continuously improving AI core that, in terms of conversational planning depth, outclasses what AI Findr publishes. Since the initial comparison was written, the platform has shipped:

- **v1.3** — Redis session persistence, SSE streaming, LangGraph turn checkpointing, async quality evaluator
- **v1.4** — Three-layer semantic retrieval (Chroma + Redis cache), multi-mall LRU cache with three-tier resource design, experience layer response composition
- **v1.4.1** — Scene-signal query augmentation (SceneMemory context injected at embed time), closing the last major gap in Priority 1

The remaining investment needed to reach full feature parity is primarily in the **delivery layer** (channels, visual format) and **operational layer** (analytics, configuration tooling, compliance, Arabic) — not in the AI reasoning or retrieval layers.

Priority 1 (semantic retrieval) is now substantially complete. Priority 2 (Arabic) and the operational priorities (analytics dashboard, WhatsApp, visual responses) remain the primary gaps separating this platform from a fully deployed, independently operable product.
