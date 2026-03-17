# Multi-Mall Support

This document explains how the chatbot was extended to serve multiple malls simultaneously, and how to test it end-to-end.

---

## How it works

### Before (single-mall)

At startup, the app loaded one `MallContextLoader` into a global singleton. Every chat request — regardless of which `mall_id` was sent in the body — hit that one context. The mall was fixed by `BACKEND_MALL_ID` in `.env`.

### After (multi-mall)

The runtime now holds a **registry** (`dict[mall_id → MallContextLoader]`). At startup it loads **all** malls listed in `BACKEND_MALL_IDS`. Each chat request carries a `mall_id`; the pipeline looks up the right context from the registry before processing.

```
BACKEND_MALL_IDS=al_nakheel_plaza_28,al_nakheel_plaza_13
        │                   │
        ▼                   ▼
 MallContextLoader    MallContextLoader
 (Al Nakheel Plaza)   (Mall of Arabia)
        │                   │
        └────────┬───────────┘
                 ▼
       _mall_contexts: dict[str, MallContextLoader]
                 │
    POST /api/chat  {mall_id: "al_nakheel_plaza_13"}
                 │
        get_mall_context("al_nakheel_plaza_13")
                 │
         GPT-4o response scoped to Mall of Arabia
```

---

## Files changed

### Data pipeline scripts

| File | Change |
|---|---|
| `transform_mall_data.py` | Added `--mall-id` CLI arg (was hardcoded to mall 28). Now generates `output_mall_<id>.json` for any mall. |
| `backend/scripts/convert_to_canonical.py` | No change — was already generic. |
| `backend/scripts/synthesize_mall_data.py` | No change — was already generic. |

### Backend

| File | Change |
|---|---|
| `backend/.env` | `BACKEND_MALL_ID` → `BACKEND_MALL_IDS=al_nakheel_plaza_28,al_nakheel_plaza_13` |
| `backend/.env.example` | Same as above |
| `backend/app/config/settings.py` | `mall_id: str` → `mall_ids: str` (comma-separated) + `get_mall_id_list()` helper |
| `backend/app/runtime.py` | Single `_mall_context` → `_mall_contexts: dict[str, MallContextLoader]`; `initialize(mall_ids: list[str])`; `get_mall_context(mall_id: str)` |
| `backend/app/main.py` | Startup calls `runtime.initialize(settings.get_mall_id_list())` |
| `backend/app/services/concierge.py` | `get_mall_context()` → `get_mall_context(request.mall_id)` |
| `backend/app/api/health.py` | Health response now returns `mall_ids` list instead of single `mall_id` |
| `backend/app/nodes/compose_context.py` | `get_mall_context(state.mall_id)` |
| `backend/app/nodes/resolve_playbooks.py` | `get_mall_context(state.mall_id)` |
| `backend/app/nodes/generate_response.py` | `get_mall_context(state.mall_id)` (3 call sites) |
| `backend/app/nodes/fetch_exact_facts.py` | `get_mall_context(state.mall_id)` |
| `backend/app/nodes/update_memory.py` | `get_mall_context(state.mall_id)` |
| `backend/app/retrieval/retriever.py` | `get_mall_context(self.mall_id)` (4 call sites in `MallRetriever`) |
| `backend/app/prompts/builder.py` | `get_mall_context(state.mall_id)` / `get_mall_context(mall_id)` (6 call sites) |

### Frontend

| File | Change |
|---|---|
| `frontend/src/lib/constants.ts` | Added `al_nakheel_plaza_13` to `MALLS` and `TENANTS` arrays |

---

## Data files on disk

Both malls now have a full set of knowledge files:

```
backend/data/
├── canonical/
│   ├── al_nakheel_plaza_28.json   ← Al Nakheel Plaza (Buraidah) — 94 brands
│   └── al_nakheel_plaza_13.json   ← Mall of Arabia (Jeddah)    — 54 brands
├── semantic/
│   ├── al_nakheel_plaza_28.json
│   └── al_nakheel_plaza_13.json
├── playbooks/
│   ├── al_nakheel_plaza_28.json
│   └── al_nakheel_plaza_13.json
├── tenant_config/
│   ├── al_nakheel_plaza_28.json
│   └── al_nakheel_plaza_13.json
└── context_packs/
    ├── al_nakheel_plaza_28_context.json
    └── al_nakheel_plaza_13_context.json
```

---

## How to add a third mall

### Step 1 — Generate the raw mall JSON

```bash
# From the workspace root
python transform_mall_data.py --mall-id <N>
# Creates output_mall_<N>.json
```

Check which mall IDs are available:

```bash
python -c "
import json
data = json.load(open('data/mall_and_movie.json'))
for m in data['data']['list']:
    print(m['property_group_id'], m['marketing_name'], m['city'])
"
```

### Step 2 — Convert to canonical format

```bash
cd backend
.venv/bin/python scripts/convert_to_canonical.py ../output_mall_<N>.json
# Creates data/canonical/<mall_id>.json
# The script prints the exact mall_id string — note it for Step 4.
```

### Step 3 — Synthesize derived data files (requires OpenAI API)

```bash
cd backend
.venv/bin/python scripts/synthesize_mall_data.py data/canonical/<mall_id>.json
# Creates semantic/, playbooks/, tenant_config/, context_packs/ files for this mall.
# Takes ~10 minutes per mall (multiple GPT-4 calls).
```

### Step 4 — Register the mall

In `backend/.env`, append the new mall ID:

```env
BACKEND_MALL_IDS=al_nakheel_plaza_28,al_nakheel_plaza_13,<new_mall_id>
```

In `frontend/src/lib/constants.ts`, add entries:

```ts
export const TENANTS = [
  { id: "al_nakheel_plaza_28", label: "Al Nakheel Plaza" },
  { id: "al_nakheel_plaza_13", label: "Mall of Arabia" },
  { id: "<new_mall_id>",       label: "<Mall Display Name>" },
] as const;

export const MALLS = [
  { id: "al_nakheel_plaza_28", label: "Al Nakheel Plaza — Buraidah" },
  { id: "al_nakheel_plaza_13", label: "Mall of Arabia — Jeddah" },
  { id: "<new_mall_id>",       label: "<Mall Display Name> — <City>" },
] as const;
```

Restart the backend — the new mall is live.

---

## Testing

### 1. Health check — confirm both malls loaded

```bash
curl http://localhost:8000/api/health | python3 -m json.tool
```

Expected response:

```json
{
  "status": "ok",
  "mall_ids": ["al_nakheel_plaza_28", "al_nakheel_plaza_13"],
  "env": "development",
  "runtime_initialized": true
}
```

---

### 2. Chat — Al Nakheel Plaza (mall 28)

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What cafes are here?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": false
  }' | python3 -m json.tool
```

The response should mention cafes specific to Al Nakheel Plaza (Buraidah).

---

### 3. Chat — Mall of Arabia (mall 13)

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What cafes are here?",
    "mall_id": "al_nakheel_plaza_13",
    "tenant_id": "al_nakheel_plaza_13",
    "debug": false
  }' | python3 -m json.tool
```

The response should mention cafes specific to Mall of Arabia (Jeddah) — a different set of stores.

---

### 4. Verify context isolation with debug mode

Send the same query to both malls with `"debug": true` and compare `selected_entities` in the debug payload — they must be different.

```bash
# Mall 28
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Show me fashion stores","mall_id":"al_nakheel_plaza_28","tenant_id":"al_nakheel_plaza_28","debug":true}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Mall 28 entities:', [e['name'] for e in d['debug']['selected_entities'][:5]])"

# Mall 13
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Show me fashion stores","mall_id":"al_nakheel_plaza_13","tenant_id":"al_nakheel_plaza_13","debug":true}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Mall 13 entities:', [e['name'] for e in d['debug']['selected_entities'][:5]])"
```

---

### 5. Test invalid mall ID returns a clear error

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","mall_id":"nonexistent_mall","tenant_id":"nonexistent_mall"}' \
  | python3 -m json.tool
```

Expected: HTTP 500 with `detail` explaining which malls are available.

---

### 6. Frontend — mall selector

Start the frontend (`npm run dev` in `frontend/`), open the browser, and use the mall dropdown in the top bar to switch between:

- **Al Nakheel Plaza — Buraidah**
- **Mall of Arabia — Jeddah**

Ask the same question in both and observe that the responses reference different stores.

---

## Mall reference

| Mall ID | Name | City | Brands | Cinema |
|---|---|---|---|---|
| `al_nakheel_plaza_28` | Al Nakheel Plaza | Buraidah | 94 | Muvi Cinema (6 movies) |
| `al_nakheel_plaza_13` | Mall of Arabia | Jeddah | 54 | Cinema (11 movies) |
