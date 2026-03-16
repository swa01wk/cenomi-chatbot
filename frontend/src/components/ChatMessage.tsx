import { Fragment, type ReactNode } from "react";
import { User, Bot, ExternalLink, Lightbulb, MessageCircle } from "lucide-react";
import FeedbackControls from "./FeedbackControls";
import type { ChatMessage as ChatMessageType, MessageFeedback } from "../types/chat";

interface ChatMessageProps {
  message: ChatMessageType;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onFeedback: (id: string, update: Partial<MessageFeedback>) => void;
}

interface ParsedBlock {
  type: "text" | "shortlist-item" | "tip" | "follow-up";
  content: string;
}

function parseBlocks(text: string): ParsedBlock[] {
  const lines = text.split("\n");
  const blocks: ParsedBlock[] = [];
  let buffer: string[] = [];
  let currentType: ParsedBlock["type"] = "text";

  const flush = () => {
    const joined = buffer.join("\n").trim();
    if (joined) blocks.push({ type: currentType, content: joined });
    buffer = [];
    currentType = "text";
  };

  for (const line of lines) {
    const trimmed = line.trim();

    const isShortlistItem =
      /^\*\*[^*]+\*\*/.test(trimmed) &&
      (trimmed.includes("—") || trimmed.includes("–") || trimmed.includes("-"));
    const isTip =
      /^(tip:|pro tip:|💡|note:)/i.test(trimmed) ||
      /^(for a quick|all three|both have|each one)/i.test(trimmed);
    const isFollowUp =
      trimmed.endsWith("?") &&
      (trimmed.startsWith("Want") ||
        trimmed.startsWith("Would") ||
        trimmed.startsWith("Shall") ||
        trimmed.startsWith("Should") ||
        trimmed.startsWith("Which") ||
        trimmed.startsWith("What") ||
        trimmed.startsWith("How about") ||
        trimmed.startsWith("Need") ||
        trimmed.startsWith("Looking for") ||
        trimmed.startsWith("Interested"));

    if (isShortlistItem) {
      if (currentType !== "shortlist-item") flush();
      currentType = "shortlist-item";
      buffer.push(trimmed);
      flush();
    } else if (isTip) {
      flush();
      currentType = "tip";
      buffer.push(trimmed);
      flush();
    } else if (isFollowUp) {
      flush();
      currentType = "follow-up";
      buffer.push(trimmed);
      flush();
    } else {
      if (currentType !== "text") flush();
      currentType = "text";
      buffer.push(line);
    }
  }
  flush();
  return blocks;
}

function renderInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, j) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={j} className="font-semibold text-gray-900">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <Fragment key={j}>{part}</Fragment>;
  });
}

function renderShortlistItem(text: string, idx: number) {
  const dashSplit = text.split(/\s[—–-]\s/);
  const heading = dashSplit[0];
  const detail = dashSplit.slice(1).join(" — ");
  return (
    <div key={idx} className="concierge-shortlist-item">
      <div className="text-[13px] font-medium text-gray-900">
        {renderInline(heading)}
      </div>
      {detail && (
        <div className="mt-0.5 text-[13px] leading-relaxed text-gray-600">
          {renderInline(detail)}
        </div>
      )}
    </div>
  );
}

function AssistantContent({ text }: { text: string }) {
  const blocks = parseBlocks(text);

  return (
    <div className="concierge-response space-y-2.5">
      {blocks.map((block, i) => {
        switch (block.type) {
          case "shortlist-item":
            return renderShortlistItem(block.content, i);
          case "tip":
            return (
              <div key={i} className="concierge-tip">
                <Lightbulb size={12} className="mt-0.5 shrink-0 text-amber-500" />
                <span>{renderInline(block.content)}</span>
              </div>
            );
          case "follow-up":
            return (
              <div key={i} className="concierge-followup">
                <MessageCircle size={12} className="mt-0.5 shrink-0 text-blue-400" />
                <span>{renderInline(block.content)}</span>
              </div>
            );
          default:
            return (
              <div key={i} className="text-[13.5px] leading-relaxed text-gray-700">
                {block.content.split("\n").map((line, li) => (
                  <Fragment key={li}>
                    {li > 0 && <br />}
                    {renderInline(line)}
                  </Fragment>
                ))}
              </div>
            );
        }
      })}
    </div>
  );
}

export default function ChatMessage({
  message,
  isSelected,
  onSelect,
  onFeedback,
}: ChatMessageProps) {
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
          className={`rounded-2xl px-4 py-3.5 text-sm leading-relaxed ${
            isUser
              ? "bg-blue-600 text-white"
              : `bg-white text-gray-800 shadow-sm ring-1 ring-gray-100 ${
                  message.debug ? "cursor-pointer" : ""
                } ${isSelected ? "ring-2 ring-blue-400" : ""}`
          }`}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap">{message.content}</div>
          ) : (
            <AssistantContent text={message.content} />
          )}

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
