"""
Prompt builder — assembles the full LLM prompt from pipeline state.

Prompt structure (in order of LLM message injection):

1. SYSTEM: Concierge identity + behavioral rules + tone + strategy
2. SYSTEM: Mall context (operational info, topic blocks, entity details)
3. SYSTEM: Scene memory + playbook reasoning + response instructions
4. USER/ASSISTANT: Conversation history (last N turns)
5. USER: Current turn query with inline scene context

The generate_response node delegates all prompt assembly here.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.state import ConciergeState
from app.runtime import get_mall_context
from app.services.tenant_runtime import TenantRuntime


class PromptBuilder:
    """Builds the complete prompt payload for the concierge LLM calls."""

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def build_messages(self, state: ConciergeState) -> list[dict[str, str]]:
        """
        Assemble the full message list for the LLM call.

        Only includes:
          1. System prompt (identity, rules, tone, strategy)
          2. Mall context (operational, topics, entities, retrieval, events)
          3. Scene + playbook instructions
          4. Current user message

        Previous LLM responses are never injected.  Continuity is
        provided by structured scene memory and session metadata
        (last_intent, conversation_mode) embedded in the scene block.
        """
        messages: list[dict[str, str]] = []

        messages.append({
            "role": "system",
            "content": self.build_system_prompt(state),
        })

        context_block = self.build_context_block(state)
        if context_block:
            messages.append({"role": "system", "content": context_block})

        scene_block = self.build_scene_and_instructions(state)
        if scene_block:
            messages.append({"role": "system", "content": scene_block})

        user_turn = self._build_current_turn(state)
        messages.append({"role": "user", "content": user_turn})

        return messages

    # ──────────────────────────────────────────────────────────────────
    # System prompt — identity, behavior, tone, strategy
    # ──────────────────────────────────────────────────────────────────

    def build_system_prompt(self, state: ConciergeState) -> str:
        parts: list[str] = []

        parts.append(self._identity_block(state))
        parts.append(self._behavioral_rules())
        parts.append(self._hallucination_constraints())
        parts.append(self._tone_block(state))
        parts.append(self._strategy_block(state))

        return "\n\n".join(p for p in parts if p)

    def _identity_block(self, state: ConciergeState) -> str:
        mall_name = "Cenomi Mall"
        pack = {}
        try:
            ctx = get_mall_context(state.mall_id)
            pack = ctx.get_context_pack()
            summary = pack.get("mall_profile_summary", {})
            mall_name = summary.get("name", mall_name)
        except RuntimeError:
            pass

        return (
            f"You are the {mall_name} Concierge — a warm, confident, and knowledgeable "
            f"in-mall assistant who helps visitors find exactly what they need.\n"
            f"You know this mall inside out: every store, restaurant, cinema screen, "
            f"ongoing event, and current promotion.\n"
            f"You speak from direct knowledge — never like a brochure or database."
        )

    def _behavioral_rules(self) -> str:
        return (
            "BEHAVIORAL RULES (strictly follow):\n"
            "1. ANSWER FIRST — always provide a useful answer before asking follow-up questions.\n"
            "2. LOW CLARIFICATION — infer intent boldly from context. Only ask if truly ambiguous.\n"
            "3. CONCIERGE TONE — speak as if you're standing in the mall, guiding someone in person.\n"
            "4. CONTEXTUAL CONTINUITY — remember what the visitor said earlier in this conversation. "
            "If this is a follow-up, interpret the query in the context of the previous question. "
            "For example, if they asked about kids' gifts and then say 'food?', answer about "
            "kid-friendly food — not generic food options.\n"
            "5. PRACTICAL — give floor locations, zone names, walking directions when relevant.\n"
            "6. REAL ENTITIES ONLY — use actual store/restaurant names from provided context. Never invent.\n"
            "7. CONCISE — be helpful but brief. No walls of text. Use short paragraphs or bullets.\n"
            "8. STRUCTURED RECOMMENDATIONS — when recommending stores or restaurants, group them into "
            "meaningful subcategories (e.g. Jewelry, Beauty & Perfume, Fashion Accessories) rather than "
            "a flat list.\n"
            "9. INTERACTIVE — end with a natural next-step suggestion or question when appropriate.\n"
            "10. GROUNDED — if you don't have the information, say so and suggest the Information Desk.\n"
            "11. NO BROCHURE LANGUAGE — avoid 'wide array of', 'plethora of', 'boasts'. Speak naturally.\n"
            "12. STRICT CATEGORY BOUNDARIES — when listing stores for a specific category, ONLY include "
            "stores that genuinely belong to that category. Do NOT mix categories. For example: "
            "jewelry stores are NOT beauty stores; sportswear is NOT clothing/fashion; "
            "beauty stores are NOT perfume specialists unless they specialize in perfume.\n"
            "13. NO PADDING — do NOT add unrequested suggestions from other categories. If the visitor "
            "asks about cafes, list cafes — do NOT append restaurant suggestions. If they ask about "
            "perfume stores, list perfume stores — do NOT suggest beauty stores instead. "
            "Only suggest related options if the visitor's category has very few results (1 or fewer).\n"
            "14. COMPLETE LISTS — when the visitor asks about a category, list ALL matching entities "
            "from the provided context, not just 1-2 examples. Include a brief description and "
            "location for each. Aim for 4-6 curated suggestions per recommendation. If the "
            "direct category has few matches, naturally supplement with related options.\n"
            "15. EXPERIENCE-BASED RECOMMENDATIONS — when the visitor shares context about who they're "
            "with or what they're doing (family, date, kids, friends), frame suggestions as a guided "
            "experience flow rather than a flat list. Example: 'Start at X for shopping → grab lunch "
            "at Y → finish with dessert at Z'. Think like a friend showing them around.\n"
            "16. FOCUSED PICKS — for recommendation queries, lead with your TOP 5-6 picks and briefly "
            "explain why each one fits. Present your best 3 first, then naturally mention the remaining "
            "as further options. Give the visitor enough choice to decide confidently."
        )

    def _hallucination_constraints(self) -> str:
        return (
            "CRITICAL RULES:\n"
            "1. You must never invent information.\n"
            "2. Only use tenants and facilities provided in the context.\n"
            "3. If you are unsure about a promotion or event, respond generically.\n"
            "   Allowed:  \"You may want to check the store for current offers.\"\n"
            "   Forbidden: \"Sephora currently has a 20% discount.\"\n"
            "4. Only state facts about tenants that are present in the context.\n"
            "   Allowed:  \"VOX Cinemas is located in the mall.\"\n"
            "   Forbidden: \"Avengers is currently playing at VOX.\""
        )

    def _tone_block(self, state: ConciergeState) -> str:
        if not state.active_tenant_parameters:
            return ""
        try:
            rt = TenantRuntime(state.active_tenant_parameters)
            tone_text = rt.tone_instructions()
            return f"TONE: {tone_text}"
        except Exception:
            return ""

    def _strategy_block(self, state: ConciergeState) -> str:
        plan = state.response_plan
        if not plan.chosen_strategy:
            return ""

        parts = [f"RESPONSE STRATEGY: {plan.chosen_strategy}"]

        shape_instructions: dict[str, str] = {
            "brief_answer": "Give a direct, factual answer in 1-3 sentences.",
            "numbered_shortlist": (
                "Present your TOP 5-6 picks with a brief reason why each fits. "
                "Lead with the best 3, then mention 2-3 more as further options. "
                "Include floor/zone. If the visitor has companions or an occasion, "
                "explain why each pick suits their situation."
            ),
            "step_by_step": "Lay out a mini-itinerary as numbered steps the visitor can follow.",
            "curated_picks": (
                "Present your TOP 5-6 gift picks with the recipient in mind — "
                "name, why it fits, and location. Frame your best 3 as: "
                "'For [recipient], I'd suggest X because... → then Y → and Z', "
                "then list 2-3 more as alternatives. "
                "Optionally suggest a coffee or dessert spot to complete the outing."
            ),
            "combo_suggestion": (
                "Suggest a combined plan as a natural flow: "
                "'Start with X → then Y → finish with Z'. "
                "Make it feel like a planned experience, not a list."
            ),
            "structured_itinerary": "Build a structured visit plan with activities, times, and locations.",
            "casual_shortlist": "Suggest a relaxed set of 3 options with laid-back tone and brief reasoning.",
            "value_focused_list": "Emphasize value and savings — mention prices, deals, budget-friendly options.",
            "conversational": "Respond naturally in conversational style.",
            "mini_itinerary": (
                "Offer a mini mall itinerary — a natural conversational flow of 2-4 things "
                "the visitor could do, covering different categories (shopping, dining, entertainment). "
                "Use specific store/restaurant names. Frame it as a journey: "
                "'Start at X → grab lunch at Y → finish with Z'. "
                "Keep it casual and personal, like a friend showing them around. "
                "Use short paragraphs separated by line breaks — NOT bullet points or numbered lists."
            ),
        }

        if plan.chosen_strategy == "exploration_overview":
            companion_hint = ""
            if state.scene.companions:
                companion_hint = (
                    f" The visitor is with {', '.join(state.scene.companions)}, "
                    "so tailor your itinerary to their group."
                )
            parts.append(
                "This is a vague/open exploration query. The visitor wants to know what's available. "
                "DO NOT ask clarifying questions. Instead, proactively offer a guided experience — "
                "a natural flow of 2-3 things they could do: "
                "'Start at X → grab a bite at Y → finish with Z'. "
                "Mention specific store/restaurant/entertainment names from the context. "
                "Sound like a friend who knows the mall, not like a search engine."
                + companion_hint
            )

        shape = plan.response_shape_hint
        if shape in shape_instructions:
            parts.append(f"SHAPE: {shape_instructions[shape]}")

        if plan.response_constraints:
            parts.append(f"CONSTRAINTS: {', '.join(plan.response_constraints)}")

        return "\n".join(parts)

    # ──────────────────────────────────────────────────────────────────
    # Context block — mall data, entities, retrieval facts
    # ──────────────────────────────────────────────────────────────────

    def build_context_block(self, state: ConciergeState) -> str:
        parts: list[str] = []

        parts.append(self._mall_operational_context(state.mall_id))
        parts.append(self._topic_block_context(state))
        parts.append(self._entity_context(state))
        parts.append(self._retrieval_facts(state))
        parts.append(self._events_offers_context(state.mall_id))

        block = "\n\n".join(p for p in parts if p)
        if block:
            return f"MALL INTELLIGENCE (use this to ground your answers):\n\n{block}"
        return ""

    def _mall_operational_context(self, mall_id: str) -> str:
        try:
            ctx = get_mall_context(mall_id)
            pack = ctx.get_context_pack()
        except RuntimeError:
            return ""

        op = pack.get("operational_context", {})
        if not op:
            return ""

        lines = ["[Mall Operations]"]
        hours = op.get("hours", {})
        if hours:
            for period, time_str in hours.items():
                if time_str:
                    lines.append(f"  {period}: {time_str}")

        parking = op.get("parking", {})
        if parking:
            lines.append(
                f"  Parking: {parking.get('capacity', 0)} spaces, "
                f"rate {parking.get('rate', 'N/A')}, "
                f"valet {'available' if parking.get('valet') else 'not available'}"
            )

        return "\n".join(lines)

    def _topic_block_context(self, state: ConciergeState) -> str:
        try:
            ctx = get_mall_context(state.mall_id)
        except RuntimeError:
            return ""

        blocks_text: list[str] = []
        for topic_name in state.context.selected_topic_blocks:
            block = ctx.get_topic_block(topic_name)
            if not block:
                continue
            lines = [f"[{block.topic.replace('_', ' ').title()}]"]
            if block.summary:
                lines.append(f"  {block.summary}")
            for hint in block.concierge_tips[:4]:
                lines.append(f"  • {hint}")
            for highlight in block.semantic_highlights[:3]:
                lines.append(f"  ★ {highlight}")
            blocks_text.append("\n".join(lines))

        return "\n\n".join(blocks_text)

    def _entity_context(self, state: ConciergeState) -> str:
        entities = state.context.selected_entities
        if not entities:
            return ""

        is_category = any(
            e.get("source", "").startswith("category/") for e in entities
        )
        is_discovery = any(
            e.get("source", "").startswith("related/") for e in entities
        ) or any(
            e.get("source", "") == "category/all_stores" for e in entities
        )
        is_offer = any(
            e.get("entity_type") in ("offer", "event") for e in entities
        )
        # Category lookups and offers: show all. Recommendations: cap at 10.
        if is_category or is_discovery or is_offer:
            display_limit = len(entities)
        else:
            display_limit = 10

        ranking_notes = state.context.ranking_notes
        has_category_note = any("CATEGORY RETRIEVAL" in n for n in ranking_notes)
        has_broad_note = any("BROAD SHOPPING DISCOVERY" in n for n in ranking_notes)
        has_expanded_note = any("EXPANDED DISCOVERY" in n for n in ranking_notes)

        header = (
            "[Curated Options — present your TOP 5-6 with reasoning, "
            "leading with your best 3, then mentioning the rest as further options.]"
        )
        if has_broad_note:
            header = (
                "[Stores Across Categories — present 5-6 curated suggestions "
                "grouped by category, with brief reasoning for each.]"
            )
        elif has_expanded_note:
            header = (
                "[Primary Matches + Related Suggestions — present your best "
                "direct matches first, then related options. Aim for 5-6 total.]"
            )
        elif has_category_note:
            header = (
                "[Category-Matched Entities — these are ALL the matching "
                "tenants in this category. List ALL of them in your response.]"
            )

        lines = [header]
        for e in entities[:display_limit]:
            name = e.get("name", "Unknown")
            etype = e.get("entity_type", "")
            category = e.get("category", "")
            subcategory = e.get("subcategory", "")
            description = e.get("description", "")
            floor = e.get("floor", "")
            zone = e.get("zone", "")
            notes = e.get("concierge_notes", "")
            tags = e.get("semantic_tags", [])

            loc_str = ""
            if floor or zone:
                loc_str = f" — {floor}"
                if zone:
                    loc_str += f", {zone}"

            cat_str = ""
            if category:
                cat_str = f" | {category}"
                if subcategory:
                    cat_str += f" > {subcategory}"

            line = f"  • {name} ({etype}{cat_str}){loc_str}"
            if tags:
                line += f" [{', '.join(tags[:4])}]"
            lines.append(line)

            if description:
                lines.append(f"    {description[:150]}")
            elif notes:
                lines.append(f"    Note: {notes[:150]}")

        return "\n".join(lines)

    def _retrieval_facts(self, state: ConciergeState) -> str:
        results = state.retrieval.retrieval_results
        if not results:
            return ""

        available = [r for r in results if r.get("data")]
        if not available:
            return ""

        lines = ["[Exact Facts Retrieved — use these for factual accuracy]"]
        for r in available:
            data = r["data"]
            fact_type = data.get("type", "fact")
            lines.append(f"  [{fact_type}]")
            for k, v in data.items():
                if k == "type":
                    continue
                if isinstance(v, (dict, list)):
                    lines.append(f"    {k}: {json.dumps(v, default=str)}")
                else:
                    lines.append(f"    {k}: {v}")

        return "\n".join(lines)

    def _events_offers_context(self, mall_id: str) -> str:
        try:
            ctx = get_mall_context(mall_id)
            pack = ctx.get_context_pack()
        except RuntimeError:
            return ""

        items = pack.get("events_and_offers", [])
        if not items:
            return ""

        lines = ["[Current Events & Offers — mention when relevant]"]
        for item in items[:6]:
            itype = item.get("type", "")
            title = item.get("title", "")
            desc = item.get("description", "")[:100]
            lines.append(f"  • [{itype}] {title}: {desc}")

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────
    # Scene + instructions block
    # ──────────────────────────────────────────────────────────────────

    def build_scene_and_instructions(self, state: ConciergeState) -> str:
        parts: list[str] = []

        parts.append(self._scene_block(state))
        parts.append(self._playbook_block(state))
        parts.append(self._reasoning_hints(state.mall_id))

        block = "\n\n".join(p for p in parts if p)
        return block

    def _scene_block(self, state: ConciergeState) -> str:
        scene = state.scene
        lines = ["VISITOR CONTEXT (use to personalize your response):"]

        if scene.companions:
            lines.append(f"  With: {', '.join(scene.companions)}")
        if scene.occasion:
            lines.append(f"  Occasion: {scene.occasion}")
        if scene.budget:
            lines.append(f"  Budget: {scene.budget}")
        if scene.current_area:
            lines.append(f"  Currently near: {scene.current_area}")
        if scene.audience:
            lines.append(f"  Audience signals: {', '.join(scene.audience)}")
        if scene.active_topic:
            lines.append(f"  Active topic: {scene.active_topic}")
        if scene.active_shortlist:
            lines.append(f"  Previous suggestions: {', '.join(scene.active_shortlist)}")
        if scene.rejected_options:
            lines.append(f"  Rejected: {', '.join(scene.rejected_options)} — DO NOT suggest these again")

        if state.last_intent:
            lines.append(f"  Previous intent: {state.last_intent}")

        msg_kind = state.intent.message_kind
        if msg_kind == "correction":
            lines.append("  ⚠ The visitor is CORRECTING a previous answer — acknowledge and adjust.")
        elif msg_kind == "refinement":
            lines.append("  The visitor is refining their request — build on your previous answer.")
            if scene.previous_need:
                lines.append(f"  Previous question was: {scene.previous_need}")
        elif msg_kind == "topic_switch":
            lines.append("  The visitor has switched topics — start fresh for this domain.")
        elif msg_kind == "followup":
            lines.append(
                "  ⚠ This is a FOLLOW-UP — interpret the current message in the context "
                "of what the visitor previously asked. Do NOT treat it as a standalone query."
            )
            if scene.previous_need:
                lines.append(f"  Previous question was: {scene.previous_need}")
            if scene.companions:
                lines.append(
                    f"  Remember: they are with {', '.join(scene.companions)} — "
                    "tailor your answer accordingly."
                )

        return "\n".join(lines) if len(lines) > 1 else ""

    def _playbook_block(self, state: ConciergeState) -> str:
        pb = state.playbook
        if not pb.selected_playbook:
            return ""

        try:
            ctx = get_mall_context(state.mall_id)
            pb_obj = ctx.match_playbook(intent=pb.selected_playbook)
            if pb_obj:
                lines = [f"ACTIVE PLAYBOOK: {pb_obj.scenario}"]
                if pb_obj.response_shape_hint:
                    lines.append(f"  Shape: {pb_obj.response_shape_hint}")
                if pb_obj.concierge_reasoning_notes:
                    lines.append(f"  Reasoning: {pb_obj.concierge_reasoning_notes}")
                if pb_obj.fallback_rules:
                    lines.append(f"  Fallbacks: {'; '.join(pb_obj.fallback_rules[:3])}")
                if pb_obj.next_step_hint:
                    lines.append(f"  Suggest next: {pb_obj.next_step_hint}")
                return "\n".join(lines)
        except Exception:
            pass

        return f"ACTIVE PLAYBOOK: {pb.selected_playbook}"

    def _reasoning_hints(self, mall_id: str) -> str:
        try:
            ctx = get_mall_context(mall_id)
            pack = ctx.get_context_pack()
        except RuntimeError:
            return ""

        hints = pack.get("contextual_reasoning_hints", [])
        if not hints:
            return ""

        lines = ["REASONING HINTS:"]
        for hint in hints[:5]:
            lines.append(f"  • {hint}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────
    # Current turn message
    # ──────────────────────────────────────────────────────────────────

    def _build_current_turn(self, state: ConciergeState) -> str:
        msg = state.normalized_user_message

        if state.intent.message_kind in ("followup", "refinement") and state.scene.previous_need:
            context_parts = [f"[Previous question: {state.scene.previous_need}]"]
            if state.scene.audience:
                context_parts.append(f"[Audience: {', '.join(state.scene.audience)}]")
            if state.scene.companions:
                context_parts.append(f"[With: {', '.join(state.scene.companions)}]")
            context = "\n".join(context_parts)
            return f"{context}\n\nCurrent follow-up: {msg}"

        return msg
