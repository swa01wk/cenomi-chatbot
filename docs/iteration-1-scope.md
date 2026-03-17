# Iteration 1 — Scope Definition

## Goal

Build a production-grade single-mall concierge chatbot that delivers an AI Findr-like experience: answer first, context-aware, interactive, and strongly grounded in mall intelligence.

## In Scope

| Component | Description |
|-----------|-------------|
| Single mall knowledge layer | Canonical entities, semantic intelligence, playbooks for one mall |
| Tenant parameter system | Per-tenant tunable weights for recommendation behavior |
| LangGraph state & node contracts | Typed state model, defined node interfaces and edges |
| Single-mall concierge runtime | End-to-end chat pipeline: route → retrieve → generate → guardrail |
| Chatbot testing UI | React console with chat, debug panel, and feedback |
| Feedback system | Capture ratings and comments, store locally, link to tenants |

## Out of Scope (Iteration 2+)

> **Note:** Items marked ✓ have since shipped — see [CHANGELOG.md](../CHANGELOG.md).

- ✓ Multi-mall support (shipped v1.1)
- ✓ Cross-mall queries / inline brand awareness (shipped v1.2)
- Mall comparison features (side-by-side stats across malls)
- Cross-mall tenant hopping (route visitor to another mall for a brand)
- Production deployment infrastructure
- User authentication
- Analytics dashboards
- Real-time data ingestion from mall systems
- Automated tenant parameter tuning (manual in iteration 1)

## Target Behavior

The concierge should:
1. **Answer immediately** — no unnecessary clarification questions
2. **Be grounded** — every factual claim traces to mall data
3. **Be context-aware** — remember conversation, apply mall knowledge
4. **Be proactive** — suggest related options, nearby services
5. **Handle ambiguity** — make reasonable assumptions, offer alternatives
6. **Respect boundaries** — say "I don't know" when data is missing

## Success Criteria

- [ ] User can chat with the concierge about mall stores, dining, and services
- [ ] Responses are grounded in the canonical tenant directory
- [ ] Playbooks activate for common scenarios (e.g., family dining, gift shopping)
- [ ] Debug panel shows pipeline state at each node
- [ ] Feedback can be submitted and is stored
- [ ] Tenant parameters influence response behavior

## Implementation Order

1. Tenant knowledge model & global mall context builder
2. Tenant parameter system
3. LangGraph state & node contracts
4. Core concierge pipeline
5. Chatbot UI
6. Feedback system
