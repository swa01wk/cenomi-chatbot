# Folder Structure

## Root

```
cenomi-chatbot/
├── backend/                   Python concierge engine
├── frontend/                  React testing console
├── docs/                      Architecture and planning docs
├── data/                      Raw API source files for the ETL script
│   ├── sample.json            Output schema contract (target shape)
│   ├── mall_and_movie.json    API: mall metadata + movie listings
│   ├── services.json          API: mall services (pre-filtered for mall 28)
│   ├── engagements.json       API: promotions and offers
│   └── brands.json            API: brand/tenant records
├── transform_mall_data.py     ETL script: ./data/ → output_mall_28.json
├── output_mall_28.json        Transformed output (→ feeds backend/data/canonical/)
├── .env.example               Root-level environment template
├── .gitignore                 Git ignore rules
└── README.md                  Project overview
```

See [`docs/data-pipeline.md`](data-pipeline.md) for full documentation on the source files, the transform script, and the context-building pipeline.

## Backend

```
backend/
├── app/
│   ├── api/                    FastAPI route handlers
│   │   ├── health.py           Health check endpoint
│   │   ├── chat.py             Chat endpoint (main concierge interface)
│   │   ├── session.py          Session management endpoints
│   │   └── feedback.py         Feedback submission endpoint
│   │
│   ├── graph/                  LangGraph pipeline definition
│   │   └── builder.py          Graph construction and compilation
│   │
│   ├── nodes/                  Individual graph node implementations (one file per node)
│   │   ├── load_session.py     Session load / turn init
│   │   ├── interpret_turn.py   Hybrid intent classifier (rule + LLM fallback)
│   │   ├── smalltalk.py        Fast-path for greetings — zero LLM cost
│   │   ├── update_scene_memory.py  Extract + persist visitor context signals
│   │   ├── resolve_playbooks.py    Scenario playbook matching
│   │   ├── choose_strategy.py  Intent + playbook → response strategy
│   │   ├── compose_context.py  Entity selection + context building
│   │   ├── compose_fact_response_context.py  Fact-query context path
│   │   ├── rank_and_dedupe.py  Semantic ranking + entity deduplication
│   │   ├── decide_retrieval.py Retrieval gate (needed / not needed)
│   │   ├── fetch_exact_facts.py    Targeted canonical lookups
│   │   ├── resolve_fact_scope.py   Scoping for exact-fact queries
│   │   ├── generate_response.py    LLM prompt assembly + response generation
│   │   ├── update_memory.py    Post-response entity + scene state write-back
│   │   ├── route_flow.py       Conditional routing (smalltalk / standard path)
│   │   ├── emit_debug_payload.py   Build turn debug trace
│   │   └── _tracing.py         Node tracing helpers
│   │
│   ├── services/               Business logic and orchestration
│   │   ├── concierge.py        Chat turn orchestration
│   │   ├── context_builder.py  Context pack assembly from data layers
│   │   ├── clean_context.py    Per-turn context sanitisation (no history contamination)
│   │   ├── cross_mall_brand.py Cross-mall brand extraction and scene-fallback resolution
│   │   ├── session_store.py    In-memory + Redis session store; updates mall_id on reuse
│   │   ├── playbook_engine.py  Playbook resolution (thin adapter over app layer)
│   │   ├── enricher.py         Semantic enrichment helpers
│   │   ├── normalizer.py       Canonical entity normalisation
│   │   ├── semantic_signals.py Semantic signal extraction
│   │   ├── experience_resolver.py  Experience-type entity classification
│   │   ├── cta_generator.py    Call-to-action generation
│   │   ├── feedback_service.py     Feedback storage
│   │   ├── feedback_normalizer.py  Feedback normalisation pipeline
│   │   ├── implicit_feedback_detector.py  Detect implicit feedback signals
│   │   ├── knowledge_gap_analyzer.py  Identify gaps in canon coverage
│   │   ├── session_tuning_engine.py   Per-session parameter tuning
│   │   ├── tenant_parameter_tuner.py  Feedback-driven param adjustment
│   │   ├── tenant_params.py    Tenant parameter model
│   │   └── tenant_runtime.py   Runtime tenant parameter resolution
│   │
│   ├── models/                 Pydantic data models
│   │   ├── api.py              API request/response contracts
│   │   ├── state.py            LangGraph pipeline state (ConciergeState)
│   │   ├── tenant.py           Canonical entities + tunable params
│   │   ├── mall.py             Mall profile, semantic entries, playbooks
│   │   ├── context_pack.py     Context pack and topic block models
│   │   ├── semantic.py         Semantic intelligence models
│   │   ├── playbook.py         Scenario playbook models
│   │   └── feedback.py         Feedback records
│   │
│   ├── config/                 Application configuration
│   │   ├── settings.py         Environment-driven settings
│   │   └── constants.py        Shared constants
│   │
│   ├── prompts/                LLM prompt construction
│   │   └── builder.py          Full prompt assembly (identity + context + scene + grounding)
│   │
│   ├── context/                Mall context loading and assembly
│   │   ├── mall_context.py     Context pack builder + entity lookup
│   │   └── semantic_mall_model.py  Semantic tag model for audience/vibe/outing scoring
│   │
│   ├── retrieval/              App-layer retrieval (delegates to retrieval/)
│   │   └── retriever.py        Multi-layer mall intelligence search
│   │
│   ├── observability/          Logging, tracing, debugging
│   │   └── logger.py           Structured logging + debug tracer
│   │
│   └── utils/                  Shared utilities
│       └── ids.py              ID generation helpers
│
├── guardrails/                 Post-generation hallucination prevention
│   └── hallucination_guard.py  Validates LLM output against canonical facts
│
├── intent/                     Intent classification layer
│   ├── intent_parser.py        Low-level intent parsing utilities
│   └── query_classifier.py     Hybrid classifier — normalization table, short-query intents, keyword rules, LLM fallback; also: normalize_query_with_pattern, is_likely_unsupported, maybe_correct_brand
│
├── llm/                        LLM prompt templates
│   └── prompts/
│       ├── concierge_prompt.py     Concierge system prompt with grounding rules
│       └── query_expander.py       Short-query expansion prompts
│
├── playbooks/                  Deterministic playbook engine
│   └── playbook_engine.py      Pre-built itinerary plans, keyword-matched (no LLM)
│
├── response/                   Deterministic response composition
│   └── concierge_composer.py   Structured blueprint builder + LLM polish layer
│
├── retrieval/                  Structured canonical data retrieval
│   └── mall_retriever.py       LLM-safe category/entity lookups over canonical JSON
│
├── tests/                      Test suite
│   ├── test_stability.py       80-test stability suite — 14 AC categories (intent, routing, context, dedup, unsupported inputs)
│   └── test_cross_mall_v1.py   Cross-mall unit tests — brand resolution, fact context ordering, flow hints (no LLM calls)
├── scripts/                    Dev and ops scripts
│   ├── convert_to_canonical.py     ETL: output_mall_XX.json → canonical/{mall_id}.json
│   ├── synthesize_mall_data.py     LLM synthesis: canonical → semantic, playbooks, etc.
│   └── generate_mall_data.py       Full pipeline: raw → all 5 intelligence layers
│
├── data/
│   ├── raw/                    Unprocessed source data (ETL input)
│   ├── canonical/              Normalized tenant entities (al_nakheel_plaza_*.json)
│   ├── semantic/               Derived semantic intelligence
│   ├── playbooks/              Scenario playbooks (28 per mall)
│   ├── context_packs/          Pre-assembled LLM context bundles
│   ├── tenant_config/          Per-mall behavior tuning + global defaults
│   ├── feedback/               Implicit and normalized feedback records
│   └── examples/               Sample data for development
│
├── pyproject.toml              Python project configuration
└── .env.example                Backend environment template
```

## Frontend

```
frontend/
├── src/
│   ├── components/          Reusable UI components
│   │   ├── ChatMessage.tsx  Single message bubble
│   │   ├── ChatInput.tsx    Message input area
│   │   ├── DebugPanel.tsx   Pipeline debug viewer
│   │   └── FeedbackWidget.tsx  Rating widget
│   │
│   ├── pages/               Page-level components
│   │   └── ChatPage.tsx     Main chat interface
│   │
│   ├── hooks/               Custom React hooks
│   │   └── useChat.ts       Chat state management
│   │
│   ├── api/                 Backend API client
│   │   └── client.ts        HTTP client for all endpoints
│   │
│   ├── lib/                 Shared utilities and constants
│   │   └── constants.ts     App-wide constants
│   │
│   ├── types/               TypeScript type definitions
│   │   ├── api.ts           API contract types
│   │   └── chat.ts          Chat domain types
│   │
│   ├── mock/                Mock data for offline development
│   │   └── responses.ts     Sample API responses
│   │
│   ├── styles/              Global styles
│   │   └── index.css        Tailwind imports
│   │
│   ├── App.tsx              Root component
│   └── main.tsx             Entry point
│
├── index.html               HTML shell
├── vite.config.ts           Vite + Tailwind configuration
├── package.json             Node dependencies
└── .env.example             Frontend environment template
```

## Conventions

| Concern | Location |
|---------|----------|
| API contracts | `backend/app/models/api.py` + `frontend/src/types/api.ts` |
| Pipeline state | `backend/app/models/state.py` |
| Domain models | `backend/app/models/*.py` |
| Configuration | `backend/app/config/settings.py` via `.env` |
| Mall data | `backend/data/` (structured by intelligence layer) |
| Graph nodes | `backend/app/nodes/` (one file per node) |
| Prompts | `backend/app/prompts/builder.py` + `backend/llm/prompts/` |
| Intent classification | `backend/intent/query_classifier.py` |
| Response composition | `backend/response/concierge_composer.py` |
| Hallucination guard | `backend/guardrails/hallucination_guard.py` |
| Canonical retrieval | `backend/retrieval/mall_retriever.py` |
