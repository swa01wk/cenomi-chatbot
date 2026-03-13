import { useState } from "react";
import { ThumbsUp, ThumbsDown, Send } from "lucide-react";
import { FEEDBACK_REASONS, FEEDBACK_REASON_LABELS } from "../lib/constants";
import type { MessageFeedback } from "../types/chat";

interface FeedbackControlsProps {
  messageId: string;
  feedback: MessageFeedback;
  onFeedback: (messageId: string, update: Partial<MessageFeedback>) => void;
}

export default function FeedbackControls({
  messageId,
  feedback,
  onFeedback,
}: FeedbackControlsProps) {
  const [showReasons, setShowReasons] = useState(false);
  const [comment, setComment] = useState("");

  if (feedback.submitted) {
    return (
      <span className="animate-fade-in text-xs text-gray-400">
        {feedback.rating === "up" ? "Thanks for the feedback!" : "Feedback recorded — we'll improve"}
      </span>
    );
  }

  const handleUp = () => {
    onFeedback(messageId, { rating: "up", submitted: true });
  };

  const handleDown = () => {
    onFeedback(messageId, { rating: "down" });
    setShowReasons(true);
  };

  const toggleTag = (tag: string) => {
    const current = feedback.tags;
    const next = current.includes(tag)
      ? current.filter((t) => t !== tag)
      : [...current, tag];
    onFeedback(messageId, { tags: next });
  };

  const submitNegative = () => {
    onFeedback(messageId, {
      comment,
      submitted: true,
    });
  };

  return (
    <div className="animate-fade-in space-y-2">
      <div className="flex items-center gap-1">
        <button
          onClick={handleUp}
          className={`rounded-md p-1.5 transition ${
            feedback.rating === "up"
              ? "bg-green-50 text-green-600"
              : "text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          }`}
          title="Helpful"
        >
          <ThumbsUp size={14} />
        </button>
        <button
          onClick={handleDown}
          className={`rounded-md p-1.5 transition ${
            feedback.rating === "down"
              ? "bg-red-50 text-red-500"
              : "text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          }`}
          title="Not helpful"
        >
          <ThumbsDown size={14} />
        </button>
      </div>

      {showReasons && feedback.rating === "down" && (
        <div className="animate-fade-in space-y-2.5 rounded-lg border border-gray-200 bg-gray-50 p-3">
          <p className="text-xs font-medium text-gray-500">
            What went wrong?
          </p>
          <div className="flex flex-wrap gap-1.5">
            {FEEDBACK_REASONS.map((reason) => (
              <button
                key={reason}
                onClick={() => toggleTag(reason)}
                className={`rounded-full px-2.5 py-1 text-xs transition ${
                  feedback.tags.includes(reason)
                    ? "bg-red-100 text-red-700 ring-1 ring-red-200"
                    : "bg-white text-gray-500 ring-1 ring-gray-200 hover:bg-gray-100"
                }`}
              >
                {FEEDBACK_REASON_LABELS[reason] ?? reason}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Optional: tell us more..."
              className="flex-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs focus:border-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
              onKeyDown={(e) => {
                if (e.key === "Enter" && (feedback.tags.length > 0 || comment.trim())) {
                  submitNegative();
                }
              }}
            />
            <button
              onClick={submitNegative}
              disabled={feedback.tags.length === 0 && !comment.trim()}
              className="flex items-center gap-1 rounded-md bg-gray-800 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-gray-700 disabled:opacity-40"
            >
              <Send size={12} />
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
