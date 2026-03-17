#!/usr/bin/env python3
"""
Mall Data Synthesizer
=====================
Takes a canonical mall JSON file and generates all derived data files
needed by the Cenomi Concierge system:

  1. Semantic enrichment  (semantic/<mall_id>.json)
  2. Playbooks            (playbooks/<mall_id>.json)
  3. Tenant config        (tenant_config/<mall_id>.json)
  4. Context pack         (context_packs/<mall_id>_context.json)

Usage:
  python scripts/synthesize_mall_data.py data/canonical/my_mall.json
  python scripts/synthesize_mall_data.py data/canonical/my_mall.json --steps semantic playbooks
  python scripts/synthesize_mall_data.py raw_input.json --from-raw

Requirements:
  OPENAI_API_KEY must be set (in .env or environment).
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"

EXAMPLE_CANONICAL = DATA_DIR / "canonical" / "al_nakheel_plaza_28.json"
EXAMPLE_SEMANTIC = DATA_DIR / "semantic" / "al_nakheel_plaza_28.json"
EXAMPLE_PLAYBOOKS = DATA_DIR / "playbooks" / "al_nakheel_plaza_28.json"
EXAMPLE_TENANT_CFG = DATA_DIR / "tenant_config" / "al_nakheel_plaza_28.json"
EXAMPLE_CONTEXT = DATA_DIR / "context_packs" / "al_nakheel_plaza_28_context.json"
TENANT_DEFAULTS = DATA_DIR / "tenant_config" / "tenant_defaults.json"


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  ✓ Wrote {path.relative_to(BACKEND_DIR)}")


def _truncate(obj: dict | list, max_items: int = 3) -> dict | list:
    """Return a truncated copy of a list/dict for prompt brevity."""
    if isinstance(obj, list):
        return obj[:max_items]
    return obj


def _entity_summary(canonical: dict) -> str:
    """Build a compact entity summary string for prompts."""
    lines: list[str] = []
    mall = canonical.get("mall_profile", {})
    lines.append(f"Mall: {mall.get('name')} ({mall.get('city')}, {mall.get('country')})")
    lines.append(f"Floors: {mall.get('floors')}")
    lines.append(f"Zones: {json.dumps([z['name'] for z in mall.get('zones', [])])}")

    for section in ("stores", "dining", "cinemas", "movies", "services", "events", "offers"):
        items = canonical.get(section, [])
        if items:
            names = [i.get("name") or i.get("title", "?") for i in items]
            lines.append(f"{section} ({len(items)}): {', '.join(names)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
def _strip_fences(raw: str) -> str:
    """Remove markdown code fences from LLM output."""
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.index("\n")
        raw = raw[first_nl + 1:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    return raw.strip()


def _llm_generate_json(
    llm: ChatOpenAI,
    system_prompt: str,
    user_prompt: str,
    tag: str,
) -> dict | list:
    """Call the LLM and parse the JSON response."""
    print(f"  ⏳ Generating {tag} …")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    resp = llm.invoke(messages)
    raw = _strip_fences(resp.content)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Try to recover a truncated JSON object/array
        truncated_at = exc.pos
        snippet = raw[:truncated_at]
        # For arrays: count open/close brackets and try to close them
        if raw.lstrip().startswith("["):
            depth = 0
            for ch in snippet:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
            # Strip trailing comma if present
            snippet = snippet.rstrip().rstrip(",")
            snippet += "]" * max(depth, 1)
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        # For dicts
        if raw.lstrip().startswith("{"):
            depth = 0
            for ch in snippet:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
            snippet = snippet.rstrip().rstrip(",")
            snippet += "}" * max(depth, 1)
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        raise


# ===================================================================
# STEP 0: RAW → CANONICAL
# ===================================================================
CANONICAL_SYSTEM = textwrap.dedent("""\
You are a data architect for a mall concierge AI system.
Your job is to take a raw mall data file (any format) and convert it into
the canonical schema used by the system.

Output ONLY valid JSON matching the canonical schema.
Do NOT add commentary outside the JSON.
""")


def generate_canonical(raw_data: dict | list, llm: ChatOpenAI) -> dict:
    example_str = json.dumps(_load_json(EXAMPLE_CANONICAL), indent=2, ensure_ascii=False)
    # Truncate the example to save tokens — show structure with a few items each
    example_obj = _load_json(EXAMPLE_CANONICAL)
    schema_sample = {
        "mall_profile": example_obj["mall_profile"],
        "stores": example_obj["stores"][:2],
        "dining": example_obj["dining"][:1],
        "cinemas": example_obj.get("cinemas", [])[:1],
        "movies": example_obj.get("movies", [])[:1],
        "services": example_obj.get("services", [])[:1],
        "events": example_obj.get("events", [])[:1],
        "offers": example_obj.get("offers", [])[:1],
    }

    user_prompt = textwrap.dedent(f"""\
    Here is the CANONICAL SCHEMA (truncated example showing structure):

    ```json
    {json.dumps(schema_sample, indent=2, ensure_ascii=False)}
    ```

    Now convert this RAW MALL DATA into the canonical schema above.
    Preserve all entities. Infer missing fields where reasonable.
    Generate entity_ids using the pattern: t-store-001, t-dining-001, t-cinema-001, etc.
    Generate zone_ids using: z-<name>.
    Generate facility_ids, landmark_ids, service_ids, event_ids, offer_ids similarly.

    RAW DATA:
    ```json
    {json.dumps(raw_data, indent=2, ensure_ascii=False)}
    ```

    Output the complete canonical JSON:
    """)

    return _llm_generate_json(llm, CANONICAL_SYSTEM, user_prompt, "canonical")


# ===================================================================
# STEP 1: SEMANTIC ENRICHMENT
# ===================================================================
SEMANTIC_SYSTEM = textwrap.dedent("""\
You are a semantic data engineer for a mall concierge AI.
Your job is to generate a semantic enrichment file for a mall.

The file contains:
1. enrichment_rules — general rules that map entity attributes (category, price_range,
   dining_style, zone) to semantic tags, audience fit, vibe, outing fit, gift fit, and
   priority boosts for playbook scenarios.
2. profiles — per-entity semantic profiles with tags, audience_fit, vibe, price_band,
   outing_fit, gift_fit, meal_fit, when_to_recommend, avoid_if, priority_scores, and
   concierge_notes.

Rules for generating:
- enrichment_rules should cover every category/subcategory/dining_style/zone present.
- profiles must exist for EVERY store, dining, and cinema entity in the canonical data.
- priority_scores keys are playbook scenario names (family_visit_plan, solo_visit_plan,
  anniversary_plan, gift_for_girlfriend, gift_for_family, movie_plus_food,
  kid_friendly_movie_plus_food, quick_lunch, shopping_plus_dessert_combo, date_plan,
  budget_family_outing, luxury_shopping_plan, last_minute_gift,
  child_activity_while_parents_shop).
- concierge_notes should be specific, mentioning the entity by name, what makes it special,
  and how to pair it with other entities.
- Output ONLY valid JSON. No commentary.
""")


def generate_semantic(canonical: dict, llm: ChatOpenAI) -> dict:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_SEMANTIC)

    example_condensed = {
        "mall_id": example["mall_id"],
        "tag_taxonomy_version": example["tag_taxonomy_version"],
        "enrichment_rules": example["enrichment_rules"][:4],
        "profiles": example["profiles"][:3],
    }

    # ── Step A: Generate enrichment rules ──────────────────────────
    rules_prompt = textwrap.dedent(f"""\
    EXAMPLE (condensed):
    ```json
    {json.dumps(example_condensed, indent=2, ensure_ascii=False)}
    ```

    Generate ONLY the enrichment_rules array for mall_id "{mall_id}".
    Cover every category, subcategory, dining_style, and zone present.

    CANONICAL DATA (mall profile + entity summaries):
    ```json
    {json.dumps({
        "mall_profile": canonical["mall_profile"],
        "store_categories": list({s.get("category", "") for s in canonical.get("stores", [])}),
        "dining_styles": list({d.get("dining_style", "") for d in canonical.get("dining", [])}),
        "zones": [z["name"] for z in canonical["mall_profile"].get("zones", [])],
    }, indent=2, ensure_ascii=False)}
    ```

    Output ONLY a JSON array of enrichment_rule objects.
    """)

    rules_system = SEMANTIC_SYSTEM + "\nOutput ONLY a JSON array of enrichment_rule objects. No keys."
    rules = _llm_generate_json(llm, rules_system, rules_prompt, "semantic/rules")
    if not isinstance(rules, list):
        rules = rules.get("enrichment_rules", [])

    # ── Step B: Generate profiles in batches of 20 ─────────────────
    all_entities = (
        canonical.get("stores", [])
        + canonical.get("dining", [])
        + canonical.get("cinemas", [])
    )
    BATCH = 20
    all_profiles: list[dict] = []
    profile_system = SEMANTIC_SYSTEM + "\nOutput ONLY a JSON array of profile objects. No wrapping keys."

    for batch_start in range(0, len(all_entities), BATCH):
        batch = all_entities[batch_start: batch_start + BATCH]
        batch_num = batch_start // BATCH + 1
        total_batches = (len(all_entities) + BATCH - 1) // BATCH

        profile_prompt = textwrap.dedent(f"""\
        EXAMPLE profile (for reference):
        ```json
        {json.dumps(example_condensed["profiles"][:2], indent=2, ensure_ascii=False)}
        ```

        Generate semantic profiles for these {len(batch)} entities (batch {batch_num}/{total_batches}).
        Mall: "{mall_id}". Use these playbook scenario keys for priority_scores:
        family_visit_plan, solo_visit_plan, anniversary_plan, gift_for_girlfriend,
        gift_for_family, movie_plus_food, kid_friendly_movie_plus_food, quick_lunch,
        shopping_plus_dessert_combo, date_plan, budget_family_outing,
        luxury_shopping_plan, last_minute_gift, child_activity_while_parents_shop.

        ENTITIES:
        ```json
        {json.dumps(batch, indent=2, ensure_ascii=False)}
        ```

        Output ONLY a JSON array of profile objects.
        """)

        batch_profiles = _llm_generate_json(
            llm, profile_system, profile_prompt, f"semantic/profiles batch {batch_num}/{total_batches}"
        )
        if isinstance(batch_profiles, list):
            all_profiles.extend(batch_profiles)
        elif isinstance(batch_profiles, dict):
            all_profiles.extend(batch_profiles.get("profiles", []))

    return {
        "mall_id": mall_id,
        "tag_taxonomy_version": "1.0",
        "enrichment_rules": rules,
        "profiles": all_profiles,
    }


# ===================================================================
# STEP 2: PLAYBOOKS
# ===================================================================
PLAYBOOKS_SYSTEM = textwrap.dedent("""\
You are a concierge scenario designer for a mall AI assistant.
Your job is to generate playbooks — structured scenario plans that guide the
concierge when handling common visitor intents.

Generate exactly 14 playbooks covering these scenarios:
  family_visit_plan, solo_visit_plan, anniversary_plan, gift_for_girlfriend,
  gift_for_family, movie_plus_food, kid_friendly_movie_plus_food, quick_lunch,
  shopping_plus_dessert_combo, date_plan, budget_family_outing,
  luxury_shopping_plan, last_minute_gift, child_activity_while_parents_shop

Each playbook must include:
- playbook_id, scenario, description, trigger_conditions, preferred_entity_types,
  preferred_semantic_tags, ranking_biases, fallback_rules, response_shape_hint,
  shortlist_size_hint, next_step_hint, do_not_include, concierge_reasoning_notes

The concierge_reasoning_notes should reference ACTUAL entity names, locations,
and features from the canonical data.
Output ONLY a valid JSON array. No commentary.
""")


def generate_playbooks(canonical: dict, llm: ChatOpenAI) -> list:
    example = _load_json(EXAMPLE_PLAYBOOKS)
    example_condensed = example[:3]

    # Build a compact entity list so the prompt stays manageable
    entity_summary = {
        "stores": [
            {"id": s["entity_id"], "name": s.get("name"), "category": s.get("category"),
             "subcategory": s.get("subcategory"), "price_range": s.get("price_range"),
             "zone": s.get("location", {}).get("zone"), "floor": s.get("location", {}).get("floor")}
            for s in canonical.get("stores", [])
        ],
        "dining": [
            {"id": d["entity_id"], "name": d.get("name"), "dining_style": d.get("dining_style"),
             "price_range": d.get("price_range"), "zone": d.get("location", {}).get("zone")}
            for d in canonical.get("dining", [])
        ],
        "cinemas": [{"id": c["entity_id"], "name": c.get("name")} for c in canonical.get("cinemas", [])],
        "movies": [{"id": m["entity_id"], "title": m.get("title"), "genre": m.get("genre"), "status": m.get("status")} for m in canonical.get("movies", [])],
    }
    mall_name = canonical["mall_profile"].get("name", "")
    city = canonical["mall_profile"].get("city", "")
    zones = [z["name"] for z in canonical["mall_profile"].get("zones", [])]

    user_prompt = textwrap.dedent(f"""\
    EXAMPLE playbooks (showing 3 of 14 for reference):
    ```json
    {json.dumps(example_condensed, indent=2, ensure_ascii=False)}
    ```

    Mall: {mall_name}, {city}, Saudi Arabia
    Zones: {zones}

    ENTITIES:
    ```json
    {json.dumps(entity_summary, indent=2, ensure_ascii=False)}
    ```

    Generate all 14 playbooks as a JSON array. Reference actual entity names and locations.
    """)

    return _llm_generate_json(llm, PLAYBOOKS_SYSTEM, user_prompt, "playbooks")


# ===================================================================
# STEP 3: TENANT CONFIG
# ===================================================================
TENANT_CONFIG_SYSTEM = textwrap.dedent("""\
You are a configuration specialist for a mall concierge AI.
Your job is to generate a mall-specific configuration file that tunes
the concierge's behavior for a specific mall.

The config includes:
- mall_id, config_version, description
- tone (warmth, directness, formality, concierge_confidence, etc. — floats 0–1)
- clarification (ambiguity_tolerance, answer_first_bias, assumption_aggressiveness)
- strategy_weights (shortlist_recommendation, family_plan, gift_formula, etc.)
- response_shape (default_shortlist_size, location_detail_level, etc.)
- context_weights (prioritize_playbooks, prioritize_family_signals, etc.)
- ranking (favor_family_friendly, favor_kid_friendly, favor_anchor_entities, etc.)
- session_adaptation (preserve_companions_strongly, etc.)
- entity_params (optional per-entity overrides)

Tune the values based on the mall's character — e.g., a family-oriented mall
should have higher family/kid weights, a luxury mall should lean premium.
Output ONLY valid JSON. No commentary.
""")


def generate_tenant_config(canonical: dict, llm: ChatOpenAI) -> dict:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_TENANT_CFG)
    defaults = _load_json(TENANT_DEFAULTS)

    user_prompt = textwrap.dedent(f"""\
    EXAMPLE mall-specific config:

    ```json
    {json.dumps(example, indent=2, ensure_ascii=False)}
    ```

    DEFAULT config (values to override or keep):

    ```json
    {json.dumps(defaults, indent=2, ensure_ascii=False)}
    ```

    MALL SUMMARY:
    {_entity_summary(canonical)}

    Generate the tenant config JSON for mall_id "{mall_id}".
    Tune values based on the mall's character and offerings.
    Only include fields that differ from defaults, plus mall_id and config_version.
    """)

    return _llm_generate_json(llm, TENANT_CONFIG_SYSTEM, user_prompt, "tenant_config")


# ===================================================================
# STEP 4: CONTEXT PACK
# ===================================================================
CONTEXT_PACK_SYSTEM = textwrap.dedent("""\
You are a context architect for a mall concierge AI.
Your job is to generate a pre-built context pack that the concierge uses
at runtime to ground its responses.

The context pack includes:
- mall_id, version
- mall_profile_summary (name, city, tagline, floors, key_zones, highlights)
- operational_context (hours, parking, accessibility, essential_services,
  current_season, active_promotions, upcoming_events)
- topic_blocks — one per topic area (dining, gift, family, movie, quick_visit,
  services). Each has summary, entities (compact), semantic_highlights, concierge_tips.
- events_and_offers — flat list of current events and offers
- playbooks — condensed summaries of each playbook (id, scenario, trigger, approach)
- contextual_reasoning_hints — list of reasoning tips for the LLM
- concierge_guidelines — persona, tone, grounding rules, cultural notes, do_not list

The context pack must reference ACTUAL entities, locations, and details from the
canonical data. It should be comprehensive enough that the concierge can answer
most common questions from this context alone.
Output ONLY valid JSON. No commentary.
""")


def generate_context_pack(
    canonical: dict,
    semantic: dict,
    playbooks: list,
    llm: ChatOpenAI,
) -> dict:
    mall_id = canonical["mall_profile"]["mall_id"]
    example = _load_json(EXAMPLE_CONTEXT)

    # Condense the example to show structure without full content
    example_condensed = {
        "mall_id": example["mall_id"],
        "version": example["version"],
        "mall_profile_summary": example["mall_profile_summary"],
        "operational_context": example["operational_context"],
        "topic_blocks": {
            "dining": {
                "topic": "dining",
                "summary": example["topic_blocks"]["dining"]["summary"],
                "entities": example["topic_blocks"]["dining"]["entities"][:2],
                "semantic_highlights": example["topic_blocks"]["dining"]["semantic_highlights"][:3],
                "concierge_tips": example["topic_blocks"]["dining"]["concierge_tips"][:2],
            }
        },
        "events_and_offers": example["events_and_offers"][:2],
        "playbooks": example["playbooks"][:3],
        "contextual_reasoning_hints": example["contextual_reasoning_hints"][:5],
        "concierge_guidelines": example["concierge_guidelines"],
    }

    # Build a condensed semantic summary
    semantic_summary = []
    for p in semantic.get("profiles", [])[:5]:
        semantic_summary.append({
            "entity_id": p["entity_id"],
            "tags": p.get("semantic_tags", [])[:5],
            "vibe": p.get("vibe", []),
            "note": (p.get("concierge_notes", "") or "")[:120],
        })

    # Build a condensed playbooks summary
    pb_summary = []
    for pb in playbooks[:5]:
        pb_summary.append({
            "id": pb.get("playbook_id"),
            "scenario": pb.get("scenario"),
        })

    # Build a compact canonical for the context pack prompt
    compact_canonical = {
        "mall_profile": canonical["mall_profile"],
        "stores": [
            {"entity_id": s["entity_id"], "name": s.get("name"), "category": s.get("category"),
             "subcategory": s.get("subcategory"), "price_range": s.get("price_range"),
             "location": s.get("location"), "description": (s.get("description") or "")[:120]}
            for s in canonical.get("stores", [])
        ],
        "dining": canonical.get("dining", []),
        "cinemas": canonical.get("cinemas", []),
        "movies": canonical.get("movies", []),
        "services": canonical.get("services", []),
        "events": canonical.get("events", []),
        "offers": canonical.get("offers", []),
    }

    user_prompt = textwrap.dedent(f"""\
    EXAMPLE context pack (condensed — showing structure):

    ```json
    {json.dumps(example_condensed, indent=2, ensure_ascii=False)}
    ```

    CANONICAL DATA (compact):
    ```json
    {json.dumps(compact_canonical, indent=2, ensure_ascii=False)}
    ```

    SEMANTIC PROFILES (sample):
    ```json
    {json.dumps(semantic_summary, indent=2, ensure_ascii=False)}
    ```

    PLAYBOOKS (all {len(playbooks)} scenarios):
    ```json
    {json.dumps([{"id": pb.get("playbook_id"), "scenario": pb.get("scenario"), "trigger": pb.get("trigger_conditions", [])[:2]} for pb in playbooks], indent=2, ensure_ascii=False)}
    ```

    Generate the COMPLETE context pack for mall_id "{mall_id}".
    Include ALL topic blocks: dining, gift, family, movie, quick_visit, services.
    Reference actual entity names and locations. Include all events, offers, and playbooks.
    """)

    return _llm_generate_json(llm, CONTEXT_PACK_SYSTEM, user_prompt, "context_pack")


# ===================================================================
# MAIN
# ===================================================================
ALL_STEPS = ("semantic", "playbooks", "tenant_config", "context_pack")


def main() -> None:
    load_dotenv(BACKEND_DIR / ".env")

    # The backend uses BACKEND_OPENAI_API_KEY (pydantic-settings prefix).
    # LangChain needs the standard OPENAI_API_KEY — map it if not already set.
    import os
    if not os.environ.get("OPENAI_API_KEY"):
        fallback = os.environ.get("BACKEND_OPENAI_API_KEY", "")
        if fallback:
            os.environ["OPENAI_API_KEY"] = fallback

    parser = argparse.ArgumentParser(
        description="Synthesize all derived data files from a canonical mall JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python scripts/synthesize_mall_data.py data/canonical/my_mall.json
          python scripts/synthesize_mall_data.py data/canonical/my_mall.json --steps semantic playbooks
          python scripts/synthesize_mall_data.py raw_input.json --from-raw
          python scripts/synthesize_mall_data.py raw_input.json --from-raw --model gpt-4.1
        """),
    )
    parser.add_argument("input", type=Path, help="Path to the mall JSON file")
    parser.add_argument(
        "--from-raw",
        action="store_true",
        help="Input is raw data (not canonical). Generate canonical first.",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=ALL_STEPS,
        default=list(ALL_STEPS),
        help="Which derived files to generate (default: all)",
    )
    parser.add_argument(
        "--model",
        default="gpt-4.1",
        help="OpenAI model to use (default: gpt-4.1)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DATA_DIR,
        help=f"Output base directory (default: {DATA_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without calling the LLM.",
    )
    args = parser.parse_args()

    # Resolve input path
    input_path = args.input
    if not input_path.is_absolute():
        input_path = BACKEND_DIR / input_path
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📂 Input: {input_path}")
    raw_data = _load_json(input_path)

    # Step 0: Resolve canonical data
    if args.from_raw:
        canonical = raw_data
        mall_id = "unknown_mall"
        # Try to extract mall_id from raw data
        if isinstance(raw_data, dict):
            mall_id = (
                raw_data.get("mall_profile", {}).get("mall_id")
                or raw_data.get("mall_id")
                or "unknown_mall"
            )
        print(f"\n🔄 Step 0: Will convert raw data → canonical format (mall_id={mall_id})")
    else:
        canonical = raw_data
        mall_id = canonical.get("mall_profile", {}).get("mall_id")
        if not mall_id:
            print("Error: input JSON missing mall_profile.mall_id", file=sys.stderr)
            print("  Hint: use --from-raw if the input is not in canonical format", file=sys.stderr)
            sys.exit(1)

    entity_count = sum(
        len(canonical.get(k, []))
        for k in ("stores", "dining", "cinemas", "movies", "services", "events", "offers")
    )
    print(f"🏬 Mall: {canonical.get('mall_profile', {}).get('name', mall_id)} ({mall_id})")
    print(f"   Entities: {entity_count}")
    print(f"   Steps: {', '.join(args.steps)}")

    if args.dry_run:
        print("\n(dry run — would generate the above steps)")
        sys.exit(0)

    # Init LLM (only when not dry-run)
    llm = ChatOpenAI(model=args.model, temperature=0.3, max_tokens=16384)

    # Generate canonical from raw if needed
    if args.from_raw:
        print("\n🔄 Step 0: Converting raw data → canonical format")
        canonical = generate_canonical(raw_data, llm)
        mall_id = canonical.get("mall_profile", {}).get("mall_id", mall_id)
        out_path = args.output_dir / "canonical" / f"{mall_id}.json"
        _save_json(out_path, canonical)

    out = args.output_dir
    semantic = None
    playbooks_data = None

    # Step 1: Semantic
    if "semantic" in args.steps:
        print("\n🧠 Step 1: Generating semantic enrichment")
        semantic = generate_semantic(canonical, llm)
        _save_json(out / "semantic" / f"{mall_id}.json", semantic)

    # Step 2: Playbooks
    if "playbooks" in args.steps:
        print("\n📋 Step 2: Generating playbooks")
        playbooks_data = generate_playbooks(canonical, llm)
        _save_json(out / "playbooks" / f"{mall_id}.json", playbooks_data)

    # Step 3: Tenant Config
    if "tenant_config" in args.steps:
        print("\n⚙️  Step 3: Generating tenant config")
        tenant_cfg = generate_tenant_config(canonical, llm)
        _save_json(out / "tenant_config" / f"{mall_id}.json", tenant_cfg)

    # Step 4: Context Pack
    if "context_pack" in args.steps:
        print("\n📦 Step 4: Generating context pack")
        # Load semantic/playbooks from disk if not generated in this run
        if semantic is None:
            sem_path = out / "semantic" / f"{mall_id}.json"
            if sem_path.exists():
                semantic = _load_json(sem_path)
            else:
                print("  ⚠ Semantic file not found — generating it first")
                semantic = generate_semantic(canonical, llm)
                _save_json(sem_path, semantic)

        if playbooks_data is None:
            pb_path = out / "playbooks" / f"{mall_id}.json"
            if pb_path.exists():
                playbooks_data = _load_json(pb_path)
            else:
                print("  ⚠ Playbooks file not found — generating them first")
                playbooks_data = generate_playbooks(canonical, llm)
                _save_json(pb_path, playbooks_data)

        context = generate_context_pack(canonical, semantic, playbooks_data, llm)
        _save_json(out / "context_packs" / f"{mall_id}_context.json", context)

    print("\n✅ Done! All requested files generated.")


if __name__ == "__main__":
    main()
