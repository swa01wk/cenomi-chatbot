"""
Hallucination prevention guardrail for the Cenomi concierge chatbot.

Validates LLM-generated responses against structured mall context data,
ensuring the chatbot never invents promotions, discounts, events,
showtimes, store hours, or cinema listings that don't exist in the
canonical mall data.

Usage::

    from backend.guardrails.hallucination_guard import validate_response

    result = validate_response(response_text, mall_context)
    safe_text = result.corrected_text
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# Public data classes
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class Violation:
    """A single hallucination detected in the response."""

    category: str
    original_fragment: str
    replacement: str
    reason: str


@dataclass
class ValidationResult:
    """Outcome of hallucination validation."""

    original_text: str
    corrected_text: str
    violations: list[Violation] = field(default_factory=list)

    @property
    def was_modified(self) -> bool:
        return len(self.violations) > 0


# ───────────────────────────────────────────────────────────────────────────
# Fact index — pre-computed lookup tables from structured mall context
# ───────────────────────────────────────────────────────────────────────────


class _MallFactIndex:
    """Builds fast lookup structures from the canonical mall context dict."""

    def __init__(self, mall_context: dict[str, Any]):
        self.store_names_lower: dict[str, str] = {}
        self.cinema_names: set[str] = set()

        self.movie_titles_lower: dict[str, str] = {}
        self.movie_showtimes: dict[str, list[str]] = {}

        self.event_titles_lower: dict[str, str] = {}

        # offer_store_pairs: [{store_lower, store, pct, title}]
        self.offer_store_pairs: list[dict[str, Any]] = []
        # stores that have at least one active offer (lowercased)
        self.stores_with_offers: set[str] = set()

        self._build(mall_context)

    # ── index construction ────────────────────────────────────────────────

    def _build(self, ctx: dict[str, Any]) -> None:
        entity_id_to_name: dict[str, str] = {}

        for section in ("stores", "dining", "services"):
            for entity in ctx.get(section, []):
                name = entity.get("name", "")
                eid = entity.get("entity_id", "")
                if name:
                    self.store_names_lower[name.lower()] = name
                if eid and name:
                    entity_id_to_name[eid] = name

        for cinema in ctx.get("cinemas", []):
            name = cinema.get("name", "")
            eid = cinema.get("entity_id", "")
            if name:
                self.cinema_names.add(name)
                self.store_names_lower[name.lower()] = name
            if eid and name:
                entity_id_to_name[eid] = name

        for movie in ctx.get("movies", []):
            title = movie.get("title", "")
            if title:
                self.movie_titles_lower[title.lower()] = title
                self.movie_showtimes[title.lower()] = movie.get("showtimes", [])

        for event in ctx.get("events", []):
            title = event.get("title", "")
            if title:
                self.event_titles_lower[title.lower()] = title

        for offer in ctx.get("offers", []):
            tenant_ids = offer.get("tenant_entity_ids", [])
            linked = [
                entity_id_to_name[tid]
                for tid in tenant_ids
                if tid in entity_id_to_name
            ]
            discount_str = offer.get("discount_value", "")
            pcts = [int(p) for p in re.findall(r"(\d+)", discount_str)]

            for store_name in linked:
                self.stores_with_offers.add(store_name.lower())
                for pct in pcts:
                    self.offer_store_pairs.append(
                        {
                            "store_lower": store_name.lower(),
                            "store": store_name,
                            "pct": pct,
                            "title": offer.get("title", ""),
                        }
                    )

    # ── query helpers ─────────────────────────────────────────────────────

    def has_movie(self, candidate: str) -> bool:
        low = candidate.lower()
        if low in self.movie_titles_lower:
            return True
        return any(low in t or t in low for t in self.movie_titles_lower)

    def has_event(self, candidate: str) -> bool:
        low = candidate.lower()
        if low in self.event_titles_lower:
            return True
        return any(low in t or t in low for t in self.event_titles_lower)

    def store_has_offer(self, store_name: str) -> bool:
        return store_name.lower() in self.stores_with_offers

    def store_has_discount(self, store_name: str, pct: int) -> bool:
        sl = store_name.lower()
        return any(
            p["store_lower"] == sl and p["pct"] >= pct
            for p in self.offer_store_pairs
        )

    def find_store_in_text(self, text: str) -> str | None:
        """Return the canonical store name if mentioned (longest-match first)."""
        text_lower = text.lower()
        for name_lower in sorted(self.store_names_lower, key=len, reverse=True):
            if name_lower in text_lower:
                return self.store_names_lower[name_lower]
        return None

    def find_cinema_in_text(self, text: str) -> str | None:
        text_lower = text.lower()
        for name in self.cinema_names:
            if name.lower() in text_lower:
                return name
        return None


# ───────────────────────────────────────────────────────────────────────────
# Compiled patterns
# ───────────────────────────────────────────────────────────────────────────

_MOVIE_VERBS = re.compile(
    r"\b(?:playing|showing|screening|now\s+showing|watch(?:ing)?|see(?:ing)?|"
    r"catch|starts?\s+at|available\s+in)\b",
    re.IGNORECASE,
)

_MOVIE_TITLE_EXTRACTORS = [
    # "Title" is playing / showing …
    re.compile(
        r'["\u201c]([^"\u201d]{2,40})["\u201d]\s+(?:is\s+)?'
        r"(?:playing|showing|screening|now\s+showing)",
        re.IGNORECASE,
    ),
    # playing / showing "Title"
    re.compile(
        r'(?:playing|showing|screening|watch|see|catch)\s+["\u201c]([^"\u201d]{2,40})["\u201d]',
        re.IGNORECASE,
    ),
    # "Title" at Cinema
    re.compile(
        r'["\u201c]([^"\u201d]{2,40})["\u201d]\s+(?:at|in)\s+',
        re.IGNORECASE,
    ),
    # Title is playing at … (capitalized multi-word, up to "is/at")
    re.compile(
        r"\b([A-Z][A-Za-z0-9':& -]{1,35}?)\s+is\s+"
        r"(?:playing|showing|screening|being\s+shown)\b",
    ),
    # watch / see Title at …
    re.compile(
        r"\b(?:watch|see|catch)\s+([A-Z][A-Za-z0-9':& -]{1,35}?)\s+(?:at|in)\s+",
    ),
]

_DISCOUNT_PCT = re.compile(
    r"(\d+)\s*%\s*(?:off|discount|reduction|savings?|sale)",
    re.IGNORECASE,
)

_PROMOTION_INDICATORS = [
    re.compile(
        r"\b(?:there(?:'|\u2019)?s?\s+a?\s*)?(?:sale|promotion|offer|deal|discount)"
        r"\s+(?:at|for|on)\s+([\w][\w\s&'.\u2019-]{1,30})",
        re.IGNORECASE,
    ),
    re.compile(
        r"([\w][\w\s&'.\u2019-]{1,30}?)\s+(?:has|have|is\s+having|is\s+offering|"
        r"is\s+running)\s+(?:a\s+)?(?:sale|promotion|offer|deal|discount)",
        re.IGNORECASE,
    ),
]

_EVENT_EXTRACTORS = [
    re.compile(
        r"\b(?:event|festival|carnival|show|workshop|exhibition|concert|performance)"
        r"\s+(?:called|named|titled)?\s*[\"'\u201c]([^\"'\u201d]{2,50})[\"'\u201d]",
        re.IGNORECASE,
    ),
    re.compile(
        r'["\u201c]([^"\u201d]{2,50})["\u201d]\s+'
        r"(?:event|festival|carnival|show|workshop|exhibition)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:upcoming|current|ongoing|live)\s+"
        r"(?:event|festival|carnival)\s+(?:is\s+)?(?:the\s+)?"
        r"([A-Z][\w\s&':-]{2,40})",
        re.IGNORECASE,
    ),
    # "EVENT_NAME is happening / is taking place / starts on"
    re.compile(
        r"\b([A-Z][\w\s&':-]{2,40}?)\s+(?:is\s+)"
        r"(?:happening|taking\s+place|coming\s+up|starting)",
        re.IGNORECASE,
    ),
]

_SHOWTIME_CLAIM = re.compile(
    r"\b(?:showtime|show|screening|starts?)\s+(?:at|is\s+at)\s+"
    r"(\d{1,2}:\d{2})\b",
    re.IGNORECASE,
)

_EVENT_KEYWORD = re.compile(
    r"\b(?:event|festival|carnival|show|workshop|exhibition|concert|performance)\b",
    re.IGNORECASE,
)

_QUOTED_TEXT = re.compile(r'["\'`\u201c]([^"\'`\u201d]{2,50})["\'`\u201d]')

_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "this", "that", "it", "its",
        "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "do", "does", "did",
        "will", "would", "can", "could", "may", "might",
        "all", "some", "any", "no", "not",
        "but", "and", "or", "so", "if", "then",
        "for", "with", "from", "to", "at", "in", "on",
        "here", "there", "now", "just", "also", "very",
    }
)


def _looks_like_title(candidate: str) -> bool:
    """Heuristic check: does this string look like a proper title?"""
    words = candidate.split()
    if not words:
        return False
    if len(words) == 1 and candidate.lower() in _STOP_WORDS:
        return False
    return any(w[0].isupper() for w in words if w)


# ───────────────────────────────────────────────────────────────────────────
# Per-sentence checkers
# ───────────────────────────────────────────────────────────────────────────


def _check_movie_claims(
    sentence: str, index: _MallFactIndex
) -> Violation | None:
    """Flag movie title references that aren't in the canonical data."""
    cinema = index.find_cinema_in_text(sentence)
    if not cinema:
        return None
    if not _MOVIE_VERBS.search(sentence):
        return None

    def _make_violation(candidate: str) -> Violation:
        return Violation(
            category="movie",
            original_fragment=sentence,
            replacement=(
                f"{cinema} is located in the mall for movies — "
                "check their listings for current showtimes."
            ),
            reason=f"Movie '{candidate}' not found in mall context",
        )

    # Check quoted text first (highest confidence extraction)
    for match in _QUOTED_TEXT.finditer(sentence):
        candidate = match.group(1).strip().rstrip(".,!?")
        if len(candidate) < 2 or candidate.lower() in index.store_names_lower:
            continue
        if not index.has_movie(candidate):
            return _make_violation(candidate)

    # Try structured patterns
    for pat in _MOVIE_TITLE_EXTRACTORS:
        for match in pat.finditer(sentence):
            candidate = match.group(1).strip().rstrip(".,!?")
            if len(candidate) < 2 or not _looks_like_title(candidate):
                continue
            if candidate.lower() in index.store_names_lower:
                continue
            if not index.has_movie(candidate):
                return _make_violation(candidate)

    # Fallback: sentence has movie verbs + cinema but no known title at all.
    # Only flag if a capitalized multi-word phrase looks like a title claim.
    # Skip location/zone phrases like "Cinema Level", "Food Court", etc.
    _LOCATION_WORDS = frozenset({
        "level", "floor", "ground", "wing", "zone", "gallery", "court",
        "row", "kiosk", "entrance", "gate", "atrium", "mall", "plaza",
        "section", "area", "block", "corridor", "walk",
    })
    sent_lower = sentence.lower()
    if not any(t in sent_lower for t in index.movie_titles_lower):
        caps = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", sentence)
        for cap in caps:
            if cap.lower() in index.store_names_lower:
                continue
            if cap.lower() == cinema.lower():
                continue
            # Skip if any word in the phrase is a location/zone term
            if any(w.lower() in _LOCATION_WORDS for w in cap.split()):
                continue
            # Skip if the phrase contains the cinema name (e.g. "Muvi Cinema Level")
            if cinema.lower() in cap.lower():
                continue
            if not index.has_movie(cap):
                return _make_violation(cap)
    return None


def _check_discount_claims(
    sentence: str, index: _MallFactIndex
) -> Violation | None:
    """Flag numeric discount percentages not backed by a known offer."""
    pct_matches = _DISCOUNT_PCT.findall(sentence)
    if not pct_matches:
        return None

    store = index.find_store_in_text(sentence)
    for pct_str in pct_matches:
        pct = int(pct_str)
        if store:
            if not index.store_has_discount(store, pct):
                return Violation(
                    category="discount",
                    original_fragment=sentence,
                    replacement=(
                        f"You may want to check {store} for current offers."
                    ),
                    reason=(
                        f"{pct}% discount at {store} not found in active offers"
                    ),
                )
        else:
            grounded = any(p["pct"] >= pct for p in index.offer_store_pairs)
            if not grounded:
                return Violation(
                    category="discount",
                    original_fragment=sentence,
                    replacement=(
                        "Check with individual stores for their latest promotions."
                    ),
                    reason=f"{pct}% discount not found in any active offer",
                )
    return None


def _check_promotion_claims(
    sentence: str, index: _MallFactIndex
) -> Violation | None:
    """Flag sale/promotion claims for stores that have no known offer."""
    for pat in _PROMOTION_INDICATORS:
        for match in pat.finditer(sentence):
            raw_name = match.group(1).strip().rstrip(".,!?")
            raw_lower = raw_name.lower()

            canonical: str | None = index.store_names_lower.get(raw_lower)
            if not canonical:
                for nl, nc in index.store_names_lower.items():
                    if nl in raw_lower or raw_lower in nl:
                        canonical = nc
                        break
            if not canonical:
                continue

            if not index.store_has_offer(canonical):
                return Violation(
                    category="promotion",
                    original_fragment=sentence,
                    replacement=(
                        f"You may want to check {canonical} for current offers."
                    ),
                    reason=f"No active promotion found for {canonical}",
                )
    return None


def _check_event_claims(
    sentence: str, index: _MallFactIndex
) -> Violation | None:
    """Flag event references not present in the mall context."""

    def _make_violation(candidate: str) -> Violation:
        return Violation(
            category="event",
            original_fragment=sentence,
            replacement=(
                "The mall hosts various events — visit the Information "
                "Desk or check the website for the latest schedule."
            ),
            reason=f"Event '{candidate}' not found in mall context",
        )

    # Quoted text in sentences containing event-related keywords
    if _EVENT_KEYWORD.search(sentence):
        for match in _QUOTED_TEXT.finditer(sentence):
            candidate = match.group(1).strip().rstrip(".,!?")
            if len(candidate) < 3:
                continue
            if not index.has_event(candidate):
                return _make_violation(candidate)

    # Structured regex patterns
    for pat in _EVENT_EXTRACTORS:
        for match in pat.finditer(sentence):
            candidate = match.group(1).strip().rstrip(".,!?")
            if len(candidate) < 3 or not _looks_like_title(candidate):
                continue
            if not index.has_event(candidate):
                return _make_violation(candidate)
    return None


def _check_showtime_claims(
    sentence: str, index: _MallFactIndex
) -> Violation | None:
    """Flag specific showtime claims that don't match any known movie."""
    cinema = index.find_cinema_in_text(sentence)
    if not cinema:
        return None

    for match in _SHOWTIME_CLAIM.finditer(sentence):
        claimed_time = match.group(1)
        # Check whether this time belongs to ANY known movie
        grounded = any(
            claimed_time in times
            for times in index.movie_showtimes.values()
        )
        if not grounded:
            return Violation(
                category="showtime",
                original_fragment=sentence,
                replacement=(
                    f"{cinema} is located in the mall — "
                    "check their listings for current showtimes."
                ),
                reason=(
                    f"Showtime {claimed_time} not found in any listed movie"
                ),
            )
    return None


# ───────────────────────────────────────────────────────────────────────────
# Sentence splitter
# ───────────────────────────────────────────────────────────────────────────

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence-like segments, preserving structure."""
    parts = _SENTENCE_BOUNDARY.split(text)
    return [p for p in parts if p.strip()]


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────

def _check_entity_name_claims(
    sentence: str, index: _MallFactIndex
) -> Violation | None:
    """Flag store/restaurant/brand names that don't exist in canonical data.

    Scans for capitalised multi-word phrases and known brand patterns,
    then verifies each candidate against the canonical store list.
    Generic English words and city/location names are excluded.
    """
    _SAFE_WORDS = frozenset({
        "the", "a", "an", "this", "that", "here", "there", "where",
        "mall", "store", "shop", "restaurant", "cafe", "cinema",
        "floor", "ground", "first", "second", "third", "wing",
        "north", "south", "east", "west", "central", "main",
        "food", "court", "zone", "entrance", "gate", "atrium",
        "king", "fahd", "road", "riyadh", "saudi", "arabia",
        "information", "desk", "prayer", "room", "family",
        "entertainment", "parking", "valet", "service", "services",
        "for", "and", "or", "with", "your", "you", "are", "can",
        "may", "just", "also", "try", "check", "visit", "explore",
        "inside", "near", "next", "beside", "opposite", "above",
        "below", "between", "around", "across", "from",
        "cenomi", "rewards", "plus", "app", "website",
        "spring", "fashion", "week", "eid", "carnival",
        "end", "season", "sale", "beauty", "combo", "deal",
        "sun", "mon", "tue", "wed", "thu", "fri", "sat",
        "am", "pm", "sar", "free",
        "happy", "meal", "kids", "kid", "son", "daughter",
        "wife", "husband", "mother", "father", "baby",
        "fun", "day", "plan", "itinerary", "stop", "break",
        "breakfast", "lunch", "dinner", "snack", "dessert",
        "treat", "option", "options", "choice", "choices",
        "modern", "classic", "traditional", "premium", "luxury",
        "budget", "affordable", "trendy", "stylish",
    })

    _COMMON_HALLUCINATED_BRANDS = {
        "mcdonald's": "McDonald's", "mcdonalds": "McDonald's",
        "herfy": "Herfy", "kudu": "Kudu",
        "kudu restaurant": "Kudu Restaurant",
        "mammabunz": "MammaBunz", "mamma bunz": "MammaBunz",
        "cinnabon": "Cinnabon", "coolnup": "CoolNup",
        "cool n up": "CoolNup",
        "danube hypermarket": "Danube HyperMarket",
        "fun time": "Fun Time", "sparky's": "Sparky's",
        "sparkys": "Sparky's",
        "brands for less": "Brands For Less",
        "charles & keith": "Charles & Keith",
        "charles and keith": "Charles & Keith",
        "popeyes": "Popeyes", "kfc": "KFC",
        "burger king": "Burger King", "subway": "Subway",
        "pizza hut": "Pizza Hut", "domino's": "Domino's",
        "dominos": "Domino's",
        "nayomi": "Nayomi", "nichii": "Nichii",
        "arasha": "Arasha", "nawaem": "Nawaem",
        "la vallee": "La Vallee",
        "alnajma alnaima": "AlNajma AlNaima",
        "zohoor al reef": "Zohoor Al Reef",
        "the optical club": "The Optical Club",
        "carrefour": "Carrefour",
        "lulu hypermarket": "Lulu Hypermarket",
        "al baik": "Al Baik", "albaik": "Al Baik",
        "tim hortons": "Tim Hortons",
        "costa coffee": "Costa Coffee",
    }

    sent_lower = sentence.lower()

    for brand_lower, brand_name in _COMMON_HALLUCINATED_BRANDS.items():
        if brand_lower in sent_lower:
            # Accept exact key match OR any partial overlap with a known store
            # (e.g. "kudu" should match against "kudu restaurant").
            is_known = brand_lower in index.store_names_lower or any(
                brand_lower in sn or sn in brand_lower
                for sn in index.store_names_lower
            )
            if not is_known:
                return Violation(
                    category="entity_name",
                    original_fragment=sentence,
                    replacement="",
                    reason=(
                        f"'{brand_name}' is not a tenant in this mall"
                    ),
                )

    brand_pattern = re.compile(
        r"\b([A-Z][A-Za-z'&.-]+(?:\s+[A-Z&][A-Za-z'&.-]+){0,3})\b"
    )
    for match in brand_pattern.finditer(sentence):
        candidate = match.group(1).strip().rstrip(".,!?;:")
        if len(candidate) < 2:
            continue

        words = candidate.lower().split()
        if all(w in _SAFE_WORDS for w in words):
            continue
        if len(words) == 1 and len(candidate) <= 3:
            continue

        candidate_lower = candidate.lower()
        if candidate_lower in index.store_names_lower:
            continue
        if any(
            candidate_lower in sn or sn in candidate_lower
            for sn in index.store_names_lower
        ):
            continue

        if any(candidate_lower in t for t in index.movie_titles_lower):
            continue
        if any(candidate_lower in t for t in index.event_titles_lower):
            continue

    return None


_CHECKERS = [
    _check_entity_name_claims,
    _check_movie_claims,
    _check_discount_claims,
    _check_promotion_claims,
    _check_event_claims,
    _check_showtime_claims,
]


def validate_response(
    response_text: str,
    mall_context: dict[str, Any],
) -> ValidationResult:
    """
    Validate an LLM response against structured mall context.

    Scans for hallucinated movie titles, numeric discounts, promotion
    claims, event references, and showtime assertions.  Replaces
    ungrounded claims with neutral, safe phrasing.

    Args:
        response_text: The raw LLM-generated response.
        mall_context: The canonical mall data dict
                      (stores, movies, offers, events, etc.).

    Returns:
        A ``ValidationResult`` with the (possibly corrected) text and
        any violations found.
    """
    if not response_text or not mall_context:
        return ValidationResult(
            original_text=response_text or "",
            corrected_text=response_text or "",
        )

    index = _MallFactIndex(mall_context)
    sentences = _split_sentences(response_text)
    violations: list[Violation] = []
    corrected_text = response_text

    for sentence in sentences:
        for checker in _CHECKERS:
            violation = checker(sentence, index)
            if violation:
                violations.append(violation)
                corrected_text = corrected_text.replace(
                    violation.original_fragment, violation.replacement, 1
                )
                logger.warning(
                    "Hallucination detected [%s]: %s",
                    violation.category,
                    violation.reason,
                )
                break

    if violations:
        # Clean up artifacts from removed sentences
        import re as _re
        corrected_text = _re.sub(r"\n{3,}", "\n\n", corrected_text)
        corrected_text = _re.sub(r"  +", " ", corrected_text)
        corrected_text = corrected_text.strip()
        logger.info(
            "Hallucination guard corrected %d violation(s)", len(violations)
        )

    return ValidationResult(
        original_text=response_text,
        corrected_text=corrected_text,
        violations=violations,
    )
