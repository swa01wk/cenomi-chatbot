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
import type { DebugPayload } from "../types/chat";

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

// ─── SSE streaming types ─────────────────────────────────────────────────────

export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "done"; sessionId: string; sessionState: ChatResponse["session_state"]; debug: DebugPayload | null; suggestions: string[] }
  | { type: "error"; detail: string };

/**
 * Stream a chat response token-by-token from POST /api/chat/stream.
 *
 * Usage:
 *   for await (const event of streamMessage(req)) {
 *     if (event.type === 'token') appendText(event.text);
 *     if (event.type === 'done')  finalise(event.sessionId, event.debug);
 *   }
 */
export async function* streamMessage(req: ChatRequest): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${BASE_URL}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!res.ok || !res.body) {
    throw new Error(`Stream error: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE lines are terminated by \n; events separated by \n\n
    const lines = buffer.split("\n");
    // Keep the last incomplete line in the buffer
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        try {
          const data = JSON.parse(dataStr);
          if (currentEvent === "token") {
            yield { type: "token", text: data.text as string };
          } else if (currentEvent === "done") {
            yield {
              type: "done",
              sessionId: data.session_id as string,
              sessionState: data.session_state ?? null,
              debug: (data.debug as DebugPayload) ?? null,
              suggestions: (data.suggestions as string[]) ?? [],
            };
          } else if (currentEvent === "error") {
            yield { type: "error", detail: data.detail as string };
          }
        } catch {
          // Ignore malformed data lines
        }
        currentEvent = "";
      }
      // Blank lines are event separators — no action needed
    }
  }
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
