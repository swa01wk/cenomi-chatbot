import type { DebugPayload } from "./chat";

export interface ChatRequest {
  message: string;
  session_id?: string;
  tenant_id?: string;
  mall_id?: string;
  debug?: boolean;
  context?: Record<string, unknown>;
}

export interface SessionSummary {
  session_id: string;
  turn_count: number;
  active_topic: string;
  companions: string[];
  occasion: string;
  budget: string;
  active_shortlist: string[];
}

export interface ChatResponse {
  session_id: string;
  message: string;
  session_state?: SessionSummary | null;
  sources: Array<{ source: string; content?: string; score?: number }>;
  suggestions: string[];
  debug?: DebugPayload | null;
}

export interface FeedbackRequest {
  session_id: string;
  tenant_id?: string;
  mall_id?: string;
  turn_id?: string;
  message_id?: string;
  user_message?: string;
  assistant_response?: string;
  feedback_type: "thumbs_up" | "thumbs_down";
  feedback_reasons?: string[];
  feedback_text?: string;
  strategy_used?: string;
  playbook_used?: string;
  selected_entities?: Array<Record<string, unknown>>;
  selected_context_blocks?: string[];
}

export interface FeedbackResponse {
  feedback_id: string;
  status: string;
  session_tuning_applied: boolean;
  normalized_signal_count: number;
}

export interface SessionFeedbackResponse {
  session_id: string;
  explicit_events: Array<Record<string, unknown>>;
  implicit_events: Array<Record<string, unknown>>;
  normalized_signals: Array<Record<string, unknown>>;
  session_tuning: Record<string, unknown>;
}

export interface TenantSummaryResponse {
  tenant_id: string;
  mall_id: string;
  total_events: number;
  overall_approval_rate: number;
  buckets: Array<Record<string, unknown>>;
  recommended_adjustments: Array<Record<string, unknown>>;
}

export interface PlaybookPerformanceResponse {
  playbook_buckets: Array<Record<string, unknown>>;
  knowledge_gaps: Array<Record<string, unknown>>;
}

export interface SessionResetRequest {
  session_id: string;
  mall_id?: string;
}

export interface SessionResetResponse {
  session_id: string;
  status: string;
}
