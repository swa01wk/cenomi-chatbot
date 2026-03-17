"""
Small Talk node — handles greetings and casual messages with fast,
mall-anchored responses that skip the full pipeline.

CONTRACT
────────
  Purpose:  Respond to greetings, pleasantries, and casual messages
            with warm, on-brand mall concierge replies.  Always steers
            the conversation back toward mall services.
  Reads:    normalized_user_message, raw_user_message, turn_id
  Writes:   final_response_text, response_debug_summary,
            messages (appends assistant Message)
  Failure:  None — uses static responses, no LLM call
  Routing:  Always → update_memory
"""

from __future__ import annotations

import logging
import random
import re

from app.models.state import ConciergeState, Message
from app.nodes._tracing import traced_node

logger = logging.getLogger(__name__)

# ── Pattern matchers (anchored — full message must match) ─────────────

_GREETING_RE = re.compile(
    r"^(h(i|ey|ello|owdy|ola)|yo+|sup"
    r"|what['']?s\s*up"
    r"|good\s*(morning|afternoon|evening|day)"
    r"|assalamu?\s*alaikum|marhaba|ahlan|salam"
    r"|greetings|hiya|heya)"
    r"[!.?,\s]*$",
    re.IGNORECASE,
)

_HOWRU_RE = re.compile(
    r"^(how\s*(are|r)\s*(you|u|ya)"
    r"|how['']?s\s*it\s*going"
    r"|what['']?s\s*(good|new|happening))"
    r"[!?.,\s]*$",
    re.IGNORECASE,
)

_THANKS_RE = re.compile(
    r"^(thanks?(\s*you)?|thx|ty|thank\s*u|cheers|much\s*appreciated|shukran)"
    r"[!.?,\s]*$",
    re.IGNORECASE,
)

_BYE_RE = re.compile(
    r"^(bye|goodbye|good\s*bye|see\s*ya|later|take\s*care"
    r"|ciao|ma['']?a?\s*salama|farewell)"
    r"[!.?,\s]*$",
    re.IGNORECASE,
)

# ── Response pools (always redirect to mall services) ─────────────────

_RESPONSES: dict[str, list[str]] = {
    "greeting": [
        "Hi! Welcome to the mall concierge. Looking for shopping, food, or something fun to do?",
        "Hello! I'm your mall concierge — here to help you find the best spots. What are you in the mood for?",
        "Hey there! Whether it's dining, shopping, or entertainment, I've got you covered. What can I help with?",
        "Welcome! I can help you discover great stores, restaurants, and activities. What sounds good?",
    ],
    "howru": [
        "Just here to help you explore the mall! What are you in the mood for today?",
        "Doing great — ready to help you make the most of your visit! Shopping, food, or fun?",
        "All good on my end! Tell me what you're looking for and I'll point you in the right direction.",
    ],
    "thanks": [
        "You're welcome! Let me know if there's anything else you'd like to explore.",
        "Happy to help! Need anything else — maybe a dining spot or some shopping?",
        "Anytime! If you need more suggestions, just ask.",
    ],
    "bye": [
        "Goodbye! Hope you enjoy your time at the mall. Come back anytime!",
        "See you around! Enjoy your visit.",
        "Take care! Hope you have a great time here.",
    ],
}


def classify_smalltalk(msg: str) -> str | None:
    """Return the small talk category or ``None`` if not small talk."""
    text = msg.strip()
    if _GREETING_RE.match(text):
        return "greeting"
    if _HOWRU_RE.match(text):
        return "howru"
    if _THANKS_RE.match(text):
        return "thanks"
    if _BYE_RE.match(text):
        return "bye"
    return None


def is_smalltalk(state: ConciergeState) -> bool:
    """Graph-routing predicate: ``True`` when the turn is small talk."""
    msg = state.normalized_user_message or state.raw_user_message
    return classify_smalltalk(msg) is not None


@traced_node("smalltalk")
async def smalltalk(state: ConciergeState) -> dict:
    msg = state.normalized_user_message or state.raw_user_message
    category = classify_smalltalk(msg) or "greeting"

    response_text = random.choice(_RESPONSES[category])

    assistant_msg = Message(
        role="assistant",
        content=response_text,
        turn_id=state.turn_id,
        metadata={"strategy": "smalltalk", "category": category},
    )

    return {
        "final_response_text": response_text,
        "response_debug_summary": f"smalltalk/{category}",
        "messages": [assistant_msg],
        "_trace_summary": f"Small talk ({category}): {len(response_text)} chars",
    }
