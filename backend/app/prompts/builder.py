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

    MAX_HISTORY_TURNS = 10

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def build_messages(self, state: ConciergeState) -> list[dict[str, str]]:
        """Assemble the full message list for the LLM call."""
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

        history = state.messages[:-1] if state.messages else []
        for msg in history[-self.MAX_HISTORY_TURNS:]:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})

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
        parts.append(self._tone_block(state))
        parts.append(self._strategy_block(state))

        return "\n\n".join(p for p in parts if p)

    def _identity_block(self, state: ConciergeState) -> str:
        mall_name = "Cenomi Mall"
        pack = {}
        try:
            ctx = get_mall_context()
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
            "4. CONTEXTUAL CONTINUITY — remember what the visitor said earlier in this conversation.\n"
            "5. PRACTICAL — give floor locations, zone names, walking directions when relevant.\n"
            "6. REAL ENTITIES ONLY — use actual store/restaurant names from provided context. Never invent.\n"
            "7. CONCISE — be helpful but brief. No walls of text. Use short paragraphs or bullets.\n"
            "8. INTERACTIVE — end with a natural next-step suggestion or question when appropriate.\n"
            "9. GROUNDED — if you don't have the information, say so and suggest the Information Desk.\n"
            "10. NO BROCHURE LANGUAGE — avoid 'wide array of', 'plethora of', 'boasts'. Speak naturally."
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
            "numbered_shortlist": "Present 2-4 options as a numbered list with name, one-line description, and floor/zone.",
            "step_by_step": "Lay out a mini-itinerary as numbered steps the visitor can follow.",
            "curated_picks": "Present gift picks with the recipient in mind — name, why it fits, price range, location.",
            "combo_suggestion": "Suggest a combined plan (e.g. movie + dinner) as a natural flow.",
            "structured_itinerary": "Build a structured visit plan with activities, times, and locations.",
            "casual_shortlist": "Suggest a relaxed set of options with laid-back tone.",
            "value_focused_list": "Emphasize value and savings — mention prices, deals, budget-friendly options.",
            "conversational": "Respond naturally in conversational style.",
            "mini_itinerary": (
                "Offer a mini mall itinerary — a natural conversational flow of 2-4 things "
                "the visitor could do, covering different categories (shopping, dining, entertainment). "
                "Use specific store/restaurant names. Keep it casual and personal, like a friend "
                "showing them around. Use short paragraphs separated by line breaks — NOT bullet "
                "points or numbered lists. Example tone:\n"
                "  'You can start with some shopping — fashion brands like Zara and Mango are popular.\n"
                "  If you're in the mood for entertainment, the cinema is a good option.\n"
                "  Or you can relax with coffee or dessert — cafés like %Arabica and Paul are great for that.'"
            ),
        }

        if plan.chosen_strategy == "exploration_overview":
            parts.append(
                "This is a vague/open exploration query. The visitor wants to know what's available. "
                "DO NOT ask clarifying questions. Instead, proactively offer a mini itinerary "
                "covering different categories. Mention specific store/restaurant/entertainment names "
                "from the context. Sound like a friend who knows the mall, not like a search engine."
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

        parts.append(self._mall_operational_context())
        parts.append(self._topic_block_context(state))
        parts.append(self._entity_context(state))
        parts.append(self._retrieval_facts(state))
        parts.append(self._events_offers_context())

        block = "\n\n".join(p for p in parts if p)
        if block:
            return f"MALL INTELLIGENCE (use this to ground your answers):\n\n{block}"
        return ""

    def _mall_operational_context(self) -> str:
        try:
            ctx = get_mall_context()
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
            ctx = get_mall_context()
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

        lines = ["[Selected Entities for This Turn]"]
        for e in entities[:8]:
            name = e.get("name", "Unknown")
            etype = e.get("entity_type", "")
            floor = e.get("floor", "")
            zone = e.get("zone", "")
            notes = e.get("concierge_notes", "")
            tags = e.get("semantic_tags", [])

            loc_str = ""
            if floor or zone:
                loc_str = f" — {floor}"
                if zone:
                    loc_str += f", {zone}"

            line = f"  • {name} ({etype}){loc_str}"
            if tags:
                line += f" [{', '.join(tags[:4])}]"
            lines.append(line)

            if notes:
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

    def _events_offers_context(self) -> str:
        try:
            ctx = get_mall_context()
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
        parts.append(self._reasoning_hints())

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

        msg_kind = state.intent.message_kind
        if msg_kind == "correction":
            lines.append("  ⚠ The visitor is CORRECTING a previous answer — acknowledge and adjust.")
        elif msg_kind == "refinement":
            lines.append("  The visitor is refining their request — build on your previous answer.")
        elif msg_kind == "topic_switch":
            lines.append("  The visitor has switched topics — start fresh for this domain.")
        elif msg_kind == "followup":
            lines.append("  This is a follow-up — continue the current thread naturally.")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _playbook_block(self, state: ConciergeState) -> str:
        pb = state.playbook
        if not pb.selected_playbook:
            return ""

        try:
            ctx = get_mall_context()
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

    def _reasoning_hints(self) -> str:
        try:
            ctx = get_mall_context()
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
        return state.normalized_user_message
