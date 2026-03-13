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
"""

from __future__ import annotations

from app.models.state import ConciergeState, SceneMemory
from app.nodes._tracing import traced_node

_COMPANION_SIGNALS: dict[str, str] = {
    "girlfriend": "girlfriend",
    "boyfriend": "boyfriend",
    "wife": "wife",
    "husband": "husband",
    "kids": "kids",
    "children": "kids",
    "family": "family",
    "friend": "friend",
    "friends": "friends",
    "alone": "solo",
    "solo": "solo",
    "by myself": "solo",
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

    # ── Current need ──────────────────────────────────────────────────
    scene.current_need = state.normalized_user_message

    # TODO: Replace keyword heuristics with LLM-based scene extraction.

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
