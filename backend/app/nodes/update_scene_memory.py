"""
Update Scene Memory node — enriches the persistent scene from current turn.

CONTRACT
────────
  Purpose:  Extract visitor-context signals from the current message and
            merge them into scene memory.  Behaves like a scene compiler,
            not a shallow memory updater.
  Reads:    intent, normalized_user_message, scene, messages (recent history)
  Writes:   scene (updated SceneMemory), debug_enrichment (partial)
  Failure:  Parse error → preserve previous scene unchanged + warning
  Routing:  Always → resolve_playbooks

Extraction strategy (LLM-first):
  1. LLM structured-delta call (gpt-4o-mini) — understands natural language,
     returns only fields that change.  Handles nuanced phrasing keyword lists miss.
  2. Keyword scanner — runs after LLM as a supplement/fallback.  Fills any
     fields the LLM left empty and catches structured signals (age, visit plan,
     exclusion patterns) that are easier to extract deterministically.

Special behaviors:
  - correction         → strongly override the relevant scene fields
  - topic_switch       → archive active_topic → previous_topic, reset shortlist
  - followup           → inherit scene, update current_need only
  - sequential         → advance through visit_plan using completed_steps
  - constraint_refinement → append constraints only, preserve companions/goal/shortlist
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config.settings import get_settings
from app.models.state import ConciergeState, DebugEnrichment, SceneMemory, ShoppingTask
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# ── LLM scene extractor ───────────────────────────────────────────────────

_scene_llm: ChatOpenAI | None = None


def _get_scene_llm() -> ChatOpenAI:
    global _scene_llm
    if _scene_llm is None:
        settings = get_settings()
        _scene_llm = ChatOpenAI(
            model=settings.classifier_model,
            temperature=0.0,
            api_key=settings.openai_api_key,
            max_tokens=250,
        )
    return _scene_llm


_SCENE_EXTRACTOR_PROMPT = """\
You are a scene-context extractor for a mall concierge chatbot.
Given the visitor's current message, their existing scene memory, and recent conversation,
return ONLY the scene fields that should be UPDATED or SET this turn as a JSON delta.

Return ONLY valid JSON. Only include fields that are new or changed — omit unchanged fields.
Use null to explicitly clear a field (e.g. when topic switches away from prior context).

Available scene fields you may return:
{
  "companions": ["wife"|"husband"|"girlfriend"|"boyfriend"|"kids"|"child"|"friends"|"family"|"solo"],
  "target_person": "child"|"wife"|"husband"|"girlfriend"|"boyfriend"|"parent"|"self"|"bride"|"groom"|"guest"|"bridesmaid"|"someone" or null,
  "occasion": "date"|"birthday"|"anniversary"|"gift"|"celebration"|"casual"|"before_movie"|"after_movie" or null,
  "goal": "dining"|"shopping"|"entertainment"|"gift_shopping"|"exploration"|"kids_activity"|"browsing" or null,
  "visit_type": "couple"|"family_visit"|"solo"|"group" or null,
  "budget": "budget"|"mid_range"|"premium"|"luxury" or null,
  "visit_constraints": ["quick"|"kid_friendly_required"|"near_cinema_preferred"|"budget_sensitive"|"light"|"healthy"|"affordable"],
  NOTE on kid_friendly_required: add this constraint ONLY when the user explicitly mentions
  children, kids, a child, son, daughter, baby, toddler, or uses child-related language
  ("something for the kids", "kid-friendly", "child-safe"). NEVER infer kid_friendly_required
  from dietary requests such as "veg options", "vegetarian food", "healthy choices", or
  "light meals" — dietary preferences have NO implication about children being present.
  "scenario": "wedding_related"|"family_outing"|"date"|"gift_shopping"|"before_movie"|"quick_visit"|"birthday"|"first_visit"|"group_outing"|"solo_visit" or null,
  "user_role": "bridesmaid"|"bride"|"groom"|"maid_of_honor"|"best_man"|"mother_of_bride"|"father_of_bride"|"tourist"|"first_time_visitor" or null,
  "style_intent": ["elegant"|"occasion_wear"|"romantic"|"casual"|"practical"|"quick"|"luxury"|"premium"|"budget"|"fun"],
  "excluded_domains": ["dining"|"cafe"|"shopping"|"entertainment"],
  "visit_plan": ["dining"|"coffee"|"shopping"|"movie"|"entertainment"|"dessert"|"kids_activity"],
  "shopping_task": {
    "product_type": string or null,
    "product_category": string or null,
    "target_person": string or null,
    "budget_preference": "affordable"|"mid_range"|"premium"|"luxury" or null,
    "use_case": "gift"|"personal"|"household" or null,
    "target_gender": "male"|"female" or null
  },
  "inferred_scene_notes": ["...human-readable interpretation of what the visitor wants..."],
  "clear_shopping_task": true  (set to true ONLY when the user switches away from shopping entirely)
}

RULES:
- companions: ADD to existing list unless this is a fresh_start or correction.
- visit_constraints: ADD to existing list (constraints accumulate across turns).
- excluded_domains: ADD to list ONLY when the user uses explicit negation words to reject an entire domain:
  "no food", "no shopping", "no cinema", "skip dining", "avoid coffee", "without food", "strictly no food",
  "don't want restaurants", "no dining", "avoid shopping", "no movies".
  NEVER set excluded_domains for:
  • Requests for a specific menu item or food dish ("Can I get tiramisu?", "do you have sushi?",
    "is there pizza here?") — these are dining REQUESTS, not domain rejections.
  • Dietary preference requests ("veg options", "vegetarian", "healthy food") — these are filters,
    not domain exclusions.
  • Any question that is seeking information WITHIN a domain rather than rejecting it.
  Examples:
  • "Can I get Tiramisu Cake here?" → excluded_domains: []  (asking ABOUT food, not rejecting food)
  • "Can you give me some veg options?" → excluded_domains: []  (dietary preference, not exclusion)
  • "no food, just shopping" → excluded_domains: ["dining"]  (explicit domain rejection)
  Never remove previously excluded domains.
- inferred_scene_notes: always include 1-2 brief notes about the visitor's intent.
- If the message is about someone else ("she's into", "for my wife"), set target_person.
- "a bit special", "something nice", "treat ourselves" → budget=premium (implicit premium signal).
- "not too expensive", "affordable", "budget" → budget=budget or visit_constraints+=budget_sensitive.
- If message_kind is "fresh_start" (re-engagement after frustration), clear shopping_task and stale constraints.
- scenario: set to the real-world context. "wedding_related" for bridal/wedding roles; "family_outing" for visits with kids/family; "date" for couples/anniversary; "gift_shopping" for buying gifts; "before_movie" for pre-movie visits; "quick_visit" for time-constrained; "birthday" for celebrations; "first_visit" for first-timers; "group_outing" for friend groups; "solo_visit" for solo visitors.
- user_role: set explicitly declared roles (bridesmaid, tourist, etc.). Do not override once set.
- style_intent: extract from style signals ("elegant", "luxury", "casual", "budget", etc.).
- visit_plan: extract from explicit multi-step plans ("shopping then coffee then a movie" → ["shopping", "coffee", "movie"]).
- shopping_task.product_category: when product_type is set, also set product_category to the closest
  canonical category from this list:
  "outerwear" — jacket, coat, hoodie, warm clothes, winter wear, something warmer, puffer, blazer
  "menswear"  — men's shirt, men's trousers, men's suit, something for a man
  "womenswear" — women's dress, women's top, ladies clothes
  "footwear"  — shoes, sneakers, boots, sandals, heels, trainers
  "sportswear" — gym wear, activewear, workout clothes, running gear, sports clothes
  "accessories" — bag, handbag, belt, wallet, sunglasses, watch (non-luxury), scarf
  "jewelry"   — necklace, ring, bracelet, earrings, luxury watch
  "fragrance" — perfume, cologne, oud, scent
  "beauty"    — makeup, skincare, lipstick, foundation, moisturiser
  "kids_fashion" — kids clothes, children's wear, toddler clothes
  "toys"      — toy, game, puzzle, lego, kids game
  "gifts"     — gift, present (when no specific product type is clear)
  Leave product_category null when product_type is null or too vague to categorise.
- PRONOUN DISAMBIGUATION (CRITICAL — read before setting target_person):
  "for him" / "for his" / "him" — resolve based on who is present:
    If companions include a child (son, child, kids) AND a female partner (girlfriend, wife):
      → target_person="child" (the "him" is the child, the partner is female so cannot be "him")
    If companions include only a male partner (boyfriend, husband) with no child:
      → target_person="boyfriend" or "husband"
  "for her" / "for his" / "her" — resolve based on who is present:
    If companions include "daughter" specifically:
      → target_person="child"
    If companions include a female partner (girlfriend, wife) with no daughter:
      → target_person="girlfriend" or "wife"
  NEVER assign "boyfriend" or "husband" when the companion is a "girlfriend" or "wife".
  NEVER assign "girlfriend" or "wife" when the companion is a "boyfriend" or "husband".
  When multiple companions exist, use pronoun gender to disambiguate.
- WEDDING / EVENT ROLE DISAMBIGUATION: When the user says "for the bride", "for my groom", "for a guest", "for a bridesmaid", set target_person to the exact role ("bride", "groom", "guest", "bridesmaid"). Do NOT collapse these to "someone" — the specific role is needed for accurate recommendations.
- ABBREVIATION NORMALIZATION (always expand to canonical form before using in any field):
  "gf" or "GF" → "girlfriend"
  "bf" or "BF" → "boyfriend"
  "hubby" → "husband"
  "wifey" → "wife"
  "SO" → use context to determine "girlfriend"/"boyfriend"/"wife"/"husband"
  Never store abbreviations like "gf" or "bf" in companions or target_person — always expand them.
"""


async def _llm_extract_scene_delta(
    message: str,
    scene: SceneMemory,
    recent_messages: list,
    message_kind: str,
) -> dict[str, Any]:
    """
    Call gpt-4o-mini to extract a structured scene delta from the current
    message, informed by existing scene state and recent conversation.

    Returns a dict of field → new value (delta only, not full scene).
    Returns empty dict on any failure — keyword scanner runs as fallback.
    """
    try:
        llm = _get_scene_llm()

        scene_summary: dict[str, Any] = {}
        if scene.companions:
            scene_summary["companions"] = scene.companions
        if scene.target_person:
            scene_summary["target_person"] = scene.target_person
        if scene.occasion:
            scene_summary["occasion"] = scene.occasion
        if scene.goal:
            scene_summary["goal"] = scene.goal
        if scene.visit_type:
            scene_summary["visit_type"] = scene.visit_type
        if scene.budget:
            scene_summary["budget"] = scene.budget
        if scene.visit_constraints:
            scene_summary["visit_constraints"] = scene.visit_constraints
        if scene.shopping_task and scene.shopping_task.product_type:
            scene_summary["shopping_task"] = {
                "product_type": scene.shopping_task.product_type,
                "target_person": scene.shopping_task.target_person,
                "budget_preference": scene.shopping_task.budget_preference,
            }

        # Recent conversation (last 4 turns, capped at 120 chars each)
        recent_lines = []
        for m in recent_messages[-4:]:
            role = getattr(m, "role", "")
            content = getattr(m, "content", "")[:120]
            if role and content:
                recent_lines.append(f"  {role.capitalize()}: {content}")

        user_input_parts = [
            f"Current message: {message}",
            f"Message kind: {message_kind}",
            f"Current scene: {json.dumps(scene_summary) if scene_summary else '(empty)'}",
        ]
        if recent_lines:
            user_input_parts.append(
                "Recent conversation:\n" + "\n".join(recent_lines)
            )

        response = await llm.ainvoke([
            SystemMessage(content=_SCENE_EXTRACTOR_PROMPT),
            HumanMessage(content="\n".join(user_input_parts)),
        ])

        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        delta = json.loads(raw)
        return delta if isinstance(delta, dict) else {}

    except Exception as exc:
        logger.warning("LLM scene extractor failed: %s", exc)
        return {}


def _apply_llm_scene_delta(
    delta: dict[str, Any],
    scene: SceneMemory,
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """
    Merge a validated LLM scene delta into the scene object.
    Only sets fields that are present in the delta and non-null.
    """
    if not delta:
        return

    if "companions" in delta and isinstance(delta["companions"], list):
        for c in delta["companions"]:
            if c and c not in scene.companions:
                scene.companions.append(c)
                changes.append(f"+companion:{c}(llm)")

    if "target_person" in delta:
        val = delta["target_person"]
        if val is None:
            if scene.target_person:
                changes.append(f"-target_person:{scene.target_person}(llm)")
                scene.target_person = ""
        elif val and not scene.target_person:
            scene.target_person = val
            changes.append(f"target_person={val}(llm)")

    if "occasion" in delta:
        val = delta["occasion"]
        if val is None:
            if scene.occasion:
                changes.append(f"-occasion:{scene.occasion}(llm)")
                scene.occasion = ""
        elif val and not scene.occasion:
            scene.occasion = val
            changes.append(f"occasion={val}(llm)")

    if "goal" in delta:
        val = delta["goal"]
        if val is None:
            if scene.goal:
                changes.append(f"-goal:{scene.goal}(llm)")
                scene.goal = ""
        elif val and not scene.goal:
            scene.goal = val
            changes.append(f"goal={val}(llm)")

    if "visit_type" in delta:
        val = delta["visit_type"]
        if val and not scene.visit_type:
            scene.visit_type = val
            changes.append(f"visit_type={val}(llm)")

    if "budget" in delta:
        val = delta["budget"]
        if val and not scene.budget:
            scene.budget = val
            changes.append(f"budget={val}(llm)")

    if "visit_constraints" in delta and isinstance(delta["visit_constraints"], list):
        for c in delta["visit_constraints"]:
            if c and c not in scene.visit_constraints:
                scene.visit_constraints.append(c)
                changes.append(f"+constraint:{c}(llm)")

    # ── New LLM-extracted fields ──────────────────────────────────────────────

    if "scenario" in delta:
        val = delta["scenario"]
        if val and not scene.scenario:
            scene.scenario = val
            changes.append(f"scenario={val}(llm)")

    if "user_role" in delta:
        val = delta["user_role"]
        if val and not scene.user_role:
            scene.user_role = val
            changes.append(f"user_role={val}(llm)")

    if "style_intent" in delta and isinstance(delta["style_intent"], list):
        for si in delta["style_intent"]:
            if si and si not in scene.style_intent:
                scene.style_intent.append(si)
                changes.append(f"+style_intent:{si}(llm)")

    if "excluded_domains" in delta and isinstance(delta["excluded_domains"], list):
        for domain in delta["excluded_domains"]:
            if domain and domain not in scene.excluded_domains:
                scene.excluded_domains.append(domain)
                changes.append(f"+excluded_domain:{domain}(llm)")

    if "visit_plan" in delta and isinstance(delta["visit_plan"], list):
        if delta["visit_plan"] and not scene.visit_plan:
            scene.visit_plan = [s for s in delta["visit_plan"] if s]
            changes.append(f"visit_plan={scene.visit_plan}(llm)")

    if delta.get("clear_shopping_task"):
        scene.shopping_task = ShoppingTask()
        changes.append("shopping_task cleared(llm:topic_switch)")

    if "shopping_task" in delta and isinstance(delta["shopping_task"], dict) and not delta.get("clear_shopping_task"):
        st = delta["shopping_task"]
        if st.get("product_type") and not scene.shopping_task.product_type:
            scene.shopping_task.product_type = st["product_type"]
            changes.append(f"shopping_task.product_type={st['product_type']}(llm)")
        if st.get("target_person") and not scene.shopping_task.target_person:
            scene.shopping_task.target_person = st["target_person"]
            changes.append(f"shopping_task.target_person={st['target_person']}(llm)")
        if st.get("budget_preference") and not scene.shopping_task.budget_preference:
            scene.shopping_task.budget_preference = st["budget_preference"]
            changes.append(f"shopping_task.budget_preference={st['budget_preference']}(llm)")
        if st.get("use_case") and not scene.shopping_task.use_case:
            scene.shopping_task.use_case = st["use_case"]
            changes.append(f"shopping_task.use_case={st['use_case']}(llm)")
        if st.get("target_gender") and not scene.shopping_task.target_gender:
            scene.shopping_task.target_gender = st["target_gender"]
            changes.append(f"shopping_task.target_gender={st['target_gender']}(llm)")

    if "inferred_scene_notes" in delta and isinstance(delta["inferred_scene_notes"], list):
        for note in delta["inferred_scene_notes"]:
            if note:
                scene_notes.append(f"[llm] {note}")

# ── Age pattern ──────────────────────────────────────────────────────────
# Matches: "5 yr old", "5 year old", "5 years old", "5-year-old"
_AGE_PATTERN = re.compile(r"(\d+)\s*[-\s]?(?:yr|year|years)[-\s]?old", re.I)

# Message kinds that should preserve existing topic/scenario continuity
_CONTINUITY_PRESERVING_KINDS: frozenset[str] = frozenset({
    "followup", "refinement", "constraint_refinement", "context_setting",
})


def _should_preserve_topic_continuity(intent, msg: str) -> bool:
    """
    Return True when the current turn should preserve existing topic / scenario.

    Continuity is preserved for:
    - Any follow-up / refinement / constraint_refinement message kind
    - Context-setting messages (they add scene context, never reset topic)
    - Short messages (≤ 4 words) that are not fresh requests
    """
    if intent.message_kind == "topic_switch":
        return False
    if intent.message_kind in _CONTINUITY_PRESERVING_KINDS:
        return True
    words = msg.strip().split()
    if len(words) <= 4 and intent.message_kind not in ("fresh_request",):
        return True
    return False


def _apply_scene_corrections(
    corrections: list[str],
    scene: "SceneMemory",
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """
    Apply LLM-generated scene correction directives.

    Called at the very start of update_scene_memory so corrections are always
    applied before any additive extraction runs.  This guarantees that explicit
    user denials ("I don't have kids", "I'm alone") override any keyword-based
    companion additions that follow.

    Token formats:
      "all_family_context"     — clear ALL family-related scene fields
      "companion:<name>"       — remove a specific companion entry
      "visit_type:<value>"     — set visit_type to <value> (or clear if "none")
      "visit_type:solo"        — explicitly set solo
      "target_person"          — clear target_person
      "scenario"               — clear scenario
      "audience:family"        — remove family-related audience tags
    """
    if not corrections:
        return

    _FAMILY_COMPANIONS: frozenset[str] = frozenset({
        "family", "child", "kids", "son", "daughter",
    })
    _FAMILY_AUDIENCE_TAGS: frozenset[str] = frozenset({
        "family_friendly", "kid_friendly", "parent_with_child", "family",
    })

    for token in corrections:
        token = token.strip().lower()

        if token == "all_family_context":
            removed = [c for c in scene.companions if c in _FAMILY_COMPANIONS]
            scene.companions = [c for c in scene.companions if c not in _FAMILY_COMPANIONS]
            scene.companion_details = [
                d for d in scene.companion_details if d.get("type") != "child"
            ]
            scene.audience = [a for a in scene.audience if a not in _FAMILY_AUDIENCE_TAGS]
            if scene.visit_type in ("family_visit", "family"):
                scene.visit_type = ""
                changes.append("visit_type cleared(all_family_context)")
            if scene.target_person in ("child", "son", "daughter", "kids"):
                scene.target_person = ""
                changes.append("target_person cleared(all_family_context)")
            if scene.scenario in ("family_outing", "family_shopping", "family_day"):
                scene.scenario = ""
                changes.append("scenario cleared(all_family_context)")
            if scene.implicit_goal and "child" in scene.implicit_goal:
                scene.implicit_goal = ""
                changes.append("implicit_goal cleared(all_family_context)")
            if removed:
                changes.append(f"companions cleared {removed}(all_family_context)")
            scene_notes.append(
                "LLM scene correction: all_family_context removed — "
                f"companions={removed}, visit_type and family tags reset"
            )

        elif token.startswith("companion:"):
            name = token.split(":", 1)[1]
            if name in scene.companions:
                scene.companions.remove(name)
                changes.append(f"-companion:{name}(llm_correction)")
                scene_notes.append(f"LLM correction: removed companion '{name}'")

        elif token.startswith("visit_type:"):
            vtype = token.split(":", 1)[1]
            old = scene.visit_type
            scene.visit_type = "" if vtype == "none" else vtype
            changes.append(f"visit_type={scene.visit_type}(llm_correction, was={old})")
            scene_notes.append(f"LLM correction: visit_type set to '{scene.visit_type}'")

        elif token == "target_person":
            if scene.target_person:
                changes.append(f"-target_person:{scene.target_person}(llm_correction)")
                scene.target_person = ""
                scene_notes.append("LLM correction: target_person cleared")

        elif token == "scenario":
            if scene.scenario:
                changes.append(f"-scenario:{scene.scenario}(llm_correction)")
                scene.scenario = ""
                scene_notes.append("LLM correction: scenario cleared")

        elif token == "audience:family":
            removed = [a for a in scene.audience if a in _FAMILY_AUDIENCE_TAGS]
            scene.audience = [a for a in scene.audience if a not in _FAMILY_AUDIENCE_TAGS]
            if removed:
                changes.append(f"-audience:{removed}(llm_correction)")
                scene_notes.append(f"LLM correction: removed family audience tags {removed}")


@traced_node("update_scene_memory")
async def update_scene_memory(state: ConciergeState) -> dict:
    msg = state.normalized_user_message.lower()
    intent = state.intent
    scene = state.scene.model_copy(deep=True)
    changes: list[str] = []
    scene_notes: list[str] = []

    # ── Apply LLM scene corrections first ────────────────────────────────────
    # Explicit user denials ("I don't have kids", "I'm alone") come through
    # the classifier as scene_corrections and must override anything extracted.
    if intent.scene_corrections:
        _apply_scene_corrections(intent.scene_corrections, scene, changes, scene_notes)

    # ── LLM scene extraction ─────────────────────────────────────────────────
    # Run for all message kinds except those with dedicated fast-exit paths.
    # The LLM handles all field extraction: companions, occasion, budget,
    # scenario, user_role, style_intent, excluded_domains, visit_plan, etc.
    # category_negation is intentionally NOT skipped: the scene-extractor LLM
    # is the correct mechanism to detect and populate excluded_domains when the
    # user explicitly refuses a category (e.g. "no food", "skip dining").
    _SKIP_LLM_EXTRACTION_KINDS = frozenset({
        "acknowledgement", "companion_correction", "disengagement",
    })
    if intent.message_kind not in _SKIP_LLM_EXTRACTION_KINDS:
        llm_delta = await _llm_extract_scene_delta(
            message=state.normalized_user_message,
            scene=scene,
            recent_messages=list(state.messages),
            message_kind=intent.message_kind,
        )
        _apply_llm_scene_delta(llm_delta, scene, changes, scene_notes)

    # ── Acknowledgement: preserve scene exactly, no extraction ───────────────
    if intent.message_kind == "acknowledgement":
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        ack_note = "Acknowledgement turn — scene preserved; bot will ask clarifying question"
        scene_notes.append(ack_note)
        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=scene_notes,
                scene_update_reason="acknowledgement",
                continuity_preserved=True,
                scene_sufficient=_is_scene_sufficient(scene),
            ),
            "_trace_summary": "Acknowledgement: scene preserved, clarification needed",
        }

    # ── Companion correction: corrections already applied above ──────────────
    if intent.message_kind == "companion_correction":
        if any(sig in msg for sig in ("alone", "solo", "by myself", "just me")):
            if "solo" not in scene.companions:
                scene.companions.append("solo")
                changes.append("+companion:solo(companion_correction)")
            if not scene.visit_type or scene.visit_type != "solo":
                scene.visit_type = "solo"
                changes.append("visit_type=solo(companion_correction)")

        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        scene_notes.append(
            f"Companion correction applied: corrections={intent.scene_corrections}, "
            f"companions now={scene.companions}, visit_type={scene.visit_type}"
        )
        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=scene_notes,
                scene_update_reason="companion_correction",
                continuity_preserved=True,
                scene_sufficient=_is_scene_sufficient(scene),
            ),
            "_trace_summary": (
                f"Companion correction: {', '.join(changes) if changes else 'no changes'}"
            ),
        }

    # ── Factual flow: light topic update, LLM delta already applied ──────────
    if state.flow_type == "factual":
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        if intent.domain and intent.domain != "general":
            scene.previous_topic = scene.active_topic
            scene.active_topic = intent.domain
            changes.append(f"light_topic → {intent.domain}")
        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=["factual_flow:light_scene_update"] + scene_notes,
                scene_update_reason="factual_light",
                continuity_preserved=True,
            ),
            "_trace_summary": (
                f"SceneMemory[factual]: {', '.join(changes) or 'no changes'}"
            ),
        }

    # ── Constraint refinement: LLM delta already applied above ───────────────
    if intent.message_kind == "constraint_refinement":
        new_constraints = [c for c in scene.visit_constraints if c not in state.scene.visit_constraints]
        refinement_note = (
            f"Constraint refinement applied: constraints={new_constraints}"
            if new_constraints
            else "Constraint refinement: no new constraints"
        )
        scene_notes.append(refinement_note)
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=scene_notes,
                last_refinement_applied=refinement_note,
                continuity_preserved=True,
                scene_update_reason="constraint_refinement",
                scenario_persisted=bool(scene.scenario),
                topic_switch_detected=False,
                scene_sufficient=_is_scene_sufficient(scene),
            ),
            "_trace_summary": (
                f"Constraint refinement: {', '.join(changes) if changes else 'no changes'}"
            ),
        }

    # ── Category negation: excluded_domains handled by LLM delta ─────────────
    if intent.message_kind == "category_negation":
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        exclusion_note = (
            f"Category negation: excluded_domains={scene.excluded_domains}"
            if scene.excluded_domains
            else "Category negation detected but no domain matched"
        )
        scene_notes.append(exclusion_note)
        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=scene_notes,
                last_refinement_applied=exclusion_note,
                continuity_preserved=True,
                scene_update_reason="category_negation",
                topic_switch_detected=False,
                scene_sufficient=_is_scene_sufficient(scene),
            ),
            "_trace_summary": (
                f"Category negation: excluded_domains={scene.excluded_domains}; "
                f"{', '.join(changes) if changes else 'no changes'}"
            ),
        }

    # ── Disengagement: preserve scene, note frustration ──────────────────────
    if intent.message_kind == "disengagement":
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        disengagement_note = "Disengagement detected — visitor frustrated or dismissing prior content"
        scene_notes.append(disengagement_note)
        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=scene_notes,
                last_refinement_applied=disengagement_note,
                continuity_preserved=True,
                scene_update_reason="disengagement",
                topic_switch_detected=False,
                scene_sufficient=_is_scene_sufficient(scene),
            ),
            "_trace_summary": (
                f"Disengagement: {', '.join(changes) if changes else 'no changes'}"
            ),
        }

    # ── Topic management ──────────────────────────────────────────────────────
    topic_switch_detected = False
    continuity_preserved = False

    if intent.message_kind == "topic_switch":
        topic_switch_detected = True
        scene.previous_topic = scene.active_topic
        scene.active_topic = intent.domain
        scene.active_shortlist = []
        changes.append(f"topic_switch → {intent.domain}")
    elif intent.message_kind == "correction":
        changes.append("correction: strongly overriding scene")
    else:
        preserve = _should_preserve_topic_continuity(intent, msg)
        if preserve and scene.active_topic:
            continuity_preserved = True
            changes.append(f"topic preserved: {scene.active_topic} (continuity turn)")
        elif intent.domain and intent.domain != scene.active_topic:
            scene.previous_topic = scene.active_topic
            scene.active_topic = intent.domain
            changes.append(f"topic → {intent.domain}")

    # ── Topic history ─────────────────────────────────────────────────────────
    if intent.domain and intent.domain not in ("general",):
        if not scene.topic_history or scene.topic_history[-1] != intent.domain:
            scene.topic_history = (scene.topic_history + [intent.domain])[-10:]

    # ── Current need ─────────────────────────────────────────────────────────
    if scene.current_need and scene.current_need != state.normalized_user_message:
        scene.previous_need = scene.current_need
    scene.current_need = state.normalized_user_message

    scenario_persisted = bool(scene.scenario and not topic_switch_detected)
    scene_sufficient = _is_scene_sufficient(scene)

    # ── Reset scene_acknowledged when companions changed this turn ────────────
    if any(c.startswith("+companion:") for c in changes):
        scene.scene_acknowledged = False
        changes.append("scene_acknowledged reset (companions changed)")

    return {
        "scene": scene,
        "debug_enrichment": DebugEnrichment(
            inferred_scene_notes=scene_notes,
            scene_update_reason=intent.message_kind,
            continuity_preserved=continuity_preserved,
            scenario_persisted=scenario_persisted,
            topic_switch_detected=topic_switch_detected,
            scene_sufficient=scene_sufficient,
        ),
        "_trace_summary": f"Scene: {', '.join(changes) if changes else 'no changes'}",
    }


def _is_scene_sufficient(scene: "SceneMemory") -> bool:
    """
    Return True when the scene holds enough context to infer a response
    without asking the visitor a clarifying question.

    A scene is considered sufficient when ANY of the following are true:
    - Companions are known (family, kid, couple, etc.)
    - Budget is stated
    - Target person is explicitly set
    - Occasion is set
    - A specific scenario has been established (e.g. family_outing, date)
    - A shopping task with a product type is active
    - The visitor has declared a user role (bridesmaid, tourist, etc.)
    """
    return bool(
        scene.companions
        or scene.budget
        or (scene.target_person and scene.target_person != "self")
        or scene.occasion
        or scene.scenario
        or (scene.shopping_task.product_type and scene.shopping_task.target_person)
        or scene.user_role
    )


_VAGUE_GIFT_SIGNALS: frozenset[str] = frozenset({
    "gift_recommendation", "gift_search", "gift_ideas",
})

_VAGUE_SHOPPING_SIGNALS: frozenset[str] = frozenset({
    "shopping_general", "general_shopping",
})
