#!/usr/bin/env python3
"""
generate_mall_data.py
=====================
Full end-to-end pipeline: output_mall_XX.json → all 5 intelligence layers.

Takes a raw Cenomi mall output file and generates:
  1. data/canonical/{mall_id}.json          (deterministic ETL, no LLM)
  2. data/semantic/{mall_id}.json           (LLM-synthesized enrichment)
  3. data/playbooks/{mall_id}.json          (LLM-synthesized playbooks)
  4. data/tenant_config/{mall_id}.json      (LLM-synthesized config)
  5. data/context_packs/{mall_id}_context.json  (LLM-synthesized context pack)

Usage:
  python scripts/generate_mall_data.py ../../output_mall_28.json
  python scripts/generate_mall_data.py ../../output_mall_13.json
  python scripts/generate_mall_data.py ../../output_mall_28.json --steps semantic playbooks
  python scripts/generate_mall_data.py ../../output_mall_28.json --model gpt-4.1 --dry-run

Requirements:
  OPENAI_API_KEY or BACKEND_OPENAI_API_KEY must be set (in .env or environment).
  python-dotenv, langchain-openai installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"

# Reference examples — fully enriched to v2 schema
EXAMPLE_CANONICAL = DATA_DIR / "canonical" / "al_nakheel_plaza_28.json"
EXAMPLE_SEMANTIC   = DATA_DIR / "semantic"   / "al_nakheel_plaza_28.json"
EXAMPLE_PLAYBOOKS  = DATA_DIR / "playbooks"  / "al_nakheel_plaza_28.json"
EXAMPLE_TENANT_CFG = DATA_DIR / "tenant_config" / "al_nakheel_plaza_28.json"
EXAMPLE_CONTEXT    = DATA_DIR / "context_packs" / "al_nakheel_plaza_28_context.json"
TENANT_DEFAULTS    = DATA_DIR / "tenant_config" / "tenant_defaults.json"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  ✓ Wrote {path.relative_to(BACKEND_DIR)}")


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.index("\n")
        raw = raw[first_nl + 1:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    return raw.strip()


# ---------------------------------------------------------------------------
# STEP 1: Deterministic conversion  output_mall_XX.json → canonical
# ---------------------------------------------------------------------------
# Re-use the existing convert_to_canonical logic by importing from it.
# If the import fails (e.g. running from a different CWD), fall back to inline.
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from convert_to_canonical import convert as _convert_to_canonical  # type: ignore
    _HAS_CONVERTER = True
except ImportError:
    _HAS_CONVERTER = False


def step_canonical(raw_data: dict, output_dir: Path) -> tuple[dict, str]:
    """Convert raw output_mall data to canonical JSON. Returns (canonical, mall_id)."""
    if not _HAS_CONVERTER:
        raise RuntimeError(
            "convert_to_canonical.py not found. "
            "Run from backend/scripts/ or ensure convert_to_canonical is importable."
        )

    import tempfile
    # Write raw data to a temp file so convert() can read it
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(raw_data, tf, ensure_ascii=False)
        tmp_path = Path(tf.name)

    try:
        canonical, mall_id = _convert_to_canonical(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    out_path = output_dir / "canonical" / f"{mall_id}.json"
    _save_json(out_path, canonical)
    print(f"  Mall:     {canonical['mall_profile']['name']} ({mall_id})")
    print(f"  Stores:   {len(canonical.get('stores', []))}")
    print(f"  Dining:   {len(canonical.get('dining', []))}")
    print(f"  Cinemas:  {len(canonical.get('cinemas', []))}")
    print(f"  Movies:   {len(canonical.get('movies', []))}")
    return canonical, mall_id


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------
def _get_llm(model: str):
    from langchain_openai import ChatOpenAI  # type: ignore
    return ChatOpenAI(model=model, temperature=0.3, max_tokens=16384)


def _llm_json(llm, system: str, user: str, tag: str) -> dict | list:
    print(f"  ⏳ Generating {tag} …")
    resp = llm.invoke([
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ])
    raw = _strip_fences(resp.content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Best-effort recovery for truncated arrays/dicts
        snippet = raw[:exc.pos].rstrip().rstrip(",")
        if raw.lstrip().startswith("["):
            depth = snippet.count("[") - snippet.count("]")
            try:
                return json.loads(snippet + "]" * max(depth, 1))
            except json.JSONDecodeError:
                pass
        if raw.lstrip().startswith("{"):
            depth = snippet.count("{") - snippet.count("}")
            try:
                return json.loads(snippet + "}" * max(depth, 1))
            except json.JSONDecodeError:
                pass
        raise


def _entity_summary(canonical: dict) -> str:
    lines: list[str] = []
    mall = canonical.get("mall_profile", {})
    lines.append(f"Mall: {mall.get('name')} ({mall.get('city')}, {mall.get('country')})")
    lines.append(f"Overview: {mall.get('overview_summary', '')}")
    lines.append(f"Floors: {mall.get('floors')}")
    lines.append(f"Zones: {[z['name'] for z in mall.get('zones', [])]}")
    for section in ("stores", "dining", "cinemas", "movies", "services"):
        items = canonical.get(section, [])
        if items:
            names = [i.get("name") or i.get("title", "?") for i in items]
            lines.append(f"{section} ({len(items)}): {', '.join(names[:20])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STEP 2: Semantic enrichment
# ---------------------------------------------------------------------------
SEMANTIC_SYSTEM = textwrap.dedent("""\
You are a semantic data engineer for a mall concierge AI.

Generate a semantic enrichment file for the given mall. The file has three sections:

1. enrichment_rules — bulk rules that map entity attributes (category, subcategory,
   dining_style, zone, price_range) to semantic tags, audience_fit, vibe, outing_fit,
   gift_fit, and priority_boosts. Cover every category/zone present.

2. enrichment_rules_v2 — scenario-specific override rules. Must include at minimum:
   - er-cinema-zone: Cinema entities → entertainment_anchor, showtime_lookup, movie_night,
     date_night, cinema_now + scenario priority boosts
   - er-before-movie-foodcourt: Food Court dining → near_cinema, before_movie, quick_stop
   - er-before-movie-kiosk: Kiosk Row snacks → near_cinema, before_movie, grab_and_go
   - er-after-movie-reward: Dessert/cafe → after_movie, reward_stop, near_cinema
   - er-kids-entertainment-plan: Kids Entertainment → child_relief_anchor,
     entertainment_anchor, stroller_friendly
   - If animation/family films exist: er-movie-kids-animation → kid_friendly, family_movie
   - If adult genre films exist: er-movie-adults → adult_movie, date_night

3. profiles — per-entity semantic profiles for EVERY store, dining, and cinema entity.
   Each profile must include:
   - entity_id, entity_type
   - semantic_tags (10-15 specific tags)
   - audience_fit (who it appeals to)
   - vibe (atmosphere/feeling)
   - price_band
   - outing_fit (scenario-level fit)
   - gift_fit, meal_fit
   - routing_tags (zone/proximity hints)
   - when_to_recommend, avoid_if
   - priority_scores (per-scenario floats 0.0–1.0) for these scenarios:
     family_visit_plan, solo_visit_plan, anniversary_plan, gift_for_girlfriend,
     gift_for_family, movie_plus_food, kid_friendly_movie_plus_food, quick_lunch,
     shopping_plus_dessert_combo, date_plan, budget_family_outing,
     luxury_shopping_plan, last_minute_gift, child_activity_while_parents_shop,
     movie_showtime_lookup, movie_night_plan, family_movie_plan,
     kids_entertainment_plan, family_shopping_with_child
   - concierge_notes (specific, mentioning actual entity name + pairing suggestions)

   Cinema entities additionally need:
   - now_showing_summary: one-line summary of current lineup
   Output ONLY valid JSON. No commentary outside the JSON.
""")


def step_semantic(canonical: dict, llm, output_dir: Path) -> dict:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_SEMANTIC)

    example_condensed = {
        "mall_id": example["mall_id"],
        "tag_taxonomy_version": example["tag_taxonomy_version"],
        "enrichment_rules": example["enrichment_rules"][:3],
        "enrichment_rules_v2": example.get("enrichment_rules_v2", [])[:3],
        "profiles": example["profiles"][:2],
    }

    # Build a focused entity summary for the prompt
    movie_summary = [
        {
            "title": m.get("title"),
            "genre": m.get("genre"),
            "status": m.get("status"),
            "language": m.get("language"),
        }
        for m in canonical.get("movies", [])
        if m.get("status") == "SHOWING_NOW"
    ][:10]

    all_entities = (
        canonical.get("stores", [])
        + canonical.get("dining", [])
        + canonical.get("cinemas", [])
    )

    BATCH = 20
    all_profiles: list[dict] = []

    # Step A: enrichment rules (both v1 and v2)
    rules_user = textwrap.dedent(f"""\
    EXAMPLE (condensed for structure reference):
    ```json
    {json.dumps(example_condensed, indent=2, ensure_ascii=False)}
    ```

    Generate BOTH the enrichment_rules array AND the enrichment_rules_v2 array for mall_id "{mall_id}".

    Mall context:
    ```json
    {json.dumps({
        "mall_profile": {
            "name": canonical["mall_profile"].get("name"),
            "city": canonical["mall_profile"].get("city"),
            "zones": [z["name"] for z in canonical["mall_profile"].get("zones", [])],
            "overview_summary": canonical["mall_profile"].get("overview_summary", ""),
        },
        "store_categories": sorted({s.get("category", "") for s in canonical.get("stores", [])} - {""}),
        "dining_styles": sorted({d.get("dining_style", "") for d in canonical.get("dining", [])} - {""}),
        "now_showing_movies": movie_summary,
    }, indent=2, ensure_ascii=False)}
    ```

    Output a JSON object with exactly two keys: "enrichment_rules" (array) and "enrichment_rules_v2" (array).
    """)

    rules_system = SEMANTIC_SYSTEM + "\nOutput a JSON object with keys 'enrichment_rules' and 'enrichment_rules_v2'."
    rules_result = _llm_json(llm, rules_system, rules_user, "semantic/rules")
    if isinstance(rules_result, list):
        enrichment_rules = rules_result
        enrichment_rules_v2 = []
    else:
        enrichment_rules = rules_result.get("enrichment_rules", [])
        enrichment_rules_v2 = rules_result.get("enrichment_rules_v2", [])

    # Step B: profiles in batches
    profile_system = SEMANTIC_SYSTEM + "\nOutput ONLY a JSON array of profile objects."
    profile_example = example["profiles"][:2]

    for batch_start in range(0, len(all_entities), BATCH):
        batch = all_entities[batch_start: batch_start + BATCH]
        batch_num = batch_start // BATCH + 1
        total_batches = (len(all_entities) + BATCH - 1) // BATCH

        profile_user = textwrap.dedent(f"""\
        EXAMPLE profiles (for schema reference):
        ```json
        {json.dumps(profile_example, indent=2, ensure_ascii=False)}
        ```

        Generate semantic profiles for batch {batch_num}/{total_batches} ({len(batch)} entities).
        Mall: "{mall_id}" ({canonical['mall_profile'].get('city')}).

        Current movie lineup (SHOWING_NOW): {json.dumps([m['title'] for m in movie_summary], ensure_ascii=False)}

        ENTITIES:
        ```json
        {json.dumps(batch, indent=2, ensure_ascii=False)}
        ```

        For cinema entities, include now_showing_summary and routing_tags.
        For kids entertainment entities, set child_relief_anchor tag and boost kids_entertainment_plan.
        Output ONLY a JSON array of profile objects.
        """)

        batch_profiles = _llm_json(llm, profile_system, profile_user,
                                   f"semantic/profiles {batch_num}/{total_batches}")
        if isinstance(batch_profiles, list):
            all_profiles.extend(batch_profiles)
        elif isinstance(batch_profiles, dict):
            all_profiles.extend(batch_profiles.get("profiles", []))

    result = {
        "mall_id": mall_id,
        "tag_taxonomy_version": "1.0",
        "enrichment_rules": enrichment_rules,
        "enrichment_rules_v2": enrichment_rules_v2,
        "profiles": all_profiles,
    }
    _save_json(output_dir / "semantic" / f"{mall_id}.json", result)
    return result


# ---------------------------------------------------------------------------
# STEP 3: Playbooks
# ---------------------------------------------------------------------------
PLAYBOOKS_SYSTEM = textwrap.dedent("""\
You are a concierge scenario designer for a mall AI assistant.

Generate 28 scenario playbooks for the given mall. Cover ALL of these scenarios:
  pb-mall-overview, pb-brand-lookup, pb-category-shopping, pb-family-shopping,
  pb-solo-browse, pb-gift-girlfriend, pb-gift-family, pb-family-food, pb-quick-bite,
  pb-movie-showtime-lookup, pb-movie-night, pb-family-movie, pb-kids-entertainment,
  pb-movie-food, pb-kid-movie-food, pb-date-night, pb-shopping-dessert,
  pb-budget-family, pb-luxury-shopping, pb-anniversary, pb-last-minute-gift,
  pb-child-activity, pb-service-lookup, pb-route-refinement, pb-quick-snack,
  pb-teen-hangout, pb-exploration, pb-after-movie

Each playbook MUST include all of these fields:
  playbook_id, name, description,
  trigger_domains (list of domain strings),
  trigger_sub_intents (list of sub_intent strings),
  required_scene_signals (list — can be empty),
  optional_scene_signals (list),
  required_semantic_signals (list),
  ranking_boost_tags (list of semantic tags to boost),
  ranking_penalty_tags (list of semantic tags to penalize),
  excluded_tags (list — can be empty),
  preferred_response_shape ("direct_fact" | "concise_shortlist" | "guided_plan" | "itinerary"),
  preferred_strategy ("exact_retrieval" | "direct_lookup" | "direct_fact" |
                      "shortlist_recommendation" | "gift_formula" | "movie_plus_food" |
                      "mini_itinerary" | "family_plan" | "guided_plan" |
                      "proximity_guided_shortlist" | "budget_plan" |
                      "mall_overview" | "exploration_overview"),
  entity_cap (integer 1-6),
  must_include_entity_types (list — can be empty),
  fallback_entity_types (list — can be empty),
  itinerary_template (string or null),
  retrieval_triggers (list of source strings, e.g. "canonical.movies"),
  priority (integer 1-10, lower = higher priority),
  concierge_reasoning_notes (string — reference actual entity names, zones, pairings)

Critical rules:
- pb-movie-showtime-lookup: priority=1, strategy=exact_retrieval, must trigger canonical.movies
- pb-service-lookup: strategy=direct_fact, entity_cap=2, no shortlist
- pb-family-movie: include family-friendly movie context; note if no animation films available
- pb-kids-entertainment: must_include_entity_types=["kids_entertainment"],
  child_relief_anchor in ranking_boost_tags
- pb-route-refinement: ranking_boost_tags must include near_cinema, near_food
- pb-before-movie / pb-quick-snack: preferred_zones should bias Food Court and Kiosk Row

Output ONLY a valid JSON array of 28 playbook objects. No commentary.
""")


def step_playbooks(canonical: dict, llm, output_dir: Path) -> list:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_PLAYBOOKS)
    example_condensed = example[:3]

    entity_summary = {
        "stores": [
            {"id": s["entity_id"], "name": s.get("name"), "category": s.get("category"),
             "subcategory": s.get("subcategory"), "price_range": s.get("price_range"),
             "zone": s.get("location", {}).get("zone")}
            for s in canonical.get("stores", [])
        ],
        "dining": [
            {"id": d["entity_id"], "name": d.get("name"), "dining_style": d.get("dining_style"),
             "price_range": d.get("price_range"), "zone": d.get("location", {}).get("zone")}
            for d in canonical.get("dining", [])
        ],
        "cinemas": [
            {"id": c["entity_id"], "name": c.get("name"),
             "zone": c.get("location", {}).get("zone")}
            for c in canonical.get("cinemas", [])
        ],
        "movies": [
            {"id": m["entity_id"], "title": m.get("title"), "genre": m.get("genre"),
             "status": m.get("status"), "language": m.get("language")}
            for m in canonical.get("movies", [])
        ],
        "services": [
            {"id": s["entity_id"], "name": s.get("name"),
             "service_category": s.get("service_category"),
             "zone": s.get("location", {}).get("zone")}
            for s in canonical.get("services", [])
        ],
    }

    # Summarize movie lineup for cinema-specific notes
    now_showing = [m["title"] for m in canonical.get("movies", []) if m.get("status") == "SHOWING_NOW"]
    coming_soon = [m["title"] for m in canonical.get("movies", []) if m.get("status") == "COMING_SOON"]
    animation_films = [
        m["title"] for m in canonical.get("movies", [])
        if any(g in (m.get("genre") or []) for g in ["Animation", "Family", "Comedy"])
        and m.get("status") == "SHOWING_NOW"
    ]

    user_prompt = textwrap.dedent(f"""\
    EXAMPLE playbooks (showing 3 of 28 for schema reference):
    ```json
    {json.dumps(example_condensed, indent=2, ensure_ascii=False)}
    ```

    Mall: {canonical['mall_profile'].get('name')}, {canonical['mall_profile'].get('city')}, Saudi Arabia
    Mall ID: {mall_id}
    Zones: {[z["name"] for z in canonical["mall_profile"].get("zones", [])]}
    Overview: {canonical["mall_profile"].get("overview_summary", "")}

    Cinema lineup:
    - Now Showing: {now_showing or "none"}
    - Coming Soon: {coming_soon[:5] if coming_soon else "none"}
    - Animation/Family films now showing: {animation_films or "NONE — redirect family+kids movie queries to kids entertainment"}

    ENTITIES:
    ```json
    {json.dumps(entity_summary, indent=2, ensure_ascii=False)}
    ```

    Generate all 28 playbooks as a JSON array. Reference actual entity names and zones.
    Tailor cinema/movie playbooks to the actual lineup above.
    """)

    result = _llm_json(llm, PLAYBOOKS_SYSTEM, user_prompt, "playbooks")
    if isinstance(result, dict):
        result = result.get("playbooks", [])
    _save_json(output_dir / "playbooks" / f"{mall_id}.json", result)
    return result


# ---------------------------------------------------------------------------
# STEP 4: Tenant config
# ---------------------------------------------------------------------------
TENANT_CONFIG_SYSTEM = textwrap.dedent("""\
You are a configuration specialist for a mall concierge AI.

Generate a tenant config JSON (v2.0.0) for the given mall. The config must include:

- mall_id, config_version ("2.0.0"), description
- tone: warmth, directness, formality, concierge_confidence, mall_branding_prominence,
  practicalness_vs_exploratory (all floats 0.0–1.0)
- clarification: ambiguity_tolerance, answer_first_bias, assumption_aggressiveness
- behavior_rules (object with named rules):
    movie_intent_override: {enabled, trigger_signals, preferred_strategy ("exact_retrieval"),
      preferred_response_shape ("direct_fact"), override_shopping_signals, cinema_entity_id, note}
    kids_movie_redirect OR kids_movie_highlight (depending on whether animation films exist):
      redirect: {enabled, trigger_signals, redirect_entity_id, redirect_message_hint}
      highlight: {enabled, trigger_signals, cinema_entity_id, highlight_tags}
    service_lookup: {enabled, trigger_signals, preferred_strategy ("direct_fact"), entity_cap: 2}
    before_movie_quick_stop: {enabled, trigger_signals, ranking_boost_tags, preferred_zones, entity_cap: 3}
    after_movie_reward: {enabled, trigger_signals, ranking_boost_tags, entity_cap: 3}
    family_guided_plan: {enabled, trigger_signals, preferred_strategy ("guided_plan"),
      must_include_entity_types, child_relief_anchor_entity_id, preferred_response_shape}
    route_proximity_refinement: {enabled, trigger_signals, ranking_boost_tags}
    brand_direct_lookup: {enabled, preferred_strategy ("direct_lookup")}
    cross_mall_fallback: {enabled, fallback_scope ("same_city_first"), max_fallback_results}
- strategy_weights (floats, higher = preferred): shortlist_recommendation, family_plan,
  gift_formula, movie_plus_food, movie_showtime_lookup (1.5), family_movie_plan (1.5),
  kids_entertainment_plan (1.4), before_movie_plan (1.2), after_movie_plan (1.1),
  service_direct_fact (1.4), route_refinement (1.1), quick_errand (1.2)
- response_shape: default_shortlist_size (4), movie_shortlist_size (5-6),
  service_shortlist_size (2), before_after_movie_shortlist_size (3),
  kids_entertainment_shortlist_size (2-3), location_detail_level ("floor_zone"),
  recommendation_density, exact_name_usage_bias, always_include_location_hint
- context_weights: prioritize_playbooks, prioritize_family_signals, prioritize_kid_signals,
  prioritize_exact_entity_examples, prioritize_movie_signals (0.90),
  prioritize_service_signals (0.88), prioritize_proximity_signals
- retrieval: movie_showtime_query, family_movie_query (if animation films exist),
  service_query, kids_entertainment_query, before_movie_query, after_movie_query,
  near_cinema_query — each with strategy, source/required_tags, max_results
- ranking: favor_family_friendly, favor_kid_friendly, favor_anchor_entities,
  favor_diversity_vs_confidence, favor_near_cinema_for_movie_context (0.85),
  favor_quick_stop_for_time_sensitive, penalize_shopping_for_explicit_movie_query,
  boost_child_relief_anchor_for_family
- session_adaptation: preserve_companions_strongly, reinterpret_short_followups_aggressively,
  carry_movie_context_across_turns (true), carry_family_context_across_turns (true),
  treat_refinement_as_proximity_filter (true)
- intent_override_priority: movie_showtime_lookup=1, service_lookup=2,
  kids_entertainment=3, before_after_movie=4, family_guided_plan=5,
  gift_plan=6, dining=7, shopping=8

Tune all values based on the mall's character and actual offerings.
If no animation films are currently showing, use kids_movie_redirect (not highlight).
If animation films ARE showing, use kids_movie_highlight.
Output ONLY valid JSON. No commentary.
""")


def step_tenant_config(canonical: dict, llm, output_dir: Path) -> dict:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_TENANT_CFG)
    defaults = _load_json(TENANT_DEFAULTS) if TENANT_DEFAULTS.exists() else {}

    animation_films = [
        m["title"] for m in canonical.get("movies", [])
        if any(g in (m.get("genre") or []) for g in ["Animation", "Family"])
        and m.get("status") == "SHOWING_NOW"
    ]

    kids_entities = [
        {"id": s["entity_id"], "name": s.get("name")}
        for s in canonical.get("stores", [])
        if s.get("subcategory") in ("Kids Entertainment", "Toddlers specialised area")
    ]

    cinema_entities = [
        {"id": c["entity_id"], "name": c.get("name")}
        for c in canonical.get("cinemas", [])
    ]

    user_prompt = textwrap.dedent(f"""\
    EXAMPLE config (for schema reference):
    ```json
    {json.dumps(example, indent=2, ensure_ascii=False)}
    ```

    DEFAULTS (base values):
    ```json
    {json.dumps(defaults, indent=2, ensure_ascii=False)}
    ```

    MALL SUMMARY:
    {_entity_summary(canonical)}

    Animation/family films currently showing: {animation_films or "NONE"}
    → Use "kids_movie_redirect" behavior rule (not "kids_movie_highlight")
    {"→ Redirect target: " + kids_entities[0]["id"] + " (" + kids_entities[0]["name"] + ")" if kids_entities else "→ No kids entertainment entity found — omit redirect_entity_id"}

    Cinema entities: {json.dumps(cinema_entities, ensure_ascii=False)}

    Generate the complete tenant config JSON for mall_id "{mall_id}".
    """)

    result = _llm_json(llm, TENANT_CONFIG_SYSTEM, user_prompt, "tenant_config")
    _save_json(output_dir / "tenant_config" / f"{mall_id}.json", result)
    return result


# ---------------------------------------------------------------------------
# STEP 5: Context pack
# ---------------------------------------------------------------------------
CONTEXT_PACK_SYSTEM = textwrap.dedent("""\
You are a context architect for a mall concierge AI.

Generate a pre-built context pack JSON that the concierge uses at runtime.

The context pack MUST include:

- mall_id, version ("2.0")
- mall_profile_summary: name, city, tagline, address, floors, key_zones, highlights,
  opening_hours_summary, map_url
- operational_context: hours (structured by day), parking, accessibility,
  essential_services (with locations), current_season, active_promotions, upcoming_events
- topic_blocks (object — one entry per topic):
    mall_overview: summary, key_facts, facilities, contact
    shopping_overview: summary, anchor_brands, category_highlights, entities[]
    dining_overview: summary, entities[] with zone and style
    services: summary, entities[] with location and service_category
    entertainment: summary, entities[] (cinema + kids entertainment anchors)
    cinema_and_movies: summary, cinema_entity, now_showing[] (with genre, language, booking_url),
      coming_soon[] (titles only), pairing_suggestions, booking_note
    family_visit: summary, recommended_itinerary, child_anchor, dining_suggestion, entities[]
    kids_visit: summary, primary_anchor (kids entertainment), cinema_note, dining_near_anchor
    gift_ideas: summary, by_recipient{girlfriend, family, last_minute}, entities[]
    quick_visit: summary, quick_stop_zones, grab_and_go_options, entities[]
    date_night: summary, suggested_plan, dining_options, gift_options, cinema_option
    before_movie: summary, recommended_options[] (name, zone, why), time_estimate
    after_movie: summary, recommended_options[] (name, zone, why)
  Each topic block must have: summary (string), entities (array of compact entity refs), semantic_highlights, concierge_tips
- events_and_offers: flat list of current events and active offers
- contextual_reasoning_hints: 8-12 reasoning tips for the LLM
- concierge_guidelines: persona, tone, grounding_rules[], cultural_notes[], do_not[]

All topic blocks must reference ACTUAL entity names and locations from the canonical data.
cinema_and_movies must be rich — include every SHOWING_NOW film with genre and booking_url.
before_movie and after_movie must reference Food Court and Kiosk Row entities by name.
Output ONLY valid JSON. No commentary.
""")


def step_context_pack(
    canonical: dict,
    semantic: dict,
    playbooks: list,
    llm,
    output_dir: Path,
) -> dict:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_CONTEXT)

    # Condense example to show structure
    example_condensed = {
        "mall_id": example["mall_id"],
        "version": example.get("version", "2.0"),
        "mall_profile_summary": example.get("mall_profile_summary", {}),
        "operational_context": example.get("operational_context", {}),
        "topic_blocks": {
            k: {
                "summary": v.get("summary", ""),
                "entities": (v.get("entities") or [])[:2],
                "semantic_highlights": (v.get("semantic_highlights") or [])[:3],
                "concierge_tips": (v.get("concierge_tips") or [])[:2],
            }
            for k, v in list((example.get("topic_blocks") or {}).items())[:3]
            if isinstance(v, dict)
        },
        "contextual_reasoning_hints": (example.get("contextual_reasoning_hints") or [])[:4],
        "concierge_guidelines": example.get("concierge_guidelines", {}),
    }

    # Semantic sample (top profiles by usefulness)
    semantic_sample = [
        {
            "entity_id": p["entity_id"],
            "tags": (p.get("semantic_tags") or [])[:6],
            "vibe": p.get("vibe", []),
            "concierge_note": (p.get("concierge_notes") or "")[:150],
        }
        for p in (semantic.get("profiles") or [])[:8]
    ]

    compact_canonical = {
        "mall_profile": canonical["mall_profile"],
        "stores": [
            {"entity_id": s["entity_id"], "name": s.get("name"),
             "category": s.get("category"), "subcategory": s.get("subcategory"),
             "price_range": s.get("price_range"), "location": s.get("location"),
             "description": (s.get("description") or "")[:100]}
            for s in canonical.get("stores", [])
        ],
        "dining": canonical.get("dining", []),
        "cinemas": canonical.get("cinemas", []),
        "movies": canonical.get("movies", []),
        "services": canonical.get("services", []),
        "events": canonical.get("events", []),
        "offers": (canonical.get("offers") or [])[:5],
    }

    user_prompt = textwrap.dedent(f"""\
    EXAMPLE context pack (condensed for structure):
    ```json
    {json.dumps(example_condensed, indent=2, ensure_ascii=False)}
    ```

    CANONICAL DATA:
    ```json
    {json.dumps(compact_canonical, indent=2, ensure_ascii=False)}
    ```

    SEMANTIC SAMPLE (top profiles):
    ```json
    {json.dumps(semantic_sample, indent=2, ensure_ascii=False)}
    ```

    PLAYBOOK SCENARIOS ({len(playbooks)} total):
    {", ".join(pb.get("playbook_id", "") for pb in playbooks)}

    Generate the COMPLETE context pack for mall_id "{mall_id}".
    Include ALL 13 topic blocks: mall_overview, shopping_overview, dining_overview,
    services, entertainment, cinema_and_movies, family_visit, kids_visit, gift_ideas,
    quick_visit, date_night, before_movie, after_movie.

    For cinema_and_movies: list every SHOWING_NOW film with genre, language, and booking_url.
    For before_movie: recommend specific Food Court and Kiosk Row options by name.
    For kids_visit: reference the actual kids entertainment anchor by name.
    """)

    result = _llm_json(llm, CONTEXT_PACK_SYSTEM, user_prompt, "context_pack")
    _save_json(output_dir / "context_packs" / f"{mall_id}_context.json", result)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
ALL_STEPS = ("canonical", "semantic", "playbooks", "tenant_config", "context_pack")


def main() -> None:
    load_dotenv(BACKEND_DIR / ".env")

    # Map BACKEND_OPENAI_API_KEY → OPENAI_API_KEY if needed
    if not os.environ.get("OPENAI_API_KEY"):
        fallback = os.environ.get("BACKEND_OPENAI_API_KEY", "")
        if fallback:
            os.environ["OPENAI_API_KEY"] = fallback

    parser = argparse.ArgumentParser(
        description="Full pipeline: output_mall_XX.json → all 5 mall intelligence layers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python scripts/generate_mall_data.py ../../output_mall_28.json
          python scripts/generate_mall_data.py ../../output_mall_13.json
          python scripts/generate_mall_data.py ../../output_mall_28.json --steps semantic playbooks
          python scripts/generate_mall_data.py ../../output_mall_28.json --model gpt-4.1 --dry-run

        Steps:
          canonical    — Deterministic ETL (no LLM, no API key needed)
          semantic     — LLM-synthesized enrichment rules + per-entity profiles
          playbooks    — LLM-synthesized 28 scenario playbooks
          tenant_config — LLM-synthesized behavior rules + weights
          context_pack — LLM-synthesized topic blocks for runtime composition
        """),
    )
    parser.add_argument("input", type=Path, help="Path to output_mall_XX.json")
    parser.add_argument(
        "--steps", nargs="+", choices=ALL_STEPS, default=list(ALL_STEPS),
        help="Which layers to generate (default: all 5)",
    )
    parser.add_argument(
        "--model", default="gpt-4.1",
        help="OpenAI model to use for LLM steps (default: gpt-4.1)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DATA_DIR,
        help=f"Output base directory (default: {DATA_DIR})",
    )
    parser.add_argument(
        "--canonical-only", action="store_true",
        help="Run only the deterministic canonical conversion (no LLM required)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be generated without calling the LLM or writing files",
    )
    args = parser.parse_args()

    if args.canonical_only:
        args.steps = ["canonical"]

    # Resolve input
    input_path = args.input
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📂 Input: {input_path}")
    raw_data = _load_json(input_path)

    if not isinstance(raw_data, dict):
        print("Error: input file must be a JSON object", file=sys.stderr)
        sys.exit(1)

    mall_name = (
        raw_data.get("mall_information", {}).get("MallNameEn")
        or raw_data.get("marketing_name", "Unknown")
    )
    prop_id = raw_data.get("property_group_id", "?")
    estimated_mall_id = f"al_nakheel_plaza_{prop_id}"

    print(f"🏬 Mall:  {mall_name} (property_group_id={prop_id})")
    print(f"   Steps: {', '.join(args.steps)}")
    print(f"   Output: {args.output_dir}")

    if args.dry_run:
        print("\n(dry-run — no files written, no LLM calls made)")
        print(f"\nWould generate: {', '.join(args.steps)}")
        print(f"Mall ID would be: {estimated_mall_id}")
        sys.exit(0)

    out = args.output_dir
    canonical = None
    semantic = None
    playbooks_data = None

    # Step 1: Canonical
    if "canonical" in args.steps:
        print("\n🔄 Step 1: Converting to canonical format (deterministic ETL)")
        canonical, mall_id = step_canonical(raw_data, out)
    else:
        # Load existing canonical
        canon_path = out / "canonical" / f"{estimated_mall_id}.json"
        if canon_path.exists():
            print(f"\n📂 Loading existing canonical: {canon_path.name}")
            canonical = _load_json(canon_path)
            mall_id = canonical["mall_profile"]["mall_id"]
        else:
            print(f"Error: canonical file not found at {canon_path}", file=sys.stderr)
            print("  Hint: run with --steps canonical first, or add 'canonical' to --steps", file=sys.stderr)
            sys.exit(1)

    # Check API key for LLM steps
    llm_steps = [s for s in args.steps if s != "canonical"]
    if llm_steps and not os.environ.get("OPENAI_API_KEY"):
        print("\nError: OPENAI_API_KEY not set. Cannot run LLM steps.", file=sys.stderr)
        print("  Set it in .env as OPENAI_API_KEY or BACKEND_OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    if not llm_steps:
        print("\n✅ Canonical generated. Run with --steps semantic playbooks tenant_config context_pack to generate derived layers.")
        return

    llm = _get_llm(args.model)
    print(f"   Model: {args.model}")

    # Step 2: Semantic
    if "semantic" in args.steps:
        print("\n🧠 Step 2: Generating semantic enrichment")
        semantic = step_semantic(canonical, llm, out)

    # Step 3: Playbooks
    if "playbooks" in args.steps:
        print("\n📋 Step 3: Generating playbooks")
        playbooks_data = step_playbooks(canonical, llm, out)

    # Step 4: Tenant config
    if "tenant_config" in args.steps:
        print("\n⚙️  Step 4: Generating tenant config")
        step_tenant_config(canonical, llm, out)

    # Step 5: Context pack (needs semantic + playbooks)
    if "context_pack" in args.steps:
        print("\n📦 Step 5: Generating context pack")

        if semantic is None:
            sem_path = out / "semantic" / f"{mall_id}.json"
            if sem_path.exists():
                semantic = _load_json(sem_path)
            else:
                print("  ⚠ Semantic file not found — generating it first")
                semantic = step_semantic(canonical, llm, out)

        if playbooks_data is None:
            pb_path = out / "playbooks" / f"{mall_id}.json"
            if pb_path.exists():
                playbooks_data = _load_json(pb_path)
            else:
                print("  ⚠ Playbooks file not found — generating them first")
                playbooks_data = step_playbooks(canonical, llm, out)

        step_context_pack(canonical, semantic, playbooks_data, llm, out)

    print(f"\n✅ Done! All requested layers written to {out}")
    print(f"\nFiles generated:")
    for step in args.steps:
        if step == "canonical":
            print(f"  data/canonical/{mall_id}.json")
        elif step == "semantic":
            print(f"  data/semantic/{mall_id}.json")
        elif step == "playbooks":
            print(f"  data/playbooks/{mall_id}.json")
        elif step == "tenant_config":
            print(f"  data/tenant_config/{mall_id}.json")
        elif step == "context_pack":
            print(f"  data/context_packs/{mall_id}_context.json")


if __name__ == "__main__":
    main()
