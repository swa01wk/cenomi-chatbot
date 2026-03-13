# Component Map — Implementation Guide

This document maps each planned component to its location in the codebase and the prompt that will implement it.

## Prompt Sequence

### Prompt 1: Tenant Knowledge Model & Global Mall Context Builder

**What it builds:** The canonical entity system and mall context assembly.

| File | Action |
|------|--------|
| `backend/app/models/tenant.py` | Flesh out TenantEntity, add validation, normalization |
| `backend/app/models/mall.py` | Flesh out MallProfile, SemanticEntry, Playbook |
| `backend/app/context/mall_context.py` | Implement full data loading and context assembly |
| `backend/data/canonical/` | Populate with canonical tenant data |
| `backend/data/semantic/` | Populate with semantic intelligence entries |
| `backend/data/playbooks/` | Populate with scenario playbooks |
| `backend/data/context_packs/global_context.json` | Finalize global context structure |

---

### Prompt 2: Tenant Parameter System

**What it builds:** Tunable per-tenant behavioral weights.

| File | Action |
|------|--------|
| `backend/app/models/tenant.py` | Flesh out TenantParams with full tuning schema |
| `backend/app/services/tenant_params.py` | New — load, apply, and update tenant params |
| `backend/app/config/` | Add tenant param defaults and overrides |

---

### Prompt 3: LangGraph State & Node Contracts

**What it builds:** The state model and typed node interfaces.

| File | Action |
|------|--------|
| `backend/app/models/state.py` | Finalize ConciergeState with all fields |
| `backend/app/nodes/router.py` | Implement intent classification |
| `backend/app/nodes/retriever.py` | Implement multi-layer retrieval node |
| `backend/app/nodes/generator.py` | Implement grounded generation |
| `backend/app/nodes/guardrail.py` | Implement safety and quality checks |
| `backend/app/graph/builder.py` | Wire nodes into LangGraph StateGraph |

---

### Prompt 4: Core Concierge Pipeline

**What it builds:** End-to-end chat pipeline wired together.

| File | Action |
|------|--------|
| `backend/app/services/concierge.py` | Implement full orchestration |
| `backend/app/graph/builder.py` | Finalize graph compilation |
| `backend/app/retrieval/retriever.py` | Implement retrieval layers |
| `backend/app/prompts/builder.py` | Implement full prompt assembly |
| `backend/app/api/chat.py` | Wire to real pipeline |
| `backend/app/observability/logger.py` | Add tracing to pipeline |

---

### Prompt 5: Chatbot UI

**What it builds:** Full-featured testing console.

| File | Action |
|------|--------|
| `frontend/src/pages/ChatPage.tsx` | Polish chat experience |
| `frontend/src/components/` | Add rich message rendering, suggestions |
| `frontend/src/components/DebugPanel.tsx` | Full debug visualization |
| `frontend/src/hooks/useChat.ts` | Enhance with streaming, session mgmt |

---

### Prompt 6: Feedback System

**What it builds:** Feedback capture, storage, and tenant param tuning.

| File | Action |
|------|--------|
| `backend/app/feedback/service.py` | Full implementation |
| `backend/app/api/feedback.py` | Wire to service |
| `backend/app/models/feedback.py` | Extend with analysis fields |
| `frontend/src/components/FeedbackWidget.tsx` | Polish UI |
