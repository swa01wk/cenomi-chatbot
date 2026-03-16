export const APP_NAME = "Cenomi Mall Concierge";
export const DEFAULT_TENANT_ID = "cenomi_mall_01";
export const DEFAULT_MALL_ID = "cenomi_mall_01";
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
  "wrong_thread",
  "lost_context",
  "too_generic",
  "too_much_detail",
  "wrong_shortlist",
  "wrong_playbook",
  "poor_follow_up",
  "sounds_like_directory",
  "not_enough_options",
  "too_many_options",
] as const;

export const FEEDBACK_REASON_LABELS: Record<string, string> = {
  wrong_thread: "Wrong thread",
  lost_context: "Lost context",
  too_generic: "Too generic",
  too_much_detail: "Too much detail",
  wrong_shortlist: "Wrong shortlist",
  wrong_playbook: "Wrong playbook",
  poor_follow_up: "Poor follow-up",
  sounds_like_directory: "Sounds like directory",
  not_enough_options: "Not enough options",
  too_many_options: "Too many options",
};

export const TENANTS = [
  { id: "cenomi_mall_01", label: "Cenomi Mall" },
] as const;

export const MALLS = [
  { id: "cenomi_mall_01", label: "Cenomi Mall — Main" },
] as const;
