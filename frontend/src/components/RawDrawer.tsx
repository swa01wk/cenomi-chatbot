import { useState } from "react";
import { ChevronUp, ChevronDown, Copy, Check } from "lucide-react";
import type { DebugPayload } from "../types/chat";

interface RawDrawerProps {
  debug: DebugPayload;
}

type Tab = "prompt" | "state" | "trace";

export default function RawDrawer({ debug }: RawDrawerProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("trace");
  const [copied, setCopied] = useState(false);

  const tabData: Record<Tab, unknown> = {
    trace: debug.node_trace,
    state: debug.scene_summary,
    prompt: {
      playbook: debug.selected_playbook,
      strategy: debug.chosen_strategy,
      topic_blocks: debug.selected_topic_blocks,
      entities: debug.selected_entities,
      signals: debug.selected_semantic_signals,
    },
  };

  const raw = JSON.stringify(tabData[tab], null, 2);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(raw);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="border-t border-slate-700">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-300"
      >
        Raw Inspector
        {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>

      {open && (
        <div className="animate-fade-in px-4 pb-4">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex gap-1">
              {(["trace", "state", "prompt"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`rounded px-2.5 py-1 text-[10px] font-medium capitalize transition ${
                    tab === t
                      ? "bg-slate-600 text-slate-200"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-300"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <pre className="max-h-56 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-300">
            {raw}
          </pre>
        </div>
      )}
    </div>
  );
}
