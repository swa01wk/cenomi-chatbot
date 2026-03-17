"""
CTA Generator — produces a short, domain-aware call-to-action instruction
that is injected at the end of the LLM prompt.

The CTA is expressed as a prompt *instruction* (not hardcoded response text),
which lets the LLM weave it in naturally — or skip it if the answer already
covers it.

Rules:
  - CTA must depend on domain and response type
  - CTA must feel useful, not pushy
  - CTA must not overshadow the main answer
  - CTA instruction is ONE sentence of optional guidance
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# CTA example banks  (first item = canonical example shown to the LLM)
# ─────────────────────────────────────────────────────────────────────────────

_CTA_BANKS: dict[str, list[str]] = {
    "movie_refinement": [
        "Tell me the genre you're in the mood for and I'll narrow it down.",
        "If you want, I can help you pick the best one for your group.",
        "If you choose one, I can share the booking details.",
    ],
    "family_narrowing": [
        "If you're with a child, I can focus on the most family-friendly options.",
        "Want me to narrow this down to the best picks for kids?",
        "I can filter for the most kid-friendly choices if you'd like.",
    ],
    "dining_suggestion": [
        "I can also suggest something quick, family-friendly, or near the cinema.",
        "Want me to narrow down by cuisine, speed, or proximity?",
        "If you want, I can suggest the best dining spot for your group.",
    ],
    "shopping_narrowing": [
        "If you want, I can narrow this down by budget, gifts, or family-friendly options.",
        "Tell me who you're shopping for and I'll point you to the best stores.",
        "Want me to filter by category or price range?",
    ],
    "service_help": [
        "If you want, I can guide you to the nearest one.",
        "Need directions to any other service? Just ask.",
    ],
    "overview_continue": [
        "If you tell me your plan — shopping, food, cinema, or kids' fun — I can suggest the best spots.",
        "What would you like to explore first?",
    ],
    "route_help": [
        "If you need directions to anything else nearby, just ask.",
        "Want me to guide you to any other spot from here?",
    ],
    "cross_mall": [
        "I can also check if it's available in any other Cenomi mall.",
        "Want me to search across all Cenomi malls?",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_cta_instruction(cta_type: str) -> str:
    """
    Return a prompt instruction for the LLM to (optionally) append a CTA.

    The instruction shows the LLM an example CTA so it can include one
    naturally at the end of its response — or skip it if already covered.

    Returns an empty string if no CTA applies for this type.
    """
    if not cta_type or cta_type not in _CTA_BANKS:
        return ""

    example = _CTA_BANKS[cta_type][0]
    return (
        f"OPTIONAL CTA: You MAY close with one brief, natural follow-up offer "
        f"if it hasn't already been covered in your answer. "
        f"Example style: \"{example}\" "
        f"Keep it to one sentence. Do NOT add it if the answer already invites "
        f"a follow-up or if it would feel forced.\n"
    )


def get_cta_example(cta_type: str) -> str:
    """Return just the example text for the given CTA type (for testing / logging)."""
    bank = _CTA_BANKS.get(cta_type, [])
    return bank[0] if bank else ""
