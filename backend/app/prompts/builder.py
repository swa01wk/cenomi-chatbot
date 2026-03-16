"""
Prompt builder — assembles the full LLM prompt from pipeline state.

Prompt structure (in order of LLM message injection):

1. SYSTEM: Concierge identity + behavioral rules + tone + strategy
2. SYSTEM: Mall context (operational info, topic blocks, entity details)
3. SYSTEM: Scene memory + playbook reasoning + response instructions
4. SYSTEM: AI Findr response formatter contract (binding output structure)
5. USER/ASSISTANT: Conversation history (last N turns)
6. USER: Current turn query with inline scene context

The generate_response node delegates all prompt assembly here.
"""

from __future__ import annotations

import json

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

        formatter_block = self._ai_findr_formatter_block(state)
        if formatter_block:
            messages.append({"role": "system", "content": formatter_block})

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
        parts.append(self._response_policy_block(state))
        parts.append(self._strategy_block(state))

        return "\n\n".join(p for p in parts if p)

    def _identity_block(self, state: ConciergeState) -> str:
        mall_name = "Cenomi Mall"
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

    def _response_policy_block(self, state: ConciergeState) -> str:
        if not state.active_tenant_parameters:
            return ""
        try:
            rt = TenantRuntime(state.active_tenant_parameters)
            return rt.response_policy_instructions()
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

        parts.append(self._response_contract_block(state))

        return "\n".join(p for p in parts if p)

    def _response_contract_block(self, state: ConciergeState) -> str:
        """Translate the response_contract into binding LLM instructions."""
        contract = state.response_contract
        lines = ["RESPONSE CONTRACT (strictly follow):"]

        intro_map = {
            "scene_aware": "Open with a brief scene-aware intro that mirrors the visitor's situation.",
            "topic_continuation": "Continue naturally from the previous turn — no re-greeting.",
            "fresh_greeting": "Open with a warm but brief greeting.",
            "none": "Skip the intro — jump straight to the answer.",
        }
        lines.append(f"  INTRO: {intro_map.get(contract.intro_style, 'scene_aware')}")
        lines.append(f"  SHORTLIST SIZE: exactly {contract.shortlist_size} options")

        if contract.explanation_style == "one_line_reason":
            lines.append("  EXPLANATIONS: one line per option — why it fits this visitor.")
        elif contract.explanation_style == "paragraph":
            lines.append("  EXPLANATIONS: short paragraph per option.")
        else:
            lines.append("  EXPLANATIONS: none — names only.")

        loc_map = {
            "always": "Include floor/zone for every option.",
            "when_useful": "Include floor/zone only when it helps the visitor decide.",
            "never": "Omit floor/zone details.",
        }
        lines.append(f"  LOCATION: {loc_map.get(contract.location_visibility, 'when_useful')}")

        if contract.followup_style == "narrowing_question":
            lines.append("  CLOSE: end with a narrowing question that helps the visitor decide.")
        elif contract.followup_style == "open_ended":
            lines.append("  CLOSE: end with an open-ended follow-up.")
        else:
            lines.append("  CLOSE: no follow-up question needed.")

        if contract.continuity_requirement:
            lines.append("  CONTINUITY: reference the active thread from previous turns.")

        if contract.brochure_tone_forbidden:
            lines.append("  TONE: NO brochure language — no 'wide array', 'plethora', 'boasts'.")

        price_map = {
            "range_only": "Use rough price ranges (e.g. 'mid-range', '~200 SAR') — never exact prices.",
            "tier_label": "Use price tier labels only (budget, mid-range, premium).",
            "suppress": "Do not mention pricing at all.",
        }
        lines.append(f"  PRICING: {price_map.get(contract.exact_price_mode, 'range_only')}")

        if state.candidate_reason_map:
            lines.append("  ENTITY REASONS (use these for each option):")
            for entry in state.candidate_reason_map:
                if entry.one_line_reason:
                    lines.append(f"    - {entry.entity_name}: {entry.one_line_reason}")

        if state.narrowing_followup.is_useful:
            lines.append(
                f"  SUGGESTED CLOSING QUESTION: {state.narrowing_followup.suggested_question}"
            )

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────
    # AI Findr Response Formatter — enforces output structure
    # ──────────────────────────────────────────────────────────────────

    def _ai_findr_formatter_block(self, state: ConciergeState) -> str:
        """
        The binding AI Findr output format contract.

        Enforces the 5-part response structure:
          1. short scene-aware intro
          2. curated shortlist or grouped answer
          3. one-line reason per option
          4. optional practical tip
          5. one narrowing follow-up question if helpful
        """
        contract = state.response_contract
        anchor = state.continuity_anchor
        scene = state.scene
        continuity_res = state.continuity_resolution

        lines = [
            "AI FINDR RESPONSE FORMAT (strictly follow this output structure):",
            "",
            "Your response MUST follow this exact structure:",
            "",
        ]

        # Part 1: Intro
        if contract.intro_style == "topic_continuation":
            lines.append(
                "1. INTRO: Continue naturally from the last turn. Do NOT re-greet or "
                "re-introduce the topic. One sentence max that bridges from the previous "
                "answer to this refinement."
            )
        elif contract.intro_style == "scene_aware" and (
            scene.companions or scene.occasion or scene.budget
        ):
            scene_fragments: list[str] = []
            if scene.companions:
                scene_fragments.append(f"visitor is with {', '.join(scene.companions)}")
            if scene.occasion:
                scene_fragments.append(f"occasion: {scene.occasion}")
            if scene.budget:
                scene_fragments.append(f"budget: {scene.budget}")
            lines.append(
                f"1. INTRO: One short sentence that acknowledges the visitor's situation "
                f"({'; '.join(scene_fragments)}). Example: 'For your 5-year-old son, ...' "
                f"or 'Since you're looking for something budget-friendly, ...'. "
                f"Do NOT write a generic greeting."
            )
        else:
            lines.append(
                "1. INTRO: One short contextual sentence. No generic greetings like "
                "'Sure!' or 'Of course!'. Jump straight into value."
            )

        # Part 2: Shortlist
        lines.append(
            f"2. SHORTLIST: Present exactly {contract.shortlist_size} curated options. "
            f"Mention each by real name. Weave them into natural prose — do NOT use "
            f"numbered lists or bullet points. The options should flow conversationally, "
            f"like a knowledgeable friend recommending."
        )

        # Part 3: Reasons
        lines.append(
            "3. REASONS: For each option, include ONE concise reason why it fits this "
            "specific visitor. Embed the reason naturally in the same sentence as the "
            "option name. Example: 'Max is usually the easiest budget-friendly option "
            "for kidswear' — not a separate bullet."
        )

        # Part 4: Practical tip
        lines.append(
            "4. PRACTICAL TIP (optional): If there's a useful practical detail "
            "(timing, proximity, a deal), weave it in naturally. Only include if "
            "genuinely helpful — do NOT force tips."
        )

        # Part 5: Narrowing follow-up
        if state.narrowing_followup.is_useful:
            lines.append(
                f"5. CLOSING: End with this narrowing question (paraphrase naturally): "
                f"'{state.narrowing_followup.suggested_question}'"
            )
        elif contract.followup_style == "narrowing_question":
            lines.append(
                "5. CLOSING: End with ONE narrowing question that helps the visitor "
                "decide between the options. Example: 'If you want, I can narrow it "
                "further for under 100 SAR.' Do NOT ask 'anything else?' or "
                "'would you like more information?'."
            )
        else:
            lines.append(
                "5. CLOSING: End naturally — no forced follow-up needed."
            )

        # Anti-patterns
        lines.extend([
            "",
            "ANTI-PATTERNS (never do these):",
            "- Do NOT sound like a mall directory or information kiosk.",
            "- Do NOT overuse floor/zone details — mention only if the visitor needs them.",
            "- Do NOT over-explain or include marketing copy about stores.",
            "- Do NOT use numbered lists or bullet points — use flowing prose.",
            "- Do NOT say 'here at Cenomi Mall' repeatedly — say 'here' or 'the mall'.",
            "- Do NOT ask clarifying questions if you have enough context to answer.",
            "- Help the visitor CHOOSE, not just list options.",
        ])

        # Price expectation handling
        if contract.exact_price_mode == "range_only":
            lines.extend([
                "",
                "PRICE HANDLING:",
                "- You do NOT have live prices. Never state exact prices.",
                "- Use rough positioning: 'budget-friendly', 'mid-range', 'usually a bit higher'.",
                "- If the visitor asks about price, give relative positioning between options.",
                "- Example: 'Max is usually the most budget-friendly, Brands For Less varies "
                "depending on the brand, and Zara is usually a bit higher.'",
            ])

        # Continuity context
        if continuity_res.continuity_type == "refinement" and anchor.is_strong:
            lines.extend([
                "",
                "CONTINUITY:",
                f"- This is a refinement of the active thread: {anchor.domain}/{anchor.topic}.",
                f"- Refinement dimensions: {continuity_res.refinement_dimensions}.",
                "- Build on what you said before. Do NOT restart the conversation.",
                "- Reference the previous shortlist if relevant.",
            ])

        # Example output (for guidance)
        lines.extend([
            "",
            "EXAMPLE OUTPUT (for guidance only — adapt to actual context):",
            "\"For your 5-year-old son, Max and Brands For Less are the best places to start. "
            "Max is usually the easiest budget-friendly option for kidswear, and Brands For Less "
            "is good if you want discounted branded jackets. Zara is worth checking if you want "
            "something a bit more stylish. If you want, I can narrow it further for under 100 SAR.\"",
        ])

        return "\n".join(lines)

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

            # Include price expectation for entity context
            price_exp = e.get("price_expectation", {})
            if isinstance(price_exp, dict) and price_exp.get("rough_price_positioning"):
                lines.append(f"    Price: {price_exp['rough_price_positioning']}")

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
        anchor = state.continuity_anchor
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

        if anchor.is_strong:
            lines.append(f"  Thread: {anchor.domain}/{anchor.topic}")
            if anchor.subtopic:
                lines.append(f"  Narrowing: {anchor.subtopic}")
            if anchor.audience:
                lines.append(f"  Audience bias: {anchor.audience}")
            if anchor.last_successful_shortlist:
                lines.append(
                    f"  Last shortlist: {', '.join(anchor.last_successful_shortlist[:5])}"
                )

        # Continuity resolution context
        cr = state.continuity_resolution
        if cr.continuity_type != "fresh_request":
            lines.append(f"  Continuity: {cr.continuity_type} ({cr.reason})")
            if cr.refinement_dimensions:
                lines.append(f"  Refining: {', '.join(cr.refinement_dimensions)}")

        msg_kind = state.intent.message_kind
        if msg_kind == "correction":
            lines.append("  ⚠ The visitor is CORRECTING a previous answer — acknowledge and adjust.")
        elif msg_kind == "refinement":
            lines.append("  The visitor is refining their request — build on your previous answer.")
            if state.intent.detected_refinement_cues:
                lines.append(
                    f"  Refinement cues: {', '.join(state.intent.detected_refinement_cues)}"
                )
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
