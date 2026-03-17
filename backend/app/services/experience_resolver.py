"""
Experience Layer Resolver — maps pipeline state to a response_experience_mode
and companion orchestration fields that shape the final answer presentation.

This is a pure-function module: no I/O, no LLM calls, no side-effects.
Called by generate_response to decide HOW the answer should be framed
before the LLM prompt is assembled.

Output fields:
  response_experience_mode   – the primary presentation mode
  cta_type                   – which CTA category to append (or "" for none)
  itinerary_allowed          – True when a micro-itinerary structure is appropriate
  refinement_acknowledgement – human-readable note about what filter was applied
  followup_prompt_hint       – suggestion for what to ask the user next
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# Experience mode constants
# ─────────────────────────────────────────────────────────────────────────────

GREETING_SCAFFOLD = "greeting_scaffold"
STRUCTURED_OVERVIEW = "structured_overview"
FACTUAL_LIST = "factual_list"
FILTERED_FACTUAL_LIST = "filtered_factual_list"
CONCISE_GUIDED = "concise_guided"
CURATED_SHORTLIST = "curated_shortlist"
MICRO_ITINERARY = "micro_itinerary"
DIRECT_LOOKUP = "direct_lookup"
ROUTE_HINT = "route_hint"
NEXT_BEST_ACTION = "next_best_action"

# ─────────────────────────────────────────────────────────────────────────────
# CTA type constants
# ─────────────────────────────────────────────────────────────────────────────

CTA_MOVIE_REFINEMENT = "movie_refinement"
CTA_FAMILY_NARROWING = "family_narrowing"
CTA_DINING_SUGGESTION = "dining_suggestion"
CTA_SHOPPING_NARROWING = "shopping_narrowing"
CTA_SERVICE_HELP = "service_help"
CTA_OVERVIEW_CONTINUE = "overview_continue"
CTA_ROUTE_HELP = "route_help"
CTA_CROSS_MALL = "cross_mall"
CTA_NONE = ""

# ─────────────────────────────────────────────────────────────────────────────
# Classification sets
# ─────────────────────────────────────────────────────────────────────────────

_FAMILY_SIGNALS: frozenset[str] = frozenset({
    "family_filter", "kid_friendly", "family_friendly",
    "parent_with_child", "parent_friendly",
})

_BUDGET_SIGNALS: frozenset[str] = frozenset({
    "budget_filter", "budget_sensitive", "affordable", "cheap",
})

_ROMANTIC_SIGNALS: frozenset[str] = frozenset({
    "romantic_filter", "romantic", "couple_friendly",
})

_PROXIMITY_SIGNALS: frozenset[str] = frozenset({
    "proximity_filter", "near_cinema", "before_movie_constraint", "time_sensitive",
})

_ALL_FILTER_SIGNALS: frozenset[str] = (
    _FAMILY_SIGNALS | _BUDGET_SIGNALS | _ROMANTIC_SIGNALS | _PROXIMITY_SIGNALS
)

# Strategies that allow micro-itinerary output
_ITINERARY_STRATEGIES: frozenset[str] = frozenset({
    "guided_plan", "mini_itinerary", "movie_plus_food",
    "family_plan", "route_plus_plan",
})

# Strategies that produce a curated shortlist
_SHORTLIST_STRATEGIES: frozenset[str] = frozenset({
    "concise_shortlist", "shortlist_recommendation", "gift_formula",
    "quick_answer", "exploration_overview",
})


# ─────────────────────────────────────────────────────────────────────────────
# Output dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExperienceResolution:
    """Full output of the experience resolver for one pipeline turn."""

    response_experience_mode: str = ""
    cta_type: str = ""
    itinerary_allowed: bool = False
    refinement_acknowledgement: str = ""
    followup_prompt_hint: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Main resolver
# ─────────────────────────────────────────────────────────────────────────────

def resolve_experience(
    *,
    flow_type: str,
    primary_intent: str,
    secondary_intents: list[str],
    modifiers: list[str],
    response_strategy: str,
    chosen_strategy: str,
    message_kind: str,
    domain: str,
    sub_intent: str,
    fact_scope: str,
    has_companions: bool,
    has_occasion: bool,
    has_child: bool,
    visit_constraints: list[str],
    active_shortlist: list[str],
) -> ExperienceResolution:
    """
    Deterministic mapper: pipeline context → ExperienceResolution.

    Priority order (first match wins):
      1. Greeting turn            → greeting_scaffold
      2. Mall overview domain     → structured_overview
      3. Cross-mall flow          → direct_lookup (specialised)
      4. Factual flow + filters   → filtered_factual_list
      5. Factual flow             → factual_list / direct_lookup / route_hint
      6. Concierge + itinerary    → micro_itinerary
      7. Concierge + shortlist    → curated_shortlist
      8. Concierge + guided plan  → concise_guided
      9. Default concierge        → concise_guided
    """
    all_signals: frozenset[str] = frozenset(secondary_intents) | frozenset(modifiers)

    # ── 1. Greeting ───────────────────────────────────────────────────────────
    if message_kind == "greeting" or domain == "greeting":
        return ExperienceResolution(
            response_experience_mode=GREETING_SCAFFOLD,
            cta_type=CTA_NONE,
            itinerary_allowed=False,
            refinement_acknowledgement="",
            followup_prompt_hint="Ask what they are looking for today",
        )

    # ── 2. Mall overview ──────────────────────────────────────────────────────
    if domain == "mall_info" or chosen_strategy == "mall_overview":
        return ExperienceResolution(
            response_experience_mode=STRUCTURED_OVERVIEW,
            cta_type=CTA_OVERVIEW_CONTINUE,
            itinerary_allowed=False,
            refinement_acknowledgement="",
            followup_prompt_hint=(
                "Ask about their plan — shopping, food, cinema, or kids' fun"
            ),
        )

    # ── 3. Factual flow ───────────────────────────────────────────────────────
    if flow_type == "factual":
        has_any_filter = bool(all_signals & _ALL_FILTER_SIGNALS)

        # Filtered factual: factual primary intent + secondary filters active
        if has_any_filter or response_strategy == "filtered_factual_list":
            ack = _build_factual_refinement_ack(
                fact_scope=fact_scope,
                message_kind=message_kind,
                active_shortlist=active_shortlist,
                all_signals=all_signals,
            )
            cta = _resolve_factual_cta(
                fact_scope=fact_scope,
                domain=domain,
                has_child=has_child,
                all_signals=all_signals,
            )
            return ExperienceResolution(
                response_experience_mode=FILTERED_FACTUAL_LIST,
                cta_type=cta,
                itinerary_allowed=False,
                refinement_acknowledgement=ack,
                followup_prompt_hint=_factual_followup_hint(fact_scope, filtered=True),
            )

        # Route hint
        if fact_scope == "route_hint":
            return ExperienceResolution(
                response_experience_mode=ROUTE_HINT,
                cta_type=CTA_ROUTE_HELP,
                itinerary_allowed=False,
                refinement_acknowledgement="",
                followup_prompt_hint="Offer to guide to any nearby spot",
            )

        # Brand / store lookup
        if fact_scope in ("brand_availability", "store_lookup"):
            cta = CTA_CROSS_MALL if domain == "cross_mall" else CTA_NONE
            return ExperienceResolution(
                response_experience_mode=DIRECT_LOOKUP,
                cta_type=cta,
                itinerary_allowed=False,
                refinement_acknowledgement="",
                followup_prompt_hint="",
            )

        # Movie / cinema schedule
        if fact_scope == "movie_schedule" or sub_intent in (
            "movie_showtime", "movie_lookup",
        ):
            return ExperienceResolution(
                response_experience_mode=FACTUAL_LIST,
                cta_type=CTA_MOVIE_REFINEMENT,
                itinerary_allowed=False,
                refinement_acknowledgement="",
                followup_prompt_hint=(
                    "Ask about genre, companions, or booking preference"
                ),
            )

        # Default factual
        cta = _resolve_factual_cta(
            fact_scope=fact_scope,
            domain=domain,
            has_child=has_child,
            all_signals=all_signals,
        )
        return ExperienceResolution(
            response_experience_mode=FACTUAL_LIST,
            cta_type=cta,
            itinerary_allowed=False,
            refinement_acknowledgement="",
            followup_prompt_hint=_factual_followup_hint(fact_scope, filtered=False),
        )

    # ── 4. Concierge flow ─────────────────────────────────────────────────────

    # Refinement / follow-up acknowledgement
    ack = _build_concierge_refinement_ack(
        message_kind=message_kind,
        visit_constraints=visit_constraints,
        has_child=has_child,
        active_shortlist=active_shortlist,
        all_signals=all_signals,
    )

    # Itinerary eligibility
    itinerary_allowed = _is_itinerary_eligible(
        chosen_strategy=chosen_strategy,
        has_companions=has_companions,
        has_occasion=has_occasion,
        has_child=has_child,
        domain=domain,
    )

    # Micro-itinerary: strategy explicitly calls for it
    if chosen_strategy in _ITINERARY_STRATEGIES and itinerary_allowed:
        cta = _resolve_concierge_cta(
            domain=domain,
            has_child=has_child,
            has_occasion=has_occasion,
            chosen_strategy=chosen_strategy,
        )
        return ExperienceResolution(
            response_experience_mode=MICRO_ITINERARY,
            cta_type=cta,
            itinerary_allowed=True,
            refinement_acknowledgement=ack,
            followup_prompt_hint="Ask if they want to add or adjust any step",
        )

    # Curated shortlist
    if chosen_strategy in _SHORTLIST_STRATEGIES:
        mode = CURATED_SHORTLIST
        cta = _resolve_concierge_cta(
            domain=domain,
            has_child=has_child,
            has_occasion=has_occasion,
            chosen_strategy=chosen_strategy,
        )
        return ExperienceResolution(
            response_experience_mode=mode,
            cta_type=cta,
            itinerary_allowed=False,
            refinement_acknowledgement=ack,
            followup_prompt_hint=_concierge_followup_hint(domain),
        )

    # Guided plan (companions/occasion present even without explicit strategy)
    if chosen_strategy == "guided_plan" or (has_companions and has_occasion):
        cta = _resolve_concierge_cta(
            domain=domain,
            has_child=has_child,
            has_occasion=has_occasion,
            chosen_strategy=chosen_strategy,
        )
        return ExperienceResolution(
            response_experience_mode=CONCISE_GUIDED,
            cta_type=cta,
            itinerary_allowed=itinerary_allowed,
            refinement_acknowledgement=ack,
            followup_prompt_hint=_concierge_followup_hint(domain),
        )

    # Default concierge fallback
    cta = _resolve_concierge_cta(
        domain=domain,
        has_child=has_child,
        has_occasion=has_occasion,
        chosen_strategy=chosen_strategy,
    )
    return ExperienceResolution(
        response_experience_mode=CONCISE_GUIDED,
        cta_type=cta,
        itinerary_allowed=itinerary_allowed,
        refinement_acknowledgement=ack,
        followup_prompt_hint=_concierge_followup_hint(domain),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_factual_refinement_ack(
    *,
    fact_scope: str,
    message_kind: str,
    active_shortlist: list[str],
    all_signals: frozenset[str],
) -> str:
    if message_kind not in ("refinement", "constraint_refinement", "followup"):
        return ""
    if all_signals & _FAMILY_SIGNALS:
        scope_label = fact_scope or "list"
        return f"child/family filter applied to active {scope_label}"
    if all_signals & _BUDGET_SIGNALS:
        return "budget filter applied"
    if all_signals & _ROMANTIC_SIGNALS:
        return "couple/romantic filter applied"
    if active_shortlist:
        return f"filter applied to active {fact_scope or 'list'}"
    return ""


def _build_concierge_refinement_ack(
    *,
    message_kind: str,
    visit_constraints: list[str],
    has_child: bool,
    active_shortlist: list[str],
    all_signals: frozenset[str],
) -> str:
    if message_kind not in ("refinement", "constraint_refinement", "followup"):
        return ""
    if visit_constraints:
        constraint_str = ", ".join(visit_constraints[:2])
        return f"{constraint_str} constraint applied to active context"
    if has_child and active_shortlist:
        return "child filter applied to active suggestion list"
    if message_kind == "followup" and active_shortlist:
        return "continuing from previous suggestion"
    if all_signals & _BUDGET_SIGNALS:
        return "budget constraint applied"
    return ""


def _is_itinerary_eligible(
    *,
    chosen_strategy: str,
    has_companions: bool,
    has_occasion: bool,
    has_child: bool,
    domain: str,
) -> bool:
    if chosen_strategy in _ITINERARY_STRATEGIES:
        return True
    if has_companions and has_occasion:
        return True
    if has_child and domain in ("shopping", "exploration", "dining", "entertainment"):
        return True
    if domain in ("exploration",):
        return True
    return False


def _resolve_factual_cta(
    *,
    fact_scope: str,
    domain: str,
    has_child: bool,
    all_signals: frozenset[str],
) -> str:
    if fact_scope == "movie_schedule":
        if has_child or bool(all_signals & _FAMILY_SIGNALS):
            return CTA_FAMILY_NARROWING
        return CTA_MOVIE_REFINEMENT
    if fact_scope in ("route_hint", "service_lookup", "facility_location"):
        return CTA_ROUTE_HELP
    if domain == "dining":
        return CTA_DINING_SUGGESTION
    if domain in ("shopping",):
        return CTA_SHOPPING_NARROWING
    return CTA_NONE


def _resolve_concierge_cta(
    *,
    domain: str,
    has_child: bool,
    has_occasion: bool,
    chosen_strategy: str,
) -> str:
    if domain in ("entertainment", "cinema"):
        if has_child:
            return CTA_FAMILY_NARROWING
        return CTA_MOVIE_REFINEMENT
    if domain == "dining":
        return CTA_DINING_SUGGESTION
    if domain in ("shopping", "exploration"):
        if has_child:
            return CTA_FAMILY_NARROWING
        return CTA_SHOPPING_NARROWING
    if chosen_strategy in ("route_plus_plan", "route_hint"):
        return CTA_ROUTE_HELP
    return CTA_NONE


def _factual_followup_hint(fact_scope: str, *, filtered: bool) -> str:
    if fact_scope == "movie_schedule":
        if filtered:
            return "Ask which movie they want or if they'd like to narrow by age group"
        return "Ask about genre, companions, or booking preference"
    if fact_scope == "route_hint":
        return "Offer to find any nearby spot or service"
    return ""


def _concierge_followup_hint(domain: str) -> str:
    if domain in ("shopping", "exploration"):
        return "Ask about budget, gifts, or family-friendly options"
    if domain == "dining":
        return "Ask about cuisine preference, speed, or proximity to cinema"
    if domain in ("entertainment", "cinema"):
        return "Ask about genre or companion preferences"
    return ""
