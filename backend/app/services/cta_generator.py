"""
CTA Generator — produces a short, domain-aware call-to-action instruction
that is injected at the end of the LLM prompt.

The CTA is expressed as a prompt *instruction* (not hardcoded response text),
which lets the LLM weave it in naturally.

Tiered CTA rules:
  - REQUIRED strategies: guided_plan, concise_shortlist, gift_formula,
    movie_plus_food, mini_itinerary
    → The LLM MUST end with a specific guided next action.
  - OPTIONAL strategies: direct_fact, quick_answer, mall_overview,
    exploration_overview, and all others
    → The LLM may include a follow-up offer if natural.

Rule: CTA must depend on domain and response type; must feel useful, not pushy.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Strategies that require a mandatory engagement continuation
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_CTA_STRATEGIES: frozenset[str] = frozenset({
    "guided_plan",
    "concise_shortlist",
    "gift_formula",
    "movie_plus_food",
    "mini_itinerary",
    "budget_plan",
    "route_plus_plan",
})

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
        "Want me to narrow this down to the best picks for kids?",
        "I can filter for the most kid-friendly choices if you'd like.",
        "Just say the word and I'll focus on family-friendly options.",
    ],
    "dining_suggestion": [
        "Want me to narrow down by cuisine, speed, or proximity?",
        "I can also suggest somewhere for dessert or coffee after.",
        "If you want, I can suggest the best dining spot for your group.",
    ],
    "dining_next": [
        "Want me to suggest a dessert spot or café to finish the evening?",
        "I can find a coffee place nearby if you need a quick break.",
        "Fancy somewhere for a sweet treat after the meal?",
    ],
    "shopping_narrowing": [
        "Tell me who you're shopping for and I'll point you to the best stores.",
        "Want me to filter by category or price range?",
        "If you want, I can narrow this down by budget or gift type.",
    ],
    "service_help": [
        "If you want, I can guide you to the nearest one.",
        "Need directions to any other service? Just ask.",
    ],
    "overview_continue": [
        "What would you like to explore first — shopping, food, cinema, or kids' fun?",
        "If you tell me your plan, I can suggest the best spots in order.",
    ],
    "route_help": [
        "If you need directions to anything else nearby, just ask.",
        "Want me to guide you to any other spot from here?",
    ],
    "cross_mall": [
        "I can also check if it's available in any other Cenomi mall.",
        "Want me to search across all Cenomi malls?",
    ],
    "cinema_followup": [
        "I can walk you through the booking options or check what's on next.",
        "Want me to find a good dinner spot near the cinema for before or after?",
        "If you'd like, I can suggest the best format — standard, IMAX, or VIP.",
    ],
    "offer_followup": [
        "Want me to check if any of these stores have active deals or promotions?",
        "I can pull up the latest offers across the mall if you're interested.",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def get_cta_instruction(cta_type: str, strategy: str = "") -> str:
    """
    Return a prompt instruction for the LLM to append a CTA.

    For REQUIRED strategies (guided_plan, concise_shortlist, etc.) the
    instruction is mandatory — the LLM MUST end with a specific guided
    next action that connects to what the visitor is doing.

    For all other strategies the instruction is optional — the LLM may
    include a natural follow-up if it hasn't already been covered.

    Returns an empty string if no CTA applies for this type.
    """
    if not cta_type or cta_type not in _CTA_BANKS:
        return ""

    example = _CTA_BANKS[cta_type][0]
    is_required = strategy in _REQUIRED_CTA_STRATEGIES

    if is_required:
        return (
            f"ENGAGEMENT CONTINUATION (REQUIRED): You MUST end your response with "
            f"ONE specific, guided next action that connects directly to what the "
            f"visitor is currently doing. It must feel natural and move the visit "
            f"forward — never generic. "
            f"Example style: \"{example}\" "
            f"Keep it to one sentence. Do NOT use 'How can I help?' or 'Let me know "
            f"if you need anything.' Those are dead ends.\n"
        )

    return (
        f"ENGAGEMENT CONTINUATION (optional): You may close with one brief, natural "
        f"follow-up offer if it hasn't already been covered in your answer. "
        f"Example style: \"{example}\" "
        f"Keep it to one sentence. Do NOT add it if the answer already invites "
        f"a follow-up or if it would feel forced.\n"
    )


def get_cta_example(cta_type: str) -> str:
    """Return just the example text for the given CTA type (for testing / logging)."""
    bank = _CTA_BANKS.get(cta_type, [])
    return bank[0] if bank else ""
