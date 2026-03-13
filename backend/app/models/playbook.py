"""
Scenario playbooks — pre-built recommendation strategies.

Playbooks encode human-like mall reasoning patterns for common visitor
scenarios.  They bridge user intent and entity selection via semantic
tags, ranking biases, and fallback logic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScenarioPlaybook(BaseModel):
    """
    A complete recommendation strategy for a specific visitor scenario.

    Encodes what entity types to prefer, which semantic tags to match,
    how to rank results, what to exclude, and how to shape the response.
    """

    playbook_id: str
    scenario: str
    description: str = ""

    # --- Activation ---
    trigger_conditions: list[str] = Field(default_factory=list)

    # --- Entity selection ---
    preferred_entity_types: list[str] = Field(default_factory=list)
    preferred_semantic_tags: list[str] = Field(default_factory=list)

    # --- Ranking ---
    ranking_biases: dict[str, float] = Field(default_factory=dict)

    # --- Fallback ---
    fallback_rules: list[str] = Field(default_factory=list)

    # --- Response shaping ---
    response_shape_hint: str = ""
    shortlist_size_hint: int = 3
    next_step_hint: str = ""

    # --- Exclusions ---
    do_not_include: list[str] = Field(default_factory=list)

    # --- Concierge reasoning ---
    concierge_reasoning_notes: str = ""
