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
| Tenant behavior | `TenantParams` model | Configurable — tuned via feedback |
| Prompt persona | `app/prompts/builder.py` | Template-driven — mall name injected |

Core code should **never** hardcode tenant names, store categories, or mall-specific logic.
Tenant-specific behavior flows through `TenantParams` and data files.

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
raw/ → (normalization script) → canonical/
raw/ → (semantic extraction) → semantic/
canonical/ + semantic/ → (playbook authoring) → playbooks/
canonical/ + semantic/ + playbooks/ → context_packs/
```

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
