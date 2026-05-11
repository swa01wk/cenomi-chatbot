# Cenomi Mall Concierge — v1.7 detailed reference

**Release:** `v1.7`  
**Canonical date:** 2026-04-24  
**Audience:** Engineers, QA, product, and operators integrating or reviewing this release.

This document expands on the summary in [`CHANGELOG.md`](../CHANGELOG.md) § v1.7. For an executive line list, prefer the changelog entry.

---

## 1. Executive summary

Version 1.7 adds **full Arabic + English bilingual operation** across API, prompting, streaming metadata, UI (including RTL), and smalltalk pools. It also improves **merchant presentation** via **tenant cards**, **Parsed CTA pills** derived from markdown bold spans, an embedded **Mappedin** map drawer, strengthens **evaluation and regression tooling**, and refreshes select **mall 28 knowledge** (semantic/playbook assets).

---

## 2. Language architecture

### 2.1 State field

| Field | Type | Meaning |
| --- | --- | --- |
| `ConciergeState.detected_language` | `str`, `"en"` \| `"ar"` | Per-turn language used by all prompt assembly and auxiliary LLM paths. Defined and documented on `ConciergeState` in `backend/app/models/state.py`. |

### 2.2 Detection vs override

Resolution happens in **`load_session`** (`backend/app/nodes/load_session.py`):

1. If the client already supplied a language on `ConciergeState` (seeded from the API request — see §3), **`load_session`** uses that value (`state.detected_language`).
2. If the client sends an **empty string** placeholder, **`load_session`** replaces it via **`detect_language(normalized)`** from `backend/app/utils/language.py`.

Therefore:

- **`language` omitted or `null` in JSON** behaves as falsy → typically becomes `""` in `clean_context` → **`load_session`** auto-detects.
- **`language: "ar"` | `"en"`** locks the pipeline to that locale for the turn (and persists through state if the session layer stores downstream — client override wins over script ratio on each new turn when provided).

### 2.3 `detect_language()` algorithm

Implementer: **`backend/app/utils/language.py`** (no ML dependencies):

- Filters to “script-bearing” characters: Arabic block **U+0600–U+06FF** or Latin alphabet characters.
- Computes the fraction of those characters that are Arabic.
- If **fraction ≥ 0.25**, classifies **`"ar"`**; otherwise **`"en"`**.
- Empty or whitespace-only input → **`"en"`**.

This threshold avoids misclassifying English messages that contain a single Arabic name or token.

### 2.4 Where language is consumed (backend)

| Area | Behaviour |
| --- | --- |
| **`clean_context_builder`** | Passes `"detected_language"` from API into graph initial state; empty means “defer to `load_session`”. (`backend/app/services/clean_context.py`) |
| **`load_session`** | Sets **`detected_language`** on state after merge of override vs detection; logs `lang=` in `_trace_summary`. |
| **`get_concierge_system_prompt`** / **`get_mall_overview_system_prompt`** | When `language == "ar"`, append **`_ARABIC_LANGUAGE_INSTRUCTION`** (MSA retail register, brand-name Latin exception, emoji rules, bold chip rules using Arabic conjunctions **`،`** / **`أو`**, RTL note, banned hollow openers). (`backend/llm/prompts/concierge_prompt.py`) |
| **`generate_response`** | Calls prompt builders with `language=state.detected_language`; additional closure/auxiliary prompts add an **Arabic suffix** where the guest is in Arabic so sub-calls emit MSA-only output (pattern: `arabic_suffix` / `lang` checks near helper LLM prompts). (`backend/app/nodes/generate_response.py`) |
| **`smalltalk`** | Parallel **`_AR_*`** static response pools for crisis, identity, greetings (three progressive tiers), thanks, emotional, HOWRU pools; farewell uses LLM + inline Arabic directive when `ar`. (`backend/app/nodes/smalltalk.py`) |
| **`get_cta_suggestions`** | Returns **`_CTA_CHIP_LABELS_AR`** when language is **`"ar"`**, else English labels. (`backend/app/services/cta_generator.py`) |
| **Blocking + streaming exits** | `get_cta_suggestions(..., language=result.detected_language)` attaches localized chip text to **`ChatResponse`** / SSE **`done`**. |

### 2.5 Query expansion and Arabic

Short-query **`expand_short_query`** (`backend/llm/prompts/query_expander.py`) expands terse English-heavy tokens for **retrieval embeddings**. Arabic-heavy user queries still flow through **`normalized_user_message`** for classification/UI; expansions primarily target sparse English shorthand. Operational guidance: bilingual UX depends on **`detected_language`** + prompts; retrieval quality for Arabic-heavy text may rely more on classifier + retrieval stack than expansion map keys.

---

## 3. API contract

### 3.1 `ChatRequest.language`

Model: **`backend/app/models/api.py`** — **`ChatRequest`**

```text
language: str | None = Field(
    default=None,
    ...
    Accepted values: 'ar' (Arabic), 'en' (English).
    When provided, overrides server-side auto-detection.
    When omitted, language is auto-detected from the message text.
)
```

**Integration:**

- Frontend sends **`language`** on every **`streamMessage`** / blocking chat call (`frontend/src/types/api.ts`, `frontend/src/hooks/useChat.ts`).
- Persistence: **`localStorage`** key **`cenomi_language`** mirrors UI choice (`useChat.ts`).

### 3.2 Blocking response

**`handle_chat`** in `backend/app/services/concierge.py` merges `request.language` into initial state via **`detected_language`**: falsy becomes `""` so **`load_session`** can detect.

Suggestions on the packaged response use **`result.detected_language`** for **`get_cta_suggestions`** (same pattern as streaming).

### 3.3 Streaming response (SSE)

**`POST /api/chat/stream`** (`backend/app/api/stream.py`):

| Event | Relevant multilingual fields |
| --- | --- |
| **`done`** | **`suggestions`**: localized CTA chips. **`tenants`**: card payloads for the UI (see §5). **`debug`** (if enabled): unchanged shape; inspect `scene_summary` as before. |

**`detected_language`** is carried on the hydrated result state post-graph; SSE uses **`result.detected_language`** for suggestions.

---

## 4. Frontend behaviour

### 4.1 Document and layout direction

**`frontend/src/App.tsx`**:

- `useEffect` sets **`document.documentElement.lang`** and **`document.documentElement.dir`** (`"rtl"` when Arabic).
- Outer layout **`div`** uses **`dir={chat.language === "ar" ? "rtl" : "ltr"}`**.

### 4.2 Language toggle

**`frontend/src/components/TopBar.tsx`**: switches between **`en`** and **`ar`**; tooltips indicate target language (**Switch to Arabic** / **التبديل إلى الإنجليزية**).

### 4.3 Copy bundles

**`frontend/src/lib/constants.ts`**:

- **`SUGGESTED_QUERIES`** / **`SUGGESTED_QUERIES_AR`** via **`getSuggestedQueries(language)`**.
- **`FEEDBACK_REASON_LABELS`** / **`FEEDBACK_REASON_LABELS_AR`** via **`getFeedbackReasonLabels(language)`**.
- **`UI_COPY_AR`**: centralized Arabic strings for welcome, mall picker, placeholders, typing label, footer actions, debug labels.

Screens/components wired to **`language`**: **`ChatPage`**, **`ChatInput`**, **`FeedbackControls`**, **`TypingIndicator`**, **`MallPickerScreen`**, **`WelcomeScreen`**.

### 4.4 CTA pills (bold parsing)

**Implementer:** **`extractCtaPills`** in **`frontend/src/components/ChatMessage.tsx`**.

- Iterates Markdown paragraphs (**split on blank lines**) **from bottom to top** until a paragraph yields **≥ 2** pills.
- Parses `**segment**`; splits inner text on commas (Latin **`,`** and Arabic **`،`**), **`or`** / **`and`**, **`أو`** / **`و`** (Unicode aware).
- Renders horizontally scrollable **pill buttons** that call **`onSend(pill)`** with pill text verbatim.

Purpose: aligns UI quick picks with whichever language the assistant used inside bold markers.

### 4.5 Tenant cards and map modal

**Types:** `TenantCard` in `frontend/src/types/chat.ts`:

```typescript
interface TenantCard {
  name: string;
  category?: string;
  image?: string;
  floor?: string;
  zone?: string;
  unit_number?: string;
  map_url?: string;
}
```

**Hydration (`useChat.ts`):** On **`done`**, **`tenantCards`** = **`event.tenants`** filtered to entries with a **`name`**.

**Rendering (`ChatMessage.tsx`):**

- **`TenantCardItem`**: thumbnail (or placeholder), badge, **`MapPin`** when **`map_url`** exists; keyboard-accessible **`role="button"`** when clickable.
- **Map drawer:** **`MapModal`** opens when a card has **`map_url`**.

**`MapModal`** (`frontend/src/components/MapModal.tsx`):

- Strips fragment from base URL then navigates Mappedin **`#/profile?location=`** (`encodeURIComponent(unit_number || storeName)`).
- **Escape** closes; backdrop click closes.
- **`postMessage`**: listens for **`app-loaded`**, then sends **`set-state`** with **`location`** payload as compatibility fallback.

**Backend tenant payload** (`backend/app/api/stream.py`): built from **`result.context.selected_entities`**, enriched via **`mall_ctx.get_entity_by_id`** for **`banner`/`brand_logo`** and **`map_url`** = **`mall_ctx.get_map_url()`** for each card row.

Cards appear when **`selected_entities`** is populated (concierge flows with curated entities).

---

## 5. Supporting scripts and documentation

High-signal artefacts introduced or heavily used for v1.7:

| Path | Purpose |
| --- | --- |
| **`backend/scripts/run_arabic_tests.py`** | Translates English suite strings to Arabic (gpt-4o-mini batches); caches **`backend/scripts/arabic_translations_cache.json`**; posts **`language: "ar"`** per request to **`/api/chat`**. Supports suites: smoke, broken, complex, session, new-malls, v16 (`--suite`, `--no-translate`). Passthrough garbage queries optionally stay English. |
| **`docs/evaluation-methodology.md`** | Operator-facing evaluation methodology (see repo for scope). |
| **`docs/pipeline-stability-and-query-capability.md`** | Pipeline stability / capability reference. |
| **`backend/scripts/run_pipeline_evaluation.py`** | Pipeline evaluation batches (paired with evaluator config in repo). |
| **`backend/scripts/run_comparison_queries.py`** | Comparative query runners for regression snapshots. |
| **`backend/scripts/generate_evaluation_report.py`** | Consolidates evaluation artefacts to reports. |
| **`generate_client_report.py`** | Repo-root client report helper (consumes exported run data). |
| **`backend/scripts/audit_session.py`** | Session audit / inspection tooling (expanded for evaluator workflows). |
| **`backend/tests/test_client_feedback.py`**, **`backend/tests/test_concierge_scenarios.py`** | New or expanded behavioural coverage aligned with bilingual + UX regressions. |

Run artefacts (JSON/Markdown) may appear under **`test-results/`** or **`test-results-hs/`** when pipelines are executed; those are operational outputs rather than compile-time deps.

---

## 6. Data changes

Incremental updates committed for **Al Nakheel Plaza (mall 28)**:

- **`backend/data/semantic/al_nakheel_plaza_28.json`**
- **`backend/data/playbooks/al_nakheel_plaza_28.json`**

Treat these like any semantic/playbook rollout: QA high-value intents after deploy and compare eval reports pre/post swap.

---

## 7. Migration & checklist

### 7.1 Backward compatibility

- Existing clients omitting **`language`** continue to work; language is inferred from body text ratio.
- English-only integrations need **no payload change**.

### 7.2 New integrations

1. Decide whether to **fix UI language** (send **`language`**) vs **trust auto-detection**.
2. If fixing: send **`language: "ar"` | `"en"`** on **every** **`/api/chat`** and **`/api/chat/stream`** request.
3. Render **`done.suggestions`** as chips (optional UX); same field is localized.
4. If using streaming cards: hydrate **`tenantCards`** from **`done.tenants`**; **`map_url`** may be duplicated per tenant but points at mall Mappedin root.

### 7.3 QA checklist (smoke)

- [ ] Toggle **EN ⇄ عربي**; confirm **`cenomi_language`** persistence across reload.
- [ ] Confirm **`document.documentElement.dir`** **`rtl`** in Arabic mode.
- [ ] Plain Arabic greeting → concierge answers in Arabic (system prompt path).
- [ ] **`language:"en"`** + Arabic-script message — confirm override behaves as enforced (explicit lock).
- [ ] SSE stream: **`done.suggestions`** Arabic when **`language":"ar`**.
- [ ] Recommendation turn with **`selected_entities`**: cards visible, modal opens Map, Escape closes modal.
- [ ] **`run_arabic_tests.py`** (with backend up) completes or spot-check **`--suite smoke`**.

---

## 8. File index (principal touchpoints)

| Layer | Paths |
| --- | --- |
| State / API models | `backend/app/models/state.py`, `backend/app/models/api.py` |
| Detection | `backend/app/utils/language.py` |
| Session assembly | `backend/app/services/clean_context.py`, `backend/app/services/concierge.py` |
| Graph entry | `backend/app/nodes/load_session.py` |
| Prompting | `backend/llm/prompts/concierge_prompt.py`, `backend/llm/prompts/query_expander.py` |
| Generation | `backend/app/nodes/generate_response.py` |
| Smalltalk | `backend/app/nodes/smalltalk.py` |
| CTA chips | `backend/app/services/cta_generator.py` |
| Streaming | `backend/app/api/stream.py` |
| Frontend shell | `frontend/src/App.tsx`, `frontend/src/hooks/useChat.ts`, `frontend/src/api/client.ts` |
| Frontend UI | `frontend/src/components/TopBar.tsx`, `ChatPage.tsx`, `ChatInput.tsx`, `ChatMessage.tsx`, `FeedbackControls.tsx`, **`MapModal.tsx`**, `frontend/src/styles/index.css` |
| Frontend copy | `frontend/src/lib/constants.ts`, `frontend/src/types/api.ts`, `frontend/src/types/chat.ts` |

---

## 9. References

- Changelog slice: **`CHANGELOG.md`**, **`[v1.7] — 2026-04-24`**.
- Prior behaviour baseline: **`[v1.6] — 2026-03-31`** (LLM-first routing, scene memory, streaming CTAs foundation).
