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
#
# Greeting responses use a scaffold that:
#   1. Gives a warm, short greeting
#   2. Lists 3-5 concrete mall capabilities (teaches the user what to ask)
#   3. Ends with an open question
#
# This replaces the generic "what are you in the mood for?" with a more
# guided, mall-native experience that helps users know what's possible.

_RESPONSES: dict[str, list[str]] = {
    "greeting": [
        (
            "Hi there! 👋\n"
            "I can help with shops and brands, restaurants and cafés, "
            "cinema and entertainment, or services like parking, ATMs, and prayer rooms. "
            "What are you looking for today?"
        ),
        (
            "Hello! Welcome to the mall concierge.\n"
            "Ask me about movies and showtimes, dining options, stores and brands, "
            "or anything like 'where is the ATM' or 'is there parking nearby'. "
            "What can I help you with?"
        ),
        (
            "Hey! Good to have you here.\n"
            "I can point you to the best shops, restaurants, and entertainment — "
            "or help with practical things like opening hours, prayer rooms, and directions. "
            "What are you in the mood for?"
        ),
        (
            "Hi! I'm your mall guide.\n"
            "Whether you're here for shopping, a meal, catching a movie, or just exploring — "
            "I can help you find the right spot. What's on your agenda today?"
        ),
        (
            "Ahlan! 👋\n"
            "Looking for something specific or just exploring? I can help with stores, "
            "dining, cinema, kids' activities, or services like parking and prayer rooms. "
            "What would you like to know?"
        ),
    ],
    "howru": [
        "All good — ready to help! What are you looking for today: shopping, food, cinema, or something else?",
        "Doing great, thanks! Tell me what you need — I can help with stores, dining, movies, or services.",
        "Just here to help you make the most of your visit. What can I point you to?",
    ],
    "thanks": [
        "You're welcome! Let me know if there's anything else — more recommendations, directions, or anything.",
        "Happy to help! Need anything else? I can suggest dining, shopping, or help you find something specific.",
        "Anytime! If you want more suggestions or have another question, just ask.",
    ],
    "bye": [
        "Goodbye! Hope you have a great time. Feel free to ask if you need anything else during your visit.",
        "See you around! Enjoy your time here.",
        "Take care and enjoy the mall!",
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

    # Greetings use the greeting_scaffold experience mode for debug tracking
    experience_mode = "greeting_scaffold" if category == "greeting" else "smalltalk"

    assistant_msg = Message(
        role="assistant",
        content=response_text,
        turn_id=state.turn_id,
        metadata={
            "strategy": "smalltalk",
            "category": category,
            "experience_mode": experience_mode,
        },
    )

    return {
        "final_response_text": response_text,
        "response_debug_summary": f"smalltalk/{category} [{experience_mode}]",
        "messages": [assistant_msg],
        "_trace_summary": f"Small talk ({category}): {len(response_text)} chars",
    }
