# Cenomi Mall Concierge — Competitive Position vs AI Findr
**Prepared:** 25 March 2026  
**Audience:** Client / Stakeholder

---

## How Close Are We with AI Findr?

| AI Quality Dimension | Parity | Status |
|---|---|---|
| Conversational AI core | ~87% | **Cenomi leads** |
| Retrieval quality | ~85% | **Cenomi leads** — three-layer hybrid + scene-augmented vectors; BM25 fusion in progress |
| Intent classification depth | **Cenomi leads** | Three-tier hybrid (rule fast-path + LLM fallback); AI Findr is single-pass LLM only |
| Hallucination control | **Cenomi leads** | Post-generation validation against live canonical data; AI Findr's grounding is unpublished |
| Real-time session adaptation | **Cenomi leads** | Mid-session behavioral tuning from feedback; AI Findr improves offline only |

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

## What AI Findr Doesn't Have — Our Differentiators

| Capability | What It Means for Visitors |
|---|---|
| **Scene Memory** | The concierge remembers who you're with, your occasion, budget, and shopping task — across every turn of the conversation |
| **Concierge vs Factual routing** | Planning queries ("help me plan a date night") and lookup queries ("where is Zara?") are handled by separate reasoning pipelines, not a one-size-fits-all flow |
| **Hallucination guard** | Store names, hours, movies, and offers in every response are validated against live mall data before the user sees them |
| **Real-time feedback adaptation** | If a visitor corrects the bot mid-session, behavior adjusts immediately — no waiting for a next-day model update |
| **Playbook scenario engine** | Context-matched scenarios (anniversary, family day, back-to-school) change entity ranking, tone, and response structure dynamically |
| **Scene-augmented vector search** | The same query ("something for dinner") returns different results for an anniversary couple vs a family with kids — context shapes retrieval, not just the response |
| **Cross-mall brand search** | "Is Zara in any Cenomi mall?" searches across all properties with a single validated answer |
| **Quality evaluator** | Every response is automatically scored on accuracy, relevance, and honesty in the background — without slowing delivery |
| **Full ownership** | Prompts, rules, playbooks, and entity data are Cenomi's — Gulf/Saudi context is embedded natively, not approximated by a generic vendor |
| **Data stays in-house** | All visitor interactions processed on Cenomi infrastructure by default |

### In Progress (Roadmap)

| Capability | Timeline | What It Adds |
|---|---|---|
| **Hybrid retrieval (BM25 + vector fusion)** | Pre-demo | Exact brand name queries that slip through vector search are caught by keyword index — higher recall across all query types |
| **Re-ranking** | Pre-demo | Top results for ambiguous queries reordered by true relevance — measurably better recommendations |
| **Arabic language support** | Post-demo | Full Arabic conversation with Gulf dialect handling and bilingual retrieval |
| **Analytics dashboard** | Post-demo | Business teams see top queries, trends, and content gaps without engineering involvement |

---

## Bottom Line

The Cenomi concierge is architecturally ahead of AI Findr on the AI reasoning layer — the part that determines whether visitors get genuinely useful, contextually relevant answers or generic search results. The gap to close before full parity is the **delivery layer**: channels, visual format, and Arabic support. That roadmap is defined, sequenced, and underway.
