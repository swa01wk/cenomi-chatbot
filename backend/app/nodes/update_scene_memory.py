"""
Update Scene Memory node — enriches the persistent scene from current turn.

CONTRACT
────────
  Purpose:  Extract visitor-context signals from the current message and
            merge them into scene memory.
  Reads:    intent, normalized_user_message, scene
  Writes:   scene (updated SceneMemory)
  Failure:  Parse error → preserve previous scene unchanged + warning
  Routing:  Always → resolve_playbooks

Special behaviors:
  - correction  → strongly override the relevant scene fields
  - topic_switch → archive active_topic → previous_topic, reset shortlist
  - followup    → inherit scene, update current_need only
  - sequential  → advance through visit_plan using completed_steps
"""

from __future__ import annotations
import re

from app.models.state import ConciergeState, SceneMemory
from app.nodes._tracing import traced_node

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
    "family": "family",
    "kids": "family",
    "children": "family",
    "son": "family",
    "daughter": "family",
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
    "after movie": "after_movie",
    "celebration": "celebration",
}

# Visit constraint signals — captured as qualifiers on the visit intent
_VISIT_CONSTRAINT_SIGNALS: list[tuple[str, str]] = [
    ("quick lunch", "quick"),
    ("quick bite", "quick"),
    ("quick meal", "quick"),
    ("quick coffee", "quick"),
    ("just a quick", "quick"),
    ("in a hurry", "quick"),
    ("short visit", "quick"),
    ("not much time", "quick"),
    ("something light", "light"),
    ("light lunch", "light"),
    ("light meal", "light"),
    ("light food", "light"),
    ("light snack", "light"),
    ("not too heavy", "light"),
    ("not too much", "light"),
    ("just a snack", "light"),
    ("something small", "light"),
    ("healthy", "healthy"),
    ("something healthy", "healthy"),
    ("affordable", "affordable"),
    ("something affordable", "affordable"),
    ("not too expensive", "affordable"),
    ("budget friendly", "affordable"),
]

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
# Format: (regex_pattern, join_word)
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

    # ── Topic management ──────────────────────────────────────────────
    if intent.message_kind == "topic_switch":
        scene.previous_topic = scene.active_topic
        scene.active_topic = intent.domain
        scene.active_shortlist = []
        # On explicit topic switch, clear target_person only if
        # the new message explicitly sets a different context
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

    # ── Companion detection ───────────────────────────────────────────
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
    _infer_audience(msg, scene, changes)

    # ── Target person inference ────────────────────────────────────────
    _infer_target_person(msg, scene, changes)

    # ── Visit type inference ──────────────────────────────────────────
    if not scene.visit_type:
        _infer_visit_type(msg, scene, changes)

    # ── Goal inference ────────────────────────────────────────────────
    _infer_goal(msg, scene, changes)

    # ── Visit plan extraction ─────────────────────────────────────────
    _extract_visit_plan(msg, scene, changes)

    # ── Visit constraint extraction ───────────────────────────────────
    _extract_visit_constraints(msg, scene, changes)

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

    return {
        "scene": scene,
        "_trace_summary": f"Scene: {', '.join(changes) if changes else 'no changes'}",
    }


def _infer_audience(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    romantic_cues = ("girlfriend", "boyfriend", "romantic", "date", "wife", "husband", "anniversary")
    if any(cue in msg for cue in romantic_cues):
        if "couple_friendly" not in scene.audience:
            scene.audience.append("couple_friendly")
            changes.append("+audience:couple_friendly")

    family_cues = ("kids", "children", "family", "son", "daughter")
    if any(cue in msg for cue in family_cues):
        for tag in ("family_friendly", "kid_friendly"):
            if tag not in scene.audience:
                scene.audience.append(tag)
                changes.append(f"+audience:{tag}")

    solo_cues = ("alone", "solo", "by myself")
    if any(cue in msg for cue in solo_cues):
        if "solo_friendly" not in scene.audience:
            scene.audience.append("solo_friendly")
            changes.append("+audience:solo_friendly")


def _infer_target_person(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    """Resolve who the current query is about — persists for follow-ups."""
    for signal, person in _TARGET_PERSON_SIGNALS.items():
        if signal in msg:
            scene.target_person = person
            changes.append(f"target_person={person}")
            return

    if not scene.target_person and scene.companions:
        child_companions = {"son", "daughter", "kids"}
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


def _infer_visit_type(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    for signal, vtype in _VISIT_TYPE_SIGNALS.items():
        if signal in msg:
            scene.visit_type = vtype
            changes.append(f"visit_type={vtype}")
            return


def _infer_goal(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    for signal, goal in _GOAL_SIGNALS.items():
        if signal in msg:
            if scene.goal != goal:
                scene.goal = goal
                changes.append(f"goal={goal}")
            return


# ── Visit plan helpers ──────────────────────────────────────────────────────


def _normalize_activity(word: str) -> str:
    """Map a raw word to a normalized activity label."""
    w = word.lower().strip()
    return _ACTIVITY_LABELS.get(w, "")


def _extract_visit_plan(msg: str, scene: SceneMemory, changes: list[str]) -> None:
    """
    Detect multi-activity sequences from the user message and populate
    scene.visit_plan if a new sequence is found.

    Handles patterns like:
      - "shopping and then coffee"
      - "I want to shop, eat, and then dessert"
      - "movie + dinner"
      - "shopping followed by lunch"
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
    msg: str, scene: SceneMemory, changes: list[str],
) -> None:
    """Detect and store visit constraints like 'quick', 'light', 'affordable'."""
    msg_lower = msg.lower()
    for signal, constraint in _VISIT_CONSTRAINT_SIGNALS:
        if signal in msg_lower:
            if constraint not in scene.visit_constraints:
                scene.visit_constraints.append(constraint)
                changes.append(f"+constraint:{constraint}")


def _handle_sequential_query(
    msg: str, scene: SceneMemory, intent, changes: list[str],
) -> None:
    """
    Detect 'after that?', 'what next?' type queries and advance the
    current plan step so the LLM knows which activity we're moving to.

    Also ensures completed_steps and current_plan_step stay in sync
    when the user is moving through a stated visit_plan.
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
