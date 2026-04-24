"""
Implicit feedback detector — extracts feedback signals from user corrections.

When a user says "no, I meant inside the cinema" or "not expensive", these
are corrections that imply the previous response missed the mark.  This
component converts those linguistic patterns into structured ImplicitFeedbackEvents.

Detection strategy:
  1. Pattern-based rules (regex + keyword matching)
  2. Message-kind awareness (correction / refinement from the intent interpreter)
  3. Confidence scoring based on pattern strength

This runs on EVERY user turn, not just when the user explicitly gives feedback.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from app.models.feedback import ImplicitFeedbackEvent, ImplicitSignalType
from app.utils.ids import generate_feedback_id

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Pattern rules — each maps a regex + keywords to one or more signals
# ═══════════════════════════════════════════════════════════════════════════


_DETECTION_RULES: list[dict[str, Any]] = [
    # ── scene / context corrections ──────────────────────────────────
    {
        "patterns": [
            r"\bno[,.]?\s*i\s*meant\b",
            r"\bnot\s+that\b",
            r"\bi\s*said\b",
            r"\bi\s*was\s+asking\s+about\b",
            r"\bthat'?s\s+not\s+what\b",
        ],
        "signals": [ImplicitSignalType.INCORRECT_SCENE_INFERENCE],
        "confidence": 0.85,
    },
    # ── wrong context / location ─────────────────────────────────────
    {
        "patterns": [
            r"\binside\s+the\b",
            r"\bnear\s+the\b",
            r"\bon\s+(?:floor|level)\b",
            r"\bnot\s+(?:this|that)\s+(?:mall|area|floor|zone)\b",
            r"\bwrong\s+(?:place|location|area)\b",
        ],
        "signals": [ImplicitSignalType.WRONG_CONTEXT, ImplicitSignalType.LOCATION_CORRECTION],
        "confidence": 0.75,
    },
    # ── price mismatch ───────────────────────────────────────────────
    {
        "patterns": [
            r"\bnot\s+(?:expensive|pricey|costly)\b",
            r"\btoo\s+(?:expensive|pricey|costly)\b",
            r"\bcheaper\b",
            r"\bbudget\b",
            r"\baffordable\b",
            r"\bon\s+a\s+budget\b",
            r"\bnot\s+(?:cheap|budget)\b",
            r"\bmore\s+(?:upscale|premium|luxury)\b",
        ],
        "signals": [ImplicitSignalType.PRICE_MISMATCH],
        "confidence": 0.80,
    },
    # ── audience mismatch ────────────────────────────────────────────
    {
        "patterns": [
            r"\bfor\s+my\s+(?:son|daughter|kid|child|baby|toddler)\b",
            r"\bfor\s+(?:kids|children|family|adults|teens|couples)\b",
            r"\bnot\s+for\s+(?:kids|children|adults)\b",
            r"\bkid[\s-]*friendly\b",
            r"\bwith\s+(?:my|the)\s+(?:kids|children|family)\b",
        ],
        "signals": [ImplicitSignalType.AUDIENCE_MISMATCH],
        "confidence": 0.80,
    },
    # ── recommendation scope error ───────────────────────────────────
    {
        "patterns": [
            r"\bsomething\s+(?:else|different)\b",
            r"\bother\s+options\b",
            r"\bnot\s+(?:this|these|those)\b",
            r"\bshould\s+recommend\b",
            r"\bany\s+alternatives\b",
            r"\bmore\s+options\b",
        ],
        "signals": [ImplicitSignalType.RECOMMENDATION_SCOPE_ERROR],
        "confidence": 0.70,
    },
    # ── urgency / time correction ────────────────────────────────────
    {
        "patterns": [
            r"\bquick\b",
            r"\bfast\b",
            r"\bhurry\b",
            r"\bin\s+a\s+rush\b",
            r"\bdon'?t\s+have\s+(?:much\s+)?time\b",
            r"\bbefore\s+(?:the\s+)?movie\b",
            r"\bonly\s+\d+\s+minutes?\b",
        ],
        "signals": [ImplicitSignalType.URGENCY_SIGNAL, ImplicitSignalType.TIME_CORRECTION],
        "confidence": 0.65,
    },
    # ── preference override ──────────────────────────────────────────
    {
        "patterns": [
            r"\bi\s+(?:prefer|want|need)\b",
            r"\bactually\b",
            r"\binstead\b",
            r"\brather\b",
        ],
        "signals": [ImplicitSignalType.PREFERENCE_OVERRIDE],
        "confidence": 0.60,
    },
    # ── comparison request (implicit dissatisfaction with single rec) ─
    {
        "patterns": [
            r"\bcompare\b",
            r"\bvs\.?\b",
            r"\bversus\b",
            r"\bwhich\s+(?:is|one)\s+better\b",
            r"\bdifference\s+between\b",
        ],
        "signals": [ImplicitSignalType.COMPARISON_REQUEST],
        "confidence": 0.55,
    },
]

# ── Arabic correction-signal patterns ─────────────────────────────────────
# These detect when an Arabic-speaking guest is correcting the bot's response.
# Arabic does not have word-boundary anchors (\b) the same way as English, so
# patterns use lookahead/lookbehind or rely on whitespace anchoring where needed.

_ARABIC_DETECTION_RULES: list[dict[str, Any]] = [
    # ── scene / context corrections ──────────────────────────────────
    {
        "patterns": [
            r"لا[،,]?\s*أقصد",          # "no, I meant"
            r"ليس\s+هذا",               # "not this"
            r"كنت\s+أقصد",              # "I meant"
            r"كنت\s+أسأل\s+عن",        # "I was asking about"
            r"هذا\s+ليس\s+ما",          # "this is not what"
        ],
        "signals": [ImplicitSignalType.INCORRECT_SCENE_INFERENCE],
        "confidence": 0.85,
    },
    # ── wrong context / location ─────────────────────────────────────
    {
        "patterns": [
            r"بداخل",                   # "inside"
            r"بجانب",                   # "near/next to"
            r"الطابق\s+الخطأ",          # "wrong floor"
            r"المكان\s+الخطأ",          # "wrong place"
            r"منطقة\s+أخرى",            # "another area"
        ],
        "signals": [ImplicitSignalType.WRONG_CONTEXT, ImplicitSignalType.LOCATION_CORRECTION],
        "confidence": 0.75,
    },
    # ── price mismatch ───────────────────────────────────────────────
    {
        "patterns": [
            r"غالي\s+جداً",             # "too expensive"
            r"ليس\s+غالياً",            # "not expensive"
            r"أرخص",                    # "cheaper"
            r"ميزانية\s+محدودة",        # "limited budget"
            r"بسعر\s+معقول",            # "at a reasonable price"
            r"أكثر\s+فخامة",            # "more luxurious"
        ],
        "signals": [ImplicitSignalType.PRICE_MISMATCH],
        "confidence": 0.80,
    },
    # ── audience mismatch ────────────────────────────────────────────
    {
        "patterns": [
            r"لأطفالي",                 # "for my children"
            r"للعائلة",                 # "for the family"
            r"مع\s+الأطفال",            # "with the children"
            r"مناسب\s+للأطفال",         # "suitable for children"
            r"ليس\s+للأطفال",           # "not for children"
        ],
        "signals": [ImplicitSignalType.AUDIENCE_MISMATCH],
        "confidence": 0.80,
    },
    # ── recommendation scope error ───────────────────────────────────
    {
        "patterns": [
            r"شيء\s+آخر",              # "something else"
            r"خيارات\s+أخرى",           # "other options"
            r"ليس\s+هذا",               # "not this"
            r"يجب\s+أن\s+تقترح",        # "you should suggest"
            r"بدائل\s+أخرى",            # "other alternatives"
            r"المزيد\s+من\s+الخيارات",   # "more options"
        ],
        "signals": [ImplicitSignalType.RECOMMENDATION_SCOPE_ERROR],
        "confidence": 0.70,
    },
    # ── urgency / time correction ────────────────────────────────────
    {
        "patterns": [
            r"بسرعة",                   # "quickly"
            r"مستعجل",                  # "in a hurry"
            r"وقت\s+قصير",              # "short time"
            r"قبل\s+الفيلم",            # "before the movie"
            r"فقط\s+\d+\s+دقائق",       # "only N minutes"
        ],
        "signals": [ImplicitSignalType.URGENCY_SIGNAL, ImplicitSignalType.TIME_CORRECTION],
        "confidence": 0.65,
    },
    # ── preference override ──────────────────────────────────────────
    {
        "patterns": [
            r"أفضّل",                   # "I prefer"
            r"أريد\s+بدلاً\s+من",       # "I want instead of"
            r"في\s+الواقع",             # "actually"
            r"بل\s+أريد",               # "rather I want"
        ],
        "signals": [ImplicitSignalType.PREFERENCE_OVERRIDE],
        "confidence": 0.60,
    },
]

# Pre-compile all patterns
for rule in _DETECTION_RULES:
    rule["_compiled"] = [re.compile(p, re.IGNORECASE) for p in rule["patterns"]]

for rule in _ARABIC_DETECTION_RULES:
    rule["_compiled"] = [re.compile(p, re.UNICODE) for p in rule["patterns"]]


class ImplicitFeedbackDetector:
    """
    Detects implicit feedback from user messages.

    Called on every turn by the pipeline.  Returns None if no implicit
    feedback is detected, otherwise returns a structured event.
    """

    def detect(
        self,
        *,
        user_message: str,
        previous_assistant_response: str = "",
        session_id: str,
        tenant_id: str = "al_nakheel_plaza_28",
        mall_id: str = "al_nakheel_plaza_28",
        turn_id: str = "",
        message_kind: str = "",
    ) -> ImplicitFeedbackEvent | None:
        """
        Analyze a user message for implicit correction signals.

        Returns an ImplicitFeedbackEvent if any signals are detected,
        or None if the message is not a correction.
        """
        signals: list[ImplicitSignalType] = []
        max_confidence = 0.0
        correction_details: dict[str, Any] = {}

        # message_kind boosts confidence for correction/refinement turns
        kind_boost = 0.0
        if message_kind in ("correction", "refinement"):
            kind_boost = 0.15

        text = user_message.strip()
        if len(text) < 3:
            return None

        # Run both English and Arabic detection rules
        all_rules = _DETECTION_RULES + _ARABIC_DETECTION_RULES
        for rule in all_rules:
            for compiled in rule["_compiled"]:
                if compiled.search(text):
                    for sig in rule["signals"]:
                        if sig not in signals:
                            signals.append(sig)
                    confidence = min(1.0, rule["confidence"] + kind_boost)
                    max_confidence = max(max_confidence, confidence)
                    correction_details[rule["signals"][0].value] = {
                        "matched_pattern": compiled.pattern,
                        "confidence": confidence,
                    }
                    break  # one match per rule is enough

        if not signals:
            return None

        event = ImplicitFeedbackEvent(
            event_id=generate_feedback_id(),
            session_id=session_id,
            tenant_id=tenant_id,
            mall_id=mall_id,
            turn_id=turn_id,
            user_message=text,
            previous_assistant_response=previous_assistant_response,
            detected_signals=signals,
            correction_details=correction_details,
            confidence=max_confidence,
            timestamp=datetime.utcnow(),
        )

        logger.info(
            "Implicit feedback detected: signals=%s confidence=%.2f session=%s",
            [s.value for s in signals],
            max_confidence,
            session_id,
        )
        return event
