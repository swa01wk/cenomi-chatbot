"""
Concierge-style response composer.

Builds structured, category-grouped recommendations from pipeline state.
The LLM is used ONLY for final natural-language polish — all section
structure, entity grouping, and ordering is determined deterministically
from mall_context, playbook results, and intent tags.

Usage::

    from response.concierge_composer import ConciergeComposer

    composer = ConciergeComposer(mall_context)
    blueprint = composer.compose(
        entities=state.context.selected_entities,
        intent_tags=["gift", "jewelry", "beauty"],
        strategy=state.response_plan.chosen_strategy,
        playbook_id=state.playbook.selected_playbook,
        scene=state.scene,
    )

    # Deterministic structured output
    for section in blueprint.sections:
        print(section.heading, [p.name for p in section.picks])

    # Rule-based rendering (no LLM)
    print(blueprint.render_text())

    # Or build an LLM prompt for natural-language polish
    from response.concierge_composer import build_composer_prompt
    system_msg = build_composer_prompt(blueprint)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.context.mall_context import MallContextLoader
from app.models.context_pack import MallProfileBlock, TopicBlock

logger = logging.getLogger(__name__)

MAX_PICKS_PER_SECTION = 5
MAX_MAIN_SECTIONS = 6
MAX_BONUS_SECTIONS = 2


# ═══════════════════════════════════════════════════════════════════════════
# Blueprint data structures
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class StorePick:
    """A single entity selected for the response."""

    name: str
    entity_id: str
    category: str
    floor: str = ""
    zone: str = ""
    tags: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class Section:
    """A grouped category section in the response."""

    heading: str
    picks: list[StorePick] = field(default_factory=list)
    is_bonus: bool = False

    @property
    def pick_names(self) -> list[str]:
        return [p.name for p in self.picks]


@dataclass
class MallOverviewBlueprint:
    """Structured overview response built entirely from trusted mall data."""

    mall_name: str
    city: str = ""
    summary_line: str = ""
    address: str = ""
    opening_hours: dict[str, str] = field(default_factory=dict)
    highlights: list[str] = field(default_factory=list)
    services_and_facilities: list[str] = field(default_factory=list)
    family_friendly_notes: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    def render_text(self) -> str:
        """Deterministic rule-based rendering — no LLM required."""
        parts: list[str] = []

        if self.summary_line:
            parts.append(self.summary_line)

        parts.append("\nHere's a quick overview:")

        if self.address:
            parts.append(f"\n• Location & access: {self.address}")
        elif "address" not in self.missing_fields:
            self.missing_fields.append("address")

        if self.opening_hours:
            hours_parts = [f"{k}: {v}" for k, v in self.opening_hours.items() if v]
            if hours_parts:
                parts.append(f"\n• Opening hours: {' | '.join(hours_parts)}")
        elif "opening_hours" not in self.missing_fields:
            self.missing_fields.append("opening_hours")

        if self.highlights:
            parts.append(f"\n• What you'll find: {', '.join(self.highlights[:5])}")

        if self.services_and_facilities:
            parts.append(
                f"\n• Services & facilities: {', '.join(self.services_and_facilities[:6])}"
            )

        if self.family_friendly_notes:
            parts.append(
                f"\n• Family-friendly: {', '.join(self.family_friendly_notes[:4])}"
            )

        for field_name in self.missing_fields:
            label = field_name.replace("_", " ")
            parts.append(
                f"\n• {label.title()}: I don't have that exact detail "
                "in the current mall data."
            )

        parts.append(
            "\nIf you'd like, I can also help with the best shopping, "
            "dining, or family spots in the mall."
        )
        return "\n".join(parts)

    def to_prompt_block(self) -> str:
        """Serialize into a structured text block for LLM polish."""
        lines: list[str] = []
        lines.append(f"Mall: {self.mall_name}")
        if self.city:
            lines.append(f"City: {self.city}")
        if self.summary_line:
            lines.append(f"Summary: {self.summary_line}")
        if self.address:
            lines.append(f"Address: {self.address}")
        if self.opening_hours:
            hours = " | ".join(f"{k}: {v}" for k, v in self.opening_hours.items() if v)
            lines.append(f"Opening hours: {hours}")
        if self.highlights:
            lines.append(f"What you'll find: {', '.join(self.highlights[:5])}")
        if self.services_and_facilities:
            lines.append(
                f"Services & facilities: {', '.join(self.services_and_facilities[:6])}"
            )
        if self.family_friendly_notes:
            lines.append(
                f"Family-friendly: {', '.join(self.family_friendly_notes[:4])}"
            )
        if self.missing_fields:
            lines.append(f"MISSING (omit or state unavailable): {', '.join(self.missing_fields)}")
        return "\n".join(lines)


@dataclass
class ResponseBlueprint:
    """Complete structured response before LLM formatting."""

    intro_hint: str
    sections: list[Section] = field(default_factory=list)
    closing_hint: str = ""
    strategy: str = ""
    intent_summary: str = ""

    # ── Serialization ────────────────────────────────────────────────

    def to_prompt_block(self) -> str:
        """Serialize the blueprint into a structured text block for the LLM."""
        lines: list[str] = []

        if self.intent_summary:
            lines.append(f"Visitor intent: {self.intent_summary}")
        if self.intro_hint:
            lines.append(f"Opening context: {self.intro_hint}")
        lines.append("")

        for section in self.sections:
            tag = "[BONUS] " if section.is_bonus else ""
            lines.append(f"{tag}{section.heading}")
            for pick in section.picks:
                loc = _format_location(pick.floor, pick.zone)
                lines.append(f"  - {pick.name}{loc}")
            lines.append("")

        if self.closing_hint:
            lines.append(f"Closing: {self.closing_hint}")

        return "\n".join(lines)

    def render_text(self) -> str:
        """
        Rule-based rendering — produces clean text WITHOUT the LLM.

        Useful as a fallback or when LLM latency is unacceptable.
        """
        parts: list[str] = []

        if self.intro_hint:
            parts.append(self.intro_hint)

        main_sections = [s for s in self.sections if not s.is_bonus]
        bonus_sections = [s for s in self.sections if s.is_bonus]

        for section in main_sections:
            block = f"\n{section.heading}"
            for pick in section.picks:
                loc = _format_location(pick.floor, pick.zone)
                block += f"\n- {pick.name}{loc}"
            parts.append(block)

        for section in bonus_sections:
            intro = _bonus_transition(section.heading)
            block = f"\n{intro}"
            for pick in section.picks:
                loc = _format_location(pick.floor, pick.zone)
                block += f"\n- {pick.name}{loc}"
            parts.append(block)

        if self.closing_hint:
            parts.append(self.closing_hint)

        return "\n".join(parts)

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def total_picks(self) -> int:
        return sum(len(s.picks) for s in self.sections)

    @property
    def is_empty(self) -> bool:
        return self.total_picks == 0


# ═══════════════════════════════════════════════════════════════════════════
# Display name mappings
# ═══════════════════════════════════════════════════════════════════════════

_CATEGORY_DISPLAY: dict[str, str] = {
    "Fashion": "Fashion",
    "Jewelry": "Jewelry",
    "Beauty": "Perfumes & Beauty",
    "Electronics": "Electronics & Tech",
    "Sportswear": "Sportswear",
    "Home": "Home & Living",
    "Kids": "Kids & Baby",
}

_ENTITY_TYPE_DISPLAY: dict[str, str] = {
    "cinema": "Cinema & Entertainment",
    "event": "Events",
    "offer": "Deals & Offers",
    "service": "Services",
}

_DINING_STYLE_DISPLAY: dict[str, str] = {
    "fine_dining": "Fine Dining",
    "casual_dining": "Casual Dining",
    "fast_casual": "Quick Bites",
    "quick_service": "Quick Bites",
    "food_court_counter": "Food Court",
    "cafe": "Cafés & Coffee",
    "dessert": "Desserts & Sweets",
}


# ═══════════════════════════════════════════════════════════════════════════
# Intent-driven section ordering and bonus logic
# ═══════════════════════════════════════════════════════════════════════════

_INTENT_SECTION_ORDER: dict[str, list[str]] = {
    "gift": [
        "Jewelry", "Perfumes & Beauty", "Fashion",
        "Home & Living", "Electronics & Tech",
    ],
    "jewelry": ["Jewelry", "Perfumes & Beauty", "Fashion"],
    "fashion": ["Fashion", "Sportswear", "Jewelry", "Perfumes & Beauty"],
    "beauty": ["Perfumes & Beauty", "Fashion", "Jewelry"],
    "dining": [
        "Fine Dining", "Casual Dining", "Dining",
        "Quick Bites", "Cafés & Coffee", "Desserts & Sweets",
    ],
    "entertainment": ["Cinema & Entertainment", "Dining", "Quick Bites"],
    "kids_shopping": ["Kids & Baby", "Cinema & Entertainment", "Dining"],
    "electronics": ["Electronics & Tech", "Home & Living"],
    "home_decor": ["Home & Living", "Electronics & Tech"],
    "exploration": [
        "Fashion", "Dining", "Perfumes & Beauty",
        "Electronics & Tech", "Cinema & Entertainment",
    ],
    "shopping": [
        "Fashion", "Jewelry", "Perfumes & Beauty",
        "Electronics & Tech", "Home & Living", "Sportswear",
    ],
}

_INTENT_BONUS: dict[str, list[str]] = {
    "gift": ["Desserts & Sweets", "Cafés & Coffee"],
    "fashion": ["Cafés & Coffee"],
    "jewelry": ["Cafés & Coffee", "Desserts & Sweets"],
    "entertainment": ["Desserts & Sweets", "Quick Bites"],
    "kids_shopping": ["Desserts & Sweets"],
    "shopping": ["Cafés & Coffee"],
    "exploration": ["Desserts & Sweets"],
}


# ═══════════════════════════════════════════════════════════════════════════
# Strategy → intro / closing copy
# ═══════════════════════════════════════════════════════════════════════════

_STRATEGY_INTROS: dict[str, str] = {
    "gift_formula": "You could check out a few great gift options in the mall:",
    "shortlist_recommendation": "Here are some top picks for you:",
    "mini_itinerary": "Here's a nice route through the mall:",
    "movie_plus_food": "Here's a movie-and-food combo plan:",
    "family_plan": "Here's a family-friendly plan for the visit:",
    "budget_plan": "Some great budget-friendly options:",
    "exploration_overview": "Here are some highlights across the mall:",
    "solo_plan": "Here are some ideas for your visit:",
}

_STRATEGY_CLOSINGS: dict[str, str] = {
    "gift_formula": "Would you like directions to any of these, or more details?",
    "shortlist_recommendation": "Want more details on any of these?",
    "mini_itinerary": "Want me to adjust the plan or add anything?",
    "movie_plus_food": "Want me to check showtimes?",
    "family_plan": "Should I add anything else to the itinerary?",
    "exploration_overview": "Anything catch your eye?",
    "budget_plan": "Want me to find more deals?",
}


# ═══════════════════════════════════════════════════════════════════════════
# Sub-intent → intent tag mapping
# ═══════════════════════════════════════════════════════════════════════════

_SUB_INTENT_TAG_MAP: dict[str, list[str]] = {
    "gift_recommendation": ["gift"],
    "general_dining": ["dining"],
    "romantic_dining": ["dining"],
    "quick_bite": ["dining"],
    "family_dining": ["dining"],
    "cafe_recommendation": ["dining"],
    "dessert_recommendation": ["dining"],
    "general_shopping": ["shopping"],
    "fashion_shopping": ["fashion"],
    "general_entertainment": ["entertainment"],
    "movie_showtime": ["entertainment"],
    "open_exploration": ["exploration"],
    "activity_suggestion": ["exploration"],
    "first_visit_guide": ["exploration"],
}


# ═══════════════════════════════════════════════════════════════════════════
# Bonus section transitions
# ═══════════════════════════════════════════════════════════════════════════

_BONUS_TRANSITIONS: dict[str, str] = {
    "Desserts & Sweets": "You could even end the visit with dessert at:",
    "Cafés & Coffee": "Or take a break with coffee at:",
    "Quick Bites": "For a quick bite you could try:",
    "Dining": "And when you're ready to eat:",
}

_DINING_TAG_FILTERS: dict[str, set[str]] = {
    "Desserts & Sweets": {"dessert", "dessert_spot", "ice_cream"},
    "Cafés & Coffee": {"cafe", "coffee", "coffee_spot"},
    "Quick Bites": {"quick_bite", "fast_casual"},
}


# ═══════════════════════════════════════════════════════════════════════════
# Composer
# ═══════════════════════════════════════════════════════════════════════════


class ConciergeComposer:
    """
    Produces structured, category-grouped response blueprints.

    All structure decisions — grouping, ordering, section selection — are
    deterministic.  The LLM is only needed downstream for natural-language
    polish via :func:`build_composer_prompt`.
    """

    def __init__(self, mall_context: MallContextLoader) -> None:
        self._mall_ctx = mall_context

    # ── Public API ───────────────────────────────────────────────────

    def compose_mall_overview(
        self,
        mall_profile: MallProfileBlock | None = None,
        overview_block: TopicBlock | None = None,
    ) -> MallOverviewBlueprint:
        """
        Build a mall overview blueprint from trusted data only.

        Uses ``mall_profile`` and the ``mall_overview`` topic block.
        Any field absent from the source data is tracked in
        ``missing_fields`` so the caller can surface a safe disclaimer.
        """
        if mall_profile is None:
            mall_profile = self._mall_ctx.get_mall_profile()
        if overview_block is None:
            overview_block = self._mall_ctx.get_topic_block("mall_overview")

        if not mall_profile:
            return MallOverviewBlueprint(
                mall_name="this mall",
                summary_line="I don't have detailed overview data for this mall right now.",
                missing_fields=["address", "opening_hours", "highlights",
                                "services", "family_friendly"],
            )

        missing: list[str] = []
        if not mall_profile.address:
            missing.append("address")
        if not mall_profile.opening_hours:
            missing.append("opening_hours")

        summary_line = mall_profile.summary or mall_profile.positioning or ""
        if not summary_line:
            parts = [mall_profile.name]
            if mall_profile.city:
                parts.append(f"is a shopping destination in {mall_profile.city}")
            summary_line = " ".join(parts)

        highlights = list(mall_profile.highlights)
        services = list(mall_profile.services_and_facilities)
        family_notes = list(mall_profile.family_friendly_notes)

        if overview_block:
            for h in overview_block.semantic_highlights:
                if h not in highlights:
                    highlights.append(h)

        return MallOverviewBlueprint(
            mall_name=mall_profile.name,
            city=mall_profile.city,
            summary_line=summary_line,
            address=mall_profile.address,
            opening_hours=dict(mall_profile.opening_hours),
            highlights=highlights,
            services_and_facilities=services,
            family_friendly_notes=family_notes,
            missing_fields=missing,
        )

    def compose(
        self,
        entities: list[dict[str, Any]],
        intent_tags: list[str],
        strategy: str = "",
        playbook_id: str = "",
        scene: Any | None = None,
    ) -> ResponseBlueprint:
        """
        Build a structured response blueprint from pipeline outputs.

        Parameters
        ----------
        entities
            Selected entities from ``ContextComposition.selected_entities``.
        intent_tags
            Semantic intent tags driving the response (e.g. ``["gift", "jewelry"]``).
        strategy
            Chosen response strategy (e.g. ``"gift_formula"``).
        playbook_id
            Active playbook ID if any (e.g. ``"pb-gift-girlfriend"``).
        scene
            ``SceneMemory`` for audience / occasion context.

        Returns
        -------
        ResponseBlueprint
            Deterministic sections ready for LLM formatting or rule-based
            rendering.
        """
        enriched = self._enrich_entities(entities)
        grouped = self._group_by_category(enriched)
        primary_intent = self._pick_primary_intent(intent_tags)

        ordered = self._order_sections(grouped, primary_intent)

        existing_headings = {s.heading for s in ordered}
        bonus = self._build_bonus_sections(
            primary_intent, existing_headings,
        )

        intro = self._build_intro(strategy, intent_tags, scene)
        closing = self._build_closing(strategy, bonus)

        return ResponseBlueprint(
            intro_hint=intro,
            sections=ordered + bonus,
            closing_hint=closing,
            strategy=strategy,
            intent_summary=", ".join(intent_tags) if intent_tags else "",
        )

    def compose_from_state(self, state: Any) -> ResponseBlueprint:
        """
        Convenience wrapper that extracts fields from a ``ConciergeState``.
        """
        intent_tags = self._derive_intent_tags(state)
        return self.compose(
            entities=state.context.selected_entities,
            intent_tags=intent_tags,
            strategy=state.response_plan.chosen_strategy,
            playbook_id=state.playbook.selected_playbook,
            scene=state.scene,
        )

    # ── Intent tag derivation ────────────────────────────────────────

    @staticmethod
    def _derive_intent_tags(state: Any) -> list[str]:
        """Extract intent tags from the full pipeline state."""
        tags: list[str] = []

        sub = getattr(state.intent, "sub_intent", "")
        if sub in _SUB_INTENT_TAG_MAP:
            tags.extend(_SUB_INTENT_TAG_MAP[sub])

        domain = getattr(state.intent, "domain", "")
        if domain and domain not in tags:
            tags.append(domain)

        for signal in getattr(state.context, "selected_semantic_signals", []):
            if signal in _INTENT_SECTION_ORDER and signal not in tags:
                tags.append(signal)

        return tags

    @staticmethod
    def _pick_primary_intent(intent_tags: list[str]) -> str:
        """Select the most specific intent tag for section ordering."""
        priority = [
            "gift", "jewelry", "beauty", "fashion", "electronics",
            "kids_shopping", "home_decor", "dining", "entertainment",
            "shopping", "exploration",
        ]
        for p in priority:
            if p in intent_tags:
                return p
        return intent_tags[0] if intent_tags else "exploration"

    # ── Entity enrichment ────────────────────────────────────────────

    def _enrich_entities(
        self, entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Ensure each entity has ``category``, ``subcategory``, ``dining_style``
        by falling back to canonical data.
        """
        enriched: list[dict[str, Any]] = []
        for ent in entities:
            entry = dict(ent)
            eid = ent.get("entity_id", "")

            if eid and not entry.get("category"):
                canonical = self._mall_ctx.get_entity_by_id(eid)
                if canonical:
                    for key in ("category", "subcategory", "dining_style"):
                        entry.setdefault(key, canonical.get(key, ""))
                    if not entry.get("floor"):
                        loc = canonical.get("location", {})
                        if isinstance(loc, dict):
                            entry.setdefault("floor", loc.get("floor", ""))
                            entry.setdefault("zone", loc.get("zone", ""))

            enriched.append(entry)
        return enriched

    # ── Category grouping ────────────────────────────────────────────

    @staticmethod
    def _resolve_display_category(entity: dict[str, Any]) -> str:
        """Map an entity to its display-friendly section heading."""
        etype = entity.get("entity_type", "")
        category = entity.get("category", "")
        dining_style = entity.get("dining_style", "")

        if etype == "dining":
            if dining_style in _DINING_STYLE_DISPLAY:
                return _DINING_STYLE_DISPLAY[dining_style]
            tags = set(
                entity.get("semantic_tags", [])
                + entity.get("matched_tags", [])
            )
            if tags & {"dessert", "dessert_spot", "ice_cream"}:
                return "Desserts & Sweets"
            if tags & {"cafe", "coffee", "coffee_spot"}:
                return "Cafés & Coffee"
            return "Dining"

        if etype in _ENTITY_TYPE_DISPLAY:
            return _ENTITY_TYPE_DISPLAY[etype]

        if category in _CATEGORY_DISPLAY:
            return _CATEGORY_DISPLAY[category]

        return category or etype or "Other"

    def _group_by_category(
        self, entities: list[dict[str, Any]],
    ) -> dict[str, list[StorePick]]:
        """Group entities into display-category buckets."""
        groups: dict[str, list[StorePick]] = defaultdict(list)

        for ent in entities:
            heading = self._resolve_display_category(ent)
            pick = StorePick(
                name=ent.get("name", ""),
                entity_id=ent.get("entity_id", ""),
                category=heading,
                floor=ent.get("floor", ""),
                zone=ent.get("zone", ""),
                tags=ent.get("semantic_tags", ent.get("matched_tags", [])),
                score=ent.get("score", 0.0),
            )
            if pick.name:
                groups[heading].append(pick)

        for heading in groups:
            groups[heading].sort(key=lambda p: p.score, reverse=True)
            groups[heading] = groups[heading][:MAX_PICKS_PER_SECTION]

        return dict(groups)

    # ── Section ordering ─────────────────────────────────────────────

    @staticmethod
    def _order_sections(
        grouped: dict[str, list[StorePick]],
        primary_intent: str,
    ) -> list[Section]:
        """Order sections by intent relevance, capped to MAX_MAIN_SECTIONS."""
        preferred = _INTENT_SECTION_ORDER.get(
            primary_intent, list(grouped.keys()),
        )

        sections: list[Section] = []
        used: set[str] = set()

        for heading in preferred:
            if heading in grouped and heading not in used:
                sections.append(Section(heading=heading, picks=grouped[heading]))
                used.add(heading)

        for heading, picks in grouped.items():
            if heading not in used:
                sections.append(Section(heading=heading, picks=picks))
                used.add(heading)

        return sections[:MAX_MAIN_SECTIONS]

    # ── Bonus sections ───────────────────────────────────────────────

    def _build_bonus_sections(
        self,
        primary_intent: str,
        existing_headings: set[str],
    ) -> list[Section]:
        """Add supplementary sections from mall-wide data."""
        desired = _INTENT_BONUS.get(primary_intent, [])
        if not desired:
            return []

        bonus: list[Section] = []
        for heading in desired:
            if heading in existing_headings:
                continue
            picks = self._find_bonus_picks(heading)
            if picks:
                bonus.append(Section(
                    heading=heading,
                    picks=picks[:3],
                    is_bonus=True,
                ))
                existing_headings.add(heading)
            if len(bonus) >= MAX_BONUS_SECTIONS:
                break

        return bonus

    def _find_bonus_picks(self, heading: str) -> list[StorePick]:
        """
        Scan the dining topic block for entities matching a bonus category.
        """
        block = self._mall_ctx.get_topic_block("dining")
        if not block:
            return []

        target_tags = _DINING_TAG_FILTERS.get(heading, set())
        picks: list[StorePick] = []

        for entity_ref in block.entities:
            eid = entity_ref.get("entity_id", "")
            canonical = self._mall_ctx.get_entity_by_id(eid)
            if not canonical:
                continue

            if target_tags:
                entity_tags = set(canonical.get("tags", []))
                profile = self._mall_ctx.get_semantic_profile(eid)
                if profile:
                    entity_tags |= set(profile.semantic_tags)
                dining_style = canonical.get("dining_style", "")
                if dining_style:
                    entity_tags.add(dining_style)
                if not entity_tags & target_tags:
                    continue

            loc = canonical.get("location", {})
            picks.append(StorePick(
                name=canonical.get("name", ""),
                entity_id=eid,
                category=heading,
                floor=loc.get("floor", "") if isinstance(loc, dict) else "",
                zone=loc.get("zone", "") if isinstance(loc, dict) else "",
            ))

        return picks

    # ── Intro / closing ──────────────────────────────────────────────

    @staticmethod
    def _build_intro(
        strategy: str,
        intent_tags: list[str],
        scene: Any | None,
    ) -> str:
        base = _STRATEGY_INTROS.get(strategy, "")
        if not base:
            if intent_tags:
                base = f"Here are some {intent_tags[0]} options:"
            else:
                base = "Here are some suggestions:"

        audience_parts: list[str] = []
        if scene:
            occasion = getattr(scene, "occasion", "")
            companions = getattr(scene, "companions", [])
            budget = getattr(scene, "budget", "")
            if occasion:
                audience_parts.append(occasion)
            if companions:
                audience_parts.append(f"with {', '.join(companions)}")
            if budget:
                audience_parts.append(f"{budget} budget")

        if audience_parts:
            return f"{base} ({'; '.join(audience_parts)})"
        return base

    @staticmethod
    def _build_closing(
        strategy: str,
        bonus_sections: list[Section],
    ) -> str:
        if bonus_sections:
            return ""
        return _STRATEGY_CLOSINGS.get(strategy, "")


# ═══════════════════════════════════════════════════════════════════════════
# LLM formatting prompt
# ═══════════════════════════════════════════════════════════════════════════


def build_mall_overview_prompt(overview: MallOverviewBlueprint) -> str:
    """
    Build an LLM system message for polishing a mall overview response.

    The LLM must NOT invent facts — it only adds warm, natural phrasing
    around the deterministic data provided.
    """
    return (
        "You are formatting a structured mall overview into warm, "
        "conversational concierge text. Follow these rules:\n"
        "\n"
        "1. Start with a one-line mall summary sentence.\n"
        "2. Add a natural heading or transition like 'Here's a quick overview:'.\n"
        "3. Present each section (location, hours, highlights, services, "
        "family-friendly) as a bullet point. Use the bullet symbol •.\n"
        "4. If a fact is marked MISSING, either omit the bullet entirely "
        "or write: 'I don't have that exact detail in the current mall data.'\n"
        "5. NEVER invent addresses, hours, store names, or facility details "
        "not in the data below.\n"
        "6. End with a friendly next-step suggestion like: "
        "'If you'd like, I can also help with the best shopping, dining, "
        "or family spots in the mall.'\n"
        "7. Be concise, readable, and concierge-like. No filler phrases.\n"
        "\n"
        "MALL OVERVIEW DATA:\n"
        "\n"
        f"{overview.to_prompt_block()}"
    )


def build_composer_prompt(blueprint: ResponseBlueprint) -> str:
    """
    Build an LLM system message that turns a :class:`ResponseBlueprint`
    into warm, natural concierge text.

    The LLM must NOT alter structure — it only adds conversational tone,
    transitions, and natural phrasing around the deterministic sections.
    """
    return (
        "You are formatting a structured mall concierge recommendation "
        "into warm, conversational text.  Follow these rules:\n"
        "\n"
        "1. KEEP the exact section groupings and store names — "
        "do not add, remove, or reorder any stores.\n"
        "2. Write a brief, natural opening line based on the intent.\n"
        "3. Present each section with its heading as a bold or "
        "standalone category label, then list stores as bullet points.\n"
        "4. Sections marked [BONUS] should be introduced with a "
        "friendly transition (e.g., 'You could even end the visit "
        "with dessert at:').\n"
        "5. End with a short closing line or follow-up suggestion.\n"
        "6. Be concise — no filler phrases or brochure language.\n"
        "7. Personalize to the visitor's occasion or companions "
        "if mentioned in the data.\n"
        "\n"
        "STRUCTURED DATA TO FORMAT:\n"
        "\n"
        f"{blueprint.to_prompt_block()}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _format_location(floor: str, zone: str) -> str:
    """Produce a concise location suffix like ' — Ground, East Wing'."""
    parts = [p for p in (floor, zone) if p]
    if parts:
        return f" — {', '.join(parts)}"
    return ""


def _bonus_transition(heading: str) -> str:
    return _BONUS_TRANSITIONS.get(heading, f"{heading}:")
