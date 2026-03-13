import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { SendHorizonal } from "lucide-react";
import { MAX_MESSAGE_LENGTH } from "../lib/constants";

interface ChatInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!disabled) textareaRef.current?.focus();
  }, [disabled]);

  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setInput("");
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const charCount = input.length;
  const overLimit = charCount > MAX_MESSAGE_LENGTH;

  return (
    <div className="border-t border-gray-100 bg-white px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <div className="relative flex-1">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about stores, dining, entertainment..."
            disabled={disabled}
            rows={1}
            className="w-full resize-none rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 pr-12 text-sm leading-relaxed transition-colors focus:border-blue-400 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-400 disabled:opacity-50"
          />
          {charCount > 0 && (
            <span
              className={`absolute bottom-1.5 right-3 text-[10px] ${
                overLimit ? "text-red-500" : "text-gray-300"
              }`}
            >
              {charCount}/{MAX_MESSAGE_LENGTH}
            </span>
          )}
        </div>
        <button
          onClick={handleSend}
          disabled={disabled || !input.trim() || overLimit}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 active:scale-95 disabled:opacity-40 disabled:hover:bg-blue-600"
        >
          <SendHorizonal size={18} />
        </button>
      </div>
    </div>
  );
}
