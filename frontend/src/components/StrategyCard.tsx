import { BookOpen, Target } from "lucide-react";

interface StrategyCardProps {
  selectedPlaybook: string;
  playbookConfidence: number;
  matchedPlaybooks: string[];
  chosenStrategy: string;
  responseShape: string;
  expectedCandidates?: string[];
}

export default function StrategyCard({
  selectedPlaybook,
  playbookConfidence,
  matchedPlaybooks,
  chosenStrategy,
  responseShape,
  expectedCandidates,
}: StrategyCardProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        <BookOpen size={13} className="mt-0.5 shrink-0 text-blue-400" />
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-slate-200">
              {selectedPlaybook || "—"}
            </span>
            {playbookConfidence > 0 && (
              <ConfidenceBadge value={playbookConfidence} />
            )}
          </div>
          {matchedPlaybooks.length > 1 && (
            <div className="flex flex-wrap gap-1">
              {matchedPlaybooks
                .filter((p) => p !== selectedPlaybook)
                .map((p) => (
                  <span
                    key={p}
                    className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400"
                  >
                    {p}
                  </span>
                ))}
            </div>
          )}
          {expectedCandidates && expectedCandidates.length > 0 && (
            <div className="mt-1">
              <span className="text-[10px] text-slate-500">Expected candidates: </span>
              <div className="mt-0.5 flex flex-wrap gap-1">
                {expectedCandidates.map((c) => (
                  <span
                    key={c}
                    className="rounded bg-indigo-900/30 px-1.5 py-0.5 text-[10px] text-indigo-300"
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-start gap-2">
        <Target size={13} className="mt-0.5 shrink-0 text-purple-400" />
        <div className="text-xs">
          <span className="font-medium text-slate-200">
            {chosenStrategy.replace(/_/g, " ") || "—"}
          </span>
          {responseShape && (
            <span className="ml-2 text-slate-500">
              shape: {responseShape.replace(/_/g, " ")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 90
      ? "bg-green-900/40 text-green-400"
      : pct >= 70
        ? "bg-yellow-900/40 text-yellow-400"
        : "bg-red-900/40 text-red-400";

  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${color}`}>
      {pct}%
    </span>
  );
}
