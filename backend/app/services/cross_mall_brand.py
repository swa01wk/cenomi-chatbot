"""
Cross-mall brand resolution — extract a search string from the user message
and scene so factual cross-mall lookup can run on follow-ups ("where else…?").
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.state import ConciergeState

_CROSS_MALL_STRIP_PATTERNS: list[re.Pattern[str]] = [
    # "which [all/of your/cenomi] malls does/do/have/has/carries/carry/offers/sells X"
    re.compile(
        r"which (?:all |of (?:your|the) |cenomi )?malls? "
        r"(?:does|do|have|has|carries?|carry|offer[s]?|sell[s]?)\s+",
        re.I,
    ),
    # "in which malls [can I find/do you have] X" / "at which mall does X"
    re.compile(r"(?:in|at) which (?:cenomi )?malls?\s+(?:can i find|do you have|is|are|does|do)?\s*", re.I),
    re.compile(r"do(?:es)? (?:any|other) (?:of (?:your|the) |cenomi )?malls? (?:have|carry|offer|has)\s+", re.I),
    re.compile(r"(?:in|at) (?:both|all|other) (?:your )?malls?", re.I),
    re.compile(r"across (?:all |your |the )?malls?", re.I),
    re.compile(r"(?:at|in) any (?:cenomi |other )?mall", re.I),
    re.compile(r"(?:also|too) (?:have|carry|offer|available)", re.I),
    re.compile(r"(?:other|the other) (?:cenomi )?malls?\s*(?:also|too)?\s*(?:have|has|carry|offer)?\s*", re.I),
    re.compile(r"does (?:the other|any other|mall of arabia|al nakheel|al nakheel plaza)\s*(?:also\s*)?have\s*", re.I),
    re.compile(r"also (?:available|found|in|at) (?:other|the other|any) malls?\s*", re.I),
    re.compile(r"is (?:this|it|that) (?:brand|store|shop) (?:also |too )?(?:in|at|available)\s*", re.I),
    re.compile(r"(?:is|are)\s+\w+\s+(?:available\s+)?(?:in|at)\s+", re.I),
    re.compile(r"can i find\s+", re.I),
    re.compile(r"where can i find\s+", re.I),
    re.compile(r"where else\s*", re.I),
    re.compile(r"all (?:cenomi )?malls?\s*", re.I),
    re.compile(r"(?:this|that|the) store\s*", re.I),
]


def extract_brand_query_from_message(message: str) -> str:
    """
    Fallback: strip cross-mall trigger phrases from the raw message and return
    the remaining fragment as a search term.

    This is only called when the LLM classifier did not populate entity_query
    (e.g. very short follow-up turns like "what about other malls?").
    For primary queries the LLM-extracted entity_query is used instead.
    """
    result = message
    for pattern in _CROSS_MALL_STRIP_PATTERNS:
        result = pattern.sub(" ", result)
    result = re.sub(
        r"\b(also|too|any|both|other|all|the|your|cenomi|malls?|please|here|find"
        r"|which|does|do|is|are|have|has|can|i|in|at|for|from|get)\b",
        " ",
        result,
        flags=re.I,
    )
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip(" ?.,!")


_USELESS_TOKENS = frozenset({
    "", "it", "this", "that", "there", "store", "shop", "brand", "one", "them", "those",
})


def resolve_cross_mall_brand_query(state: ConciergeState) -> str:
    """
    Resolve brand/store/item string for cross-mall canonical search.

    Resolution order (highest confidence first):
      1. LLM-extracted entity_query via fact_query_entity (set by resolve_fact_scope
         from InterpretedIntent.entity_query — the LLM classifier extracts this directly)
      2. Regex-stripped message (fallback for turns where LLM left entity_query empty)
      3. last_resolved_entity from scene (follow-up turns: "where else?")
      4. active_shortlist[0] (last concierge recommendation)

    Returns empty string when nothing usable (caller will ask for clarification).
    """
    # 1. LLM-extracted entity (preferred — no regex fragility)
    fe = (getattr(state, "fact_query_entity", None) or "").strip()
    if fe and fe.lower() not in _USELESS_TOKENS and len(fe) >= 2:
        return fe

    # 2. Also check intent.entity_query directly in case resolve_fact_scope
    #    hasn't run yet (concierge cross-mall path in compose_context)
    intent = getattr(state, "intent", None)
    if intent:
        eq = (getattr(intent, "entity_query", None) or "").strip()
        if eq and eq.lower() not in _USELESS_TOKENS and len(eq) >= 2:
            return eq

    # 3. Regex fallback — strip cross-mall trigger phrases from raw message
    raw = (state.normalized_user_message or state.raw_user_message or "").strip()
    stripped = extract_brand_query_from_message(raw)
    cand = stripped.lower()
    if cand and cand not in _USELESS_TOKENS and len(cand) >= 2:
        return stripped

    # 4. Scene memory: entity from last resolved turn (handles "where else?")
    scene = state.scene
    lr = (getattr(scene, "last_resolved_entity", None) or "").strip()
    if lr and lr.lower() not in _USELESS_TOKENS:
        return lr

    # 5. Last concierge shortlist item
    shortlist = getattr(scene, "active_shortlist", None) or []
    if shortlist:
        first = str(shortlist[0]).strip()
        if first and first.lower() not in _USELESS_TOKENS:
            return first

    return ""
