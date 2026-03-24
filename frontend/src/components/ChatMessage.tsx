import { Fragment } from "react";
import { User, Bot, ExternalLink } from "lucide-react";
import FeedbackControls from "./FeedbackControls";
import type { ChatMessage as ChatMessageType, MessageFeedback } from "../types/chat";

interface ChatMessageProps {
  message: ChatMessageType;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onFeedback: (id: string, update: Partial<MessageFeedback>) => void;
  isStreaming?: boolean;
}

function formatContent(text: string) {
  return text.split("\n").map((line, i) => {
    const parts = line.split(/(\*\*[^*]+\*\*)/g);
    return (
      <Fragment key={i}>
        {i > 0 && <br />}
        {parts.map((part, j) => {
          if (part.startsWith("**") && part.endsWith("**")) {
            return (
              <strong key={j} className="font-semibold">
                {part.slice(2, -2)}
              </strong>
            );
          }
          return <span key={j}>{part}</span>;
        })}
      </Fragment>
    );
  });
}

export default function ChatMessage({
  message,
  isSelected,
  onSelect,
  onFeedback,
}: ChatMessageProps) {
  const streaming = message.isStreaming ?? false;
  const isUser = message.role === "user";

  return (
    <div
      className={`animate-message-in flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}
    >
      <div
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
          isUser ? "bg-blue-600" : "bg-gray-100"
        }`}
      >
        {isUser ? (
          <User size={14} className="text-white" />
        ) : (
          <Bot size={14} className="text-gray-500" />
        )}
      </div>

      <div
        className={`max-w-[80%] space-y-2 ${isUser ? "items-end text-right" : ""}`}
      >
        <div
          onClick={() => !isUser && message.debug && onSelect(message.id)}
          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
            isUser
              ? "bg-blue-600 text-white"
              : `bg-white text-gray-800 shadow-sm ring-1 ring-gray-100 ${
                  message.debug ? "cursor-pointer" : ""
                } ${isSelected ? "ring-2 ring-blue-400" : ""}`
          }`}
        >
          <div className="whitespace-pre-wrap">
            {formatContent(message.content)}
            {streaming && (
              <span className="streaming-cursor ml-px inline-block" aria-hidden="true" />
            )}
          </div>

          {message.sources && message.sources.length > 0 && (
            <div className="mt-3 border-t border-gray-100 pt-2">
              <div className="flex flex-wrap gap-1.5">
                {message.sources.map((src, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-500"
                  >
                    <ExternalLink size={9} />
                    {src.source}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {!isUser && message.feedback && (
          <FeedbackControls
            messageId={message.id}
            feedback={message.feedback}
            onFeedback={onFeedback}
          />
        )}
      </div>
    </div>
  );
}
