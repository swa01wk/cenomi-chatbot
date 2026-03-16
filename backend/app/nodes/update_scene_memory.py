"""
Update Scene Memory node — enriches the persistent scene from current turn.

CONTRACT
────────
  Purpose:  Extract visitor-context signals from the current message and
            merge them into scene memory.  Run the ContinuityResolver to
            formally classify the message and determine thread preservation.
            Preserve the prior domain/topic unless the resolver says to switch.
            Update the continuity anchor and thread preservation decision.
  Reads:    intent, normalized_user_message, scene, continuity_anchor, messages
  Writes:   scene (updated SceneMemory), continuity_anchor,
            thread_preservation (ThreadPreservationDecision),
            continuity_resolution (ContinuityResolution)
  Failure:  Parse error → preserve previous scene unchanged + warning
  Routing:  Always → resolve_playbooks

Special behaviors:
  - correction      → strongly override the relevant scene fields
  - topic_switch    → archive active_topic → previous_topic, reset shortlist,
                       reset continuity_anchor
  - refinement      → preserve active domain/topic, update subtopic/audience/budget
  - followup        → inherit scene, update current_need only
  - is_refinement_of_current_topic=True → never reset domain/topic
"""

from __future__ import annotations

from app.models.state import (
    ConciergeState,
    ContinuityAnchor,
    SceneMemory,
    ThreadPreservationDecision,
)
from app.nodes._tracing import traced_node
from app.services.continuity_resolver import ContinuityResolver

_resolver = ContinuityResolver()

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
    anchor = state.continuity_anchor.model_copy(deep=True)
    changes: list[str] = []

    # ── Run continuity resolver ─────────────────────────────────────────
    continuity = _resolver.resolve(
        message=state.normalized_user_message,
        intent=intent,
        scene=scene,
        anchor=anchor,
        history_len=len(state.messages),
    )
    changes.append(f"continuity={continuity.continuity_type}({continuity.thread_action})")

    # ── Apply continuity decision to scene/anchor ───────────────────────
    preserve_topic = True
    preserve_reason = continuity.reason

    if continuity.thread_action == "archive_and_switch":
        scene.previous_topic = scene.active_topic
        scene.active_topic = continuity.inherited_domain or intent.domain
        scene.active_shortlist = []
        anchor = ContinuityAnchor(domain=continuity.inherited_domain or intent.domain)
        preserve_topic = False
        changes.append(f"topic_switch → {scene.active_topic}")

        if not continuity.should_carry_audience:
            scene.audience = []
        if not continuity.should_carry_budget:
            scene.budget = ""

    elif continuity.thread_action == "reset":
        if intent.domain and intent.domain != scene.active_topic:
            scene.previous_topic = scene.active_topic
            scene.active_topic = intent.domain
            anchor = ContinuityAnchor(domain=intent.domain)
            preserve_topic = False
            changes.append(f"fresh → {intent.domain}")

    elif continuity.thread_action in ("extend", "preserve"):
        # Preserve the thread — carry forward domain/topic
        if continuity.inherited_domain and not anchor.domain:
            anchor.domain = continuity.inherited_domain
        if continuity.inherited_topic and not anchor.topic:
            anchor.topic = continuity.inherited_topic

        if continuity.continuity_type == "correction":
            changes.append("correction: overriding relevant fields")
        else:
            changes.append(f"extending thread: dims={continuity.refinement_dimensions}")

    # ── Companion detection ───────────────────────────────────────────
    for signal, companion in _COMPANION_SIGNALS.items():
        if signal in msg and companion not in scene.companions:
            if continuity.continuity_type == "correction":
                scene.companions = [companion]
            else:
                scene.companions.append(companion)
            changes.append(f"+companion:{companion}")

    # ── Budget detection ──────────────────────────────────────────────
    for signal, budget in _BUDGET_SIGNALS.items():
        if signal in msg:
            scene.budget = budget
            anchor.budget = budget
            changes.append(f"budget={budget}")
            break

    # ── Occasion detection ────────────────────────────────────────────
    for signal, occasion in _OCCASION_SIGNALS.items():
        if signal in msg:
            scene.occasion = occasion
            anchor.occasion = occasion
            changes.append(f"occasion={occasion}")
            break

    # ── Audience inference ────────────────────────────────────────────
    _infer_audience(msg, scene, anchor, changes)

    # ── Current need ──────────────────────────────────────────────────
    scene.current_need = state.normalized_user_message

    # ── Update continuity anchor topic/subtopic ───────────────────────
    if scene.active_topic and not anchor.domain:
        anchor.domain = scene.active_topic
    if intent.sub_intent:
        anchor.topic = intent.sub_intent
    if intent.detected_refinement_cues:
        anchor.subtopic = " + ".join(intent.detected_refinement_cues)

    thread_decision = ThreadPreservationDecision(
        preserve_current_topic=preserve_topic,
        reason=preserve_reason,
    )

    return {
        "scene": scene,
        "continuity_anchor": anchor,
        "thread_preservation": thread_decision,
        "continuity_resolution": continuity,
        "_trace_summary": (
            f"Scene: {', '.join(changes)} | "
            f"preserve={preserve_topic} | "
            f"continuity={continuity.continuity_type}"
        ),
    }


def _infer_audience(
    msg: str,
    scene: SceneMemory,
    anchor: ContinuityAnchor,
    changes: list[str],
) -> None:
    romantic_cues = ("girlfriend", "boyfriend", "romantic", "date", "wife", "husband", "anniversary")
    if any(cue in msg for cue in romantic_cues):
        if "couple_friendly" not in scene.audience:
            scene.audience.append("couple_friendly")
            changes.append("+audience:couple_friendly")
        anchor.audience = "couple"

    family_cues = ("kids", "children", "family", "son", "daughter")
    if any(cue in msg for cue in family_cues):
        for tag in ("family_friendly", "kid_friendly"):
            if tag not in scene.audience:
                scene.audience.append(tag)
                changes.append(f"+audience:{tag}")
        anchor.audience = "kids"

    solo_cues = ("alone", "solo", "by myself")
    if any(cue in msg for cue in solo_cues):
        if "solo_friendly" not in scene.audience:
            scene.audience.append("solo_friendly")
            changes.append("+audience:solo_friendly")
        anchor.audience = "solo"
