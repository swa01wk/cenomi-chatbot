import type {
  ChatRequest,
  ChatResponse,
  FeedbackRequest,
  FeedbackResponse,
  SessionFeedbackResponse,
  SessionResetRequest,
  SessionResetResponse,
  TenantSummaryResponse,
  PlaybookPerformanceResponse,
} from "../types/api";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }

  return res.json();
}

export async function sendMessage(req: ChatRequest): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function submitFeedback(
  req: FeedbackRequest,
): Promise<FeedbackResponse> {
  return request<FeedbackResponse>("/api/feedback", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function getSessionFeedback(
  sessionId: string,
): Promise<SessionFeedbackResponse> {
  return request<SessionFeedbackResponse>(
    `/api/feedback/session/${encodeURIComponent(sessionId)}`,
  );
}

export async function getTenantSummary(
  tenantId = "al_nakheel_plaza_28",
  mallId = "al_nakheel_plaza_28",
): Promise<TenantSummaryResponse> {
  return request<TenantSummaryResponse>(
    `/api/feedback/tenant-summary?tenant_id=${encodeURIComponent(tenantId)}&mall_id=${encodeURIComponent(mallId)}`,
  );
}

export async function getPlaybookPerformance(
  tenantId = "al_nakheel_plaza_28",
  mallId = "al_nakheel_plaza_28",
): Promise<PlaybookPerformanceResponse> {
  return request<PlaybookPerformanceResponse>(
    `/api/feedback/playbook-performance?tenant_id=${encodeURIComponent(tenantId)}&mall_id=${encodeURIComponent(mallId)}`,
  );
}

export async function resetSession(
  req: SessionResetRequest,
): Promise<SessionResetResponse> {
  return request<SessionResetResponse>("/api/session/reset", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export async function checkHealth(): Promise<{
  status: string;
  mall_id: string;
}> {
  return request("/api/health");
}
