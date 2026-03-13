"""
Global mall context pack — the LLM-ready intelligence structure.

Assembles canonical entities, semantic intelligence, and playbooks
into a single runtime-optimized context pack for injection into
the concierge pipeline.

Designed for selective topic-block injection: only the blocks
relevant to the detected user intent are injected into the LLM prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.mall import MallProfile
from app.models.tenant import (
    Cinema,
    DiningOutlet,
    Event,
    Movie,
    Offer,
    ServicePoint,
    Store,
)


# ---------------------------------------------------------------------------
# Canonical aggregate — all entity types for one mall
# ---------------------------------------------------------------------------


class CanonicalMallData(BaseModel):
    """Complete canonical entity data for a single mall."""

    mall_profile: MallProfile
    stores: list[Store] = Field(default_factory=list)
    dining: list[DiningOutlet] = Field(default_factory=list)
    cinemas: list[Cinema] = Field(default_factory=list)
    movies: list[Movie] = Field(default_factory=list)
    services: list[ServicePoint] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Topic block — a self-contained knowledge unit for runtime injection
# ---------------------------------------------------------------------------


class TopicBlock(BaseModel):
    """
    A self-contained knowledge block for a specific topic.

    Only relevant blocks are injected into the LLM prompt
    based on detected intent, keeping token usage efficient.
    """

    topic: str
    summary: str = ""
    entities: list[dict] = Field(default_factory=list)
    semantic_highlights: list[str] = Field(default_factory=list)
    concierge_tips: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Concierge guidelines
# ---------------------------------------------------------------------------


class ConciergeGuidelines(BaseModel):
    """Behavioral guidelines for the concierge persona."""

    persona_name: str = "Cenomi Concierge"
    tone: str = "friendly, knowledgeable, helpful, professional"
    language: str = "English (with Arabic cultural awareness)"
    response_style: str = "Answer first, then elaborate. Be concise but thorough."
    grounding_rules: list[str] = Field(default_factory=list)
    cultural_notes: list[str] = Field(default_factory=list)
    do_not: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Global context pack
# ---------------------------------------------------------------------------


class GlobalContextPack(BaseModel):
    """
    The complete LLM-ready context pack for a single mall.

    Optimized for runtime context composition — individual topic blocks
    are selected based on user intent rather than dumping everything.
    """

    mall_id: str
    version: str = "1.0"

    # 1. Mall identity
    mall_profile_summary: dict = Field(default_factory=dict)

    # 2. Operational context (hours, parking, accessibility)
    operational_context: dict = Field(default_factory=dict)

    # 3-6. Semantic knowledge blocks (keyed by topic)
    topic_blocks: dict[str, TopicBlock] = Field(default_factory=dict)

    # 7. Temporal content
    events_and_offers: list[dict] = Field(default_factory=list)

    # 8. Playbooks
    playbooks: list[dict] = Field(default_factory=list)

    # 9. Reasoning hints
    contextual_reasoning_hints: list[str] = Field(default_factory=list)

    # 10. Concierge guidelines
    concierge_guidelines: ConciergeGuidelines = Field(
        default_factory=ConciergeGuidelines
    )
