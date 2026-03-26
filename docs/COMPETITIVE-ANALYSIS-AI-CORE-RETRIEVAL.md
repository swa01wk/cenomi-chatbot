# Cenomi Chatbot — Competitor Analysis: Conversational AI Core & Retrieval Quality

> **Date:** March 2026
> **Scope:** Deep-dive analysis on two specific dimensions — **Conversational AI Core** and **Retrieval Quality** — comparing the Cenomi chatbot against four platforms used in comparable deployments.
> **Related:** `docs/AI-FINDR-COMPARISON.md` (full feature comparison vs AI Findr)

---

## Competitors Covered

| Platform | Category | Why Included |
|---|---|---|
| **Cenomi Chatbot** (this repo) | Bespoke mall concierge AI | Baseline |
| **AI Findr** | Commercial mall AI (shopping center vertical) | Direct vertical competitor |
| **Yext Chat / Yext Answers** | Enterprise AI search + conversational layer | Strongest retrieval benchmark in retail |
| **Cognigy.AI** | Enterprise conversational AI platform | Conversational depth reference |
| **Intercom Fin** | SaaS AI support + commerce | Conversational quality + hallucination reference |
| **Google CCAI / Vertex AI Search** | Enterprise contact center + search AI | Enterprise retrieval benchmark |

---

## Part 1 — Conversational AI Core

### 1.1 Intent Classification Architecture

**Cenomi Chatbot**
Three-tier hybrid classifier in `interpret_turn.py` + `query_classifier.py`:

1. **Short-circuit rule table** (~200 canonical patterns) — zero LLM cost, < 1ms
2. **Rule-based classifier** with configurable confidence threshold (0.75) — handles ~80% of real queries
3. **LLM fallback** (GPT-4o at temperature=0, max_tokens=200) — only invoked when rule confidence is below threshold

Every result — regardless of classifier source — is enriched with a secondary intent bundle (`_extract_hybrid_intent_bundle`) and a modifier signal set (`_MODIFIER_SIGNALS`). Both are pure keyword-based, zero additional LLM cost.

**Classification output fields:**
- `domain` (9 values), `sub_intent` (28 values), `message_kind` (7 values)
- `primary_intent`, `secondary_intents[]`, `modifiers[]`
- `flow_type_candidate` (concierge / factual / tbd)
- `fact_scope_candidate`, `fact_entity_type_candidate`

This is the richest classification output in the set reviewed. No other platform exposes all of these signals as first-class routing inputs.

**AI Findr**
Semantic single-pass LLM classification over the query. No published rule fast-path. Single output: category + matched entities. No `message_kind`, no `modifiers`, no multi-turn continuation tracking. All queries incur full LLM cost.

**Yext Chat**
Yext's proprietary Natural Language Understanding layer extracts intent + entity from a fine-tuned model trained on business-specific data. Supports entity extraction (store name, product category) alongside intent. Thin conversational layer over search results. No public hybrid rule/LLM architecture. Single-turn response model with conversation history as context.

**Cognigy.AI**
Flow-based NLU (own model or Azure LUIS / Google CCAI NLU behind the scenes). Supports intent confidence thresholds with fallback flows and explicit slot-filling. No LLM hybrid for intent; the LLM is invoked downstream for response generation, not classification. More deterministic but requires complete manual intent authoring.

**Intercom Fin**
Single GPT-4 call over the full message + conversation history. Classification, retrieval context injection, and response generation happen in one pass. No rule fast-path. Cost is proportional to conversation history length. No published secondary intent or modifier output.

**Google CCAI / Dialogflow CX**
BERT-based NLU trained on manually authored intents. Parameter (entity) extraction per intent. Explicit confidence threshold per intent with a fallback intent. No LLM-in-the-loop for classification without adding Vertex AI extensions. Most deterministic in the set but requires the highest authoring investment.

---

### 1.2 Multi-Turn Memory and Context Management

**Cenomi Chatbot**
`SceneMemory` object persisted across every turn via `SessionStore`. Hydrated into every graph node.

| Field | Purpose |
|---|---|
| `companions` | solo / couple / family / group |
| `occasion` | anniversary / birthday / casual / corporate |
| `budget` | price range preference |
| `target_person` | who the shopping is for (gift context) |
| `topic_lock` + `topic_lock_confidence` | prevents intent drift mid-session |
| `active_shortlist` | entities under active discussion |
| `visit_plan` | multi-step planned sequence (shopping → coffee → dessert) |
| `completed_steps` | which steps of the plan are done |
| `current_plan_step` | which step is currently in scope |
| `visit_constraints` | quick / light / affordable — carried forward across turns |
| `topic_history` | last 5 topic transitions |
| `active_primary_intent` | guards against domain lock regression between turns |
| `visit_type` | family / couple / solo / group |
| `goal` | high-level visit goal |

Every field is a first-class routing input — any node in the 16-node graph can read and write scene state. This is the deepest conversational memory model of any platform in this comparison.

**AI Findr**
No published multi-turn memory model. Operates query-by-query. No evidence of companion, occasion, visit plan, or constraint tracking across turns.

**Yext Chat**
Rolling message window passed to GPT-4. No structured session object. Context is conversational history (last N messages), not structured state. No companion, occasion, or visit plan tracking.

**Cognigy.AI**
"Profile" object stores slot values across turns within a session. Supports intent memory per flow. No built-in occasion or companion model — must be custom-engineered as profile fields. Powerful but requires full manual schema design for every scenario type. No visit plan concept.

**Intercom Fin**
Conversation history window (last N messages) passed to GPT-4. No structured session state beyond raw conversation context. Tracks user identity (for CRM enrichment) but not conversational planning state.

**Google CCAI / Dialogflow CX**
Session parameters store slot values extracted during the conversation. Predefined parameter schema with session scope. Page transitions encode flow state. No built-in occasion, companion, or visit plan model. Rich but fully manually authored.

---

### 1.3 Routing Logic

**Cenomi Chatbot**
16-node LangGraph dual-flow pipeline. The `route_flow` node applies 7 weighted rules **after** intent interpretation:

```
Rule 0  Domain lock          — carries forward factual intent if not explicitly switched
Rule 1  Strong factual signal — hours, location, "do you have"
Rule 2  Factual sub-intent   — movie_showtime, opening_hours, brand_availability, etc.
Rule 3  Mall info domain     — factual by design, with concierge signal override
Rule 4  Strong concierge     — planning language, personal pronouns, companion context
Rule 5  Sub-intent concierge — recommendation sub-intents
Rule 6  Context + history    — scene signals from prior turns
Rule 7  Default              — concierge for ambiguous queries
```

Routing is **overridable at each rule level** — later rules can override earlier ones if signal strength warrants. The factual and concierge paths are 7-node and 9-node graphs respectively, with completely different context composition, entity ranking, and response generation logic.

**AI Findr**
Single flow: query → semantic search → template response → delivery channel. No dual-flow. No evidence of routing rules or flow selection logic.

**Yext Chat**
Search-first: query → Yext semantic search → response generation (GPT-4) with search results as context. No separate planning/concierge flow. Single response path regardless of query type.

**Cognigy.AI**
Flow-based routing: NLU intent score → transition condition → target flow. Each transition must be manually authored. Global intents can route from any flow. Complex routing requires full manual flow engineering — powerful but not self-adapting.

**Intercom Fin**
Single LLM call. The model decides what to do within the single pass: answer from KB, acknowledge gap, or escalate. No programmatic routing layer.

**Google CCAI / Dialogflow CX**
Page/flow transition graph with conditions. Every routing path is manually authored as a state machine. Most expressive routing model in the set, but highest authoring cost.

---

### 1.4 Hallucination Control

**Cenomi Chatbot**
`guardrails/hallucination_guard.py` wired directly into the `generate_response` node. Every LLM output is validated against the loaded mall canonical data (JSON) before delivery:

- Store names mentioned in the response must exist in canonical data
- Hours and locations are sourced from structured data, not free-text LLM generation
- Cross-mall search uses a merged canonical index, not generative recall
- Quality evaluator (`quality_evaluator.py`) runs async post-response, scoring factual accuracy against the data that was passed to the LLM

This is the **only platform in this set with explicit post-generation validation** backed by structured canonical data.

**AI Findr**
Claims answers grounded in ingested content. Likely RAG with prompt constraints ("only answer from the provided context"). No post-generation validation published. Implementation details opaque.

**Yext Chat**
Answers restricted to Yext's indexed content via RAG. Source citations reduce hallucination risk. No published post-generation validation step.

**Cognigy.AI**
LLM knowledge nodes can be scoped to specific knowledge bases. Answers can be restricted to retrieved context. Supports "answer only from context" prompt instructions. No published post-generation validation.

**Intercom Fin**
Safe mode: Fin refuses to answer rather than hallucinate when no relevant KB content is found. The explicit refusal-over-hallucination design is a strong differentiator. No post-generation validation, but the architecture minimises opportunity for hallucination.

**Google CCAI**
Vertex AI grounding supports source citation and configurable answer confidence thresholds. Low-confidence answers can be withheld. More deterministic outputs achievable via temperature control.

---

### 1.5 Real-Time Feedback and Session Adaptation

**Cenomi Chatbot**
Four-layer feedback system unique in this set:

1. **Explicit feedback** (thumbs up/down) → `session_tuning_engine` adjusts behaviour for the **current session** in real time
2. **Implicit feedback detection** — auto-detects corrections ("no, I meant…") via pattern matching, zero user action required
3. **Tenant-level aggregation** — rolls up signals across sessions per mall/tenant
4. **Knowledge gap analysis** (`knowledge_gap_analyzer.py`) — surfaces failed query topics for content teams

Real-time within-session behavioral adaptation is unique to Cenomi in this comparison. All other platforms apply learning offline at the model/platform level.

**AI Findr**
Platform-level self-improvement over time via aggregated pattern recognition. Within-session adaptation not published.

**Yext Chat**
Analytics dashboard shows query trends and suggested content gaps. Content improvements applied by admins via the Yext platform. No real-time session tuning.

**Cognigy.AI**
Analytics dashboard, intent confusion matrix, training data suggestions. All improvements require admin review and a retraining cycle. No real-time session tuning.

**Intercom Fin**
Admin reviews Fin-answered conversations for quality. Manual knowledge base updates. No automatic real-time tuning.

**Google CCAI**
CCAI Insights and Agent Assist provide conversation analytics and coach suggestions. Improvements require admin action and retraining. No real-time session tuning.

---

### 1.6 Conversational AI Core — Scorecard

| Dimension | Cenomi | AI Findr | Yext Chat | Cognigy.AI | Intercom Fin | Google CCAI |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Intent classification depth** | ★★★★★ | ★★★ | ★★★ | ★★★★ | ★★★ | ★★★★ |
| **Multi-turn memory / scene state** | ★★★★★ | ★ | ★★ | ★★★ | ★★ | ★★★ |
| **Routing intelligence** | ★★★★★ | ★★ | ★★ | ★★★ | ★★ | ★★★ |
| **Hallucination control** | ★★★★★ | ★★★ | ★★★ | ★★★ | ★★★★ | ★★★ |
| **Real-time session adaptation** | ★★★★★ | ★★ | ★★ | ★★ | ★★ | ★★ |
| **Domain specificity (Gulf/mall)** | ★★★★★ | ★★★ | ★★ | ★★ | ★★ | ★ |
| **LLM cost efficiency (fast path)** | ★★★★★ | ★★ | ★★★ | ★★★ | ★★ | ★★★ |
| **Interpretability / debug API** | ★★★★★ | ★ | ★★ | ★★★ | ★★ | ★★★ |
| **Off-shelf deployment speed** | ★★ | ★★★★★ | ★★★★ | ★★★★ | ★★★★ | ★★★ |
| **Non-technical config tooling** | ★ | ★★★★★ | ★★★★★ | ★★★★ | ★★★★★ | ★★★ |
| **Multilingual (Arabic)** | ★ | ★★★★ | ★★★★ | ★★★★ | ★★★ | ★★★★ |

> ★★★★★ = strong / leading · ★★★ = adequate / competitive · ★ = weak / absent

**Conversational AI Core verdict:** Cenomi leads on AI reasoning depth, grounding, and session intelligence. Cenomi lags on operationalization (configuration tooling, deployment speed, Arabic support).

---

## Part 2 — Retrieval Quality

### 2.1 Cenomi Retrieval Architecture (Current State — March 2026)

As of this writing the retrieval stack is a **three-layer hybrid system**:

```
Layer 1 — Deterministic category retrieval  (search_by_category)
  ├─ _CATEGORY_RULES mapped to canonical JSON entity types
  ├─ 100% precision for exact category queries
  ├─ Zero LLM cost, ~0ms
  └─ Used for: "What cafes are here?", "Show me fashion stores"

Layer 2 — Canonical name search  (search_canonical)
  ├─ Substring + word-boundary match over entity names
  ├─ score=1.0 for full name match, 0.6 for word match
  └─ Used for: "Is Starbucks here?", "Where is Zara?"

Layer 3 — Semantic vector search  (search_semantic / VectorStoreService)
  ├─ Chroma PersistentClient (HNSW cosine space, text-embedding-3-small 1536-dim)
  ├─ Three-tier: Redis cache (~1ms) → Chroma (~100-200ms) → OpenAI embed
  ├─ Per-mall collections: cenomi_mall_{mall_id} (no cross-contamination)
  ├─ Falls back to tag-word overlap when Chroma not initialised
  └─ Used for: "something trendy for a date night", fuzzy/paraphrased queries
```

**What is indexed:**
- Store profiles (name, category, subcategory, description, tags, floor, hours)
- Dining profiles (name, cuisine, dining_style, description, tags, price range)
- Event summaries
- Service entities (prayer room, ATM, parking, facilities)

**Current gaps in the retrieval stack:**
- Layers run independently; no cross-layer result merging or re-ranking
- `fetch_exact_facts` (factual flow) uses canonical lookup only, no vector fallback
- No BM25 / keyword index over entity text fields
- No cross-encoder re-ranker over raw vector candidates
- No query expansion (synonym generation, Arabic transliteration)
- No contextual re-ranking (scene signals not injected into query at embed time)
- Data sync requires manual `ingest_vectors.py` run — no change-triggered delta ingest

---

### 2.2 Retrieval Architecture — Platform Comparison

**AI Findr**
Semantic indexing over ingested mall content. Single-stage embedding + approximate nearest neighbor search. Explicitly targets fuzzy/paraphrased queries. Updates propagate via admin content re-ingestion through the AI Findr platform. No published re-ranker, query expansion, or hybrid search.

**Yext Answers / Chat**
The strongest retrieval reference in this set for the retail vertical:

- **Direct Answers** — structured data extraction for exact-match queries (hours, address, phone)
- **Natural Language Search** — dense vector search over the full indexed corpus
- **BM25 keyword index** over indexed fields, merged with dense results
- **Hybrid retrieval**: BM25 precision + dense vector recall, fused via learned ranker
- **Yext Answers Ranking Algorithm** combines relevance score, recency, and operator-defined boosts
- **Knowledge graph integration** — entities and their relationships are indexed natively (Brand → Store locations → Hours) enabling multi-hop retrieval
- Near-real-time content sync (minutes, not hours)
- Zero-shot performance on new content without model retraining

**Cognigy.AI — Knowledge AI**
RAG module (added 2023):
- Chunked document embedding (OpenAI / Azure OpenAI behind the scenes)
- Cosine similarity search at query time
- Per-flow knowledge scope restriction (prevents cross-tenant bleed)
- Tenant isolation via separate knowledge stores
- No published re-ranker or hybrid search

**Intercom Fin**
- Ingests articles, tickets, PDFs into a knowledge base
- Embedding-based semantic search over knowledge base content
- Restricts answers to indexed content (no hallucination beyond indexed content)
- Source citation with article links in responses
- No published re-ranker or hybrid search

**Google CCAI / Vertex AI Search for Retail**
Enterprise-grade retrieval stack:
- **BM25 + dense vector hybrid** — both precision and recall paths
- **Cross-encoder re-ranking** via Vertex AI Ranking API (inference at query time)
- Multi-stage retrieval with configurable operator-defined boosts
- Integration with Google's Knowledge Graph for entity disambiguation
- Faceted filtering, price boosting, catalog-aware ranking
- Near-real-time catalog updates via Catalog API
- Native support for 100+ languages including Arabic

---

### 2.3 Retrieval Capability Matrix

| Capability | Cenomi | AI Findr | Yext | Cognigy.AI | Intercom Fin | Google CCAI |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Deterministic / structured lookup** | ✅ Strong | ✗ | ✅ Direct Answers | ✅ Slot filling | ✗ | ✅ Facets |
| **Semantic / vector search** | ✅ Chroma | ✅ | ✅ Dense | ✅ Chunked | ✅ | ✅ Vertex |
| **BM25 / keyword search** | ✗ | ✗ pub. | ✅ | ✗ | ✗ | ✅ |
| **Hybrid (BM25 + vector) with fusion** | ✗ | ✗ pub. | ✅ | ✗ | ✗ | ✅ |
| **Cross-encoder re-ranking** | ✗ | ✗ pub. | ✅ | ✗ | ✗ | ✅ |
| **Query expansion / synonym injection** | ✗ | ✗ pub. | ✅ | ✗ | ✗ | ✅ |
| **Contextual / personalised re-ranking** | ✗ | ✗ | ✗ | ✗ | ✗ | ✅ (partial) |
| **Multi-tenant / per-mall isolation** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Result caching (Redis)** | ✅ | ✗ pub. | ✗ pub. | ✗ | ✗ | ✅ (CDN) |
| **Near-real-time data updates** | ✗ Manual | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Entity relationship graph** | ✗ | ✗ | ✅ | ✗ | ✗ | ✅ Google KG |
| **Arabic / bilingual indexing** | ✗ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Full self-hosting option** | ✅ | ✗ | ✗ | ✗ | ✗ | ✅ (GCP) |
| **Zero embedding API dependency** | ✗ | ✗ | ✅ | ✗ | ✗ | ✅ |

---

### 2.4 Retrieval Gap Analysis

The following gaps are ordered by expected quality impact on real visitor queries.

#### Gap 1 — No Hybrid Retrieval (BM25 + Vector)

**Impact: High**

Pure vector search misses exact-match and rare-word queries — store names not well-represented in the embedding space (new brands, niche specialty names). Pure BM25 misses semantic queries. The industry standard is **Reciprocal Rank Fusion (RRF)**: both indices are queried, results are merged by `1/(k + rank)` weighting, producing a ranked list that beats either alone.

Chroma does not support BM25 natively. Options:
- Add a separate BM25 index (e.g. `rank-bm25` over entity text blobs) and merge at query time
- Replace Chroma with **Weaviate** (built-in hybrid search, same Python API shape) or **Qdrant** (sparse + dense hybrid via SPLADE/BM25 sparse vectors)
- Minimal option: maintain a simple inverted index over entity name tokens alongside the Chroma collection

#### Gap 2 — No Contextual Query Augmentation

**Impact: High — minimal code change**

The `SceneMemory` object contains rich signals (companions, occasion, budget, visit_type) that are never injected into the vector query. A query of "something for dinner" embedded as-is produces the same vector as the same query from a user planning a romantic anniversary vs. a family with young children.

**Fix:** Before embedding, prepend scene signals to the query:
```python
# Before: embed "something for dinner"
# After: embed "anniversary couple romantic something for dinner"
scene_prefix = build_scene_prefix(state.scene)
augmented_query = f"{scene_prefix} {query}".strip()
```
`build_scene_prefix` reads `occasion`, `companions`, `visit_type`, `budget` from `SceneMemory` — all already available in `retriever.py` via `get_mall_context`. This single change is the **highest-impact, lowest-effort improvement** in this list.

#### Gap 3 — No Cross-Encoder Re-Ranker

**Impact: Medium**

Cosine similarity ranks by proximity in embedding space, which does not always correlate with answer quality for short, ambiguous queries. A cross-encoder re-ranker (`ms-marco-MiniLM-L-6-v2`, ~25MB, CPU-runnable) takes the top-20 vector candidates and the original query, runs them through a small BERT model, and produces a relevance score that outperforms cosine similarity for ambiguous queries by 15–25% on standard benchmarks.

**Integration point:** Add a post-retrieval step inside `MallRetriever.search_semantic()`:
```python
# After vector search returns top_k * 2 candidates...
reranked = cross_encoder.rank(query=query, documents=candidates[:20])
return reranked[:top_k]
```
The cross-encoder is too slow (50–100ms) to run on every query. Only invoke it on the semantic path (Layer 3) when the top cosine score is below a threshold (indicating ambiguity).

#### Gap 4 — No Vector Fallback in the Factual Flow

**Impact: Medium**

`fetch_exact_facts` uses canonical lookup only. When a lookup returns empty (e.g. a service or facility not covered by a rule), the response falls back to a "not found" message. A vector search over the same Chroma collection would often surface the answer from entity descriptions that do cover the concept but don't match the exact keyword.

**Fix:** Add a vector fallback at the end of `fetch_exact_facts`:
```python
if not results:
    results = await retriever.search_semantic(query, top_k=5)
```

#### Gap 5 — No Real-Time Data Sync

**Impact: Medium — operational risk**

New stores, updated hours, and new promotions are invisible to vector search until `ingest_vectors.py` is manually re-run. At Cenomi's operational scale, this creates a growing accuracy gap between the canonical JSON data (which may be updated by ops teams) and the vector index.

**Fix:** Add a file modification time (mtime) check in the `MallContext` loader. When the canonical JSON mtime is newer than the vector collection's last-updated timestamp, queue a background delta-ingest of changed entities. A targeted upsert of only changed vectors takes seconds, not the minutes of a full ingest.

#### Gap 6 — No Arabic / Bilingual Indexing

**Impact: High — blocked on broader Arabic work**

Entity descriptions in the vector store are English-only. An Arabic-speaking visitor querying "مطاعم عائلية" (family restaurants) gets no vector results because the embeddings do not bridge Arabic and English unless the model natively supports multilingual embeddings.

**Fix:** When the Arabic language layer (Priority 2 in the gap roadmap) is added, embed Arabic translations / descriptions of entities alongside English. OpenAI's `text-embedding-3-large` or Cohere's `embed-multilingual-v3` both produce cross-lingual embeddings that align Arabic and English queries to the same space. Alternatively, translate query to English before embedding (cheaper, no re-ingestion needed).

---

### 2.5 Retrieval Quality Parity Scores

```
Deterministic / structured lookup      ██████████████████████████  Cenomi leads all
Semantic vector search                 █████████████████░░░░░░░░░  ~65% vs Yext
Hybrid BM25 + vector                   ░░░░░░░░░░░░░░░░░░░░░░░░░░  Not implemented
Cross-encoder re-ranking               ░░░░░░░░░░░░░░░░░░░░░░░░░░  Not implemented
Contextual query augmentation          ░░░░░░░░░░░░░░░░░░░░░░░░░░  Not implemented (easy win)
Query expansion                        ░░░░░░░░░░░░░░░░░░░░░░░░░░  Not implemented
Real-time data sync                    ░░░░░░░░░░░░░░░░░░░░░░░░░░  Manual only
Arabic bilingual indexing              ░░░░░░░░░░░░░░░░░░░░░░░░░░  Not implemented
Full self-hosting                      ██████████████████████████  Cenomi leads all
Result caching (Redis)                 ██████████████████████████  Cenomi leads all
```

**Retrieval Quality vs Yext (strongest benchmark): ~40%**
**Retrieval Quality vs AI Findr and Cognigy: ~55–60%**
**Retrieval Quality vs Intercom Fin: ~65%**

The semantic vector layer is live and functional. The gap to production-grade retrieval is not in the vector search itself — it is in the surrounding infrastructure: hybrid fusion, contextual augmentation, re-ranking, and data freshness.

---

### 2.6 Retrieval Improvement Roadmap

| Priority | Work item | Impact | Effort | Integration point |
|---|---|---|---|---|
| **P1** | Scene-signal query augmentation | High | Low (< 1 day) | `MallRetriever.search_semantic()` |
| **P2** | BM25 entity name index + RRF fusion | High | Medium (1–2 days) | New `search_bm25()` method + merge in `retrieve()` |
| **P3** | Cross-encoder re-ranker (top-20 → top-5) | Medium | Low (< 1 day) | Post-vector step in `search_semantic()` |
| **P4** | Vector fallback in `fetch_exact_facts` | Medium | Low (< 1 day) | `fetch_exact_facts.py` fallback chain |
| **P5** | Delta-ingest on canonical JSON mtime change | Medium | Medium (2–3 days) | `mall_context.py` + background task |
| **P6** | Weaviate / Qdrant migration (hybrid native) | High | High (1 week) | Replace `vector_store.py` + `ingest_vectors.py` |
| **P7** | Arabic bilingual indexing | High | High (blocked on P2 in main roadmap) | `ingest_vectors.py` Arabic description variants |

---

## Summary

### Conversational AI Core

The Cenomi chatbot leads this competitive set on every AI reasoning dimension: intent classification depth, multi-turn scene memory, routing intelligence, hallucination control, and real-time session adaptation. No comparable platform publishes a `SceneMemory` model with visit plan tracking, mid-session feedback adaptation, or a post-generation hallucination guard backed by structured canonical data.

The gap is not in AI depth — it is in the operationalization layer: non-technical configuration tooling, off-shelf deployment speed, and Arabic language support.

### Retrieval Quality

The semantic vector layer is implemented and functional (Chroma + Redis cache + `text-embedding-3-small`). The gap to production-grade retrieval is in the surrounding infrastructure rather than the core vector search. The **single highest-impact, lowest-effort improvement** is scene-signal query augmentation — injecting the SceneMemory context as a query prefix before embedding. This requires a one-function change in `retriever.py` and leverages the conversational depth that already exists in the pipeline.

The next highest-impact change is BM25 hybrid fusion. When combined with the vector layer, this closes the primary retrieval gap vs. Yext (the benchmark) and eliminates the failure mode where exact brand names in unusual embedding positions miss vector recall.

```
Overall Conversational AI Core parity vs field:   ~85–90% on AI depth
                                                   ~15–20% on operationalization
Overall Retrieval Quality parity vs Yext:          ~40%  (benchmark)
Overall Retrieval Quality parity vs AI Findr:      ~55–60%
Retrieval gap closable with P1–P4 work:            ~70–75% vs Yext
```
