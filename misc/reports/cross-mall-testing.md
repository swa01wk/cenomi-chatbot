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

## 8. Factual-path cross-mall queries (v1.5+)

Starting in v1.5, cross-mall brand queries flow through the factual pipeline (`resolve_fact_scope → compose_fact_response_context → generate_response`). The response always renders two labelled sections. Use the `debug=true` flag to verify the scope.

### Confirm `scope = cross_mall_availability` in debug output

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Which of your malls has Zara?",
    "mall_id": "al_nakheel_plaza_28",
    "tenant_id": "al_nakheel_plaza_28",
    "debug": true
  }' | python3 -c "
import sys, json
d = json.load(sys.stdin)
dbg = d.get('debug', {})
print('flow_type :', dbg.get('flow_type'))
print('scope     :', dbg.get('fact_scope'))
print()
print('Response preview:')
print(d.get('message', '')[:300])
"
```

Expected:

```
flow_type : factual
scope     : cross_mall_availability
```

### Verify AT YOUR CURRENT MALL / AT OTHER CENOMI MALLS structure

The LLM prompt receives pre-formatted context with `AT YOUR CURRENT MALL` before `AT OTHER CENOMI MALLS`. The response should mirror this ordering. Validate with any brand that exists in both malls (e.g. Zara, Starbucks):

| Query | Expected response structure |
|---|---|
| `"Which of your malls has Starbucks?"` | Leads with home-mall Starbucks details, then "Also at Mall of Arabia" |
| `"Is Zara available at both your malls?"` | Home mall confirmed first, then other mall |
| `"Does Al Nakheel Plaza have Popeyes?"` (from Mall of Arabia) | AT YOUR CURRENT MALL: not found. AT OTHER CENOMI MALLS: not found at Al Nakheel Plaza |

### Scope routing unit test

Run without LLM or live server:

```bash
cd backend
source .venv/bin/activate
pytest tests/test_cross_mall_v1.py::TestCrossMallFlowHints -v
```

Expected output — both tests pass:

```
tests/test_cross_mall_v1.py::TestCrossMallFlowHints::test_single_mall_brand_is_not_cross_mall_scope PASSED
tests/test_cross_mall_v1.py::TestCrossMallFlowHints::test_cross_mall_search_hint PASSED
```

---

## 9. Follow-up cross-mall queries and brand resolution

When a visitor asks a follow-up like `"where else can I find it?"`, `resolve_cross_mall_brand_query` resolves the brand from prior context without requiring the brand name to be repeated.

### Fallback chain

1. Stripped message (boilerplate phrases removed) — catches `"which malls have Starbucks?"`
2. `fact_query_entity` — set by retrieval on single-mall brand availability turns
3. `scene.last_resolved_entity` — set by `update_memory` after a prior brand mention
4. `scene.active_shortlist[0]` — first entity from a prior shortlist

### Test the fallback chain (unit tests, no LLM)

```bash
cd backend
source .venv/bin/activate
pytest tests/test_cross_mall_v1.py::TestResolveCrossMallBrandQuery -v
```

Expected:

```
tests/test_cross_mall_v1.py::TestResolveCrossMallBrandQuery::test_where_else_uses_last_resolved_entity PASSED
tests/test_cross_mall_v1.py::TestResolveCrossMallBrandQuery::test_empty_when_only_pronouns PASSED
tests/test_cross_mall_v1.py::TestResolveCrossMallBrandQuery::test_explicit_brand_in_message PASSED
tests/test_cross_mall_v1.py::TestResolveCrossMallBrandQuery::test_fact_query_entity_fallback PASSED
tests/test_cross_mall_v1.py::TestResolveCrossMallBrandQuery::test_active_shortlist_fallback PASSED
```

### Live follow-up session test

Send two turns with the same `session_id`. The second turn does not name the brand.

```bash
SESSION_ID="test-followup-$(date +%s)"

# Turn 1 — establish brand in scene
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"Is Zara here at Al Nakheel?\",
    \"mall_id\": \"al_nakheel_plaza_28\",
    \"session_id\": \"$SESSION_ID\"
  }" | python3 -m json.tool

# Turn 2 — follow-up without naming the brand
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"where else can I find it?\",
    \"mall_id\": \"al_nakheel_plaza_28\",
    \"session_id\": \"$SESSION_ID\",
    \"debug\": true
  }" | python3 -m json.tool
```

**Expected behaviour (Turn 2):** Response covers Zara across both malls with `AT YOUR CURRENT MALL` / `AT OTHER CENOMI MALLS` structure. The debug payload should show `scope: cross_mall_availability` and the resolved brand in the fact context.

### Edge cases

| Scenario | Expected behaviour |
|---|---|
| Follow-up with only a pronoun (`"it"`, `"this"`, `"that store"`) and no prior scene entity | Brand query resolves to empty string; response gracefully states no specific brand could be identified and prompts the visitor to name it |
| Follow-up after a multi-entity shortlist (e.g. dining suggestions) | `active_shortlist[0]` used as the brand; response may not be precise — visitor should clarify |
| Cross-mall query in a fresh session with no brand in message | Empty brand → graceful no-results response |

---

## Troubleshooting

**Both malls not loaded at startup**
Check `BACKEND_MALL_IDS` in `backend/.env` — must be comma-separated with no spaces around the comma, e.g. `al_nakheel_plaza_28,al_nakheel_plaza_13`.

**`domain` shows `shopping` instead of `cross_mall` for a cross-mall question**
The regex did not match. Check that the query contains explicit cross-mall language (e.g. "Mall of Arabia", "other mall", "both malls", "across malls", "any Cenomi mall"). Ambiguous phrasing like "Do you have Zara there?" will fall through to the LLM classifier, which may return `shopping` if there is no prior context establishing "there" as another mall.

**`scope` shows `brand_availability` instead of `cross_mall_availability`**
The cross-mall flow hint in `interpret_turn` did not fire. Confirm `intent.domain == "cross_mall"` in the debug output. If the domain is correct but the scope is wrong, check that `resolve_fact_scope.py` has the cross-mall keyword patterns (updated in v1.5).

**`KeyError` on mall_id**
The `mall_id` in the request body does not match one of the loaded IDs. Run the health check to see which IDs are loaded.
