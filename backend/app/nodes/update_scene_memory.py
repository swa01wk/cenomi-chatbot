"""
Update Scene Memory node — enriches the persistent scene from current turn.

CONTRACT
────────
  Purpose:  Extract visitor-context signals from the current message and
            merge them into scene memory.  Behaves like a scene compiler,
            not a shallow memory updater.
  Reads:    intent, normalized_user_message, scene
  Writes:   scene (updated SceneMemory), debug_enrichment (partial)
  Failure:  Parse error → preserve previous scene unchanged + warning
  Routing:  Always → resolve_playbooks

Special behaviors:
  - correction         → strongly override the relevant scene fields
  - topic_switch       → archive active_topic → previous_topic, reset shortlist
  - followup           → inherit scene, update current_need only
  - sequential         → advance through visit_plan using completed_steps
  - constraint_refinement → append constraints only, preserve companions/goal/shortlist
"""

from __future__ import annotations
import re
from typing import Any

from app.models.state import ConciergeState, DebugEnrichment, SceneMemory
from app.nodes._tracing import traced_node

# ── Age pattern ──────────────────────────────────────────────────────────
# Matches: "5 yr old", "5 year old", "5 years old", "5-year-old"
_AGE_PATTERN = re.compile(r"(\d+)\s*[-\s]?(?:yr|year|years)[-\s]?old", re.I)

_COMPANION_SIGNALS: dict[str, str] = {
    "girlfriend": "girlfriend",
    "boyfriend": "boyfriend",
    "wife": "wife",
    "husband": "husband",
    "kids": "kids",
    "children": "kids",
    "son": "son",
    "daughter": "daughter",
    "family": "family",
    "friend": "friend",
    "friends": "friends",
    "alone": "solo",
    "solo": "solo",
    "by myself": "solo",
    # Child age-indicator phrases
    "yr old": "child",
    "year old": "child",
    "years old": "child",
    "toddler": "child",
    "baby": "child",
    "infant": "child",
    "little one": "child",
    "little kid": "child",
}

_TARGET_PERSON_SIGNALS: dict[str, str] = {
    "5 year old": "child",
    "year old son": "child",
    "year old daughter": "child",
    "year old kid": "child",
    "my son": "child",
    "my daughter": "child",
    "my kid": "child",
    "my child": "child",
    "for kids": "child",
    "for children": "child",
    "for my girlfriend": "girlfriend",
    "for my boyfriend": "boyfriend",
    "for my wife": "wife",
    "for my husband": "husband",
    "for her": "girlfriend",
    "for him": "boyfriend",
    "for my mom": "parent",
    "for my mother": "parent",
    "for my dad": "parent",
    "for my father": "parent",
    "for my parents": "parent",
    "for myself": "self",
}

_VISIT_TYPE_SIGNALS: dict[str, str] = {
    "family": "family_visit",
    "kids": "family_visit",
    "children": "family_visit",
    "son": "family_visit",
    "daughter": "family_visit",
    "child": "family_visit",
    "toddler": "family_visit",
    "baby": "family_visit",
    "girlfriend": "couple",
    "boyfriend": "couple",
    "wife": "couple",
    "husband": "couple",
    "date": "couple",
    "romantic": "couple",
    "anniversary": "couple",
    "alone": "solo",
    "solo": "solo",
    "by myself": "solo",
    "friends": "group",
    "friend": "group",
}

_GOAL_SIGNALS: dict[str, str] = {
    "buy": "shopping",
    "shopping": "shopping",
    "shop": "shopping",
    "gift": "gift_shopping",
    "present": "gift_shopping",
    "eat": "dining",
    "food": "dining",
    "hungry": "dining",
    "movie": "entertainment",
    "cinema": "entertainment",
    "fun": "entertainment",
    "play": "kids_activity",
    "explore": "exploration",
    "browse": "browsing",
}

_BUDGET_SIGNALS: dict[str, str] = {
    "cheap": "budget",
    "affordable": "budget",
    "budget": "budget",
    "mid range": "mid_range",
    "moderate": "mid_range",
    "nice": "premium",
    "premium": "premium",
    "upscale": "premium",
    "luxury": "luxury",
    "expensive": "luxury",
    "high end": "luxury",
}

_OCCASION_SIGNALS: dict[str, str] = {
    "birthday": "birthday",
    "anniversary": "anniversary",
    "date": "date",
    "romantic": "date",
    "casual": "casual",
    "just browsing": "casual",
    "quick": "quick_visit",
    "before movie": "before_movie",
    "before the movie": "before_movie",
    "after movie": "after_movie",
    "after the movie": "after_movie",
    "celebration": "celebration",
    "wedding": "wedding",
    "bridesmaid": "wedding",
    "bridal": "wedding",
    "engagement": "wedding",
}

# Pace signals
_PACE_FAST_SIGNALS: frozenset[str] = frozenset({
    "quick", "hurry", "hurrying", "short visit", "fast", "rushed",
    "not much time", "in a hurry", "limited time",
})
_PACE_LEISURELY_SIGNALS: frozenset[str] = frozenset({
    "leisurely", "relaxed", "relax", "chill", "chilling", "slow",
    "take our time", "no rush", "all day",
})

# Visit constraint signals — ordered longest-match first to avoid partial matches
_VISIT_CONSTRAINT_SIGNALS: list[tuple[str, str]] = [
    # Proximity
    ("closer to the cinema", "near_cinema_preferred"),
    ("closer to cinema", "near_cinema_preferred"),
    ("near the cinema", "near_cinema_preferred"),
    ("near cinema", "near_cinema_preferred"),
    ("next to cinema", "near_cinema_preferred"),
    ("close to cinema", "near_cinema_preferred"),
    # Time-sensitive / movie-anchored
    ("before the movie", "time_sensitive"),
    ("before movie", "time_sensitive"),
    ("after the movie", "time_sensitive"),
    ("after movie", "time_sensitive"),
    # Kid-friendly requirement
    ("kid friendly", "kid_friendly_required"),
    ("kid-friendly", "kid_friendly_required"),
    ("child friendly", "kid_friendly_required"),
    ("child-friendly", "kid_friendly_required"),
    # Quick / fast
    ("quick stop", "quick_stop_preferred"),
    ("quick visit", "quick_stop_preferred"),
    ("quick lunch", "quick"),
    ("quick bite", "quick"),
    ("quick meal", "quick"),
    ("quick coffee", "quick"),
    ("just a quick", "quick"),
    ("in a hurry", "quick"),
    ("short visit", "quick"),
    ("not much time", "quick"),
    ("something quick", "quick_stop_preferred"),
    ("something quicker", "quick_stop_preferred"),
    ("quicker", "quick_stop_preferred"),
    # Budget sensitivity
    ("not too expensive", "budget_sensitive"),
    ("not expensive", "budget_sensitive"),
    ("not too pricey", "budget_sensitive"),
    ("something affordable", "budget_sensitive"),
    ("more affordable", "budget_sensitive"),
    ("budget friendly", "budget_sensitive"),
    ("budget-friendly", "budget_sensitive"),
    # Light
    ("something light", "light"),
    ("light lunch", "light"),
    ("light meal", "light"),
    ("light food", "light"),
    ("light snack", "light"),
    ("not too heavy", "light"),
    ("not too much", "light"),
    ("just a snack", "light"),
    ("something small", "light"),
    # Healthy
    ("healthy", "healthy"),
    ("something healthy", "healthy"),
    # Affordable (legacy)
    ("affordable", "affordable"),
]

# ── User role signals ────────────────────────────────────────────────────────
# Maps message signals to canonical user_role labels.

_USER_ROLE_SIGNALS: list[tuple[tuple[str, ...], str, list[str]]] = [
    # (trigger phrases, role, style_intents)
    (("bridesmaid", "brides maid", "bridesmaids"), "bridesmaid", ["elegant", "occasion_wear"]),
    (("bride",), "bride", ["elegant", "occasion_wear", "premium"]),
    (("groom",), "groom", ["elegant", "occasion_wear"]),
    (("maid of honor", "maid of honour"), "maid_of_honor", ["elegant", "occasion_wear"]),
    (("best man",), "best_man", ["elegant", "occasion_wear"]),
    (("mother of the bride", "mother of bride"), "mother_of_bride", ["elegant", "occasion_wear"]),
    (("father of the bride", "father of bride"), "father_of_bride", ["elegant", "occasion_wear"]),
    (("tourist",), "tourist", ["exploration"]),
    (("first time visitor", "first time here", "first visit"), "first_time_visitor", ["exploration"]),
]

# ── Scenario signals ──────────────────────────────────────────────────────────
# Scenario is the overarching real-world context.

_SCENARIO_MAP: list[tuple[tuple[str, ...], str]] = [
    # Wedding-related (highest priority for role-based detection)
    (("bridesmaid", "bride", "groom", "wedding", "maid of honor", "bridal",
      "engagement", "hen party", "bachelorette"), "wedding_related"),
    # Family outing
    (("with my kid", "with my kids", "with the kid", "with my child",
      "with my son", "with my daughter", "with my family",
      "family outing", "family trip", "family day"), "family_outing"),
    # Date / couple
    (("date night", "date plan", "anniversary", "romantic evening",
      "just the two of us"), "date"),
    # Birthday
    (("birthday", "celebrating", "birthday party"), "birthday"),
    # Quick visit
    (("quick visit", "short visit", "in a hurry", "not much time",
      "just passing", "quick stop"), "quick_visit"),
    # First visit
    (("first time", "first visit", "never been here"), "first_visit"),
    # Gift shopping
    (("gift for", "present for", "buying a gift"), "gift_shopping"),
]

# ── Style intent signals ──────────────────────────────────────────────────────

_STYLE_INTENT_SIGNALS: list[tuple[tuple[str, ...], list[str]]] = [
    (("elegant", "classy", "sophisticated", "upscale", "refined"), ["elegant"]),
    (("occasion", "occasion wear", "formal", "dressy", "event"), ["occasion_wear"]),
    (("romantic", "intimate", "cozy"), ["romantic"]),
    (("casual", "relaxed", "laid back", "chill"), ["casual"]),
    (("practical", "functional", "everyday"), ["practical"]),
    (("quick", "fast", "hurry", "efficient"), ["quick"]),
    (("luxury", "premium", "high end", "designer"), ["luxury", "premium"]),
    (("budget", "affordable", "cheap", "value"), ["budget"]),
    (("fun", "playful", "exciting", "lively"), ["fun"]),
]


def _extract_user_role(
    msg: str,
    scene: SceneMemory,
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """Extract user_role and associated style_intent from message."""
    if scene.user_role:
        return  # Don't override an already-set role
    for triggers, role, style_intents in _USER_ROLE_SIGNALS:
        if any(t in msg for t in triggers):
            scene.user_role = role
            changes.append(f"user_role={role}")
            scene_notes.append(f"Detected user_role='{role}' from message")
            # Add style intents
            for si in style_intents:
                if si not in scene.style_intent:
                    scene.style_intent.append(si)
            if style_intents:
                changes.append(f"style_intent={style_intents}")
            return


def _extract_scenario(
    msg: str,
    scene: SceneMemory,
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """
    Extract the overarching visit scenario.

    Priority: explicit message signals > existing occasion > companion inference.
    Does NOT override a more specific scenario already set.
    """
    # Wedding/role scenario always wins (highest priority)
    if scene.user_role in ("bridesmaid", "bride", "groom", "maid_of_honor",
                           "best_man", "mother_of_bride", "father_of_bride"):
        if scene.scenario != "wedding_related":
            scene.scenario = "wedding_related"
            changes.append("scenario=wedding_related(from_user_role)")
            scene_notes.append("Scenario set to wedding_related from user_role")
        return

    if scene.scenario:
        return  # Already set — preserve it

    for triggers, scenario in _SCENARIO_MAP:
        if any(t in msg for t in triggers):
            scene.scenario = scenario
            changes.append(f"scenario={scenario}")
            scene_notes.append(f"Detected scenario='{scenario}'")
            return


def _extract_style_intent(
    msg: str,
    scene: SceneMemory,
    changes: list[str],
) -> None:
    """Extract style_intent modifiers from message."""
    for triggers, intents in _STYLE_INTENT_SIGNALS:
        for trigger in triggers:
            if trigger in msg:
                for si in intents:
                    if si not in scene.style_intent:
                        scene.style_intent.append(si)
                        changes.append(f"+style_intent:{si}")


# Activity label normalization for visit plan extraction
_ACTIVITY_LABELS: dict[str, str] = {
    # Dining
    "eat": "dining",
    "food": "dining",
    "lunch": "dining",
    "dinner": "dining",
    "breakfast": "dining",
    "meal": "dining",
    "restaurant": "dining",
    "bite": "dining",
    # Café / dessert
    "coffee": "coffee",
    "cafe": "coffee",
    "café": "coffee",
    "dessert": "dessert",
    "sweet": "dessert",
    "ice cream": "dessert",
    "sweets": "dessert",
    # Shopping
    "shop": "shopping",
    "shopping": "shopping",
    "buy": "shopping",
    "browse": "shopping",
    "gift": "shopping",
    "gifts": "shopping",
    "store": "shopping",
    # Entertainment
    "movie": "movie",
    "cinema": "movie",
    "film": "movie",
    "fun": "entertainment",
    "play": "entertainment",
    "entertainment": "entertainment",
    "kids zone": "entertainment",
    # Kids activities
    "kids": "kids_activity",
    "children": "kids_activity",
    "play area": "kids_activity",
}

# Patterns that indicate sequential multi-activity plans
_SEQUENCE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:and\s+then|then)\b", re.I),
    re.compile(r"\b(?:followed\s+by|after\s+that|after\s+which)\b", re.I),
    re.compile(r"\band\b.*\bafter\b", re.I),
    re.compile(r"\+", re.I),
]

# Sequential query signals — queries that mean "next step in the plan"
_SEQUENTIAL_QUERY_PATTERNS: list[re.Pattern] = [
    re.compile(r"^after\s+that\?*$", re.I),
    re.compile(r"^what\s+(about\s+)?next\?*$", re.I),
    re.compile(r"^and\s+then\?*$", re.I),
    re.compile(r"^then\s+what\?*$", re.I),
    re.compile(r"^what\s+else\?*$", re.I),
    re.compile(r"^next\?*$", re.I),
    re.compile(r"^after\s+\w+\?*$", re.I),  # "after lunch?"
]


@traced_node("update_scene_memory")
async def update_scene_memory(state: ConciergeState) -> dict:
    msg = state.normalized_user_message.lower()
    intent = state.intent
    scene = state.scene.model_copy(deep=True)
    changes: list[str] = []
    scene_notes: list[str] = []

    # ── Factual flow: light scene update ─────────────────────────────
    # In factual flow, we preserve topic continuity AND extract companion
    # signals so that hybrid queries ("any movies with the kid?") still
    # populate scene memory correctly for follow-up filtering.
    if state.flow_type == "factual":
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message
        if intent.domain and intent.domain != "general":
            scene.previous_topic = scene.active_topic
            scene.active_topic = intent.domain
            changes.append(f"light_topic → {intent.domain}")

        # Still extract companion/child signals so hybrid queries work.
        # Also extract role/scenario in case user declares context on factual turn.
        _extract_user_role(msg, scene, changes, scene_notes)
        _extract_scenario(msg, scene, changes, scene_notes)
        _extract_style_intent(msg, scene, changes)
        _extract_hybrid_companions(msg, scene, changes, scene_notes)
        _infer_audience(msg, scene, changes, scene_notes)
        _infer_target_person(msg, scene, changes)
        _extract_visit_constraints(msg, scene, changes, scene_notes)

        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=["factual_flow:light_scene_update"] + scene_notes,
            ),
            "_trace_summary": (
                f"SceneMemory[factual]: light + companion update — "
                f"{', '.join(changes) or 'no changes'}"
            ),
        }

    # ── Constraint refinement: preserve scene, only add constraints ──────
    if intent.message_kind == "constraint_refinement":
        _extract_visit_constraints(msg, scene, changes, scene_notes)
        # Record what refinement was applied
        new_constraints = [c for c in scene.visit_constraints if c not in state.scene.visit_constraints]
        refinement_note = f"Constraint refinement applied: {new_constraints}" if new_constraints else "Constraint refinement (no new constraints detected)"
        scene_notes.append(refinement_note)
        scene.inferred_scene_notes = (scene.inferred_scene_notes or []) + scene_notes

        # Update current_need but keep everything else
        if scene.current_need:
            scene.previous_need = scene.current_need
        scene.current_need = state.normalized_user_message

        return {
            "scene": scene,
            "debug_enrichment": DebugEnrichment(
                inferred_scene_notes=scene_notes,
                last_refinement_applied=refinement_note,
            ),
            "_trace_summary": f"Constraint refinement: {', '.join(changes) if changes else 'no changes'}",
        }

    # ── Topic management ──────────────────────────────────────────────
    if intent.message_kind == "topic_switch":
        scene.previous_topic = scene.active_topic
        scene.active_topic = intent.domain
        scene.active_shortlist = []
        if any(sig in msg for sig in _TARGET_PERSON_SIGNALS):
            pass  # will be re-set by _infer_target_person below
        changes.append(f"topic_switch → {intent.domain}")
    elif intent.message_kind == "correction":
        changes.append("correction: strongly overriding scene")
    else:
        if intent.domain and intent.domain != scene.active_topic:
            scene.previous_topic = scene.active_topic
            scene.active_topic = intent.domain
            changes.append(f"topic → {intent.domain}")

    # ── Age extraction (must run before companion detection) ────────
    _extract_child_age(msg, scene, changes, scene_notes)

    # ── User role extraction (must run early — influences scenario) ──
    _extract_user_role(msg, scene, changes, scene_notes)

    # ── Scenario extraction (depends on user_role being set first) ──
    _extract_scenario(msg, scene, changes, scene_notes)

    # ── Style intent extraction ────────────────────────────────────
    _extract_style_intent(msg, scene, changes)

    # ── Enhanced hybrid companion extraction (catches "with the kid" etc.) ──
    _extract_hybrid_companions(msg, scene, changes, scene_notes)

    # ── Companion detection (simple keyword dict, additive) ───────────
    for signal, companion in _COMPANION_SIGNALS.items():
        if signal in msg and companion not in scene.companions:
            if intent.message_kind == "correction":
                scene.companions = [companion]
            else:
                scene.companions.append(companion)
            changes.append(f"+companion:{companion}")

    # ── Budget detection ──────────────────────────────────────────────
    for signal, budget in _BUDGET_SIGNALS.items():
        if signal in msg:
            scene.budget = budget
            changes.append(f"budget={budget}")
            break

    # ── Occasion detection ────────────────────────────────────────────
    for signal, occasion in _OCCASION_SIGNALS.items():
        if signal in msg:
            scene.occasion = occasion
            changes.append(f"occasion={occasion}")
            break

    # ── Audience inference ────────────────────────────────────────────
    _infer_audience(msg, scene, changes, scene_notes)

    # ── Target person inference ────────────────────────────────────────
    _infer_target_person(msg, scene, changes)

    # ── Visit type inference ──────────────────────────────────────────
    _infer_visit_type(msg, scene, changes, scene_notes)

    # ── Pace inference ────────────────────────────────────────────────
    _infer_pace(msg, scene, changes)

    # ── Goal inference ────────────────────────────────────────────────
    _infer_goal(msg, scene, changes)

    # ── Implicit goal inference ───────────────────────────────────────
    _infer_implicit_goal(msg, scene, intent, changes, scene_notes)

    # ── Visit plan extraction ─────────────────────────────────────────
    _extract_visit_plan(msg, scene, changes)

    # ── Visit constraint extraction ───────────────────────────────────
    _extract_visit_constraints(msg, scene, changes, scene_notes)

    # ── Sequential query detection ────────────────────────────────────
    _handle_sequential_query(msg, scene, intent, changes)

    # ── Topic history ─────────────────────────────────────────────────
    if intent.domain and intent.domain not in ("general",):
        if not scene.topic_history or scene.topic_history[-1] != intent.domain:
            scene.topic_history = (scene.topic_history + [intent.domain])[-10:]

    # ── Current need (preserve previous for follow-up context) ───────
    if scene.current_need and scene.current_need != state.normalized_user_message:
        scene.previous_need = scene.current_need
    scene.current_need = state.normalized_user_message

    # ── Persist inferred notes ────────────────────────────────────────
    scene.inferred_scene_notes = (scene.inferred_scene_notes or []) + scene_notes

    debug = DebugEnrichment(
        inferred_scene_notes=scene_notes,
    )

    return {
        "scene": scene,
        "debug_enrichment": debug,
        "_trace_summary": f"Scene: {', '.join(changes) if changes else 'no changes'}",
    }


# ── Age extraction ──────────────────────────────────────────────────────────


def _extract_child_age(
    msg: str, scene: SceneMemory, changes: list[str], scene_notes: list[str],
) -> None:
    """Extract child age from phrases like '5 yr old', '3 year old daughter'."""
    match = _AGE_PATTERN.search(msg)
    if not match:
        return

    age = int(match.group(1))
    detail: dict[str, Any] = {"type": "child", "age": age}

    # Add to companion_details if not already recorded
    existing_ages = {d.get("age") for d in scene.companion_details if d.get("type") == "child"}
    if age not in existing_ages:
        scene.companion_details.append(detail)
        changes.append(f"companion_detail:child_age={age}")
        scene_notes.append(
            f"Detected parent_with_child from age reference '{match.group(0)}' (age {age})"
        )

    # Ensure "child" is in companions
    if "child" not in scene.companions:
        scene.companions.append("child")
        changes.append("+companion:child(from_age)")

    # Force family visit type
    if not scene.visit_type:
        scene.visit_type = "family_visit"
        changes.append("visit_type=family_visit(from_child_age)")
        scene_notes.append("Inferred family_visit from child age mention")


# ── Enhanced hybrid companion extraction ─────────────────────────────────────
# These patterns capture "with the kid", "with my child", "for the kids",
# "any movies with the kid", etc. that the simple _COMPANION_SIGNALS dict misses.

_HYBRID_CHILD_SIGNALS: tuple[str, ...] = (
    "with the kid", "with my kid", "with my kids", "with kids",
    "with child", "with my child", "with the children", "with children",
    "with my daughter", "with my son",
    "for the kid", "for my kid", "for the kids", "for kids",
    "for my children", "for children", "for child",
    "movies with", "movies for kids", "movies for children",
    "any movies with", "kid friendly", "kid-friendly",
    "child friendly", "child-friendly",
    "little one", "little kid",
    "my 5", "my 4", "my 6", "my 7", "my 8", "my 3",  # age-prefix companions
)

_HYBRID_COMPANION_SIGNALS: list[tuple[tuple[str, ...], str, str]] = [
    # (keywords, companion_value, visit_type)
    (("with my girlfriend", "my girlfriend"), "girlfriend", "couple"),
    (("with my boyfriend", "my boyfriend"), "boyfriend", "couple"),
    (("with my wife", "my wife", "with wife"), "wife", "couple"),
    (("with my husband", "my husband", "with husband"), "husband", "couple"),
    (("with friends", "with my friends", "with a friend"), "friends", "group"),
    (("with family", "with my family"), "family", "family_visit"),
]


def _extract_hybrid_companions(
    msg: str,
    scene: SceneMemory,
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """
    Strong companion extraction that picks up hybrid patterns missed by
    the simple keyword dict approach.  Idempotent — safe to call multiple
    times.
    """
    # ── Child/kid patterns ────────────────────────────────────────────
    child_detected = any(sig in msg for sig in _HYBRID_CHILD_SIGNALS)
    if child_detected:
        if "child" not in scene.companions:
            scene.companions.append("child")
            changes.append("+companion:child(hybrid_pattern)")
            scene_notes.append("Detected child companion via hybrid pattern matching")
        if not scene.visit_type:
            scene.visit_type = "family_visit"
            changes.append("visit_type=family_visit(hybrid)")
        elif scene.visit_type not in ("family_visit", "family"):
            scene.visit_type = "family_visit"
            changes.append("visit_type=family_visit(hybrid_override)")

    # ── Couple/group/family patterns ──────────────────────────────────
    for keywords, companion_val, visit_type_val in _HYBRID_COMPANION_SIGNALS:
        if any(kw in msg for kw in keywords):
            if companion_val not in scene.companions:
                scene.companions.append(companion_val)
                changes.append(f"+companion:{companion_val}(hybrid)")
            if not scene.visit_type:
                scene.visit_type = visit_type_val
                changes.append(f"visit_type={visit_type_val}(hybrid)")


# ── Audience inference ───────────────────────────────────────────────────────


def _infer_audience(
    msg: str, scene: SceneMemory, changes: list[str], scene_notes: list[str],
) -> None:
    romantic_cues = ("girlfriend", "boyfriend", "romantic", "date", "wife", "husband", "anniversary")
    if any(cue in msg for cue in romantic_cues):
        if "couple_friendly" not in scene.audience:
            scene.audience.append("couple_friendly")
            changes.append("+audience:couple_friendly")

    family_cues = (
        "kids", "children", "family", "son", "daughter", "child", "toddler", "baby",
        "with the kid", "with my kid", "with kids", "with child", "with my child",
        "for kids", "for children", "kid friendly", "child friendly",
    )
    child_detected = (
        any(cue in msg for cue in family_cues)
        or any(d.get("type") == "child" for d in scene.companion_details)
        or "child" in scene.companions
    )
    if child_detected:
        for tag in ("family_friendly", "kid_friendly"):
            if tag not in scene.audience:
                scene.audience.append(tag)
                changes.append(f"+audience:{tag}")
        if "parent_with_child" not in scene.audience:
            scene.audience.append("parent_with_child")
            changes.append("+audience:parent_with_child")
        if "family" not in scene.audience:
            scene.audience.append("family")
            changes.append("+audience:family")
        scene_notes.append("Inferred family / parent_with_child audience")

    solo_cues = ("alone", "solo", "by myself")
    if any(cue in msg for cue in solo_cues):
        if "solo_friendly" not in scene.audience:
            scene.audience.append("solo_friendly")
            changes.append("+audience:solo_friendly")


# ── Target person inference ─────────────────────────────────────────────────


def _infer_target_person(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    """Resolve who the current query is about — persists for follow-ups."""
    for signal, person in _TARGET_PERSON_SIGNALS.items():
        if signal in msg:
            scene.target_person = person
            changes.append(f"target_person={person}")
            return

    if not scene.target_person and scene.companions:
        child_companions = {"son", "daughter", "kids", "child"}
        partner_companions = {"girlfriend", "boyfriend", "wife", "husband"}
        if child_companions & set(scene.companions):
            scene.target_person = "child"
            changes.append("target_person=child (inferred from companions)")
        elif partner_companions & set(scene.companions):
            last = next(
                (c for c in reversed(scene.companions) if c in partner_companions),
                "",
            )
            if last:
                scene.target_person = last
                changes.append(f"target_person={last} (inferred from companions)")


# ── Visit type inference ────────────────────────────────────────────────────


def _infer_visit_type(
    msg: str, scene: SceneMemory, changes: list[str], scene_notes: list[str],
) -> None:
    if scene.visit_type:
        return
    # Check companion_details for child first
    if any(d.get("type") == "child" for d in scene.companion_details):
        scene.visit_type = "family_visit"
        changes.append("visit_type=family_visit")
        return
    for signal, vtype in _VISIT_TYPE_SIGNALS.items():
        if signal in msg:
            scene.visit_type = vtype
            changes.append(f"visit_type={vtype}")
            return


# ── Pace inference ──────────────────────────────────────────────────────────


def _infer_pace(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    if scene.pace:
        return
    if any(sig in msg for sig in _PACE_FAST_SIGNALS):
        scene.pace = "fast"
        changes.append("pace=fast")
    elif any(sig in msg for sig in _PACE_LEISURELY_SIGNALS):
        scene.pace = "leisurely"
        changes.append("pace=leisurely")
    elif scene.companions or scene.visit_type:
        # Default for family/couple visits
        scene.pace = "moderate"
        changes.append("pace=moderate (default)")


# ── Goal inference ──────────────────────────────────────────────────────────


def _infer_goal(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    for signal, goal in _GOAL_SIGNALS.items():
        if signal in msg:
            if scene.goal != goal:
                scene.goal = goal
                changes.append(f"goal={goal}")
            return


# ── Implicit goal inference ─────────────────────────────────────────────────


def _infer_implicit_goal(
    msg: str,
    scene: SceneMemory,
    intent,
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """Infer a rich implicit goal from the combination of scene signals."""
    if scene.implicit_goal:
        return

    has_child = (
        "child" in scene.companions
        or any(d.get("type") == "child" for d in scene.companion_details)
    )
    has_partner = bool(
        {"girlfriend", "boyfriend", "wife", "husband"} & set(scene.companions)
    )

    if has_child and scene.goal == "shopping":
        scene.implicit_goal = "shopping while keeping child engaged"
        scene_notes.append("Inferred implicit goal: shopping while keeping child engaged")
        changes.append("implicit_goal=shopping_with_child")
    elif has_child and scene.goal == "dining":
        scene.implicit_goal = "family-friendly dining"
        scene_notes.append("Inferred implicit goal: family-friendly dining")
        changes.append("implicit_goal=family_dining")
    elif has_child and intent.domain == "shopping":
        scene.implicit_goal = "shopping while keeping child engaged"
        scene_notes.append("Inferred implicit goal: shopping while keeping child engaged")
        changes.append("implicit_goal=shopping_with_child")
    elif has_child and intent.domain == "entertainment":
        scene.implicit_goal = "entertainment suitable for the whole family"
        changes.append("implicit_goal=family_entertainment")
    elif has_partner and scene.goal == "gift_shopping":
        scene.implicit_goal = "finding a gift for partner"
        scene_notes.append("Inferred implicit goal: finding a gift for partner")
        changes.append("implicit_goal=gift_for_partner")
    elif scene.occasion == "before_movie":
        scene.implicit_goal = "activity before the movie — time-sensitive"
        scene_notes.append("Inferred implicit goal: activity before the movie")
        changes.append("implicit_goal=before_movie_activity")
    elif scene.occasion == "after_movie":
        scene.implicit_goal = "activity after the movie — casual winding down"
        changes.append("implicit_goal=after_movie_activity")
    elif has_partner and intent.domain in ("dining", "shopping", "entertainment"):
        scene.implicit_goal = "enjoyable outing with partner"
        changes.append("implicit_goal=couple_outing")


# ── Visit plan helpers ───────────────────────────────────────────────────────


def _normalize_activity(word: str) -> str:
    """Map a raw word to a normalized activity label."""
    w = word.lower().strip()
    return _ACTIVITY_LABELS.get(w, "")


def _extract_visit_plan(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    """
    Detect multi-activity sequences from the user message and populate
    scene.visit_plan if a new sequence is found.
    """
    is_sequential = any(p.search(msg) for p in _SEQUENCE_PATTERNS)
    if not is_sequential:
        return

    words = re.split(r"\band\s+then\b|\bthen\b|\bfollowed\s+by\b|\bafter\s+that\b|\+|,\s*", msg, flags=re.I)
    activities: list[str] = []
    for chunk in words:
        chunk = chunk.strip()
        for raw, label in _ACTIVITY_LABELS.items():
            if raw in chunk.lower() and label not in activities:
                activities.append(label)
                break

    if len(activities) >= 2:
        scene.visit_plan = activities
        scene.multi_activity_mode = True
        if not scene.current_plan_step and activities:
            scene.current_plan_step = activities[0]
        changes.append(f"visit_plan={activities}")


def _extract_visit_constraints(
    msg: str,
    scene: SceneMemory,
    changes: list[str],
    scene_notes: list[str],
) -> None:
    """Detect and store visit constraints like 'quick', 'budget_sensitive', 'near_cinema_preferred'."""
    msg_lower = msg.lower()
    for signal, constraint in _VISIT_CONSTRAINT_SIGNALS:
        if signal in msg_lower:
            if constraint not in scene.visit_constraints:
                scene.visit_constraints.append(constraint)
                changes.append(f"+constraint:{constraint}")
                scene_notes.append(f"Inferred constraint '{constraint}' from phrase '{signal}'")


def _handle_sequential_query(
    msg: str, scene: SceneMemory, intent, changes: list[str],
) -> None:
    """
    Detect 'after that?', 'what next?' type queries and advance the
    current plan step so the LLM knows which activity we're moving to.
    """
    is_sequential = any(p.match(msg.strip()) for p in _SEQUENTIAL_QUERY_PATTERNS)

    if is_sequential and scene.visit_plan:
        current = scene.current_plan_step
        if current and current not in scene.completed_steps:
            scene.completed_steps.append(current)
            changes.append(f"completed:{current}")
        try:
            idx = scene.visit_plan.index(current)
            if idx + 1 < len(scene.visit_plan):
                scene.current_plan_step = scene.visit_plan[idx + 1]
                changes.append(f"plan_step→{scene.current_plan_step}")
        except (ValueError, IndexError):
            pass
        return

    # Non-sequential: mark current active_topic as completed when moving on
    if (
        scene.active_topic
        and intent.domain
        and intent.domain != scene.active_topic
        and intent.domain not in ("general",)
        and scene.active_topic not in scene.completed_steps
    ):
        scene.completed_steps = (scene.completed_steps + [scene.active_topic])[-8:]
        changes.append(f"completed:{scene.active_topic}")

    # Update current_plan_step from visit_plan if intent matches next step
    if scene.visit_plan and intent.domain:
        domain = intent.domain
        for i, step in enumerate(scene.visit_plan):
            step_domain = _ACTIVITY_LABELS.get(step, step)
            if step_domain == domain and step not in scene.completed_steps:
                scene.current_plan_step = step
                changes.append(f"plan_step={step}")
                break
