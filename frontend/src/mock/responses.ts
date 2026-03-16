import type { ChatResponse } from "../types/api";
import type { DebugPayload } from "../types/chat";

function mockDebug(overrides: Partial<DebugPayload> = {}): DebugPayload {
  return {
    turn_id: `turn-${Date.now()}`,
    session_id: "mock-session-001",
    mall_id: "cenomi_mall_01",
    intent_domain: "general",
    intent_sub: "greeting",
    intent_confidence: 0.92,
    message_kind: "fresh_request",
    scene_summary: {
      visit_type: "casual",
      companions: [],
      occasion: "",
      budget: "",
      current_need: "",
    },
    selected_playbook: "pb-001-general-greeting",
    playbook_confidence: 0.88,
    matched_playbooks: ["pb-001-general-greeting"],
    chosen_strategy: "fallback_guided_response",
    response_shape: "conversational",
    selected_topic_blocks: ["mall_overview"],
    selected_entities: [],
    selected_semantic_signals: ["greeting", "exploration"],
    ranking_notes: [
      "No specific intent detected — using general greeting playbook",
    ],
    retrieval_needed: false,
    retrieval_reason: "General greeting does not require retrieval",
    retrieval_targets: [],
    retrieval_results_count: 0,
    latency_by_node: {
      interpret_turn: 45,
      update_scene_memory: 12,
      resolve_playbooks: 18,
      compose_context: 22,
      decide_retrieval: 8,
      choose_strategy: 15,
      generate_response: 320,
      emit_debug_payload: 3,
    },
    total_latency_ms: 443,
    node_count: 8,
    warnings: [],
    node_trace: [
      { node: "interpret_turn", latency_ms: 45, summary: "Detected greeting" },
      { node: "update_scene_memory", latency_ms: 12, summary: "Init scene" },
      { node: "resolve_playbooks", latency_ms: 18, summary: "Matched pb-001" },
      {
        node: "compose_context",
        latency_ms: 22,
        summary: "Selected mall_overview",
      },
      {
        node: "decide_retrieval",
        latency_ms: 8,
        summary: "Skipped — no retrieval needed",
      },
      {
        node: "choose_strategy",
        latency_ms: 15,
        summary: "Fallback guided response",
      },
      {
        node: "generate_response",
        latency_ms: 320,
        summary: "Generated greeting",
      },
      {
        node: "emit_debug_payload",
        latency_ms: 3,
        summary: "Debug payload emitted",
      },
    ],
    continuity_anchor: "new_session",
    thread_preservation_decision: "start_fresh",
    expected_playbook_candidates: ["pb-001-general-greeting"],
    shortlisted_entities: [],
    response_contract: "greeting_with_exploration_prompt",
    price_expectation_mode: false,
    narrowing_followup_opportunity: "",
    ...overrides,
  };
}

const MOCK_RESPONSES: Record<string, ChatResponse> = {
  default: {
    session_id: "mock-session-001",
    message:
      "Welcome to Cenomi Mall! I'd love to help you explore. We have over 200 stores, 30+ dining spots, a cinema, and entertainment for all ages. What are you in the mood for today?",
    sources: [],
    suggestions: [
      "Show me dining options",
      "Where is the cinema?",
      "What stores sell electronics?",
    ],
    debug: mockDebug(),
  },
  dining: {
    session_id: "mock-session-001",
    message:
      "Great choice! Here are some top dining picks:\n\n**The Cheesecake Factory** — Second Floor, South Wing. Famous for their extensive menu and amazing cheesecakes. Perfect for groups.\n\n**Shake Shack** — Ground Floor, near Gate 3. Quick burgers, fries, and shakes.\n\n**PF Chang's** — First Floor, East Wing. Asian-inspired dishes in a stylish setting.\n\nAll three have plenty of seating right now — weekday lunch is usually relaxed.\n\nWould you like more details about any of these, or should I suggest based on a specific craving?",
    sources: [
      { source: "tenant_directory", content: "dining options catalog" },
      { source: "mall_map", content: "floor plans" },
    ],
    suggestions: [
      "Tell me more about Cheesecake Factory",
      "Quick lunch options",
      "Restaurants open late",
    ],
    debug: mockDebug({
      intent_domain: "dining",
      intent_sub: "dining_exploration",
      intent_confidence: 0.95,
      message_kind: "fresh_request",
      scene_summary: {
        visit_type: "casual",
        companions: [],
        occasion: "",
        budget: "",
        current_need: "dining",
        active_topic: "dining",
      },
      selected_playbook: "pb-010-dining-recommendation",
      playbook_confidence: 0.94,
      matched_playbooks: [
        "pb-010-dining-recommendation",
        "pb-011-quick-food",
      ],
      chosen_strategy: "shortlist_recommendation",
      response_shape: "numbered_list_with_details",
      selected_topic_blocks: ["dining_directory", "dining_highlights"],
      selected_entities: [
        {
          name: "The Cheesecake Factory",
          type: "restaurant",
          floor: "2nd",
          wing: "South",
        },
        {
          name: "Shake Shack",
          type: "restaurant",
          floor: "Ground",
          wing: "Gate 3",
        },
        {
          name: "PF Chang's",
          type: "restaurant",
          floor: "1st",
          wing: "East",
        },
      ],
      selected_semantic_signals: ["dining", "exploration", "variety"],
      ranking_notes: [
        "Ranked by popularity and variety",
        "Mixed price points for general recommendation",
      ],
      retrieval_needed: true,
      retrieval_reason: "Dining query requires tenant directory lookup",
      retrieval_targets: ["dining_directory"],
      retrieval_results_count: 8,
      total_latency_ms: 620,
      latency_by_node: {
        interpret_turn: 52,
        update_scene_memory: 15,
        resolve_playbooks: 22,
        compose_context: 35,
        decide_retrieval: 12,
        fetch_exact_facts: 145,
        choose_strategy: 18,
        generate_response: 315,
        emit_debug_payload: 6,
      },
      node_count: 9,
      continuity_anchor: "dining_exploration",
      thread_preservation_decision: "continue_thread",
      expected_playbook_candidates: [
        "pb-010-dining-recommendation",
        "pb-011-quick-food",
        "pb-012-cafe-recommendation",
      ],
      shortlisted_entities: [
        { name: "The Cheesecake Factory", reason: "Top-rated full-service restaurant, broad menu appeal" },
        { name: "Shake Shack", reason: "Popular quick-service, high visitor rating" },
        { name: "PF Chang's", reason: "Variety play — Asian cuisine balances the shortlist" },
      ],
      response_contract: "shortlist_3_with_location_and_followup",
      price_expectation_mode: false,
      narrowing_followup_opportunity: "cuisine_type_or_budget",
    }),
  },
  gift: {
    session_id: "mock-session-001",
    message:
      "Looking for a gift for your girlfriend? Here's my curated pick:\n\n**Luxury Touch:** Swarovski (1st Floor) — Crystal jewelry starting from SAR 250.\n\n**Trendy & Fun:** Bath & Body Works (Ground Floor) — Gift sets from SAR 80.\n\n**Personal Touch:** Pandora (1st Floor) — Charm bracelets she can keep building.\n\nWant me to narrow it down by budget or her style preferences?",
    sources: [{ source: "tenant_directory", content: "gift shops" }],
    suggestions: ["Under 200 SAR", "Something unique", "Where is Swarovski?"],
    debug: mockDebug({
      intent_domain: "shopping",
      intent_sub: "gift_recommendation",
      intent_confidence: 0.91,
      message_kind: "fresh_request",
      scene_summary: {
        visit_type: "shopping",
        companions: ["girlfriend"],
        occasion: "gift",
        budget: "",
        current_need: "gift for girlfriend",
        active_topic: "gift_shopping",
      },
      selected_playbook: "pb-020-gift-formula",
      playbook_confidence: 0.9,
      matched_playbooks: ["pb-020-gift-formula", "pb-021-shopping-general"],
      chosen_strategy: "gift_formula",
      response_shape: "curated_list_with_prices",
      selected_topic_blocks: ["retail_directory", "gift_guides"],
      selected_entities: [
        {
          name: "Swarovski",
          type: "store",
          floor: "1st",
          category: "jewelry",
        },
        {
          name: "Bath & Body Works",
          type: "store",
          floor: "Ground",
          category: "beauty",
        },
        {
          name: "Pandora",
          type: "store",
          floor: "1st",
          category: "jewelry",
        },
      ],
      selected_semantic_signals: ["gift", "girlfriend", "recommendation"],
      retrieval_needed: true,
      retrieval_reason: "Gift query requires store catalog lookup",
      retrieval_targets: ["retail_directory", "gift_guides"],
      retrieval_results_count: 12,
      total_latency_ms: 580,
      continuity_anchor: "gift_shopping_thread",
      thread_preservation_decision: "continue_thread",
      expected_playbook_candidates: [
        "pb-020-gift-formula",
        "pb-021-shopping-general",
      ],
      shortlisted_entities: [
        { name: "Swarovski", reason: "Luxury tier — high gifting intent match" },
        { name: "Bath & Body Works", reason: "Mid-range fallback — safe crowd-pleaser" },
        { name: "Pandora", reason: "Personal/sentimental angle — repeatable gifting" },
      ],
      response_contract: "gift_formula_curated_3",
      price_expectation_mode: true,
      narrowing_followup_opportunity: "budget_or_style_preference",
    }),
  },
  movie: {
    session_id: "mock-session-001",
    message:
      "Movie time! Here's a quick plan:\n\n**VOX Cinema** — Third Floor, East Wing. Current showtimes start every 30 minutes.\n\n**Cinnabon** — right next to the cinema entrance, perfect for a quick snack.\n\n**Subway** — 2-minute walk, Ground Floor East for something more filling.\n\nShall I check what's showing right now?",
    sources: [
      { source: "entertainment_directory" },
      { source: "dining_near_cinema" },
    ],
    suggestions: [
      "What's showing today?",
      "Family-friendly movies",
      "Dinner after the movie",
    ],
    debug: mockDebug({
      intent_domain: "entertainment",
      intent_sub: "movie_planning",
      intent_confidence: 0.93,
      message_kind: "fresh_request",
      chosen_strategy: "movie_plus_food",
      selected_playbook: "pb-030-movie-plus-food",
      scene_summary: {
        visit_type: "entertainment",
        current_need: "quick food before movie",
        active_topic: "movie_planning",
      },
      retrieval_needed: true,
      retrieval_reason: "Cinema + nearby food query",
      retrieval_results_count: 5,
      total_latency_ms: 510,
      continuity_anchor: "movie_planning_thread",
      thread_preservation_decision: "start_fresh",
      expected_playbook_candidates: [
        "pb-030-movie-plus-food",
        "pb-031-entertainment-general",
      ],
      shortlisted_entities: [
        { name: "VOX Cinema", reason: "Only cinema in mall — direct match" },
        { name: "Cinnabon", reason: "Proximity to cinema entrance, quick service" },
        { name: "Subway", reason: "Fast casual fallback near cinema wing" },
      ],
      response_contract: "plan_with_shortlist_and_followup",
      price_expectation_mode: false,
      narrowing_followup_opportunity: "showtime_or_genre",
    }),
  },
  family: {
    session_id: "mock-session-001",
    message:
      "A family day out — here's a plan everyone will love:\n\n**Kidzania** — 2nd Floor. Immersive role-play for kids aged 4–14.\n\n**The Cheesecake Factory** — Great kids' menu and plenty of space for family lunch.\n\n**Arcade Zone** — 3rd Floor. Games for all ages, or catch a family movie at VOX Cinema.\n\nEach spot is within a short walk of the next — easy circuit for families.\n\nWant me to adjust this for specific ages or interests?",
    sources: [
      { source: "entertainment_directory" },
      { source: "family_guide" },
    ],
    suggestions: [
      "Activities for toddlers",
      "Budget-friendly family plan",
      "Where can kids play?",
    ],
    debug: mockDebug({
      intent_domain: "entertainment",
      intent_sub: "family_outing",
      intent_confidence: 0.96,
      chosen_strategy: "family_plan",
      selected_playbook: "pb-040-family-plan",
      scene_summary: {
        visit_type: "family",
        companions: ["family", "kids"],
        current_need: "family outing ideas",
        active_topic: "family_entertainment",
      },
      total_latency_ms: 650,
      warnings: ["Kids' age not specified — using general family plan"],
      continuity_anchor: "family_outing_thread",
      thread_preservation_decision: "start_fresh",
      expected_playbook_candidates: [
        "pb-040-family-plan",
        "pb-041-kids-activities",
      ],
      shortlisted_entities: [
        { name: "Kidzania", reason: "Top family attraction — wide age range" },
        { name: "The Cheesecake Factory", reason: "Family-friendly dining with kids' menu" },
        { name: "Arcade Zone", reason: "Afternoon entertainment — complements Kidzania" },
      ],
      response_contract: "day_plan_with_shortlist",
      price_expectation_mode: false,
      narrowing_followup_opportunity: "age_range_or_budget",
    }),
  },
  coffee: {
    session_id: "mock-session-001",
    message:
      "Craving coffee and dessert? You're in luck:\n\n**% Arabica** — Ground Floor, Main Atrium. Minimalist vibes, exceptional pour-over.\n\n**Magnolia Bakery** — 1st Floor, West Wing. Famous banana pudding + great lattes.\n\n**Le Pain Quotidien** — Ground Floor, South. Organic pastries and specialty coffee.\n\nAll three have comfortable seating if you want to linger.\n\nWhich sounds good?",
    sources: [{ source: "dining_directory", content: "cafe options" }],
    suggestions: [
      "Best dessert in the mall",
      "Quiet spots to work",
      "Tea options instead",
    ],
    debug: mockDebug({
      intent_domain: "dining",
      intent_sub: "coffee_dessert",
      intent_confidence: 0.94,
      chosen_strategy: "shortlist_recommendation",
      selected_playbook: "pb-012-cafe-recommendation",
      retrieval_needed: true,
      retrieval_reason: "Cafe-specific directory lookup",
      retrieval_results_count: 6,
      total_latency_ms: 495,
      continuity_anchor: "coffee_dessert_thread",
      thread_preservation_decision: "start_fresh",
      expected_playbook_candidates: [
        "pb-012-cafe-recommendation",
        "pb-010-dining-recommendation",
      ],
      shortlisted_entities: [
        { name: "% Arabica", reason: "Specialty coffee leader — high visitor rating" },
        { name: "Magnolia Bakery", reason: "Best dessert pairing with coffee" },
        { name: "Le Pain Quotidien", reason: "Organic angle — appeals to health-conscious" },
      ],
      response_contract: "shortlist_3_with_vibe_and_followup",
      price_expectation_mode: false,
      narrowing_followup_opportunity: "seating_preference_or_dessert_type",
    }),
  },
};

const RESPONSE_KEYS = Object.keys(MOCK_RESPONSES);

export function getMockResponse(message: string): ChatResponse {
  const lower = message.toLowerCase();

  if (
    lower.includes("eat") ||
    lower.includes("food") ||
    lower.includes("dining") ||
    lower.includes("lunch") ||
    lower.includes("dinner") ||
    lower.includes("restaurant")
  ) {
    return MOCK_RESPONSES.dining;
  }
  if (
    lower.includes("gift") ||
    lower.includes("girlfriend") ||
    lower.includes("boyfriend")
  ) {
    return MOCK_RESPONSES.gift;
  }
  if (
    lower.includes("movie") ||
    lower.includes("cinema") ||
    lower.includes("quick before")
  ) {
    return MOCK_RESPONSES.movie;
  }
  if (
    lower.includes("family") ||
    lower.includes("kids") ||
    lower.includes("outing")
  ) {
    return MOCK_RESPONSES.family;
  }
  if (
    lower.includes("coffee") ||
    lower.includes("dessert") ||
    lower.includes("cafe") ||
    lower.includes("tea")
  ) {
    return MOCK_RESPONSES.coffee;
  }

  const key = RESPONSE_KEYS[Math.floor(Math.random() * RESPONSE_KEYS.length)];
  return MOCK_RESPONSES[key];
}
