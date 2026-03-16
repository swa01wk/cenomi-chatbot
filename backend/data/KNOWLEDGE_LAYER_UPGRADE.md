# Mall Knowledge Layer Upgrade — Implementation Notes

## Overview

This upgrade transforms the mall knowledge model from a basic entity catalog
into a concierge intelligence layer optimized for AI Findr-style responses:
short contextual intros, curated shortlists with per-option reasons,
narrowing follow-ups, and strong topic continuity across turns.

---

## Schema Changes Summary

### 1. SemanticProfile (semantic.py)

Three new nested models added to `SemanticProfile`:

| Field | Type | Purpose |
|-------|------|---------|
| `concierge_reasons` | `ConciergeReasons` | Pre-written one-liners per audience/scenario (short_reason, kid_reason, budget_reason, couple_reason, family_reason, quick_reason, movie_reason, gift_reason, when_best_used, avoid_when, followup_hint) |
| `response_fit_tags` | `list[str]` | Indicates which response shapes this entity fits: shortlist, itinerary, comparison, standalone, gift_formula |
| `topic_continuity` | `TopicContinuity` | topic_domain, topic_subdomain, continuity_keywords, refinement_keywords, likely_followups |
| `price_expectation` | `PriceExpectation` | price_confidence_mode (exact/range_only/unknown), rough_price_positioning, price_expectation_notes, price_question_response_hint |

All fields have defaults — existing profiles remain valid without the new fields.

### 2. ScenarioPlaybook (playbook.py)

New fields on `ScenarioPlaybook`:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `continuity_priority` | `int` (0-100) | 50 | Higher = stickier. Audience+product playbooks: 70-90. Generic: 30-50 |
| `must_preserve_topic` | `bool` | False | Lock follow-ups to this playbook's domain |
| `must_preserve_audience` | `bool` | False | Persist audience context (kids, couple, family) across turns |
| `excluded_semantic_tags` | `list[str]` | [] | Tags that disqualify entities from this playbook |
| `narrowing_question_hint` | `str` | "" | Ready-to-use follow-up question |
| `topic_domain` | `str` | "" | e.g. shopping, dining, entertainment, planning, information |
| `topic_subdomain` | `str` | "" | e.g. kidswear_outerwear, kid_friendly_food |
| `continuity_keywords` | `list[str]` | [] | Keywords that keep the user in this playbook |
| `refinement_keywords` | `list[str]` | [] | Keywords that narrow within (not switch away from) this playbook |
| `likely_followups` | `list[str]` | [] | Anticipated next questions |

All fields have defaults — existing playbooks remain valid.

---

## Playbook Inventory (25 total)

### Retained (14, now with new fields populated)
- pb-family-visit, pb-solo-visit, pb-anniversary
- pb-gift-girlfriend, pb-gift-family
- pb-movie-food, pb-kid-movie-food, pb-quick-lunch
- pb-dessert-combo, pb-date-plan
- pb-budget-family, pb-luxury-shopping, pb-last-minute-gift
- pb-child-activity-parents

### New (11, requested in Part 2)
- **pb-kids-jackets** — product-specific kidswear outerwear (priority 80)
- **pb-affordable-kids-jackets** — budget refinement of kids_jackets (priority 90)
- **pb-food-with-child** — dining with a child present (priority 70)
- **pb-family-movie-rec** — family movie recommendation (priority 70)
- **pb-quick-food-before-movie** — pre-movie quick food (priority 75)
- **pb-cinema-vs-mall-food** — cinema snacks vs mall restaurants (priority 65)
- **pb-price-expectation** — handling price questions without live data (priority 60)
- **pb-girlfriend-family-shopping** — dual-audience gift shopping (priority 70)
- **pb-gift-for-child** — child gift recommendations (priority 75)
- **pb-family-food-budget** — affordable family dining (priority 70)
- **pb-movie-for-child** — child-specific movie recommendation (priority 80)

---

## Ranking Precedence Rules

### Rule 1: Audience+Product > Generic
Playbooks with both an audience qualifier AND a product qualifier
outrank generic playbooks.

**Example:** `affordable_kids_jackets` (priority 90) outranks
`budget_family_outing` (priority 45) when the active topic is
jacket shopping for a child.

### Rule 2: Continuity Priority as Tie-Breaker
When multiple playbooks match the current turn, `continuity_priority`
determines which one wins. Higher = stickier.

**Priority tiers:**
- 80-90: Product+audience-specific (affordable_kids_jackets, kids_jackets, movie_for_child)
- 70-75: Audience-specific (food_with_child, gift_for_girlfriend, gift_for_child, quick_food_before_movie)
- 50-65: Scenario-generic (family_visit, date_plan, anniversary, price_expectation)
- 30-45: Context-light (solo_visit, quick_lunch, dessert_combo, budget_family)

### Rule 3: must_preserve_topic Locks the Domain
When a playbook has `must_preserve_topic: true`, follow-up turns that
contain its `continuity_keywords` or `refinement_keywords` stay
within the same playbook — even if another playbook partially matches.

**Example:** If `pb-kids-jackets` is active and the user says
"something affordable", the engine should recognize "affordable"
as a `refinement_keyword` and escalate to `pb-affordable-kids-jackets`
rather than switching to the generic `pb-budget-family`.

### Rule 4: must_preserve_audience Persists Across Turns
When `must_preserve_audience: true`, audience context (kids, couple,
family) is carried forward until the user explicitly changes it.

**Example:** If the user starts with "I'm here with my kids" and
later asks "where can I eat?", the `kid_friendly` and `family_friendly`
tags should still influence the dining recommendation.

### Rule 5: Excluded Tags as Hard Filters
`excluded_semantic_tags` removes entities from consideration entirely.
This prevents romantic restaurants from appearing in kid-focused
playbooks or luxury stores from appearing in budget playbooks.

---

## Runtime Integration Guide

### How to use `concierge_reasons`

When building a shortlist response, for each entity in the list:

1. Check the active playbook scenario (e.g., `food_with_child`)
2. Select the matching reason key:
   - food_with_child → use `kid_reason`
   - gift_for_girlfriend → use `gift_reason`
   - budget queries → use `budget_reason`
   - movie plans → use `movie_reason`
   - default → use `short_reason`
3. Inject the reason as the one-liner after the entity name
4. If the selected reason is empty, fall back to `short_reason`

### How to use `topic_continuity`

In the `interpret_turn` or `update_scene_memory` node:

1. On each user turn, check if the user's keywords match
   `continuity_keywords` of the current active playbook
2. If they match `refinement_keywords`, treat as a narrowing turn
   (stay in playbook, optionally escalate to a more specific one)
3. If they match neither, check if a new playbook matches better
4. Use `likely_followups` to generate the concierge's follow-up
   question when the response needs narrowing

### How to use `price_expectation`

In the `generate_response` or `compose_context` node:

1. When a price question is detected, look up the entity's
   `price_expectation` metadata
2. Check `price_confidence_mode`:
   - `exact`: use the number (rare — reserved for future live feeds)
   - `range_only`: use `price_question_response_hint` verbatim or paraphrase
   - `unknown`: say "I'd recommend checking at the store"
3. Always pair with current promotions/sales when available
4. Never fabricate exact prices

### How to use `narrowing_question_hint`

When the response includes recommendations but the query was
somewhat broad, append the playbook's `narrowing_question_hint`
as a follow-up. This drives AI Findr-style conversational refinement.

---

## Data File Inventory

| File | Records | Changes |
|------|---------|---------|
| `data/semantic/cenomi_mall_01.json` | 20 profiles, 15 rules | All profiles enriched with concierge_reasons, topic_continuity, price_expectation, response_fit_tags |
| `data/playbooks/cenomi_mall_01.json` | 25 playbooks | 14 existing updated with new fields; 11 new playbooks added |
| `app/models/semantic.py` | 4 models | Added ConciergeReasons, TopicContinuity, PriceExpectation; extended SemanticProfile |
| `app/models/playbook.py` | 1 model | Extended ScenarioPlaybook with 10 new fields |

---

## Backward Compatibility

All new fields have defaults. Existing code that loads the old schema
will continue to work — the new fields simply won't be populated until
the data files are updated. The Pydantic models are additive-only.

---

## Tag Taxonomy Version

Updated from `1.0` to `2.0` in the semantic data file to reflect
the new enrichment structure. The `ALL_SEMANTIC_TAGS` list in
`semantic.py` is unchanged — no new tag vocabulary was needed.
The new capability comes from the structured metadata fields, not
new tags.
