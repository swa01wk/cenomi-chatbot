export interface Source {
  source: string;
  content?: string;
  score?: number;
}

export interface TenantCard {
  name: string;
  category?: string;
  image?: string;
  floor?: string;
  zone?: string;
  unit_number?: string;
  map_url?: string;
}

export interface MessageFeedback {
  rating: "up" | "down" | null;
  tags: string[];
  comment: string;
  submitted: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  sources?: Source[];
  suggestions?: string[];
  debug?: DebugPayload | null;
  feedback?: MessageFeedback;
  tenantCards?: TenantCard[];
  /** True while tokens are still arriving from the SSE stream. */
  isStreaming?: boolean;
}

export interface DebugPayload {
  turn_id: string;
  session_id: string;
  mall_id: string;

  intent_domain: string;
  intent_sub: string;
  intent_confidence: number;
  message_kind: string;

  scene_summary: Record<string, unknown>;

  selected_playbook: string;
  playbook_confidence: number;
  matched_playbooks: string[];

  chosen_strategy: string;
  response_shape: string;

  selected_topic_blocks: string[];
  selected_entities: Array<Record<string, unknown>>;
  selected_semantic_signals: string[];
  ranking_notes: string[];

  retrieval_needed: boolean;
  retrieval_reason: string;
  retrieval_targets: string[];
  retrieval_results_count: number;

  latency_by_node: Record<string, number>;
  total_latency_ms: number;
  node_count: number;

  warnings: string[];
  node_trace: Array<Record<string, unknown>>;
}

export interface Session {
  session_id: string;
  tenant_id: string;
  mall_id: string;
  messages: ChatMessage[];
}
