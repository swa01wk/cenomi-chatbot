export const APP_NAME = "Al Nakheel Plaza Concierge";
export const DEFAULT_TENANT_ID = "al_nakheel_plaza_28";
export const DEFAULT_MALL_ID = "al_nakheel_plaza_28";
export const MAX_MESSAGE_LENGTH = 4000;

export const SUGGESTED_QUERIES = [
  "Where should I eat?",
  "Gift for my girlfriend",
  "Anything quick before movie?",
  "Family outing ideas",
  "What can I do here?",
  "Coffee and dessert?",
];

export const FEEDBACK_REASONS = [
  "not_relevant",
  "too_generic",
  "too_long",
  "wrong_assumption",
  "wanted_exact_details",
  "wrong_store_suggestion",
  "wrong_dining_suggestion",
  "tone_felt_robotic",
  "asked_too_many_questions",
  "should_recommend_something_else",
] as const;

export const FEEDBACK_REASON_LABELS: Record<string, string> = {
  not_relevant: "Not relevant",
  too_generic: "Too generic",
  too_long: "Too long",
  wrong_assumption: "Wrong assumption",
  wanted_exact_details: "Wanted exact details",
  wrong_store_suggestion: "Wrong store suggestion",
  wrong_dining_suggestion: "Wrong dining suggestion",
  tone_felt_robotic: "Tone felt robotic",
  asked_too_many_questions: "Asked too many questions",
  should_recommend_something_else: "Should recommend something else",
};

export const TENANTS = [
  { id: "al_nakheel_plaza_28", label: "Al Nakheel Plaza" },
] as const;

export const MALLS = [
  { id: "al_nakheel_plaza_28", label: "Al Nakheel Plaza — Buraidah" },
] as const;
