"""
Scenario playbooks — pre-built recommendation strategies.

Playbooks encode human-like mall reasoning patterns for common visitor
scenarios.  They bridge user intent and entity selection via semantic
tags, ranking biases, fallback logic, topic continuity, and
narrowing-question hints.

Precedence rules:
- Audience-specific playbooks (e.g. affordable_kids_jackets) outrank
  generic playbooks (e.g. budget_family) when the active topic matches.
- Product-specific playbooks outrank category-generic playbooks.
- continuity_priority determines tie-breaking: higher = stickier.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScenarioPlaybook(BaseModel):
    """
    A complete recommendation strategy for a specific visitor scenario.

    Encodes what entity types to prefer, which semantic tags to match,
    how to rank results, what to exclude, how to shape the response,
    and how to preserve topic continuity across turns.
    """

    playbook_id: str
    scenario: str
    description: str = ""

    # --- Activation ---
    trigger_conditions: list[str] = Field(default_factory=list)

    # --- Continuity & precedence ---
    continuity_priority: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Higher = stickier. Audience+product playbooks should be 70-90; "
        "generic playbooks 30-50. Used for tie-breaking and topic lock.",
    )
    must_preserve_topic: bool = Field(
        default=False,
        description="When True, follow-ups that refine this topic stay within "
        "this playbook even if other playbooks partially match.",
    )
    must_preserve_audience: bool = Field(
        default=False,
        description="When True, audience context (kids, couple, family) persists "
        "across turns until explicitly changed.",
    )

    # --- Entity selection ---
    preferred_entity_types: list[str] = Field(default_factory=list)
    preferred_semantic_tags: list[str] = Field(default_factory=list)
    excluded_semantic_tags: list[str] = Field(
        default_factory=list,
        description="Tags that disqualify entities from this playbook's shortlist.",
    )

    # --- Ranking ---
    ranking_biases: dict[str, float] = Field(default_factory=dict)

    # --- Fallback ---
    fallback_rules: list[str] = Field(default_factory=list)

    # --- Response shaping ---
    response_shape_hint: str = ""
    shortlist_size_hint: int = 3
    next_step_hint: str = ""
    narrowing_question_hint: str = Field(
        default="",
        description="A ready-to-use follow-up question the concierge can ask "
        "to narrow results within this playbook's domain.",
    )

    # --- Exclusions ---
    do_not_include: list[str] = Field(default_factory=list)

    # --- Concierge reasoning ---
    concierge_reasoning_notes: str = ""

    # --- Topic continuity helpers (Part 3) ---
    topic_domain: str = Field(default="", description="e.g. shopping, dining, entertainment")
    topic_subdomain: str = Field(default="", description="e.g. kidswear_outerwear, kid_friendly_food")
    continuity_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords that signal the user is still within this playbook's topic.",
    )
    refinement_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords that narrow/refine within this playbook rather than switching away.",
    )
    likely_followups: list[str] = Field(
        default_factory=list,
        description="Anticipated follow-up topics the user might ask about next.",
    )
