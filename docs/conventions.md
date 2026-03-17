# Engineering Conventions

## Naming

| Type | Convention | Example |
|------|-----------|---------|
| Python files | `snake_case.py` | `mall_context.py` |
| Python classes | `PascalCase` | `ConciergeState` |
| Python functions | `snake_case` | `build_concierge_graph` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_CONVERSATION_TURNS` |
| TypeScript files | `PascalCase.tsx` for components, `camelCase.ts` for utils | `ChatPage.tsx`, `client.ts` |
| API endpoints | `lowercase` nouns | `/api/chat`, `/api/feedback` |
| Tenant IDs | `t-NNN` format | `t-001` |
| Playbook IDs | `pb-NNN` format | `pb-001` |
| Session IDs | `session-{hex}` | `session-a1b2c3d4e5f60718` |

## Model Locations

- **API contracts** (request/response): `backend/app/models/api.py`
- **Pipeline state**: `backend/app/models/state.py`
- **Domain models** (tenant, mall, feedback): `backend/app/models/<domain>.py`
- **Frontend types**: `frontend/src/types/` (mirrors API contracts)

## Configuration

- All config via environment variables loaded through `pydantic-settings`
- Single source: `backend/app/config/settings.py`
- Never import `os.environ` directly — always use `get_settings()`
- Defaults are development-friendly; production overrides via `.env`

## Logging

- Use `app.observability.logger.get_logger(__name__)` everywhere
- Never use bare `print()` in production code (startup/shutdown messages excepted)
- Debug traces go into `ConciergeState.debug_trace` for the debug panel

## Core vs. Tenant-Specific

| Concern | Where | Principle |
|---------|-------|-----------|
| Pipeline logic | `app/graph/`, `app/nodes/` | Generic — works for any mall |
| Mall identity | `data/context_packs/` | Data-driven — loaded at startup |
| Tenant behavior | `TenantParams` + `data/tenant_config/` | Configurable — tuned via feedback |
| Prompt persona | `app/prompts/builder.py` | Template-driven — mall name injected |
| Concierge system prompt | `llm/prompts/concierge_prompt.py` | Grounding rules + persona |
| Intent classification | `intent/query_classifier.py` | Rule tables → LLM fallback |
| Response composition | `response/concierge_composer.py` | Deterministic blueprints |
| Hallucination guard | `guardrails/hallucination_guard.py` | Post-generation validation |
| Canonical retrieval | `retrieval/mall_retriever.py` | LLM-safe entity lookups |

Core code should **never** hardcode tenant names, store categories, or mall-specific logic.
Tenant-specific behavior flows through `TenantParams`, `data/tenant_config/`, and the behavior rules defined there.

## Debug Payloads

Every chat response includes an optional `debug` field containing:
- Which nodes executed
- What was retrieved
- Retrieval scores
- Active playbooks
- Tenant params applied

This powers the frontend debug panel and is controlled by `BACKEND_DEBUG=true`.

## Data Pipeline

```
output_mall_XX.json  →  scripts/convert_to_canonical.py  →  data/canonical/{mall_id}.json
                                                                        │
                                       ┌────────────────────────────────┤
                                       │    scripts/synthesize_mall_data.py
                                       │    (or generate_mall_data.py for full run)
                                       │
                           ┌───────────┼──────────────┬──────────────────────┐
                           ▼           ▼              ▼                      ▼
                      semantic/    playbooks/    tenant_config/        context_packs/
```

`raw/` is kept for unprocessed API dumps only. The pipeline entry point is `output_mall_XX.json` (produced by `transform_mall_data.py`). Step 1 (canonical conversion) is deterministic and needs no API key. Step 2 (all derived layers) calls GPT-4.1 and requires `OPENAI_API_KEY`.

See [`docs/data-pipeline.md`](data-pipeline.md) for full documentation.

## Environment Variables

Prefix convention:
- `BACKEND_*` — backend server settings
- `VITE_*` — frontend build-time settings
- `OPENAI_*` — LLM provider credentials
- `MALL_*` — mall identity settings

## Testing

- Backend tests: `pytest` in `backend/tests/`
- Use `httpx.AsyncClient` with `ASGITransport` for API tests
- Frontend: Vite dev server with mock data for offline development

### Stability Test Suite

`backend/tests/test_stability.py` contains 80 unit tests covering 14 acceptance criteria categories. Run with:

```bash
cd backend && .venv/bin/python -m pytest tests/test_stability.py -v
```

**Test categories:**

| Category | What it tests |
|----------|--------------|
| Query Normalization | Semantically equivalent phrasings produce the same routing outcome |
| Route Flow Discipline | Factual vs. concierge routing correctness for all sub-intent types |
| Context-Setting Detection | `context_setting` message kind identified correctly |
| Scenario Extraction | Companion, occasion, and visit-type signals extracted from free text |
| Message Kind Detection | `followup`, `refinement`, `constraint_refinement`, `topic_switch` correctly identified |
| Topic Lock & Follow-up | Topic lock established, reinforced, and cleared across turns |
| Multi-Intent Handling | Primary intent preserved when companion/filter signals coexist |
| Unsupported Input | Gibberish, keyboard mashing, and empty inputs detected without LLM call |
| Dedupe Correctness | Normalized canonical dedup collapses name variants correctly |
| Brand Lookup Discipline | Brand availability queries always route to factual flow |
| Scenario Playbook Selection | Scenario-rich queries select the correct playbook |
| Offer Query Handling | Offer queries route to factual; no hallucination when data absent |
| `_is_pure_lookup` Helper | Filtered movie/brand lookups recognized as factual, not planning |
| Acceptance Criteria Checklist | End-to-end AC validation (AC1–AC10) |

### Test Naming Convention

- Test files: `test_<feature>.py` — snake_case, prefixed with `test_`
- Test classes: `Test<Category>` — PascalCase, groups related assertions
- Test methods: `test_<specific_behavior>` — snake_case, describes the exact assertion

Example: `TestRouteFlowDiscipline::test_movie_lookup_with_kid_stays_factual`
