"""
Continuity Resolver — classifies message continuity and manages thread preservation.

Determines whether the current user message is:
  - a fresh request  → reset thread
  - a refinement     → extend the active thread (narrow audience, budget, vibe)
  - a correction     → extend but override specific fields
  - a topic switch   → archive current thread and start a new one

Decision hierarchy:
  1. Explicit switch cues override everything.
  2. Strong continuity anchor + short message → refinement (high confidence).
  3. Refinement adjectives/audience/price cues → refinement.
  4. Domain keyword mismatch with anchor → potential topic switch.
  5. No anchor, no history → fresh request.

Consumed by: update_scene_memory, resolve_playbooks, choose_strategy.
"""

from __future__ import annotations

import logging

from app.models.state import (
    ContinuityAnchor,
    ContinuityResolution,
    InterpretedIntent,
    SceneMemory,
)

logger = logging.getLogger(__name__)

# ── Lexical cue sets ─────────────────────────────────────────────────────

_EXPLICIT_SWITCH_CUES = (
    "instead", "forget that", "something else", "change topic",
    "never mind", "new question", "forget it", "actually no",
    "let's talk about", "what about something different",
)

_CORRECTION_CUES = (
    "no ", "not that", "i mean", "i meant", "actually",
    "wrong", "i said", "that's not what",
)

_REFINEMENT_ADJECTIVES = {
    "affordable", "cheap", "expensive", "luxury", "premium", "budget",
    "quiet", "quieter", "lively", "romantic", "casual", "cozy",
    "kid-friendly", "family-friendly", "halal",
    "quick", "fast", "nearby", "closer", "nicer", "better",
    "smaller", "bigger", "stylish",
}

_REFINEMENT_AUDIENCE_PHRASES = {
    "for my son", "for my daughter", "for kids", "for children",
    "for my wife", "for my husband", "for my girlfriend",
    "for a couple", "for family", "with kids", "with children",
    "with my son", "with my daughter", "for a toddler",
    "for a 5 year old", "for a teenager",
}

_REFINEMENT_PRICE_PHRASES = {
    "price", "how much", "cost", "pricing", "what does it cost",
    "under 100", "under 200", "under 300", "under 50",
    "less than", "cheaper", "something affordable",
}

_DOMAIN_KEYWORDS: dict[str, str] = {
    "eat": "dining", "food": "dining", "restaurant": "dining",
    "cafe": "dining", "hungry": "dining", "lunch": "dining",
    "dinner": "dining", "coffee": "dining", "dessert": "dining",
    "shop": "shopping", "buy": "shopping", "store": "shopping",
    "gift": "shopping", "clothes": "shopping", "jacket": "shopping",
    "movie": "entertainment", "cinema": "entertainment",
    "film": "entertainment", "watch": "entertainment",
    "park": "services", "pray": "services",
}


class ContinuityResolver:
    """
    Stateless resolver that classifies how a new message relates to the
    active conversational thread and produces a ContinuityResolution.
    """

    def resolve(
        self,
        message: str,
        intent: InterpretedIntent,
        scene: SceneMemory,
        anchor: ContinuityAnchor,
        history_len: int,
    ) -> ContinuityResolution:
        lower = message.lower().strip()
        word_count = len(lower.split())

        # Rule 1: No history → always fresh
        if history_len <= 1 and not anchor.is_strong:
            return ContinuityResolution(
                continuity_type="fresh_request",
                thread_action="reset",
                confidence=0.9,
                reason="first message in session",
            )

        # Rule 2: Explicit switch cues override everything
        if self._has_explicit_switch(lower):
            new_domain = self._detect_domain(lower) or intent.domain
            return ContinuityResolution(
                continuity_type="topic_switch",
                thread_action="archive_and_switch",
                confidence=0.95,
                reason=f"explicit switch cue detected → {new_domain}",
                inherited_domain=new_domain,
                should_carry_audience=False,
                should_carry_budget=False,
                should_carry_shortlist=False,
            )

        # Rule 3: Correction cues
        if self._has_correction_cue(lower):
            return ContinuityResolution(
                continuity_type="correction",
                thread_action="extend",
                confidence=0.85,
                reason="correction cue detected",
                inherited_domain=anchor.domain if anchor.is_strong else intent.domain,
                inherited_topic=anchor.topic if anchor.is_strong else intent.sub_intent,
                should_carry_audience=True,
                should_carry_budget=True,
                should_carry_shortlist=False,
            )

        # Rule 4: Strong anchor + refinement signals
        if anchor.is_strong:
            refinement_dims = self._detect_refinement_dimensions(lower)

            # 4a: Short message (<=4 words) with strong anchor → refinement
            if word_count <= 4 and not self._has_explicit_domain_change(lower, anchor):
                return ContinuityResolution(
                    continuity_type="refinement",
                    thread_action="extend",
                    confidence=0.90,
                    reason=f"short follow-up within active thread ({anchor.domain}/{anchor.topic})",
                    inherited_domain=anchor.domain,
                    inherited_topic=anchor.topic,
                    refinement_dimensions=refinement_dims or ["general"],
                    should_carry_audience=True,
                    should_carry_budget=True,
                    should_carry_shortlist=True,
                )

            # 4b: Refinement adjective/audience/price cues
            if refinement_dims:
                return ContinuityResolution(
                    continuity_type="refinement",
                    thread_action="extend",
                    confidence=0.85,
                    reason=f"refinement cues detected: {refinement_dims}",
                    inherited_domain=anchor.domain,
                    inherited_topic=anchor.topic,
                    refinement_dimensions=refinement_dims,
                    should_carry_audience=True,
                    should_carry_budget=True,
                    should_carry_shortlist=True,
                )

            # 4c: LLM classified as refinement/followup
            if intent.is_refinement_of_current_topic or intent.message_kind in (
                "refinement", "followup",
            ):
                return ContinuityResolution(
                    continuity_type="refinement",
                    thread_action="extend",
                    confidence=0.80,
                    reason=f"intent classified as {intent.message_kind}",
                    inherited_domain=anchor.domain,
                    inherited_topic=anchor.topic,
                    refinement_dimensions=refinement_dims or ["general"],
                    should_carry_audience=True,
                    should_carry_budget=True,
                    should_carry_shortlist=True,
                )

            # 4d: Explicit domain change detected → topic switch
            if self._has_explicit_domain_change(lower, anchor):
                new_domain = self._detect_domain(lower) or intent.domain
                return ContinuityResolution(
                    continuity_type="topic_switch",
                    thread_action="archive_and_switch",
                    confidence=0.80,
                    reason=f"domain changed from {anchor.domain} to {new_domain}",
                    inherited_domain=new_domain,
                    should_carry_audience=True,
                    should_carry_budget=False,
                    should_carry_shortlist=False,
                )

            # 4e: Same domain, new request within the thread
            return ContinuityResolution(
                continuity_type="refinement",
                thread_action="preserve",
                confidence=0.70,
                reason="message within active domain, treating as thread continuation",
                inherited_domain=anchor.domain,
                inherited_topic=anchor.topic,
                should_carry_audience=True,
                should_carry_budget=True,
                should_carry_shortlist=True,
            )

        # Rule 5: No strong anchor — classify from intent
        if intent.message_kind == "topic_switch":
            return ContinuityResolution(
                continuity_type="topic_switch",
                thread_action="archive_and_switch",
                confidence=0.70,
                reason="intent classified as topic_switch, no strong anchor",
                inherited_domain=intent.domain,
                should_carry_audience=False,
                should_carry_budget=False,
                should_carry_shortlist=False,
            )

        return ContinuityResolution(
            continuity_type="fresh_request",
            thread_action="reset",
            confidence=0.75,
            reason="no strong anchor and no refinement signals",
            inherited_domain=intent.domain,
        )

    # ── Internal detection helpers ───────────────────────────────────────

    def _has_explicit_switch(self, msg: str) -> bool:
        return any(cue in msg for cue in _EXPLICIT_SWITCH_CUES)

    def _has_correction_cue(self, msg: str) -> bool:
        return any(cue in msg for cue in _CORRECTION_CUES)

    def _detect_refinement_dimensions(self, msg: str) -> list[str]:
        dims: list[str] = []
        for adj in _REFINEMENT_ADJECTIVES:
            if adj in msg:
                if adj in ("cheap", "affordable", "expensive", "luxury", "premium", "budget"):
                    if "budget" not in dims:
                        dims.append("budget")
                elif adj in ("kid-friendly", "family-friendly"):
                    if "audience" not in dims:
                        dims.append("audience")
                else:
                    if "vibe" not in dims:
                        dims.append("vibe")
        for phrase in _REFINEMENT_AUDIENCE_PHRASES:
            if phrase in msg and "audience" not in dims:
                dims.append("audience")
        for phrase in _REFINEMENT_PRICE_PHRASES:
            if phrase in msg and "price" not in dims:
                dims.append("price")
        return dims

    def _detect_domain(self, msg: str) -> str:
        for keyword, domain in _DOMAIN_KEYWORDS.items():
            if keyword in msg:
                return domain
        return ""

    def _has_explicit_domain_change(self, msg: str, anchor: ContinuityAnchor) -> bool:
        """True only when the message explicitly mentions a domain different from the anchor."""
        detected = self._detect_domain(msg)
        if not detected:
            return False
        return detected != anchor.domain
