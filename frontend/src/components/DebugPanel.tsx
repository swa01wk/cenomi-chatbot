import { Bug } from "lucide-react";
import TurnInspector from "./TurnInspector";
import RawDrawer from "./RawDrawer";
import type { ChatMessage } from "../types/chat";

interface DebugPanelProps {
  selectedTurn: ChatMessage | null;
  messages: ChatMessage[];
  selectedTurnId: string | null;
  onSelectTurn: (id: string) => void;
}

export default function DebugPanel({
  selectedTurn,
  messages,
  selectedTurnId,
  onSelectTurn,
}: DebugPanelProps) {
  const assistantTurns = messages.filter(
    (m) => m.role === "assistant" && m.debug,
  );

  return (
    <div className="flex h-full flex-col bg-[var(--color-debug-bg)] text-slate-100">
      <div className="flex items-center justify-between border-b border-slate-700 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Bug size={14} className="text-amber-400" />
          <h2 className="text-xs font-semibold text-slate-300">
            Pipeline Inspector
          </h2>
        </div>
        {selectedTurn?.debug && (
          <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[10px] text-slate-500">
            {selectedTurn.debug.total_latency_ms.toFixed(0)}ms
          </span>
        )}
      </div>

      {/* Turn selector */}
      {assistantTurns.length > 1 && (
        <div className="flex gap-1 border-b border-slate-800 px-4 py-2">
          {assistantTurns.map((m, i) => (
            <button
              key={m.id}
              onClick={() => onSelectTurn(m.id)}
              className={`rounded px-2.5 py-1 text-[10px] font-medium transition ${
                m.id === selectedTurnId
                  ? "bg-blue-600 text-white"
                  : "bg-slate-800 text-slate-400 hover:bg-slate-700"
              }`}
            >
              Turn {i + 1}
            </button>
          ))}
        </div>
      )}

      {/* Inspector content */}
      <div className="flex-1 overflow-y-auto p-4">
        {selectedTurn?.debug ? (
          <TurnInspector debug={selectedTurn.debug} />
        ) : (
          <div className="flex h-full items-center justify-center">
            <p className="text-center text-sm text-slate-600">
              {messages.length === 0
                ? "Send a message to see debug output"
                : "Select an assistant message to inspect"}
            </p>
          </div>
        )}
      </div>

      {/* Raw drawer */}
      {selectedTurn?.debug && <RawDrawer debug={selectedTurn.debug} />}
    </div>
  );
}
