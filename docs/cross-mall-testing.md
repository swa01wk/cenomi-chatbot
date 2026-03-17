# Cross-Mall Brand Search — Run & Test Guide

This guide explains how to start the platform and test the cross-mall brand search feature introduced in v1.2.

---

## What the feature does

When a visitor is chatting in the context of one mall (their **home mall**) and asks whether a brand exists at another Cenomi mall — or across all Cenomi malls — the chatbot:

1. Detects the cross-mall intent via a high-priority regex rule (no LLM round-trip needed for detection)
2. Searches all loaded mall contexts for the brand
3. Returns home-mall status first, then other malls

Single-mall questions (`"Do you have Zara?"`) are **not** affected — they behave exactly as before.

---

## Quick brand reference

Use these in sample queries so you know what to expect.

| Brand | Al Nakheel Plaza (28) | Mall of Arabia (13) |
|---|---|---|
| Starbucks | yes | yes |
| Zara | yes | yes |
| Bershka | yes | yes |
| The Body Shop | yes | yes |
| MINISO | yes | yes |
| Stradivarius | yes | yes |
| Bath & Body Works | yes | yes |
| Baskin Robbins | yes | yes |
| Ajmal Perfumes | yes | yes |
| McDonald's | yes | **no** |
| Charles & Keith | yes | **no** |
| Nayomi | yes | **no** |
| Red Tag | yes | **no** |
| Kudu Restaurant | **no** | yes |
| Popeyes Restaurant | **no** | yes |

---

## 1. Start the backend

```bash
cd cenomi-chatbot/backend

# Activate virtual environment
source .venv/bin/activate

# Confirm .env has both malls
cat .env | grep MALL_IDS
# Expected: BACKEND_MALL_IDS=al_nakheel_plaza_28,al_nakheel_plaza_13

# Start the server
uvicorn app.main:app --reload --port 8000
```

Watch for these two lines in the startup logs — both malls must load:

```
INFO  Runtime initialized for mall al_nakheel_plaza_28
INFO  Runtime initialized for mall al_nakheel_plaza_13
INFO  Runtime ready — loaded 2 mall(s): ['al_nakheel_plaza_28', 'al_nakheel_plaza_13']
```

### Confirm with health check

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

Expected:

```json
{
  "status": "ok",
  "mall_ids": ["al_nakheel_plaza_28", "al_nakheel_plaza_13"],
  "runtime_initialized": true
}
```

---

## 2. Start the frontend (optional)

```bash
cd cenomi-chatbot/frontend
npm run dev
```

Open **http://localhost:5173**. Use the mall selector in the top bar to pick your home mall before asking a cross-mall question.

---

## 3. Sample cross-mall queries

All `curl` examples below use `al_nakheel_plaza_28` as the home mall. Swap `mall_id` / `tenant_id` to test from the other side.

---

### Query A — Brand present in both malls

> "Does Mall of Arabia also have Starbucks?"

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Does Mall of Arabia also have Starbucks?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": false
  }' | python3 -m json.tool
```

**Expected behaviour:** Response confirms Starbucks is available at both Al Nakheel Plaza (your mall) and Mall of Arabia. Home mall is mentioned first.

---

### Query B — Brand only at the home mall

> "Is Charles & Keith available in any other Cenomi mall?"

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Is Charles & Keith available in any other Cenomi mall?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": false
  }' | python3 -m json.tool
```

**Expected behaviour:** Response confirms Charles & Keith is available at Al Nakheel Plaza (your current mall) but is not listed at Mall of Arabia.

---

### Query C — Brand only at another mall

> "Does Al Nakheel Plaza have Popeyes?"

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Does Al Nakheel Plaza have Popeyes?",
    "mall_id": "al_nakheel_plaza_13",
    "tenant_id": "al_nakheel_plaza_13",
    "debug": false
  }' | python3 -m json.tool
```

**Expected behaviour:** Response tells the visitor that Popeyes is available here at Mall of Arabia but is not listed at Al Nakheel Plaza.

---

### Query D — Which malls carry a brand

> "Which of your malls has Zara?"

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Which of your malls has Zara?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": false
  }' | python3 -m json.tool
```

**Expected behaviour:** Lists both malls as carrying Zara, with Al Nakheel Plaza (home) noted first.

---

### Query E — General across-malls phrasing

> "Is Bath & Body Works at any Cenomi mall?"

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Is Bath & Body Works at any Cenomi mall?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": false
  }' | python3 -m json.tool
```

**Expected behaviour:** Confirms Bath & Body Works is in both malls.

---

## 4. Inspect the intent via debug mode

Add `"debug": true` to any request and check the `intent` field in the response to confirm cross-mall detection fired correctly.

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Does Mall of Arabia also have Starbucks?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": true
  }' | python3 -c "
import sys, json
d = json.load(sys.stdin)
dbg = d.get('debug', {})
print('domain      :', dbg.get('intent', {}).get('domain'))
print('sub_intent  :', dbg.get('intent', {}).get('sub_intent'))
print('classifier  :', dbg.get('intent', {}).get('classifier'))
print('confidence  :', dbg.get('intent', {}).get('confidence'))
print()
print('entities returned:')
for e in dbg.get('selected_entities', []):
    print(' ', e.get('name'), '|', e.get('mall_id','?'), '| home:', e.get('is_home_mall'))
"
```

Expected values:

```
domain      : cross_mall
sub_intent  : cross_mall_search
classifier  : rule_based
confidence  : 0.97
```

---

## 5. Verify single-mall queries are unaffected

The following must NOT trigger cross-mall logic:

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Do you have Zara?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": true
  }' | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('domain:', d.get('debug',{}).get('intent',{}).get('domain'))
"
```

Expected: `domain: shopping` (or `tenant_search`) — **not** `cross_mall`.

---

## 6. Test from the frontend

1. Open **http://localhost:5173**
2. Set home mall to **Al Nakheel Plaza — Buraidah** using the top-bar selector
3. Type: *"Does Mall of Arabia also have Starbucks?"*
   - Response should confirm both malls, leading with Al Nakheel Plaza
4. Switch home mall to **Mall of Arabia — Jeddah**
5. Type the same question
   - Response should now lead with Mall of Arabia, then mention Al Nakheel Plaza

---

## 7. Negative / edge-case queries

| Query | Expected behaviour |
|---|---|
| `"Does the other mall have a brand that doesn't exist"` | Chatbot states the brand was not found in any loaded mall |
| `"Do you have Nike?"` | Single-mall response (Nike is not in either mall — chatbot says so for home mall only) |
| `"Tell me about dining across all your malls"` | Cross-mall triggered; lists dining options tagged by mall |
| `"What cafes are near me?"` | Single-mall response — no cross-mall trigger |

---

## Troubleshooting

**Both malls not loaded at startup**
Check `BACKEND_MALL_IDS` in `backend/.env` — must be comma-separated with no spaces around the comma, e.g. `al_nakheel_plaza_28,al_nakheel_plaza_13`.

**`domain` shows `shopping` instead of `cross_mall` for a cross-mall question**
The regex did not match. Check that the query contains explicit cross-mall language (e.g. "Mall of Arabia", "other mall", "both malls", "across malls", "any Cenomi mall"). Ambiguous phrasing like "Do you have Zara there?" will fall through to the LLM classifier, which may return `shopping` if there is no prior context establishing "there" as another mall.

**`KeyError` on mall_id**
The `mall_id` in the request body does not match one of the loaded IDs. Run the health check to see which IDs are loaded.
