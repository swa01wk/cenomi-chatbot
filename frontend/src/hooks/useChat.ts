import { useState, useCallback, useRef } from "react";
import { sendMessage, submitFeedback, resetSession } from "../api/client";
import { getMockResponse } from "../mock/responses";
import { DEFAULT_TENANT_ID, DEFAULT_MALL_ID } from "../lib/constants";
import type { ChatMessage, DebugPayload, MessageFeedback } from "../types/chat";

const USE_MOCK = false;

function generateId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [debugMode, setDebugMode] = useState(false);
  const [tenantId, setTenantId] = useState(DEFAULT_TENANT_ID);
  const [mallId, setMallId] = useState(DEFAULT_MALL_ID);
  const turnCountRef = useRef(0);

  const send = useCallback(
    async (text: string) => {
      const userMsg: ChatMessage = {
        id: generateId(),
        role: "user",
        content: text,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsLoading(true);

      try {
        let response;

        if (USE_MOCK) {
          await new Promise((r) => setTimeout(r, 600 + Math.random() * 800));
          response = getMockResponse(text);
        } else {
          response = await sendMessage({
            message: text,
            session_id: sessionId ?? undefined,
            tenant_id: tenantId,
            mall_id: mallId,
            debug: debugMode,
          });
        }

        setSessionId(response.session_id);
        turnCountRef.current += 1;

        const assistantMsg: ChatMessage = {
          id: generateId(),
          role: "assistant",
          content: response.message,
          timestamp: Date.now(),
          sources: response.sources?.map((s) => ({
            source: s.source,
            content: s.content,
            score: s.score,
          })),
          suggestions: response.suggestions,
          debug: (response.debug as DebugPayload) ?? null,
          feedback: {
            rating: null,
            tags: [],
            comment: "",
            submitted: false,
          },
        };

        setMessages((prev) => [...prev, assistantMsg]);
        setSelectedTurnId(assistantMsg.id);
        return response;
      } catch {
        const errorMsg: ChatMessage = {
          id: generateId(),
          role: "assistant",
          content: "Sorry, something went wrong. Please try again.",
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setIsLoading(false);
      }
    },
    [sessionId, tenantId, mallId, debugMode],
  );

  const handleFeedback = useCallback(
    async (messageId: string, feedback: Partial<MessageFeedback>) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? { ...m, feedback: { ...m.feedback!, ...feedback } }
            : m,
        ),
      );

      if (feedback.submitted && sessionId) {
        const msg = messages.find((m) => m.id === messageId);
        if (msg?.feedback) {
          const mergedTags = feedback.tags || msg.feedback.tags;
          const mergedComment = feedback.comment || msg.feedback.comment;

          const userMsg = messages
            .slice(0, messages.indexOf(msg))
            .reverse()
            .find((m) => m.role === "user");

          try {
            await submitFeedback({
              session_id: sessionId,
              tenant_id: tenantId,
              mall_id: mallId,
              turn_id: msg.debug?.turn_id ?? "",
              message_id: messageId,
              user_message: userMsg?.content ?? "",
              assistant_response: msg.content,
              feedback_type: feedback.rating === "up" ? "thumbs_up" : "thumbs_down",
              feedback_reasons: mergedTags,
              feedback_text: mergedComment,
              strategy_used: msg.debug?.chosen_strategy ?? "",
              playbook_used: msg.debug?.selected_playbook ?? "",
              selected_entities: msg.debug?.selected_entities ?? [],
              selected_context_blocks: msg.debug?.selected_topic_blocks ?? [],
            });
          } catch {
            /* feedback is non-critical */
          }
        }
      }
    },
    [sessionId, tenantId, mallId, messages],
  );

  const reset = useCallback(async () => {
    if (sessionId) {
      try {
        await resetSession({ session_id: sessionId, mall_id: mallId });
      } catch {
        /* continue with local reset */
      }
    }
    setMessages([]);
    setSessionId(null);
    setSelectedTurnId(null);
    turnCountRef.current = 0;
  }, [sessionId, mallId]);

  const exportConversation = useCallback(() => {
    const data = {
      session_id: sessionId,
      tenant_id: tenantId,
      mall_id: mallId,
      exported_at: new Date().toISOString(),
      messages: messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
        sources: m.sources,
        debug: m.debug,
        feedback: m.feedback,
      })),
    };

    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `concierge-${sessionId || "draft"}-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [sessionId, tenantId, mallId, messages]);

  const selectedTurn = messages.find((m) => m.id === selectedTurnId) ?? null;

  return {
    messages,
    sessionId,
    isLoading,
    debugMode,
    tenantId,
    mallId,
    selectedTurnId,
    selectedTurn,
    send,
    reset,
    handleFeedback,
    exportConversation,
    setDebugMode,
    setTenantId,
    setMallId,
    setSelectedTurnId,
  };
}
