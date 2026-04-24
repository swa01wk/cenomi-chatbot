export const APP_NAME = "Cenomi Mall Concierge";
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

export const SUGGESTED_QUERIES_AR = [
  "أين يمكنني تناول الطعام؟",
  "هدية لصديقتي",
  "شيء سريع قبل الفيلم",
  "أفكار للخروج العائلي",
  "ماذا يمكنني أن أفعل هنا؟",
  "قهوة وحلويات؟",
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

export const FEEDBACK_REASON_LABELS_AR: Record<string, string> = {
  not_relevant: "غير ذات صلة",
  too_generic: "عامة جداً",
  too_long: "طويلة جداً",
  wrong_assumption: "افتراض خاطئ",
  wanted_exact_details: "أردت تفاصيل دقيقة",
  wrong_store_suggestion: "اقتراح متجر خاطئ",
  wrong_dining_suggestion: "اقتراح مطعم خاطئ",
  tone_felt_robotic: "النبرة تبدو آلية",
  asked_too_many_questions: "طرح أسئلة كثيرة جداً",
  should_recommend_something_else: "يجب اقتراح شيء آخر",
};

/** UI copy strings for Arabic mode */
export const UI_COPY_AR = {
  welcomeTitle: "كونسيرج سينومي몰",
  welcomeSubtitle: "اسألني عن المتاجر والمطاعم والترفيه أو أي خدمة. سأساعدك في التخطيط لزيارتك.",
  mallPickerTitle: "كونسيرج مول سينومي",
  mallPickerSubtitle: "أي مول سينومي ستزور اليوم؟",
  inputPlaceholder: "اسأل عن المتاجر والمطاعم والترفيه...",
  thinking: "جاري التفكير...",
  tapForDirections: "انقر على البطاقة للحصول على اتجاهات على خريطة المول",
  resetSession: "إعادة تعيين",
  exportConversation: "تصدير",
  debugOn: "تشغيل التصحيح",
  debug: "تصحيح",
  activeMall: "المول النشط",
};

/** Returns the correct set of suggested queries for the given language. */
export function getSuggestedQueries(language: "en" | "ar"): string[] {
  return language === "ar" ? [...SUGGESTED_QUERIES_AR] : [...SUGGESTED_QUERIES];
}

/** Returns the correct feedback reason labels for the given language. */
export function getFeedbackReasonLabels(language: "en" | "ar"): Record<string, string> {
  return language === "ar" ? FEEDBACK_REASON_LABELS_AR : FEEDBACK_REASON_LABELS;
}

export const MALLS = [
  { id: "al_nakheel_plaza_28", label: "Al Nakheel Plaza", city: "Buraidah" },
  { id: "al_nakheel_plaza_13", label: "Mall of Arabia", city: "Jeddah" },
  { id: "al_nakheel_plaza_27", label: "Al Nakheel Mall", city: "Riyadh" },
  { id: "al_nakheel_plaza_10", label: "The View Mall", city: "Riyadh" },
  { id: "al_nakheel_plaza_1", label: "Al Ahsa Mall", city: "Al Ahsa" },
] as const;

export type MallId = (typeof MALLS)[number]["id"];

/** @deprecated Kept for API compatibility — always equals the active mall ID. */
export const TENANTS = MALLS;
