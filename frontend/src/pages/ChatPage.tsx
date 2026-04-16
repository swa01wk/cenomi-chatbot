import { useRef, useEffect } from "react";
import ChatMessage from "../components/ChatMessage";
import ChatInput from "../components/ChatInput";
import SuggestedChips from "../components/SuggestedChips";
import { SUGGESTED_QUERIES, MALLS } from "../lib/constants";
import type {
  ChatMessage as ChatMessageType,
  MessageFeedback,
} from "../types/chat";

interface ChatPageProps {
  messages: ChatMessageType[];
  isLoading: boolean;
  selectedTurnId: string | null;
  mallConfirmed: boolean;
  onSend: (text: string) => void;
  onSelectTurn: (id: string) => void;
  onFeedback: (id: string, update: Partial<MessageFeedback>) => void;
  onConfirmMall: (mallId: string) => void;
}

export default function ChatPage({
  messages,
  isLoading,
  selectedTurnId,
  mallConfirmed,
  onSend,
  onSelectTurn,
  onFeedback,
  onConfirmMall,
}: ChatPageProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  const lastAssistantMsg = [...messages]
    .reverse()
    .find((m) => m.role === "assistant");
  const showInlineSuggestions =
    lastAssistantMsg?.suggestions && lastAssistantMsg.suggestions.length > 0;

  const isEmpty = messages.length === 0;

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-gray-50">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6">
          {isEmpty && !mallConfirmed ? (
            <MallPickerScreen onConfirm={onConfirmMall} />
          ) : isEmpty ? (
            <WelcomeScreen onChipSelect={onSend} />
          ) : (
            <div className="space-y-4">
              {messages.map((msg) => (
                <ChatMessage
                  key={msg.id}
                  message={msg}
                  isSelected={msg.id === selectedTurnId}
                  onSelect={onSelectTurn}
                  onFeedback={onFeedback}
                  onSend={onSend}
                />
              ))}

              {isLoading && !messages.some((m) => m.isStreaming) && (
                <TypingIndicator />
              )}

              {!isLoading && showInlineSuggestions && (
                <div className="pl-10">
                  <SuggestedChips
                    chips={lastAssistantMsg!.suggestions!}
                    onSelect={onSend}
                    disabled={isLoading}
                  />
                </div>
              )}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      {/* Input */}
      <ChatInput onSend={onSend} disabled={isLoading} />
    </div>
  );
}

function MallPickerScreen({ onConfirm }: { onConfirm: (mallId: string) => void }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center">
      <div className="mb-2 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-600 text-2xl font-bold text-white shadow-lg shadow-blue-200">
        C
      </div>
      <h1 className="mt-4 text-xl font-semibold text-gray-800">
        Cenomi Mall Concierge
      </h1>
      <p className="mt-1.5 max-w-sm text-center text-sm text-gray-500">
        Which Cenomi mall are you visiting today?
      </p>
      <div className="mt-8 flex flex-col gap-2.5 w-full max-w-xs">
        {MALLS.map((mall) => (
          <button
            key={mall.id}
            onClick={() => onConfirm(mall.id)}
            className="flex flex-col items-start rounded-xl border border-gray-200 bg-white px-5 py-3.5 text-left shadow-sm transition-all hover:border-blue-400 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <span className="text-sm font-semibold text-gray-800">{mall.label}</span>
            <span className="text-xs text-gray-400 mt-0.5">{mall.city}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function WelcomeScreen({ onChipSelect }: { onChipSelect: (q: string) => void }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center">
      <div className="mb-2 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-600 text-2xl font-bold text-white shadow-lg shadow-blue-200">
        C
      </div>
      <h1 className="mt-4 text-xl font-semibold text-gray-800">
        Cenomi Mall Concierge
      </h1>
      <p className="mt-1.5 max-w-sm text-center text-sm text-gray-500">
        Ask me anything about stores, dining, entertainment, or services.
        I'll help you plan your visit.
      </p>
      <div className="mt-8">
        <SuggestedChips chips={SUGGESTED_QUERIES} onSelect={onChipSelect} />
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gray-100">
        <div className="flex items-center gap-0.5">
          <span className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-gray-400" />
          <span className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-gray-400" />
          <span className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-gray-400" />
        </div>
      </div>
      <div className="rounded-2xl bg-white px-4 py-3 text-sm text-gray-400 shadow-sm ring-1 ring-gray-100">
        Thinking...
      </div>
    </div>
  );
}
