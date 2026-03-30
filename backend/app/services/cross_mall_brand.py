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
    re.compile(r"which (?:of (?:your|the) |cenomi )?malls? (?:has|have|carries|carry|offer[s]?)\s+", re.I),
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
    """Strip cross-mall trigger phrases; return a fragment suitable for substring brand search."""
    result = message
    for pattern in _CROSS_MALL_STRIP_PATTERNS:
        result = pattern.sub(" ", result)
    result = re.sub(
        r"\b(also|too|any|both|other|all|the|your|cenomi|malls?|please|here|find)\b",
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
    Resolve brand/store string for cross-mall canonical search.

    Order: stripped message → fact_query_entity → last_resolved_entity → active_shortlist[0].
    Returns empty string if nothing usable (caller should ask for clarification).
    """
    raw = (state.normalized_user_message or state.raw_user_message or "").strip()
    stripped = extract_brand_query_from_message(raw)
    cand = stripped.lower()
    if cand and cand not in _USELESS_TOKENS and len(cand) >= 2:
        return stripped

    fe = (getattr(state, "fact_query_entity", None) or "").strip()
    if fe and fe.lower() not in _USELESS_TOKENS:
        return fe

    scene = state.scene
    lr = (getattr(scene, "last_resolved_entity", None) or "").strip()
    if lr and lr.lower() not in _USELESS_TOKENS:
        return lr

    shortlist = getattr(scene, "active_shortlist", None) or []
    if shortlist:
        first = str(shortlist[0]).strip()
        if first and first.lower() not in _USELESS_TOKENS:
            return first

    return ""
